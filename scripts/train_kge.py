"""Trains the KGC-Benchmark embedding models on every dataset under exp_datasets/.

Each dataset is trained the way KGC.py of the KGC-Benchmark project trains a graph,
with the settings of its input.json: PyKEEN's pipeline with the sLCWA training loop,
inverse triples, filtered negative sampling and seed 1235. The split is the one
difference: KGC.py splits one file 80/20, and this script uses the `train`, `valid`
and `test` files of each dataset folder. Models train on `train` and are evaluated
on `test`. `valid` only adds known answers to filter out of the ranking, as there is
no early stopping.

The output has KGC.py's layout, so `report_results.py --root <output>` reads it:
`<output>/<dataset>/` holds the three splits and `<model>/` the trained model and its
metrics. That script doesn't filter `valid` when it recomputes the ranks, so its
per-prediction analysis leaves out a run whose MRR then differs by more than 0.001
from its results.json.

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
the same list of runs, largest dataset first, and each claims the next run no one
holds. Locks make this safe across commands too: a run (`<model>.lock`) is trained by
one process at a time, and a dataset (`.lock`) is shared by the processes training
its runs. With workers, the terminal shows a line every 10 epochs per run instead of
progress bars, and Ctrl-C (or SIGINT/SIGTERM to the main process) stops every worker.

`--skip-models CompGCN` leaves a model out of a run. Running the command again without
the flag then trains only that model, as the finished ones are skipped.
`--skip-datasets` does the same for datasets, as in
`--skip-datasets YAGO3-10 YAGO3-10_1_1`.

`--upload` pushes every finished run to the kge-results branch after each run and at
the end, so several machines collect their results in one place (upload_results.py).

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
import os
import shutil
import signal
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
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
LOG_FILE = Path("logs/train_kge.log")
INTERRUPTED_STATUS = 130
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
    """Training settings, defaulting to KGC-Benchmark's input.json and KGC.py."""

    num_epochs: int = 100
    batch_size: int = 32
    embedding_dim: int = 50
    random_seed: int = 1235
    create_inverse_triples: bool = True
    filtered_negative_sampling: bool = True


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
        if self.last_time is not None:
            self.status.train_seconds += now - self.last_time
        self.last_time = now
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


def dataset_set(name: str) -> str:
    """The set a dataset folder belongs to: a COPY_SETS name, or "base"."""
    for copy_set in COPY_SETS:
        if name.endswith(f"_{copy_set}"):
            return copy_set
    return "base"


