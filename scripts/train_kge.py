"""Trains the KGC-Benchmark embedding models on every dataset under exp_datasets/.

Each dataset is trained the way KGC.py of VANILLA (the KGC-Benchmark project) trains
a graph, with the settings of its input.json: PyKEEN's pipeline with the sLCWA
training loop, 100 epochs, batch size 32, embedding dimension 50, filtered negative
sampling and seed 1235, plus inverse triples. CompGCN trains with its own batch size
and learning rate (`Settings.compgcn_batch_size`, `compgcn_learning_rate`). The
YAGO3-10 datasets and their copies train the other models at batch size 4096
(`YAGO_BATCH_SIZE`), which `--batch-size` doesn't change. Every
dataset folder holds VANILLA's 80/20 split of the dataset, `train` and `test`, made
by scripts/split_vanilla.py with KGC.py's own code; models train on `train` and are
evaluated on `test`. A `valid` file is optional: none of the current datasets has
one, and a missing `valid` is read as empty (it would only add known answers to
filter out of the ranking, as there is no early stopping).

The output has KGC.py's layout, so `report_results.py --root <output>` reads it:
`<output>/<dataset>/` holds the splits and `<model>/` the trained model and its
metrics. docs/training-protocol.md describes every setting.

The script can be stopped at any moment, by Ctrl-C, a kill or a power cut. Running
the same command again skips the finished models and resumes the others from
`<checkpoints>/<dataset>/<model>.pt`, which is replaced after every epoch. At most
the epoch in progress is lost. Evaluation has no checkpoints, so a model stopped
during its evaluation repeats only that evaluation. PyKEEN logs a warning about a
missing stopper configuration on each resume, which is harmless: no stopper is used.
`--workers N` trains N runs at once, each in its own process. At batch size 32 a run
keeps one CPU core busy but uses about 17% of an RTX 3090. With 4 workers the GPU is
fully busy and each epoch takes 1.4 to 1.8 times as long as alone, so 4 workers
train about 2.6 times as fast as one. Each run keeps its seed
and settings, so the results are the ones a single process gives. The workers walk
the same list of runs, smallest dataset first (largest first with --largest-first),
and each claims the next run no one
holds. Locks make this safe across commands too: a run (`<model>.lock`) is trained by
one process at a time, and a dataset (`.lock`) is shared by the processes training
its runs. With workers, the terminal shows a line every 10 epochs per run instead of
progress bars, and Ctrl-C (or SIGINT/SIGTERM to the main process) stops every worker.

`--skip-models CompGCN` leaves a model out of a run. Running the command again without
the flag then trains only that model, as the finished ones are skipped.
`--skip-datasets` does the same for datasets, as in
`--skip-datasets YAGO3-10 YAGO3-10_1_1`.

`--upload` commits every finished run to the checked-out branch and pushes it, after
each run and at the end, so several machines collect their results in one place
(upload_results.py).

A model that fails, usually by running out of GPU memory, is logged and skipped. The
script goes on with the others, exits with status 1, and retries it on the next run.

The state of the experiment is on disk while it runs, so results can be read before
it ends. `<output>/progress.csv` has one row per model and dataset of the runs started
so far, with its status (pending, training, evaluating, finished or failed), epoch,
last loss, times and test metrics, and is rewritten after every epoch.
`<output>/<dataset>/status/<model>.json` holds the same fields and the loss of every
epoch. Metrics are those of `results.json`: both sides, realistic ranks. A model stays
`training` or `evaluating` if the process was killed, and `updated_at` then stops
changing. `report_results.py` can run at any moment and reads the finished models.
A model left out with `--skip-models` has no row until a run includes it.

Needs pykeen, torch, pandas, matplotlib and seaborn, pinned in requirements.txt.
"""

import argparse
import csv
import fcntl
import gc
import hashlib
import io
import json
import logging
import multiprocessing
import multiprocessing.connection
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd
import pykeen
import torch
import upload_results
from merge_splits import SPLITS, read_id_names, read_split
from pykeen.pipeline import pipeline, plot_losses
from pykeen.training import SLCWATrainingLoop
from pykeen.training.callbacks import TrainingCallback
from pykeen.triples import TriplesFactory

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

logger = logging.getLogger("train_kge")

MODELS = ("TransE", "TransH", "TransD", "RotatE", "ComplEx", "TuckER", "CompGCN")
RESULTS_FILE = "results.json"
SETTINGS_FILE = "settings.json"
LOCK_FILE = ".lock"
SETTINGS_LOCK_FILE = ".settings.lock"
# With workers, each run logs a line every this many epochs instead of progress bars.
LOG_EVERY_EPOCHS = 10
STATUS_DIR = "status"
PROGRESS_FILE = "progress.csv"
# Suffixes (after "_") of the folders of synthetic copies; the other folders are the
# base datasets. scripts/split_copies.py writes the copies' splits.
COPY_SETS = ("ours", "kbgen", "pygraft")
# The splits a dataset folder must have. `valid` is optional: VANILLA's 80/20 split
# has none, and a missing `valid` is read as empty.
REQUIRED_SPLITS = ("train", "test")
# CPU threads of each worker; PyTorch would otherwise start one per core.
DEFAULT_THREADS = 4
# The rule-level copies (docs/rule-levels.md) are named after the copy set and the
# level: `french_kbgen_r25` is kbgen's copy of `french` at level r25. They are in the
# sets `kbgen` and `ours`, which since the full copies were dropped hold only them,
# and `kbgen-levels` and `ours-levels` select the same folders.
LEVEL_SETS = ("ours-levels", "kbgen-levels")
LEVEL_COPY = re.compile(r"_(ours|kbgen|pygraft)_(r\d+|std1)$")
# The YAGO3-10 datasets (base datasets whose name starts with this prefix, and their
# copies) train every model but CompGCN at YAGO_BATCH_SIZE instead of
# Settings.batch_size: at batch size 32 a run took 14 to 31 hours on a MIG 1g.24gb
# slice, and at 4096 the slowest model (TransD) takes about 42 minutes on YAGO3-10.
YAGO_PREFIX = "YAGO"
YAGO_BATCH_SIZE = 4096
LOG_FILE = Path("logs/train_kge.log")
# Each training run's own log, `<output>/<dataset>/logs/<model>.log`, with every
# message of the run: DEBUG of the loggers in RUN_LOG_DEBUG, INFO of the others.
RUN_LOG_DIR = "logs"
RUN_LOG_DEBUG = ("train_kge", "pykeen", "torch")
# Per-epoch and per-run details, kept out of the terminal and LOG_FILE.
run_logger = logging.getLogger("train_kge.run")
# The command, passed to the workers for their run logs.
COMMAND_ENV = "TRAIN_KGE_COMMAND"
INTERRUPTED_STATUS = 130
# A worker whose CUDA context failed exits with this status, and the main process
# starts a new one on the same device: after an error such as "CUDA error: unknown
# error" every later CUDA call of the process fails too. A run or trial that meets
# MAX_CUDA_FAULTS such errors is recorded as failed instead of tried again.
WORKER_RESTART_STATUS = 75
MAX_CUDA_FAULTS = 3
# Our name of each metric and its key in results.json.
REPORTED_METRICS = {
    "mrr": "inverse_harmonic_mean_rank",
    "mean_rank": "arithmetic_mean_rank",
    "hits_at_1": "hits_at_1",
    "hits_at_3": "hits_at_3",
    "hits_at_5": "hits_at_5",
    "hits_at_10": "hits_at_10",
}
PROGRESS_COLUMNS = (
    "dataset",
    "model",
    "status",
    "epoch",
    "num_epochs",
    "last_loss",
    "train_seconds",
    "evaluate_seconds",
    *REPORTED_METRICS,
    "error",
    "updated_at",
)


@dataclass(frozen=True)
class Settings:
    """Training settings, defaulting to KGC-Benchmark's input.json and KGC.py.

    CompGCN is the exception: it encodes the whole graph at every step, so at batch
    size 32 it would take about 60 days per YAGO3-10 dataset. It trains with its own
    batch size and learning rate, the fastest pair measured on this machine (100
    epochs on YAGO3-10 in about 25 minutes on a MIG 1g.24gb slice); its results are
    compared across datasets, not with the other models.
    """

    num_epochs: int = 100
    batch_size: int = 32
    embedding_dim: int = 50
    random_seed: int = 1235
    create_inverse_triples: bool = True
    filtered_negative_sampling: bool = True
    compgcn_batch_size: int = 131072
    compgcn_learning_rate: float = 0.03


Splits = tuple[TriplesFactory, TriplesFactory, TriplesFactory]


@dataclass
class ModelStatus:
    """The state of one model on one dataset, stored as a JSON file."""

    dataset: str
    model: str
    num_epochs: int
    status: str = "pending"
    epoch: int = 0
    losses: list[float] = field(default_factory=list)
    train_seconds: float = 0.0
    evaluate_seconds: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    updated_at: str | None = None


def status_path(output: Path, dataset: str, model: str) -> Path:
    return output / dataset / STATUS_DIR / f"{model}.json"


def load_status(output: Path, dataset: str, model: str, num_epochs: int) -> ModelStatus:
    """Returns a model's recorded status, or a new pending one."""
    path = status_path(output, dataset, model)
    if path.is_file():
        return ModelStatus(**json.loads(path.read_text(encoding="utf-8")))
    return ModelStatus(dataset, model, num_epochs)