def find_datasets(
    root: Path, names: list[str] | None, dataset_set_name: str | None = None
) -> list[Path]:
    """Returns the folders under root with the three splits, smallest `train` first.

    Named datasets are returned whatever their set; otherwise, with
    `dataset_set_name`, only the folders of that set.

    Raises:
        ValueError: If a requested dataset doesn't exist.
    """
    folders = {
        d.name: d for d in root.iterdir() if all((d / s).is_file() for s in SPLITS)
    }
    if names is None and dataset_set_name is not None:
        folders = {
            n: d for n, d in folders.items() if dataset_set(n) == dataset_set_name
        }
    if names is not None:
        missing = sorted(set(names) - set(folders))
        if missing:
            raise ValueError(f"Datasets not found under {root}: {', '.join(missing)}")
        folders = {name: folders[name] for name in names}
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
            name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
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
    validation, testing = (
        TriplesFactory.from_labeled_triples(
            triples=np.array(read_split(folder / name, id_names)),
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


def train_model(
    model: str,
    splits: Splits,
    output: Path,
    checkpoints: Path,
    settings: Settings,
    root: Path,
    status: ModelStatus,
    pipeline_kwargs: Mapping[str, Any] | None = None,
    progress_bars: bool = True,
) -> Any:
    """Trains and evaluates one model, resuming from its checkpoint if it has one.

    The results appear in `<output>/<model>/` all at once, when they are complete, so
    the presence of its results file is what marks a model as finished.

    `pipeline_kwargs` replaces the arguments of PyKEEN's `pipeline` built from
    `settings`, as tuned hyperparameters do. `training_kwargs` and
    `negative_sampler_kwargs` are merged into the ones built here instead, so the
    checkpoints and the status tracking stay in place.

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
            "batch_size": settings.batch_size,
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
    if not progress_bars:
        kwargs["evaluation_kwargs"] = {"use_tqdm": False}
    for key, value in (pipeline_kwargs or {}).items():
        if key in ("training_kwargs", "negative_sampler_kwargs"):
            kwargs[key] = {**kwargs[key], **value}
        else:
            kwargs[key] = value
    result = pipeline(**kwargs)

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


def train_dataset(
    folder: Path,
    output: Path,
    checkpoints: Path,
    models: list[str],
    settings: Settings,
    pipeline_kwargs: Callable[[str, Splits], Mapping[str, Any]] | None = None,
    recorded_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    """Trains the models a dataset is missing and returns the ones that failed.

    `pipeline_kwargs`, if given, returns each model's `train_model` overrides; an
    error it raises fails only that model. `recorded_settings` replaces `settings` in
    the dataset's settings file, for runs whose settings differ between models.
    """
    output.mkdir(parents=True, exist_ok=True)
    checkpoints.mkdir(parents=True, exist_ok=True)
    with dataset_lock(checkpoints / LOCK_FILE):
        check_settings(output / SETTINGS_FILE, recorded_settings or settings, folder)

        pending = [m for m in models if not (output / m / RESULTS_FILE).is_file()]
        if not pending:
            logger.info("%s: every model is already trained.", output.name)
            return []

        logger.info("%s: loading the splits of %s.", output.name, folder)
        splits = load_splits(folder, settings)
        for name, factory in zip(SPLITS, splits, strict=True):
            save_split(factory, output / name)

        failed = []
        for model in pending:
            logger.info("%s: training %s.", output.name, model)
            root = output.parent
            status = load_status(root, output.name, model, settings.num_epochs)
            try:
                overrides = pipeline_kwargs(model, splits) if pipeline_kwargs else None
                train_model(
                    model,
                    splits,
                    output,
                    checkpoints,
                    settings,
                    root,
                    status,
                    overrides,
                )
            except Exception as error:
                # One model running out of memory must not stop the other runs.
                logger.exception("%s: %s failed.", output.name, model)
                failed.append(f"{output.name}/{model}")
                status.status = "failed"
                status.error = f"{type(error).__name__}: {error}"
                record_status(root, status)
            else:
                logger.info(
                    "%s: finished %s in %.0f s of training, MRR %.4f, Hits@10 %.4f.",
                    output.name,
                    model,
                    status.train_seconds,
                    status.metrics["mrr"],
                    status.metrics["hits_at_10"],
                )
        return failed


def run_jobs(
    jobs: list[tuple[Path, str]],
    output: Path,
    checkpoints: Path,
    settings: Settings,
    started_at: str,
    progress_bars: bool,
    upload: bool = False,
) -> tuple[int, int]:
    """Trains every run of `jobs` that no other process holds, in order.

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
    for folder, model in jobs:
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
            try:
                if loaded is None or loaded[0] != folder:
                    loaded = None
                    with wait_lock(dataset_checkpoints / SETTINGS_LOCK_FILE):
                        check_settings(dataset_output / SETTINGS_FILE, settings, folder)
                        logger.info("%s: loading the splits.", folder.name)
                        splits = load_splits(folder, settings)
                        for name, factory in zip(SPLITS, splits, strict=True):
                            save_split(factory, dataset_output / name)
                    loaded = (folder, splits)
                logger.info("%s: training %s.", folder.name, model)
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
                # One model running out of memory must not stop the other runs.
                logger.exception("%s: %s failed.", folder.name, model)
                failed += 1
                status.status = "failed"
                status.error = f"{type(error).__name__}: {error}"
                record_status(output, status)
            else:
                logger.info(
                    "%s: finished %s in %.0f s of training, MRR %.4f, Hits@10 %.4f.",
                    folder.name,
                    model,
                    status.train_seconds,
                    status.metrics["mrr"],
                    status.metrics["hits_at_10"],
                )
                if upload:
                    upload_finished(output)
    return failed, busy


def upload_finished(output: Path) -> None:
    """Uploads the finished runs; a failure is logged and doesn't stop training."""
    try:
        upload_results.upload(output)
    except Exception:
        logger.exception("Could not upload the results; the next upload retries.")


def setup_logging(worker: str | None = None) -> None:
    """Logs to the terminal and to LOG_FILE, naming the worker if there is one.

    The terminal shows this script's lines and every warning; the log file also keeps
    PyKEEN's other messages.
    """
    console = logging.StreamHandler()
    console.addFilter(
        lambda record: record.name == logger.name or record.levelno >= logging.WARNING
    )
    name = f"{worker} " if worker else ""
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s {name}%(name)s: %(message)s",
        handlers=[console, logging.FileHandler(LOG_FILE)],
        force=True,
    )
    # PyKEEN logs every saved checkpoint at INFO, which would break the progress bars.
    logging.getLogger("pykeen.training.training_loop").setLevel(logging.WARNING)


def stop_on_sigterm(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def worker_main(
    worker: str,
    jobs: list[tuple[Path, str]],
    output: Path,
    checkpoints: Path,
    settings: Settings,
    started_at: str,
    upload: bool = False,
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
    try:
        failed, busy = run_jobs(
            jobs,
            output,
            checkpoints,
            settings,
            started_at,
            progress_bars=False,
            upload=upload,
        )
    except KeyboardInterrupt:
        logger.info("Stopped.")
        sys.exit(INTERRUPTED_STATUS)
    logger.info("No run left to claim (%d held by other processes).", busy)
    sys.exit(1 if failed else 0)


def run_workers(
    workers: int,
    jobs: list[tuple[Path, str]],
    args: argparse.Namespace,
    settings: Settings,
    started_at: str,
) -> int:
    """Trains `jobs` in `workers` processes and returns the exit status."""
    # CUDA can't be used in a forked process.
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=worker_main,
            name=f"worker-{i}",
            args=(
                f"worker-{i}",
                jobs,
                args.output,
                args.checkpoints,
                settings,
                started_at,
                args.upload,
            ),
        )
        for i in range(1, workers + 1)
    ]
    for process in processes:
        process.start()
    # SIGTERM to the main process stops the workers as Ctrl-C does.
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        logger.info(
            "Interrupted, stopping the workers. Run the same command to resume."
        )
        for process in processes:
            if process.is_alive() and process.pid is not None:
                os.kill(process.pid, signal.SIGTERM)
        for process in processes:
            process.join()
        return INTERRUPTED_STATUS
    codes = [process.exitcode for process in processes]
    if any(code not in (0, 1) for code in codes):
        logger.error("A worker stopped unexpectedly, exit codes %s.", codes)
        return 1
    return 1 if 1 in codes else 0


def initialize_statuses(
    output: Path, folders: list[Path], models: list[str], num_epochs: int
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
        choices=("base", *COPY_SETS),
        default="base",
        help="datasets to train when --datasets is not given: the base datasets, or "
        "the copies whose folder names end in _ours, _kbgen or _pygraft "
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
    parser.add_argument(
        "--epochs", type=int, default=Settings.num_epochs, help="(default: %(default)s)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=Settings.batch_size,
        help="(default: %(default)s)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="upload every finished run to the kge-results branch of origin, after "
        "each run and at the end (see upload_results.py)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="runs trained at once, each in its own process; 4 fills an RTX 3090 "
        "(default: %(default)s)",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1.")
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
    folders = find_datasets(args.root, args.datasets, args.dataset_set)
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
    logger.info("Command: %s", " ".join(sys.argv))
    logger.info(
        "PyKEEN %s, PyTorch %s, GPU: %s.",
        pykeen.get_version(),
        torch.__version__,
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    )
    initialize_statuses(args.output, folders, args.models, settings.num_epochs)
    logger.info(
        "Training %s on %d datasets with %d worker(s), with %s.",
        ", ".join(args.models),
        len(folders),
        args.workers,
        settings,
    )
    # One worker goes smallest dataset first, so results come early. Several go
    # largest first, so the longest runs don't end up alone at the end.
    if args.workers > 1:
        folders.reverse()
    jobs = [(folder, model) for folder in folders for model in args.models]

    if args.workers > 1:
        code = run_workers(args.workers, jobs, args, settings, started_at)
    else:
        try:
            failed, busy = run_jobs(
                jobs,
                args.output,
                args.checkpoints,
                settings,
                started_at,
                progress_bars=True,
                upload=args.upload,
            )
        except KeyboardInterrupt:
            logger.info("Interrupted. Run the same command to resume.")
            return INTERRUPTED_STATUS
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