def write_status(output: Path, status: ModelStatus) -> None:
    status.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = status_path(output, status.dataset, status.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomically(path, json.dumps(asdict(status), indent=2) + "\n")


def write_progress(output: Path) -> None:
    """Rewrites the progress table from the status files of every dataset."""
    rows = []
    for path in sorted(output.glob(f"*/{STATUS_DIR}/*.json")):
        status = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                **status,
                **status["metrics"],
                "last_loss": status["losses"][-1] if status["losses"] else None,
            }
        )
    table = io.StringIO()
    writer = csv.DictWriter(
        table, PROGRESS_COLUMNS, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomically(output / PROGRESS_FILE, table.getvalue())


def record_status(output: Path, status: ModelStatus) -> None:
    """Saves a status and the progress table. A failure here doesn't stop training."""
    try:
        write_status(output, status)
        write_progress(output)
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Could not record the status of %s.", status.model)


def mark_finished(status: ModelStatus, results_file: Path) -> None:
    """Fills a status with the outcome stored in a finished model's results file."""
    results = json.loads(results_file.read_text(encoding="utf-8"))
    realistic = results["metrics"]["both"]["realistic"]
    status.status = "finished"
    # Early stopping can end a run before num_epochs.
    status.epoch = len(results["losses"])
    status.losses = results["losses"]
    status.evaluate_seconds = results["times"]["evaluation"]
    status.metrics = {name: realistic[key] for name, key in REPORTED_METRICS.items()}
    status.error = None
    if not status.train_seconds:
        status.train_seconds = results["times"]["training"]


class StatusCallback(TrainingCallback):
    """Records a model's status after every epoch."""

    def __init__(self, output: Path, status: ModelStatus, log_every: int = 0) -> None:
        super().__init__()
        self.output = output
        self.status = status
        self.log_every = log_every
        self.last_time: float | None = None

    def pre_batch(self, **kwargs: Any) -> None:
        if self.last_time is None:
            self.last_time = time.monotonic()

    def post_epoch(self, epoch: int, epoch_loss: float, **kwargs: Any) -> None:
        now = time.monotonic()
        seconds = now - self.last_time if self.last_time is not None else 0.0
        self.status.train_seconds += seconds
        self.last_time = now
        memory = (
            torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0
        )
        run_logger.info(
            "epoch %d/%d: loss %.8f, %.2f s, %.1f s of training so far, peak GPU "
            "memory %.2f GB.",
            epoch,
            self.status.num_epochs,
            epoch_loss,
            seconds,
            self.status.train_seconds,
            memory,
        )
        self.status.epoch = epoch
        # Holds the losses restored from the checkpoint too.
        self.status.losses = list(self.training_loop.losses_per_epochs)
        record_status(self.output, self.status)
        if self.log_every and epoch % self.log_every == 0:
            per_epoch = self.status.train_seconds / epoch
            left = per_epoch * (self.status.num_epochs - epoch)
            logger.info(
                "%s %s: epoch %d/%d, %.1f s per epoch, %.0f min left.",
                self.status.dataset,
                self.status.model,
                epoch,
                self.status.num_epochs,
                per_epoch,
                left / 60,
            )

    def post_train(self, losses: list[float], **kwargs: Any) -> None:
        self.status.status = "evaluating"
        record_status(self.output, self.status)


class AtomicCheckpointLoop(SLCWATrainingLoop):
    """sLCWA training whose checkpoint survives being killed while it is written.

    PyKEEN writes over the checkpoint in place, so a kill during the write would leave
    a truncated file and lose the run. This writes a new file and renames it.
    """

    def _save_state(self, path: Any, *args: Any, **kwargs: Any) -> None:
        temporary = Path(f"{path}.tmp")
        super()._save_state(temporary, *args, **kwargs)
        with temporary.open("rb") as f:
            os.fsync(f.fileno())
        os.replace(temporary, path)

    def _load_state(self, *args: Any, **kwargs: Any) -> Any:
        # Also called to restore the best epoch when early stopping is used.
        state = super()._load_state(*args, **kwargs)
        logger.info("Loaded the state after epoch %d.", self._epoch)
        return state


# A copy's folder: `<base>_<set>`, or `<base>_<set>_<tag>` for a variant of the copy,
# such as french_ours_std1 (made from the std confidence 1 rules).
COPY_NAME = re.compile(rf"(?P<base>.+)_(?P<set>{'|'.join(COPY_SETS)})(?:_[^_]+)?")


def dataset_set(name: str) -> str:
    """The set a dataset folder belongs to: a COPY_SETS name, or "base"."""
    match = COPY_NAME.fullmatch(name)
    return match["set"] if match else "base"


def in_set(name: str, set_name: str) -> bool:
    """Whether a dataset folder is in a set of `--set`: a LEVEL_SETS name keeps only
    the rule-level copies of that generator, which its COPY_SETS name also keeps."""
    if set_name in LEVEL_SETS:
        match = LEVEL_COPY.search(name)
        return match is not None and f"{match.group(1)}-levels" == set_name
    return dataset_set(name) == set_name


def base_dataset(name: str) -> str:
    """The base dataset of a copy, or the dataset itself."""
    match = COPY_NAME.fullmatch(name)
    return match["base"] if match else name


def dataset_settings(settings: Settings, name: str) -> Settings:
    """The settings a dataset trains with: YAGO_BATCH_SIZE for the YAGO3-10 datasets
    and their copies, so a copy trains as its source does; `settings` otherwise."""
    if base_dataset(name).startswith(YAGO_PREFIX):
        return replace(settings, batch_size=YAGO_BATCH_SIZE)
    return settings


def visible_devices() -> list[str]:
    """The CUDA devices a worker can be given, as CUDA_VISIBLE_DEVICES entries.

    CUDA_VISIBLE_DEVICES if it is set; otherwise every MIG instance from
    `nvidia-smi -L`, plus the GPUs without MIG. A CUDA process sees only one MIG
    instance, so without this every worker would use the first one.
    """
    env = os.environ.get("CUDA_VISIBLE_DEVICES")
    if env is not None:
        return [d.strip() for d in env.split(",") if d.strip()]
    try:
        listing = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    devices: list[str] = []
    gpu: str | None = None
    for line in listing.splitlines():
        match = re.search(r"\(UUID: ((GPU|MIG)-[^)]+)\)", line)
        if not match:
            continue
        if match.group(2) == "GPU":
            if gpu:
                devices.append(gpu)
            gpu = match.group(1)
        else:
            gpu = None  # A GPU with MIG on runs only on its instances.
            devices.append(match.group(1))
    if gpu:
        devices.append(gpu)
    return devices


def set_threads(threads: int) -> None:
    """Limits the CPU threads PyTorch uses in this process."""
    torch.set_num_threads(threads)


def run_log_filter(record: logging.LogRecord) -> bool:
    """DEBUG of the loggers in RUN_LOG_DEBUG, INFO and above of the others."""
    if record.levelno >= logging.INFO:
        return True
    return any(
        record.name == name or record.name.startswith(f"{name}.")
        for name in RUN_LOG_DEBUG
    )


@contextmanager
def run_log(path: Path) -> Iterator[None]:
    """Copies every log message to `path` while the block runs.

    The file is appended to, so a resumed run adds to the log of its earlier parts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.addFilter(run_log_filter)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"
        )
    )
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(level)
        handler.close()


def log_run_header(
    folder: Path,
    model: str,
    settings: Settings,
) -> None:
    """Writes what a run log needs to reproduce the run."""
    device = os.environ.get("CUDA_VISIBLE_DEVICES", "default")
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
    run_logger.info("===== %s on %s =====", model, folder.name)
    run_logger.info("Command: %s", os.environ.get(COMMAND_ENV, " ".join(sys.argv)))
    run_logger.info(
        "Host %s, PID %d, process %s, device %s (%s).",
        socket.gethostname(),
        os.getpid(),
        multiprocessing.current_process().name,
        device,
        gpu,
    )
    run_logger.info(
        "Python %s, PyTorch %s (CUDA %s, cuDNN %s), PyKEEN %s, NumPy %s, %d threads.",
        platform.python_version(),
        torch.__version__,
        torch.version.cuda,
        torch.backends.cudnn.version(),  # type: ignore[no-untyped-call]
        pykeen.get_version(),
        np.__version__,
        torch.get_num_threads(),
    )
    run_logger.info("Settings: %s", asdict(settings))
    for name in SPLITS:
        path = folder / name
        if not path.is_file():
            run_logger.info("Split %s: absent, read as empty.", path)
            continue
        run_logger.info(
            "Split %s: %d lines, sha256 %s.",
            path,
            count_lines(path),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def find_datasets(
    root: Path,
    names: list[str] | None,
    dataset_set_name: str | None = None,
    keep_order: bool = False,
) -> list[Path]:
    """Returns the folders under root with `train` and `test` (REQUIRED_SPLITS),
    smallest `train` first, or in the order of `names` with `keep_order`.

    Named datasets are returned whatever their set; otherwise, with
    `dataset_set_name`, only the folders of that set ("all" keeps every set).

    Raises:
        ValueError: If a requested dataset doesn't exist.
    """
    folders = {
        d.name: d
        for d in root.iterdir()
        if all((d / s).is_file() for s in REQUIRED_SPLITS)
    }
    if names is None and dataset_set_name not in (None, "all"):
        folders = {n: d for n, d in folders.items() if in_set(n, dataset_set_name)}
    if names is not None:
        missing = sorted(set(names) - set(folders))
        if missing:
            raise ValueError(f"Datasets not found under {root}: {', '.join(missing)}")
        folders = {name: folders[name] for name in names}
        if keep_order:
            return list(folders.values())
    # Lines, not bytes: YAGO3-10's splits hold integer IDs, so its train file is
    # smaller in bytes than those of datasets with half its triples.
    return sorted(folders.values(), key=lambda d: count_lines(d / "train"))


def count_lines(path: Path) -> int:
    """The number of lines of a file, read in binary chunks."""
    with path.open("rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


@contextmanager
def dataset_lock(path: Path) -> Iterator[None]:
    """Holds a dataset's lock. The OS releases it however the process ends.

    Raises:
        RuntimeError: If another process holds the lock.
    """
    with path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Another process holds {path}.") from None
        yield


@contextmanager
def try_lock(path: Path, shared: bool = False) -> Iterator[bool]:
    """Yields whether the lock was taken, without waiting for it.

    A shared lock lets other shared holders in and keeps exclusive ones out, so the
    workers training a dataset's runs keep out a run of dataset_lock on it.
    """
    with path.open("a") as f:
        try:
            fcntl.flock(f, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


@contextmanager
def wait_lock(path: Path) -> Iterator[None]:
    """Holds an exclusive lock, waiting for it if another process holds it."""
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def check_settings(
    path: Path, settings: Settings | Mapping[str, Any], folder: Path
) -> None:
    """Records a dataset's settings, or checks them against the recorded ones.

    Resuming with other settings, or from changed split files, would mix runs that
    can't be compared with each other.

    Raises:
        ValueError: If the dataset was started with other settings or other splits.
    """
    fields = asdict(settings) if isinstance(settings, Settings) else dict(settings)
    current = {
        **fields,
        "splits_sha256": {
            # A missing split hashes as an empty file, so a run recorded with an
            # empty `valid` still matches once the file is removed.
            name: hashlib.sha256(
                (folder / name).read_bytes() if (folder / name).is_file() else b""
            ).hexdigest()
            for name in SPLITS
        },
    }
    if not path.is_file():
        write_text_atomically(path, json.dumps(current, indent=2) + "\n")
        return
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if recorded != current:
        changed = sorted(k for k in current if recorded.get(k) != current[k])
        raise ValueError(
            f"{path} was recorded with other values of {', '.join(changed)}. Pass "
            "the original settings, or another --output to train from scratch."
        )


def write_text_atomically(path: Path, text: str) -> None:
    """Writes a text file so that a kill leaves either the whole file or none."""
    # The process ID lets two runs write the same progress table.
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def load_splits(folder: Path, settings: Settings) -> Splits:
    """Returns the training, validation and testing triples of a dataset folder.

    As in PyKEEN's PathDataset, only `train` defines the entities and relations and
    gets inverse triples. A valid or test triple using a term that `train` lacks is
    dropped, because no model has an embedding for that term. A folder with an
    `id_to_name.json` file (YAGO3-10) gets the names of its integer IDs, as its unified
    file does.
    """
    id_names = read_id_names(folder)
    training = TriplesFactory.from_labeled_triples(
        triples=np.array(read_split(folder / "train", id_names)),
        create_inverse_triples=settings.create_inverse_triples,
    )
    # A missing split (the `valid` of a VANILLA 80/20 split) is read as 0 x 3.
    validation, testing = (
        TriplesFactory.from_labeled_triples(
            triples=np.array(
                read_split(path, id_names) if (path := folder / name).is_file() else [],
                dtype=str,
            ).reshape(-1, 3),
            entity_to_id=training.entity_to_id,
            relation_to_id=training.relation_to_id,
        )
        for name in ("valid", "test")
    )
    return training, validation, testing


def save_split(factory: TriplesFactory, path: Path) -> None:
    """Writes a split in the format KGC.py writes, unless it is already there."""
    if path.is_file():
        return
    temporary = path.with_name(f"{path.name}.tmp")
    pd.DataFrame(factory.triples).to_csv(temporary, index=False, sep="\t", header=False)
    os.replace(temporary, path)


def compgcn_batch_size(settings: Settings, training: TriplesFactory) -> int:
    """CompGCN's batch size, capped at the training instances (inverse triples
    included): a larger batch leaves no full batch, and PyKEEN drops the incomplete
    one of a model with batch norm. A dataset that small trains on one batch."""
    instances = training.num_triples * (2 if training.create_inverse_triples else 1)
    return min(settings.compgcn_batch_size, instances)


def train_model(
    model: str,
    splits: Splits,
    output: Path,
    checkpoints: Path,
    settings: Settings,
    root: Path,
    status: ModelStatus,
    progress_bars: bool = True,
) -> Any:
    """Trains and evaluates one model, resuming from its checkpoint if it has one.

    The results appear in `<output>/<model>/` all at once, when they are complete, so
    the presence of its results file is what marks a model as finished.

    Without `progress_bars`, a line is logged every LOG_EVERY_EPOCHS epochs instead.

    Returns:
        PyKEEN's pipeline result.
    """
    gc.collect()
    torch.cuda.empty_cache()

    status.status = "training"
    status.error = None
    record_status(root, status)

    training, validation, testing = splits
    run_logger.info(
        "Triples: %d train (%d entities, %d relations, inverse triples %s), %d "
        "valid, %d test.",
        training.num_triples,
        training.num_entities,
        training.num_relations,
        training.create_inverse_triples,
        validation.num_triples,
        testing.num_triples,
    )
    checkpoint_name = f"{model}.pt"
    kwargs: dict[str, Any] = {
        "training": training,
        "validation": validation,
        "testing": testing,
        "model": model,
        "training_loop": AtomicCheckpointLoop,
        "model_kwargs": {"embedding_dim": settings.embedding_dim},
        "negative_sampler_kwargs": {"filtered": settings.filtered_negative_sampling},
        "training_kwargs": {
            "num_epochs": settings.num_epochs,
            "batch_size": compgcn_batch_size(settings, training)
            if model == "CompGCN"
            else settings.batch_size,
            "use_tqdm": progress_bars,
            "use_tqdm_batch": progress_bars,
            "checkpoint_directory": checkpoints,
            "checkpoint_name": checkpoint_name,
            # Minutes between checkpoints; 0 is after every epoch.
            "checkpoint_frequency": 0,
            "callbacks": StatusCallback(
                root, status, 0 if progress_bars else LOG_EVERY_EPOCHS
            ),
        },
        "random_seed": settings.random_seed,
    }
    if model == "CompGCN":
        kwargs["optimizer_kwargs"] = {"lr": settings.compgcn_learning_rate}
    if not progress_bars:
        kwargs["evaluation_kwargs"] = {"use_tqdm": False}
    run_logger.info(
        "Pipeline arguments: %s",
        {
            k: v
            for k, v in kwargs.items()
            if k not in ("training", "validation", "testing")
        },
    )
    result = pipeline(**kwargs)
    run_logger.info(
        "Test metrics: %s", json.dumps(result.metric_results.to_flat_dict())
    )

    saving = checkpoints / f"{model}.saving"
    if saving.exists():
        shutil.rmtree(saving)
    result.save_to_directory(saving)
    save_loss_plot(result, saving / "loss_plot.png")
    os.replace(saving, output / model)
    (checkpoints / checkpoint_name).unlink()
    mark_finished(status, output / model / RESULTS_FILE)
    record_status(root, status)
    return result


def save_loss_plot(result: Any, path: Path) -> None:
    """Saves the training loss plot. A failure here doesn't discard the training."""
    try:
        plot_losses(result)  # type: ignore[no-untyped-call]
        plt.savefig(path, dpi=300)
    except Exception:
        logger.exception("Could not write %s.", path)
    finally:
        plt.close()


Job = tuple[Path, Path, Path, str]
"""A run: its results folder, checkpoints folder, split folder, model and whether its
trained model is kept."""


def run_jobs(
    jobs: list[Job],
    settings: Settings,
    started_at: str,
    progress_bars: bool,
    upload: bool = False,
) -> tuple[int, int]:
    """Trains every run of `jobs` that no other process holds, in order.

    Every message of a run goes to its own log, `<dataset>/logs/<model>.log` in its
    results folder, with the command, versions, device, settings, splits, every
    epoch's loss and time, the metrics and any error with its traceback.

    A run is skipped if it is finished, held by another process, or failed earlier
    in this command (its status was updated after `started_at`), so a worker doesn't
    retry what another worker just failed. The next command retries failed runs.

    With `upload`, the finished runs are uploaded after each run (see
    upload_results.py); a failed upload is logged and retried after the next run.

    Returns:
        The numbers of runs that failed and of runs skipped because another process
        held them.
    """
    failed = busy = 0
    loaded: tuple[Path, Splits] | None = None
    for output, checkpoints, folder, model in jobs:
        dataset_output = output / folder.name
        dataset_checkpoints = checkpoints / folder.name
        if (dataset_output / model / RESULTS_FILE).is_file():
            continue
        status = load_status(output, folder.name, model, settings.num_epochs)
        if status.status == "failed" and (status.updated_at or "") >= started_at:
            continue
        dataset_output.mkdir(parents=True, exist_ok=True)
        dataset_checkpoints.mkdir(parents=True, exist_ok=True)
        with (
            try_lock(dataset_checkpoints / LOCK_FILE, shared=True) as dataset_free,
            try_lock(dataset_checkpoints / f"{model}.lock") as run_free,
        ):
            if not (dataset_free and run_free):
                busy += 1
                continue
            # Another process may have finished it since the check above.
            if (dataset_output / model / RESULTS_FILE).is_file():
                continue
            status = load_status(output, folder.name, model, settings.num_epochs)
            log_path = dataset_output / RUN_LOG_DIR / f"{model}.log"
            with run_log(log_path):
                outcome = train_job(
                    output,
                    checkpoints,
                    folder,
                    model,
                    dataset_settings(settings, folder.name),
                    status,
                    loaded,
                    progress_bars,
                )
            if outcome is None:
                failed += 1
                continue
            loaded = outcome
            if upload:
                # Don't queue behind another worker's upload: it, or a later one,
                # sends this run, and the command's last upload sends the rest.
                upload_finished(output, wait=False)
    return failed, busy


def train_job(
    output: Path,
    checkpoints: Path,
    folder: Path,
    model: str,
    settings: Settings,
    status: ModelStatus,
    loaded: tuple[Path, Splits] | None,
    progress_bars: bool,
) -> tuple[Path, Splits] | None:
    """Trains one claimed run. Returns the loaded splits, or None if it failed."""
    dataset_output = output / folder.name
    dataset_checkpoints = checkpoints / folder.name
    try:
        log_run_header(folder, model, settings)
        if loaded is None or loaded[0] != folder:
            loaded = None
            with wait_lock(dataset_checkpoints / SETTINGS_LOCK_FILE):
                check_settings(dataset_output / SETTINGS_FILE, settings, folder)
                logger.info("%s: loading the splits.", folder.name)
                splits = load_splits(folder, settings)
                for name, factory in zip(SPLITS, splits, strict=True):
                    if factory.num_triples:  # no file for an empty `valid`
                        save_split(factory, dataset_output / name)
            loaded = (folder, splits)
        logger.info("%s: training %s (%s).", folder.name, model, output)
        train_model(
            model,
            loaded[1],
            dataset_output,
            dataset_checkpoints,
            settings,
            output,
            status,
            progress_bars=progress_bars,
        )
    except Exception as error:
        faults = dataset_checkpoints / f"{model}.cuda_errors"
        if is_cuda_fault(error) and count_cuda_fault(faults) < MAX_CUDA_FAULTS:
            # The run keeps its checkpoint and status; a new process resumes it.
            logger.exception(
                "%s: %s hit a CUDA error (%s); this worker restarts.",
                folder.name,
                model,
                output,
            )
            raise CudaFaultError from error
        # One model running out of memory must not stop the other runs.
        logger.exception("%s: %s failed (%s).", folder.name, model, output)
        status.status = "failed"
        status.error = f"{type(error).__name__}: {error}"
        record_status(output, status)
        return None
    logger.info(
        "%s: finished %s in %.0f s of training, MRR %.4f, Hits@10 %.4f (%s).",
        folder.name,
        model,
        status.train_seconds,
        status.metrics["mrr"],
        status.metrics["hits_at_10"],
        output,
    )
    run_logger.info("Status: %s", json.dumps(asdict(status)))
    return loaded


def upload_finished(output: Path, wait: bool = True) -> None:
    """Uploads the finished runs; a failure is logged and doesn't stop training.

    Without `wait`, it skips the upload if another process is uploading: that or a
    later upload sends the run, and the command's last upload waits and sends the rest.
    """
    try:
        upload_results.upload(output, wait=wait)
    except Exception:
        logger.exception("Could not upload the results; the next upload retries.")


def setup_logging(worker: str | None = None) -> None:
    """Logs to the terminal and to LOG_FILE, naming the worker if there is one.

    The terminal shows this script's lines and every warning; the log file also keeps
    PyKEEN's other messages.
    """
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.addFilter(
        lambda record: record.name == logger.name or record.levelno >= logging.WARNING
    )
    shared = logging.FileHandler(LOG_FILE)
    shared.setLevel(logging.INFO)
    # The run logs keep PyKEEN's checkpoint lines and the per-epoch lines.
    shared.addFilter(
        lambda record: (
            record.levelno >= logging.WARNING
            or record.name not in ("pykeen.training.training_loop", run_logger.name)
        )
    )
    name = f"{worker} " if worker else ""
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s {name}%(name)s: %(message)s",
        handlers=[console, shared],
        force=True,
    )
    # Warnings (PyTorch's, PyKEEN's) become log messages, so the run logs keep them.
    logging.captureWarnings(True)


def stop_on_sigterm(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


class CudaFaultError(Exception):
    """The worker's CUDA context failed; it exits so a new process takes over."""


def is_cuda_fault(error: BaseException) -> bool:
    """Whether an error broke the process's CUDA context. Running out of memory
    doesn't: PyKEEN and the next run recover from it."""
    if isinstance(error, torch.OutOfMemoryError):
        return False
    accelerator_error = getattr(torch, "AcceleratorError", None)
    if accelerator_error is not None and isinstance(error, accelerator_error):
        return True
    return "CUDA error" in str(error)


def count_cuda_fault(path: Path) -> int:
    """Adds one to the CUDA errors counted in `path` and returns the count."""
    count = int(path.read_text()) + 1 if path.is_file() else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomically(path, f"{count}\n")
    return count


def supervise_workers(
    count: int,
    target: Callable[..., None],
    worker_args: Callable[[str], tuple[Any, ...]],
    devices: list[str],
    threads: int,
) -> list[int | None]:
    """Runs `count` spawned workers and returns their exit codes.

    Worker i gets device i of `devices` in turn, through CUDA_VISIBLE_DEVICES, which a
    spawned process reads before CUDA starts. A worker that exits with
    WORKER_RESTART_STATUS is started again, as a new process on the same device.
    Ctrl-C or SIGTERM stops every worker and raises KeyboardInterrupt.
    """
    # CUDA can't be used in a forked process.
    context = multiprocessing.get_context("spawn")

    def start(i: int) -> Any:
        name = f"worker-{i}"
        process = context.Process(target=target, name=name, args=worker_args(name))
        keys = ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS")
        saved = {k: os.environ.get(k) for k in keys}
        os.environ["OMP_NUM_THREADS"] = str(threads)
        if devices:
            os.environ["CUDA_VISIBLE_DEVICES"] = devices[(i - 1) % len(devices)]
        try:
            process.start()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return process

    running = {i: start(i) for i in range(1, count + 1)}
    codes: dict[int, int | None] = {}
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        while running:
            multiprocessing.connection.wait([p.sentinel for p in running.values()])
            for i, process in list(running.items()):
                if process.is_alive():
                    continue
                process.join()
                if process.exitcode == WORKER_RESTART_STATUS:
                    logger.warning(
                        "worker-%d had a CUDA error; starting a new process on %s.",
                        i,
                        devices[(i - 1) % len(devices)] if devices else "the GPU",
                    )
                    running[i] = start(i)
                else:
                    codes[i] = process.exitcode
                    del running[i]
    except KeyboardInterrupt:
        for process in running.values():
            if process.is_alive() and process.pid is not None:
                os.kill(process.pid, signal.SIGTERM)
        for process in running.values():
            process.join()
        raise
    return [codes[i] for i in sorted(codes)]


def worker_main(
    worker: str,
    jobs: list[Job],
    settings: Settings,
    started_at: str,
    upload: bool = False,
    threads: int = DEFAULT_THREADS,
) -> None:
    """Runs in a worker process. Exits with 1 if a run failed, 130 if stopped."""
    # The main process stops the workers; Ctrl-C reaching them too would interrupt
    # them twice. They also stop if the main process dies.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass
    setup_logging(worker)
    set_threads(threads)
    logger.info("Device %s.", os.environ.get("CUDA_VISIBLE_DEVICES", "default"))
    try:
        failed, busy = run_jobs(
            jobs,
            settings,
            started_at,
            progress_bars=False,
            upload=upload,
        )
    except KeyboardInterrupt:
        logger.info("Stopped.")
        sys.exit(INTERRUPTED_STATUS)
    except CudaFaultError:
        sys.exit(WORKER_RESTART_STATUS)
    logger.info("No run left to claim (%d held by other processes).", busy)
    sys.exit(1 if failed else 0)


def run_workers(
    workers: int,
    jobs: list[Job],
    args: argparse.Namespace,
    settings: Settings,
    started_at: str,
) -> int:
    """Trains `jobs` in `workers` processes and returns the exit status."""
    try:
        codes = supervise_workers(
            workers,
            worker_main,
            lambda name: (
                name,
                jobs,
                settings,
                started_at,
                args.upload,
                args.threads,
            ),
            args.devices,
            args.threads,
        )
    except KeyboardInterrupt:
        logger.info(
            "Interrupted, stopping the workers. Run the same command to resume."
        )
        return INTERRUPTED_STATUS
    if any(code not in (0, 1) for code in codes):
        logger.error("A worker stopped unexpectedly, exit codes %s.", codes)
        return 1
    return 1 if 1 in codes else 0


def initialize_statuses(
    output: Path,
    folders: list[Path],
    models: list[str],
    num_epochs: int,
) -> None:
    """Records every model of this run, so the progress table shows the work left.

    A model with a results file is finished, even if a kill came before its status
    was updated.
    """
    for folder in folders:
        for model in models:
            status = load_status(output, folder.name, model, num_epochs)
            results_file = output / folder.name / model / RESULTS_FILE
            if results_file.is_file() and status.status != "finished":
                mark_finished(status, results_file)
                write_status(output, status)
            elif not status_path(output, folder.name, model).is_file():
                write_status(output, status)
    write_progress(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train KGC-Benchmark's models on every dataset, resumably."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="folder with one subfolder per dataset (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("exp_results"),
        help="folder for the results (default: %(default)s)",
    )
    parser.add_argument(
        "--checkpoints",
        type=Path,
        default=Path("exp_checkpoints"),
        help="folder for the checkpoints (default: %(default)s)",
    )
    parser.add_argument(
        "--set",
        dest="dataset_set",
        choices=("base", *COPY_SETS, *LEVEL_SETS, "all"),
        default="base",
        help="datasets to train when --datasets is not given: the base datasets, "
        "the copies whose folder names end in _ours, _kbgen or _pygraft, with or "
        "without a tag such as _r25 (ours, kbgen, pygraft), only the rule-level "
        "copies such as french_ours_r25 (ours-levels, kbgen-levels), or all of them "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        help="dataset folders to train, of any set (default: every dataset of --set)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(MODELS),
        help="PyKEEN models to train (default: %(default)s)",
    )
    parser.add_argument(
        "--skip-models",
        nargs="+",
        default=[],
        help="models of --models to leave out of this run (default: none)",
    )
    parser.add_argument(
        "--skip-datasets",
        nargs="+",
        default=[],
        help="datasets of --datasets or --set to leave out of this run (default: none)",
    )
    parser.add_argument("--epochs", type=int, help=f"(default: {Settings.num_epochs})")
    parser.add_argument(
        "--batch-size", type=int, help=f"(default: {Settings.batch_size})"
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        help="CUDA devices given to the workers in turn, as CUDA_VISIBLE_DEVICES "
        "entries (default: CUDA_VISIBLE_DEVICES, else every MIG instance and GPU "
        "of nvidia-smi -L)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="CPU threads of each worker (default: %(default)s)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="commit every finished run to the checked-out branch and push it to "
        "origin, after each run and at the end (see upload_results.py)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="runs trained at once, each in its own process; 4 fills an RTX 3090 "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--largest-first",
        action="store_true",
        help="train the largest datasets first instead of the smallest (by the size "
        "of their train file)",
    )
    parser.add_argument(
        "--keep-order",
        action="store_true",
        help="train the datasets of --datasets in the order given instead of "
        "smallest train file first, e.g. to finish one family of datasets before "
        "the next",
    )
    args = parser.parse_args()
    if args.keep_order and not args.datasets:
        parser.error("--keep-order needs --datasets.")
    if args.keep_order and args.largest_first:
        parser.error("--keep-order and --largest-first exclude each other.")
    if args.workers < 1:
        parser.error("--workers must be at least 1.")
    args.epochs = Settings.num_epochs if args.epochs is None else args.epochs
    if args.batch_size is None:
        args.batch_size = Settings.batch_size
    if args.devices is None:
        args.devices = visible_devices()
    unknown = sorted(set(args.skip_models) - set(args.models))
    if unknown:
        parser.error(f"--skip-models has models not in --models: {', '.join(unknown)}")
    args.models = [m for m in args.models if m not in args.skip_models]
    return args


def main() -> int:
    args = parse_args()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    setup_logging()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    settings = Settings(num_epochs=args.epochs, batch_size=args.batch_size)
    folders = find_datasets(
        args.root, args.datasets, args.dataset_set, keep_order=args.keep_order
    )
    unknown = sorted(set(args.skip_datasets) - {folder.name for folder in folders})
    if unknown:
        logger.error(
            "--skip-datasets has datasets not in this run: %s", ", ".join(unknown)
        )
        return 2
    folders = [folder for folder in folders if folder.name not in args.skip_datasets]
    if not folders:
        logger.info("No dataset to train.")
        return 0
    # A new machine has no results folder yet; progress.csv is written there.
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ[COMMAND_ENV] = " ".join(sys.argv)
    logger.info("Command: %s", " ".join(sys.argv))
    logger.info(
        "PyKEEN %s, PyTorch %s, GPU: %s.",
        pykeen.get_version(),
        torch.__version__,
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    )
    initialize_statuses(args.output, folders, args.models, settings.num_epochs)
    logger.info(
        "Training %s on %d datasets with %d worker(s), with %s (batch size %d on "
        "the %s datasets).",
        ", ".join(args.models),
        len(folders),
        args.workers,
        settings,
        YAGO_BATCH_SIZE,
        YAGO_PREFIX,
    )
    if args.workers > 1:
        logger.info(
            "Devices: %s.", ", ".join(args.devices) if args.devices else "default"
        )
    # Smallest dataset first, so the fast graphs finish early and the slowest come
    # last. --largest-first reverses it, so that with several workers the longest
    # runs start at once instead of ending up alone at the end. --keep-order keeps
    # the order of --datasets instead.
    if args.largest_first:
        folders.reverse()
    jobs = [
        (args.output, args.checkpoints, folder, model)
        for folder in folders
        for model in args.models
    ]

    if args.workers > 1:
        code = run_workers(args.workers, jobs, args, settings, started_at)
    else:
        set_threads(args.threads)
        try:
            failed, busy = run_jobs(
                jobs,
                settings,
                started_at,
                progress_bars=True,
                upload=args.upload,
            )
        except KeyboardInterrupt:
            logger.info("Interrupted. Run the same command to resume.")
            return INTERRUPTED_STATUS
        except CudaFaultError:
            logger.error(
                "A CUDA error broke this process; run the same command to resume."
            )
            return 1
        if busy:
            logger.info("%d runs were held by other processes.", busy)
        code = 1 if failed else 0
    if args.upload and code != INTERRUPTED_STATUS:
        # Catches the runs whose upload failed, and those finished before.
        upload_finished(args.output)
    if code == 1:
        logger.error("Some runs failed; the next run retries them (see progress.csv).")
    elif code == 0:
        logger.info("Done.")
    return code


if __name__ == "__main__":
    sys.exit(main())
