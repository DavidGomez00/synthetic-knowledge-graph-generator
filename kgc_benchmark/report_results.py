"""
Validate the KGE models that scripts/train_kge.py trained: rank every held-out
prediction of every run, and compare the variants of each dataset family.

train_kge.py trains each model on a dataset's `train` split and saves the run
to `exp_results/<dataset>/<model>/`: the model (`trained_model.pkl`), its
training triples (`training_triples/`) and pykeen's `results.json`, next to
the dataset's `train`, `valid` and `test` splits. The model never sees `valid`
or `test`, which don't overlap, so this script ranks both as one held-out set.

Each held-out triple (h, r, t) gives a tail prediction (h, r, ?) and a head
prediction (?, r, t), ranked among every entity after removing the other
answers known to be true (train, valid and test), with ties at their mean rank
(pykeen's "realistic" rank). This is train_kge.py's filtered evaluation, so the
MRR of a run's test predictions must match its results.json; a run that
doesn't is left out. Every metric below covers the whole held-out set, head
and tail predictions pooled.

A family is an original dataset and its synthetic copies, the variants:
`french` (variant `original`) with `french_ours` (`ours`), `french_kbgen`
(`kbgen`) and `french_pygraft` (`pygraft`). The variants are compared only
through aggregates: per model, per relation, whose names every generator
keeps, and by how they rank the models (Kendall's tau, see kendall_tau.py). A
held-out triple of a copy is not the same fact as one of the original, even
when the copy keeps the entity names, so predictions are never paired across
variants.

Output, under `--output` (`exp_analysis/`):

    metrics.csv                      one row per family, variant and model:
                                     Hits@1/3/5/10/15, MRR and predictions
    <family>/
        metrics.csv                  Hits@1 to Hits@1000 and MRR per variant and model
        relations.csv                the same per relation
        relation_comparison.csv      each relation, the original vs every variant:
                                     MRR difference with a bootstrap interval,
                                     Cliff's delta, Mann-Whitney and KS tests
        ranking_agreement.csv        Kendall's tau of the model rankings, the
                                     original vs every variant
        training_losses.csv          one row per training epoch, every run
        relations.png                MRR and Hits@10 per relation, model and variant
        models_hits_at_1-15.png      Hits@1 to Hits@15 per variant, one line per model
        training_loss.png            training loss per model, one line per variant
        plots/<model>/               Hits@k curves per relation, one line per variant
        cropped/                     with --crop: every plot without its caption band
        <variant>/<model>/
            predictions.csv.gz       the rank of every held-out prediction of that run

A run's ranks are computed once (on the GPU when there is one) and read back
from its predictions.csv.gz afterwards, unless --force is given.

Usage, from the repository root:

    python kgc_benchmark/report_results.py
    python kgc_benchmark/report_results.py --families french family
    python kgc_benchmark/report_results.py --variants original ours --crop
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import json
import os
import signal
import textwrap
import time
import zlib
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd
import torch
from cliffs_delta import cliffs_delta, effect_size
from kendall_tau import ranking_agreement
from matplotlib import path as mpath
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.ticker import FuncFormatter, LogLocator
from PIL import Image
from pykeen.triples import TriplesFactory
from scipy.stats import ks_2samp, mannwhitneyu

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = SCRIPT_DIR.parent / "exp_results"
DEFAULT_OUTPUT = SCRIPT_DIR.parent / "exp_analysis"
# The summary of every run, in the output folder, and each family's metrics
# table and per-relation table, in its folder.
SUMMARY_NAME = "metrics.csv"
REPORT_NAME = "metrics.csv"
RELATIONS_NAME = "relations.csv"
# Each run's ranks, in `<output>/<family>/<variant>/<model>/`.
PREDICTIONS_FILE = "predictions.csv.gz"
# Subfolder of a family's folder for the plots of each model, and for the plots
# without their caption band (--crop).
PLOTS_FOLDER = "plots"
CROPPED_FOLDER = "cropped"
# The splits a model never trained on, ranked together as its held-out set.
HELD_OUT_SPLITS = ("valid", "test")
# In the output folder, the state of a validation while it runs: one row per run
# (PROGRESS_NAME), the stage and the current run (STATE_NAME), and the lock that
# a running validation holds (LOCK_NAME). scripts/validation_eta.py reads them.
PROGRESS_NAME = "progress.csv"
STATE_NAME = "validation_state.json"
LOCK_NAME = ".validation.lock"
PROGRESS_FIELDS = [
    "family",
    "variant",
    "model",
    "status",
    "held_out",
    "seconds",
    "test_mrr_difference",
    "error",
    "updated_at",
]
# Exit status of an interrupted validation, as for Ctrl-C in a shell.
INTERRUPTED_STATUS = 130


# pykeen's evaluation report is sliced by rank_type ("head"/"tail"/"both")
# and filtering ("optimistic"/"pessimistic"/"realistic"). "both" combines
# head- and tail-prediction ranks; "realistic" is the standard filtered
# evaluation reported in the KG completion literature (see module
# docstring), so that's the slice pulled out here.
RANK_TYPE = "both"
FILTERING = "realistic"

# The suffixes of the synthetic copies' dataset folders, as in
# scripts/train_kge.py: `french_kbgen` is the `kbgen` variant of the `french`
# family. A folder without one is its family's original, the reference that
# the other variants are compared with.
COPY_SETS = ("ours", "kbgen", "pygraft")
REFERENCE_VARIANT = "original"
# The order of the variants in every plot, which fixes their colors.
VARIANT_ORDER = [REFERENCE_VARIANT, *COPY_SETS]

# Names shown in the plots for the families and variants. One that isn't
# listed is shown by its folder name. The CSV files and the file names keep
# the folder names.
FOLDER_NAMES = {
    "original": "Original",
    "ours": "SKGG",
    "kbgen": "kbgen",
    "pygraft": "PyGraft",
    "family": "Family",
    "Family1_1": "Family 1-1",
    "Family1_N": "Family 1-N",
    "FamilyM_N": "Family M-N",
    "FamilyN_1": "Family N-1",
    "french": "French Royalty",
    "french1_1": "French Royalty 1-1",
    "french1_N": "French Royalty 1-N",
    "frenchN_1": "French Royalty N-1",
    "frenchN_M": "French Royalty N-M",
    "WN18RR1_1": "WN18RR 1-1",
    "WN18RR1_N": "WN18RR 1-N",
    "WN18RRN_1": "WN18RR N-1",
    "WN18RRN_M": "WN18RR N-M",
    "YAGO3-10_1_1": "YAGO3-10 1-1",
    "YAGO3-10_1_N": "YAGO3-10 1-N",
    "YAGO3-10_N_1": "YAGO3-10 N-1",
    "YAGO3-10_N_M": "YAGO3-10 N-M",
}

# Every k of the metrics tables (metrics_report.csv and relation_report.csv).
# results.json only holds k = 1, 3, 5 and 10; the others come from the
# recomputed ranks.
REPORT_HITS_AT = tuple(range(1, 1001))
REPORT_HITS_COLUMNS = [f"hits_at_{k}" for k in REPORT_HITS_AT]
# Every k of the Hits@k line plots and of the per-prediction hits of
# predictions.csv, which holds each prediction's rank for any other k.
HITS_AT = tuple(range(1, 16))
HITS_COLUMNS = [f"hits_at_{k}" for k in HITS_AT]
HITS_RANGE = f"Hits@{min(HITS_AT)} to Hits@{max(HITS_AT)}"
# The `relation` of the comparisons over all of a variant's held-out predictions,
# and the title of their plot panels.
ALL_RELATIONS = "all"
ALL_RELATIONS_TITLE = "all relations"
# The k of the headline Hits@k in the relation plots, the relation comparison
# and the comparison plots.
HEADLINE_K = 10
# The metrics of the metrics table that the models are ranked by in each graph,
# to compare the graphs' rankings (ranking_agreement.csv). Hits@15 is not in
# results.json, so a run without recomputed ranks has none.
AGREEMENT_HITS_AT = (1, 3, 5, 10, 15)
AGREEMENT_METRICS = [*(f"hits_at_{k}" for k in AGREEMENT_HITS_AT), "mrr"]

CSV_FIELDS = ["dataset", "model", *REPORT_HITS_COLUMNS, "mrr", "count"]
# The columns of the summary of every run (SUMMARY_NAME).
SUMMARY_HITS_AT = (1, 3, 5, 10, 15)
SUMMARY_FIELDS = [
    "family",
    "variant",
    "model",
    *(f"hits_at_{k}" for k in SUMMARY_HITS_AT),
    "mrr",
    "count",
]
# Largest gap allowed between the MRR recomputed from a run's saved model
# and splits and the MRR in its results.json. A bigger gap means the splits
# on disk were written by another run.
MRR_TOLERANCE = 1e-3
# Held-out triples scored per forward pass when ranking.
SCORE_BATCH_SIZE = 256
# Resamples of the bootstrap interval of each relation's MRR difference, with
# a fixed seed so that reruns write identical files.
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 0
# Holm-adjusted p-values below this are significant in the plots.
SIGNIFICANCE = 0.05

PREDICTION_COLUMNS = [
    "dataset",
    "model",
    "split",
    "side",
    "head",
    "relation",
    "tail",
    "rank",
    "candidates",
    "reciprocal_rank",
    *HITS_COLUMNS,
]

# Plot colors and mark sizes, from the dataviz reference palette (light).
PLOT_DPI = 150
PX = 72 / PLOT_DPI  # one pixel, in points
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
# One color per graph, in the palette's validated order (see graph_order).
CATEGORICAL = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
BAR_WIDTH_PX = 24
BAR_GAP_PX = 2
BAR_RADIUS_PX = 4
# Height of one δ line above a Hits@k curve panel, in points, its font size,
# and the gap between its name, count and delta columns, in points.
DELTA_LINE_PT = 11
NOTE_FONT_SIZE = 8
NOTE_COLUMN_GAP_PT = 6
# Height of the band at the top of every figure that holds its title and
# caption, in inches: CAPTION_HEIGHT * PLOT_DPI = 330 pixels, room for a
# caption of CAPTION_LINES lines. Nothing else is drawn in it, so cropping it
# off leaves the bare figure for a publication, where that text goes in the
# caption.
CAPTION_HEIGHT = 2.2
# Longest caption line, in characters, for readability, and the most lines a
# caption can have to fit its band.
CAPTION_LINE_CHARS = 120
CAPTION_LINES = 7
# How the Hits@k of every plot is computed, for their captions.
HITS_DEFINITION = (
    "Hits@k is the fraction of held-out predictions (valid and test, head and tail "
    "pooled) whose correct entity ranks in the top k of all candidates, under filtered "
    "ranking with ties at their mean rank."
)


def dataset_family(dataset: str) -> tuple[str, str]:
    """
    The family and variant of a dataset folder of `exp_results`: a copy's folder
    is its source's name with the copy set as suffix, e.g. `french_kbgen` is
    (`french`, `kbgen`), and a source dataset is its own family's `original`.
    """
    for variant in COPY_SETS:
        if dataset.endswith(f"_{variant}"):
            return dataset[: -len(variant) - 1], variant
    return dataset, REFERENCE_VARIANT


def find_runs(
    results_dir: Path,
    families: list[str] | None = None,
    variants: list[str] | None = None,
) -> list[dict]:
    """
    Every finished run of `results_dir` (train_kge.py's `--output`): one dict
    per `<dataset>/<model>/results.json`, with its `dataset` label
    `<family>/<variant>` (see `dataset_family`), its `model`, the dataset's
    folder `dataset_dir` and the model's folder `model_dir`. `families` and
    `variants` keep only those.
    """
    runs = []
    for results_json in sorted(results_dir.glob("*/*/results.json")):
        model_dir = results_json.parent
        dataset_dir = model_dir.parent
        family, variant = dataset_family(dataset_dir.name)
        if families and family not in families:
            continue
        if variants and variant not in variants:
            continue
        runs.append(
            {
                "dataset": f"{family}/{variant}",
                "model": model_dir.name,
                "dataset_dir": dataset_dir,
                "model_dir": model_dir,
            }
        )
    return runs


def extract_metrics(results_json: Path) -> dict | None:
    """
    Pull Hits@1/3/5/10 and MRR out of one pykeen `results.json` file.

    Returns None (and prints a warning) if the file can't be read or is
    missing the expected `metrics[RANK_TYPE][FILTERING]` slice, so a single
    malformed/incomplete result doesn't abort the whole report.
    """
    try:
        with results_json.open(encoding="utf-8") as f:
            data = json.load(f)
        metrics = data["metrics"][RANK_TYPE][FILTERING]
        return {
            "hits_at_1": metrics["hits_at_1"],
            "hits_at_3": metrics["hits_at_3"],
            "hits_at_5": metrics["hits_at_5"],
            "hits_at_10": metrics["hits_at_10"],
            # pykeen names MRR "inverse_harmonic_mean_rank" internally.
            "mrr": metrics["inverse_harmonic_mean_rank"],
            "count": metrics.get("count"),
        }
    except (json.JSONDecodeError, KeyError, OSError) as e:
        print(f"warning: skipping {results_json} ({e})")
        return None


def build_report(predictions: pd.DataFrame) -> list[dict]:
    """
    The metrics table: one row per run of `predictions`, with Hits@k for every k
    of REPORT_HITS_AT and the MRR over its whole held-out set (valid and test,
    head and tail predictions pooled, like pykeen's "both" slice), and the number
    of predictions.
    """
    if predictions.empty:
        return []
    by = ["dataset", "model"]
    table = hits_by(predictions, by)
    grouped = predictions.groupby(by)
    table["mrr"] = grouped["reciprocal_rank"].mean()
    table["count"] = grouped.size()
    return table.reset_index().to_dict("records")


def build_losses(runs: list[dict]) -> pd.DataFrame:
    """
    The training loss of every run of `runs` (from `find_runs`), one row per
    epoch, from the "losses" pykeen's `results.json` keeps: the loss of each
    epoch, averaged over the training triples.
    """
    frames = []
    for run in runs:
        with (run["model_dir"] / "results.json").open(encoding="utf-8") as f:
            losses = json.load(f).get("losses") or []
        frames.append(
            pd.DataFrame(
                {
                    "dataset": run["dataset"],
                    "model": run["model"],
                    "epoch": np.arange(1, len(losses) + 1),
                    "loss": losses,
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["dataset", "model", "epoch", "loss"])
    return pd.concat(frames, ignore_index=True)


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """
    Yield a temporary path next to `path`, renamed to `path` once the block
    has written it. An interruption leaves the old file or the new one, never
    part of one, and no temporary file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        yield temporary
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(rows: list[dict], output: Path, fields: list[str] = CSV_FIELDS) -> None:
    with atomic_path(output) as temporary:
        with temporary.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def write_table(table: pd.DataFrame, path: Path) -> None:
    """Write a table as CSV, atomically (see `atomic_path`)."""
    with atomic_path(path) as temporary:
        table.to_csv(temporary, index=False)


def save_figure(fig, path: Path) -> None:
    """Save a plot as PNG, atomically (see `atomic_path`)."""
    with atomic_path(path) as temporary:
        fig.savefig(temporary, format="png", dpi=PLOT_DPI, facecolor=SURFACE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def count_held_out(dataset_dir: Path) -> int:
    """The held-out triples of a dataset: the lines of its HELD_OUT_SPLITS."""
    total = 0
    for split in HELD_OUT_SPLITS:
        path = dataset_dir / split
        if path.is_file():
            with path.open("rb") as f:
                total += sum(1 for line in f if line.strip())
    return total


class Progress:
    """
    The state of a validation, on disk while it runs, for
    scripts/validation_eta.py: one PROGRESS_NAME row per run, with its status
    (pending, ranking, ranked, failed or left_out), its held-out triples and
    the seconds its ranking took, and the STATE_NAME file with the stage and
    the run being ranked. Both are rewritten atomically after every change.
    """

    def __init__(
        self,
        output: Path,
        runs: list[dict],
        force: bool,
        families: list[str] | None = None,
        variants: list[str] | None = None,
    ) -> None:
        self.output = output
        self.rows: dict[tuple[str, str], dict] = {}
        old = {}
        if (output / PROGRESS_NAME).is_file():
            with (output / PROGRESS_NAME).open(encoding="utf-8") as f:
                old = {
                    (r["family"], r["variant"], r["model"]): r
                    for r in csv.DictReader(f)
                }
        for run in runs:
            family, variant = run["dataset"].split("/")
            cached = predictions_path(output, run["dataset"], run["model"]).is_file()
            previous = old.get((family, variant, run["model"]), {})
            self.rows[run["dataset"], run["model"]] = {
                "family": family,
                "variant": variant,
                "model": run["model"],
                "status": "ranked" if cached and not force else "pending",
                "held_out": count_held_out(run["dataset_dir"]),
                "seconds": previous.get("seconds", "") if cached and not force else "",
                "test_mrr_difference": "",
                "error": "",
                "updated_at": now(),
            }
        self.state = {
            "stage": "ranking",
            "run": None,
            "started_at": now(),
            "pid": os.getpid(),
            # The runs this validation covers, for validation_eta.py to tell the
            # runs that finished training since from those it leaves out.
            "families": families,
            "variants": variants,
        }
        self.write()

    def set(self, dataset: str, model: str, **fields) -> None:
        self.rows[dataset, model].update(fields, updated_at=now())
        self.write()

    def stage(self, stage: str, run: str | None = None) -> None:
        self.state.update(stage=stage, run=run, updated_at=now())
        self.write()

    def write(self) -> None:
        write_csv(
            list(self.rows.values()), self.output / PROGRESS_NAME, PROGRESS_FIELDS
        )
        with atomic_path(self.output / STATE_NAME) as temporary:
            temporary.write_text(
                json.dumps(self.state, indent=2) + "\n", encoding="utf-8"
            )

    def interrupted(self) -> None:
        """Mark the run being ranked as pending again: its ranks were not kept."""
        for row in self.rows.values():
            if row["status"] == "ranking":
                row.update(status="pending", updated_at=now())
        self.state.update(stage="interrupted", run=None, updated_at=now())
        self.write()


@contextmanager
def validation_lock(output: Path) -> Iterator[bool]:
    """
    Hold the lock of a validation writing to `output`, without waiting.
    Yields whether it was taken: two validations of one output folder would
    rank the same runs and overwrite each other's progress. The OS releases it
    however the process ends.
    """
    output.mkdir(parents=True, exist_ok=True)
    with (output / LOCK_NAME).open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def read_cached_ranks(path: Path) -> pd.DataFrame | None:
    """A run's cached ranks, or None if the file is unreadable or incomplete."""
    try:
        ranks = pd.read_csv(
            path,
            dtype={"head": str, "relation": str, "tail": str},
            keep_default_na=False,
        )
    except (
        OSError,
        EOFError,
        zlib.error,
        gzip.BadGzipFile,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as e:
        print(f"warning: {path} is unreadable ({e}), ranking that run again")
        return None
    expected = {"split", "side", "head", "relation", "tail", "rank", "candidates"}
    if not expected <= set(ranks.columns):
        print(f"warning: {path} lacks columns, ranking that run again")
        return None
    return ranks


def read_split(path: Path) -> np.ndarray:
    """Read a split train_kge.py saved: one tab-separated labeled triple per line."""
    return (
        pd.read_csv(path, sep="\t", header=None, dtype=str, keep_default_na=False)
        .to_numpy()
        .reshape(-1, 3)
    )


def rank_predictions(
    dataset_dir: Path, model_dir: Path, device: torch.device
) -> pd.DataFrame | None:
    """
    Rank both predictions of every held-out triple of one run: the `valid` and
    `test` splits train_kge.py saved in `dataset_dir`, which the model never
    trained on, as one held-out set.

    Each held-out triple (h, r, t) gives a tail prediction (h, r, ?) and a head
    prediction (?, r, t). As in train_kge.py's filtered evaluation, the other
    answers known to be true -- the training triples saved in
    `training_triples/` and both held-out splits -- are removed before
    ranking, and ties get the "realistic" rank: the mean of the best and worst
    position among equal scores.

    Returns one row per prediction (split, side, head, relation, tail, rank,
    candidates), where `split` is the triple's split, `side` the entity being
    predicted and `candidates` the number of entities it was ranked among (all
    but the filtered answers, pykeen's "number of options"), or None (and
    prints a warning) if a file is missing or a label isn't in the model's
    vocabulary.
    """
    needed = [
        *(dataset_dir / split for split in HELD_OUT_SPLITS),
        model_dir / "trained_model.pkl",
        model_dir / "training_triples",
    ]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        print(f"warning: cannot rank {model_dir} (missing {', '.join(missing)})")
        return None

    splits = {split: read_split(dataset_dir / split) for split in HELD_OUT_SPLITS}
    held_out = np.concatenate(list(splits.values()))
    split_names = np.repeat(HELD_OUT_SPLITS, [len(splits[s]) for s in HELD_OUT_SPLITS])
    training = TriplesFactory.from_path_binary(model_dir / "training_triples")
    # PyKEEN reloads a column of all-numeric labels as integers; split labels are strings.
    entity_to_id = {str(label): i for label, i in training.entity_to_id.items()}
    relation_to_id = {str(label): i for label, i in training.relation_to_id.items()}
    try:
        held_out_ids = torch.tensor(
            [
                [entity_to_id[h], relation_to_id[r], entity_to_id[t]]
                for h, r, t in held_out
            ],
            dtype=torch.long,
        ).reshape(-1, 3)
    except KeyError as e:
        print(
            f"warning: cannot rank {model_dir} (label {e} is not in the model's "
            "vocabulary, so its splits were written by another run)"
        )
        return None

    # Every answer known to be true, per tail query (h, r) and head query (r, t).
    tails, heads = defaultdict(list), defaultdict(list)
    for h, r, t in torch.cat([training.mapped_triples, held_out_ids]).tolist():
        tails[h, r].append(t)
        heads[r, t].append(h)

    # The pickle holds the whole model object, not just its weights.
    model = torch.load(
        model_dir / "trained_model.pkl", map_location=device, weights_only=False
    )
    model.eval()
    ranks, candidates = {}, {}
    with torch.inference_mode():
        for side, column in (("head", 0), ("tail", 2)):
            side_ranks, side_candidates = [], []
            for batch in torch.split(held_out_ids, SCORE_BATCH_SIZE):
                batch = batch.to(device)
                scores = model.predict(hrt_batch=batch, target=side)
                rows = torch.arange(len(batch), device=device)
                true_scores = scores[rows, batch[:, column]]
                for i, (h, r, t) in enumerate(batch.tolist()):
                    scores[i, heads[r, t] if side == "head" else tails[h, r]] = float(
                        "nan"
                    )
                scores[rows, batch[:, column]] = true_scores
                # Filtered (NaN) scores compare False, so they never count.
                better = (scores > true_scores[:, None]).sum(dim=1)
                not_worse = (scores >= true_scores[:, None]).sum(dim=1)
                side_ranks.append(((better + 1 + not_worse) / 2).cpu())
                side_candidates.append(torch.isfinite(scores).sum(dim=1).cpu())
            ranks[side] = torch.cat(side_ranks).double().numpy()
            candidates[side] = torch.cat(side_candidates).numpy()

    return pd.concat(
        [
            pd.DataFrame(
                {
                    "split": split_names,
                    "side": side,
                    "head": held_out[:, 0],
                    "relation": held_out[:, 1],
                    "tail": held_out[:, 2],
                    "rank": ranks[side],
                    "candidates": candidates[side],
                }
            )
            for side in ("head", "tail")
        ],
        ignore_index=True,
    )


def predictions_path(output: Path, dataset: str, model: str) -> Path:
    """Where a run's ranks are kept: `<output>/<family>/<variant>/<model>/`."""
    return output / dataset / model / PREDICTIONS_FILE


def build_predictions(
    runs: list[dict],
    output: Path,
    device: torch.device,
    progress: Progress,
    force: bool = False,
) -> pd.DataFrame:
    """
    The ranks of every held-out prediction of every run of `runs` (from
    `find_runs`). A run's ranks are computed once and kept in
    `predictions_path`, written atomically, which later calls read back unless
    `force` is given, so an interrupted validation resumes where it stopped. A
    run whose test predictions don't reproduce the MRR in its results.json is
    left out: its splits or model on disk don't match. `progress` records each
    run's status, and a line reports each newly ranked run with an estimate of
    the time left.
    """
    pending = [
        run
        for run in runs
        if force or not predictions_path(output, run["dataset"], run["model"]).is_file()
    ]
    left = sum(progress.rows[r["dataset"], r["model"]]["held_out"] for r in pending)
    print(
        f"{len(runs) - len(pending)} run(s) already ranked, {len(pending)} to rank "
        f"({left:,} held-out triples)"
    )
    ranked_triples, ranking_seconds, done = 0, 0.0, 0
    frames = []
    for run in runs:
        dataset, model = run["dataset"], run["model"]
        path = predictions_path(output, dataset, model)
        cached = not force and path.is_file()
        ranks = read_cached_ranks(path) if cached else None
        if cached and ranks is None:
            # An unreadable cache: one more run to rank than counted above.
            pending.append(run)
            left += progress.rows[dataset, model]["held_out"]
        if ranks is None:
            progress.stage("ranking", f"{dataset}/{model}")
            progress.set(dataset, model, status="ranking", error="")
            start = time.monotonic()
            ranks = rank_predictions(run["dataset_dir"], run["model_dir"], device)
            if ranks is None:
                progress.set(
                    dataset, model, status="failed", error="could not rank, see the log"
                )
                continue
            with atomic_path(path) as temporary:
                ranks.to_csv(temporary, index=False, compression="gzip")
            seconds = time.monotonic() - start
            held_out = progress.rows[dataset, model]["held_out"]
            progress.set(dataset, model, status="ranked", seconds=round(seconds, 1))
            done += 1
            ranked_triples += held_out
            ranking_seconds += seconds
            left -= held_out
            rate = ranked_triples / ranking_seconds if ranking_seconds else 0
            eta = f", ETA {duration(left / rate)}" if rate and left > 0 else ""
            print(
                f"[{done}/{len(pending)}] {dataset}/{model}: {held_out:,} held-out triples "
                f"in {duration(seconds)} ({rate:.0f}/s); {left:,} left{eta}",
                flush=True,
            )

        metrics = extract_metrics(run["model_dir"] / "results.json")
        test = ranks[ranks["split"] == "test"]
        mrr = (1 / test["rank"]).mean()
        difference = abs(mrr - metrics["mrr"]) if metrics is not None else float("nan")
        if difference > MRR_TOLERANCE:
            print(
                f"warning: {dataset}/{model} left out (test MRR {mrr:.4f} from its saved "
                f"model and splits vs {metrics['mrr']:.4f} in results.json)"
            )
            progress.set(
                dataset, model, status="left_out", test_mrr_difference=difference
            )
            continue
        progress.set(dataset, model, test_mrr_difference=f"{difference:.2e}")
        frames.append(ranks.assign(dataset=dataset, model=model))

    if not frames:
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    predictions = pd.concat(frames, ignore_index=True)
    predictions["reciprocal_rank"] = 1 / predictions["rank"]
    for k in HITS_AT:
        predictions[f"hits_at_{k}"] = (predictions["rank"] <= k).astype(int)
    return predictions[PREDICTION_COLUMNS]


def hits_by(predictions: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """
    Hits@k for every k of REPORT_HITS_AT per group of `predictions` by the
    `by` columns: the share of the group's ranks within the top k. Counted
    from the sorted ranks rather than from one column per k, which would
    take a thousand columns per prediction.
    """
    ks = np.array(REPORT_HITS_AT)
    keys, hits = [], []
    for key, ranks in predictions.groupby(by)["rank"]:
        keys.append(key)
        sorted_ranks = np.sort(ranks.to_numpy())
        hits.append(np.searchsorted(sorted_ranks, ks, side="right") / len(ranks))
    return pd.DataFrame(
        np.array(hits, dtype=float).reshape(len(hits), len(ks)),
        index=pd.MultiIndex.from_tuples(keys, names=by),
        columns=REPORT_HITS_COLUMNS,
    )


def relation_report(predictions: pd.DataFrame) -> pd.DataFrame:
    """The table's metrics per relation, head and tail predictions pooled like the "both" slice."""
    by = ["dataset", "model", "relation"]
    grouped = predictions.groupby(by)
    report = hits_by(predictions, by)
    report["mrr"] = grouped["reciprocal_rank"].mean()
    report["count"] = grouped.size()
    return report.reset_index()


def split_dataset(table: pd.DataFrame) -> pd.DataFrame:
    """
    Add the `kg` and `graph` columns: the dataset's parent folder and its own
    name, e.g. `french` and `kbgen`.
    """
    dataset = table["dataset"].map(PurePosixPath)
    return table.assign(
        kg=dataset.map(lambda p: p.parent.as_posix()),
        graph=dataset.map(lambda p: p.name),
    )


def folder_name(folder: str) -> str:
    """The name the plots show for a folder: its FOLDER_NAMES entry, else the folder name."""
    return FOLDER_NAMES.get(folder, folder)


def kg_name(kg: str, *rest: str) -> str:
    """
    A KG's folders as the plot titles show them, followed by `rest` as is,
    e.g. "French Royalty / TuckER" for `french_royalty` and "TuckER".
    """
    return " / ".join(
        [folder_name(part) for part in PurePosixPath(kg).parts] + list(rest)
    )


def graph_order(graphs) -> list[str]:
    """
    Sort a family's variants for the plots in VARIANT_ORDER (the original
    first), then any other in name order, so each variant keeps its color.
    """
    order = {variant: i for i, variant in enumerate(VARIANT_ORDER)}
    return sorted(set(graphs), key=lambda g: (order.get(g, len(order)), g))


def normalized_rank(predictions: pd.DataFrame) -> pd.Series:
    """
    The rank as a share of the candidates, (rank - 1) / (candidates - 1): 0
    when ranked first, 1 when ranked last, 0.5 on average for a random
    guess. It puts graphs with different numbers of entities on one scale.
    """
    return (predictions["rank"] - 1) / (predictions["candidates"] - 1)


def holm(p_values: pd.Series) -> pd.Series:
    """Holm-adjust one family of p-values (step-down, capped at 1)."""
    ordered = p_values.sort_values()
    adjusted = (ordered * np.arange(len(ordered), 0, -1)).cummax().clip(upper=1)
    return adjusted.reindex(p_values.index)


def bootstrap_difference(
    x: pd.Series, y: pd.Series, rng: np.random.Generator
) -> tuple[float, float]:
    """95% percentile bootstrap interval of mean(y) - mean(x), resampling each."""
    x, y = x.to_numpy(), y.to_numpy()
    means_x = x[rng.integers(0, len(x), (BOOTSTRAP_SAMPLES, len(x)))].mean(axis=1)
    means_y = y[rng.integers(0, len(y), (BOOTSTRAP_SAMPLES, len(y)))].mean(axis=1)
    low, high = np.percentile(means_y - means_x, [2.5, 97.5])
    return float(low), float(high)


def compare_relations(predictions: pd.DataFrame, reference: str) -> pd.DataFrame:
    """
    Compare each relation's held-out predictions in the reference variant with
    those in every other variant of the same family (`predictions` with the `kg`
    and `graph` columns of `split_dataset`), for each model trained on both,
    and all their held-out predictions as one more relation, ALL_RELATIONS.
    It needs no shared entities, only shared relation names, so every
    variant is compared.

    `mrr_difference` (compared - reference) comes with a 95% bootstrap
    interval. The rank distributions are compared on `normalized_rank`:
    Cliff's delta (see cliffs_delta.py) is P(compared ranks better) -
    P(reference ranks better), negative when the compared graph does worse,
    and `effect_size` labels its size; the Mann-Whitney test asks whether
    one graph tends to rank better, the KS test whether the distributions
    differ at all. Their p-values are Holm-adjusted over the
    relations of each graph pair and model; the ALL_RELATIONS test is a
    family of its own, so its adjusted p-value is its p-value.
    """
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    k = HEADLINE_K
    predictions = predictions.assign(normalized_rank=normalized_rank(predictions))
    pairs, pooled = [], []
    for (kg, model), runs in predictions.groupby(["kg", "model"]):
        ref = runs[runs["graph"] == reference]
        for compared, cmp in runs[runs["graph"] != reference].groupby("graph"):
            for relation in sorted(set(ref["relation"]) & set(cmp["relation"])):
                x = ref[ref["relation"] == relation]
                y = cmp[cmp["relation"] == relation]
                pairs.append((kg, compared, model, relation, x, y))
            if not ref.empty:
                pooled.append((kg, compared, model, ALL_RELATIONS, ref, cmp))

    # The pooled comparisons last, so that the relations' bootstrap draws are
    # the same with or without them.
    rows = []
    for kg, compared, model, relation, x, y in pairs + pooled:
        mann_whitney = mannwhitneyu(
            x["normalized_rank"], y["normalized_rank"], alternative="two-sided"
        )
        ks = ks_2samp(x["normalized_rank"], y["normalized_rank"])
        delta = cliffs_delta(x["normalized_rank"], y["normalized_rank"])
        low, high = bootstrap_difference(
            x["reciprocal_rank"], y["reciprocal_rank"], rng
        )
        rows.append(
            {
                "kg": kg,
                "reference": reference,
                "compared": compared,
                "model": model,
                "relation": relation,
                "reference_predictions": len(x),
                "compared_predictions": len(y),
                "reference_mrr": x["reciprocal_rank"].mean(),
                "compared_mrr": y["reciprocal_rank"].mean(),
                "mrr_difference": (
                    y["reciprocal_rank"].mean() - x["reciprocal_rank"].mean()
                ),
                "mrr_difference_low": low,
                "mrr_difference_high": high,
                f"reference_hits_at_{k}": x[f"hits_at_{k}"].mean(),
                f"compared_hits_at_{k}": y[f"hits_at_{k}"].mean(),
                "cliffs_delta": delta,
                "effect": effect_size(delta),
                "mannwhitney_p": mann_whitney.pvalue,
                "ks_statistic": ks.statistic,
                "ks_p": ks.pvalue,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["kg", "reference", "compared", "model", "relation"]
        )

    comparison = pd.DataFrame(rows)
    pooled_rows = comparison["relation"] == ALL_RELATIONS
    for test in ("mannwhitney", "ks"):
        family_p = comparison.groupby(["kg", "compared", "model", pooled_rows])[
            f"{test}_p"
        ]
        comparison.insert(
            comparison.columns.get_loc(f"{test}_p") + 1,
            f"{test}_p_holm",
            family_p.transform(holm),
        )
    # Each pooled row after the relations of its graph pair and model.
    order = comparison.assign(pooled=pooled_rows).sort_values(
        ["kg", "model", "compared", "pooled"], kind="stable"
    )
    return comparison.loc[order.index].reset_index(drop=True)


def draw_column(ax, x: float, width: float, height: float, color: str) -> None:
    """
    Draw a column from 0 up to `height`, square at the baseline and with
    rounded top corners. The corner radius is converted from pixels to data
    units, so call this once the axes' limits and position are final.
    """
    if height <= 0:
        return
    px_x, px_y = np.abs(ax.transData.transform((1, 1)) - ax.transData.transform((0, 0)))
    rx, ry = min(BAR_RADIUS_PX / px_x, width / 2), min(BAR_RADIUS_PX / px_y, height)
    left, right = x - width / 2, x + width / 2
    P = mpath.Path
    vertices = [
        (left, 0),
        (left, height - ry),
        (left, height),
        (left + rx, height),
        (right - rx, height),
        (right, height),
        (right, height - ry),
        (right, 0),
        (left, 0),
    ]
    codes = [
        P.MOVETO,
        P.LINETO,
        P.CURVE3,
        P.CURVE3,
        P.LINETO,
        P.CURVE3,
        P.CURVE3,
        P.LINETO,
        P.CLOSEPOLY,
    ]
    ax.add_patch(PathPatch(P(vertices, codes), facecolor=color, edgecolor="none"))


def draw_caption(fig, height: float, x: float, title: str, subtitle: str) -> None:
    """
    Write a figure's title and subtitle in its caption band, the top
    CAPTION_HEIGHT inches of a figure `height` inches tall, from `x` (a
    fraction of the width), and close the band with a hairline rule just
    above its lower edge, so that cropping the band removes the rule too.
    The subtitle is wrapped at CAPTION_LINE_CHARS characters, never at a
    no-break space (\\u00a0), which keeps a value with its symbol.
    """
    lines = textwrap.wrap(subtitle, CAPTION_LINE_CHARS, break_on_hyphens=False)
    if len(lines) > CAPTION_LINES:
        print(
            f"warning: the caption of '{title}' has {len(lines)} lines, its band "
            f"fits {CAPTION_LINES}"
        )
    fig.text(
        x,
        1 - 0.3 / height,
        title,
        color=INK,
        fontsize=13,
        fontweight="bold",
        va="top",
    )
    fig.text(
        x,
        1 - 0.68 / height,
        "\n".join(lines),
        color=INK_SECONDARY,
        fontsize=9.5,
        va="top",
        linespacing=1.5,
    )
    rule = 1 - (CAPTION_HEIGHT - 0.04) / height
    fig.add_artist(
        Line2D(
            [0, 1],
            [rule, rule],
            transform=fig.transFigure,
            color=GRIDLINE,
            linewidth=PX,
        )
    )


def style_axes(ax) -> None:
    """Chart surface, muted tick labels, and no ticks or frame."""
    ax.set_facecolor(SURFACE)
    ax.tick_params(length=0, colors=INK_MUTED, labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_grouped_columns(
    ax, labels: list[str], series: list[tuple[np.ndarray, str]], top: float
) -> None:
    """
    Draw one group of columns per label, one column per (values, color) in
    `series`, on a y axis from 0 to `top` with a hairline grid and baseline.
    A NaN value leaves its column out. The axes limits are set first, since
    the column widths are given in pixels.
    """
    x = np.arange(len(labels))
    ax.set_xlim(-0.6, len(labels) - 0.4)
    ax.set_ylim(0, top)
    px_per_unit = ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0]
    gap = BAR_GAP_PX / px_per_unit
    # Columns are at most BAR_WIDTH_PX wide, and a group fills at most 80% of its slot.
    width = min(
        BAR_WIDTH_PX / px_per_unit, (0.8 - (len(series) - 1) * gap) / len(series)
    )
    span = len(series) * width + (len(series) - 1) * gap
    for i, (values, color) in enumerate(series):
        offset = (width - span) / 2 + i * (width + gap)
        for xi, value in zip(x + offset, values):
            if not np.isnan(value):
                draw_column(ax, xi, width, value, color)
    ax.set_xticks(x, labels)
    ax.grid(axis="y", color=GRIDLINE, linewidth=PX)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set(visible=True, color=BASELINE, linewidth=PX)


def plot_relations(report: pd.DataFrame, graphs: list[str], path: Path) -> None:
    """
    Plot one KG's `relation_report` rows (with `kg` and `graph` columns): one
    row of panels per model, MRR on the left and Hits@10 on the right, with
    one group of columns per relation and one column per graph trained with
    that model, colored in the order of `graphs`. A graph without a column
    for a relation doesn't have that relation.
    """
    kg = report["kg"].iat[0]
    models = sorted(report["model"].unique())
    relations = sorted(report["relation"].unique())
    k = HEADLINE_K
    metrics = [("mrr", "MRR"), (f"hits_at_{k}", f"Hits@{k}")]
    colors = dict(zip(graphs, CATEGORICAL))

    # Heights in inches: the caption band and legend, one row of panels per
    # model, the bottom axis.
    header, row_height, footer = CAPTION_HEIGHT + 0.75, 2.7, 0.5
    height = header + row_height * len(models) + footer
    fig, axes = plt.subplots(
        len(models), 2, figsize=(13, height), dpi=PLOT_DPI, squeeze=False
    )
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(
        left=0.06,
        right=0.98,
        top=1 - header / height,
        bottom=footer / height,
        wspace=0.12,
        hspace=0.5,
    )

    name = kg_name(kg)
    draw_caption(
        fig,
        height,
        0.06,
        f"{name}: metrics per relation" if name else "Metrics per relation",
        "Link prediction performance per relation. MRR is the mean reciprocal filtered rank "
        "of the correct entity over the held-out predictions of the relation (head and tail "
        f"pooled), and Hits@{k} the fraction of them ranked in the top {k} of all "
        "candidates; both range from 0 to 1, higher being better. A missing column means "
        "that the relation does not occur in that graph.",
    )
    fig.legend(
        handles=[
            Patch(facecolor=colors[graph], label=folder_name(graph)) for graph in graphs
        ],
        loc="upper left",
        bbox_to_anchor=(0.06, 1 - (CAPTION_HEIGHT + 0.25) / height),
        ncols=len(graphs),
        frameon=False,
        fontsize=9,
        labelcolor=INK_SECONDARY,
        handlelength=0.9,
        handleheight=0.9,
        borderaxespad=0,
        borderpad=0,
    )

    for row, model in zip(axes, models):
        runs = report[report["model"] == model]
        trained = [graph for graph in graphs if graph in set(runs["graph"])]
        for ax, (column, label) in zip(row, metrics):
            style_axes(ax)
            series = [
                (
                    runs[runs["graph"] == graph]
                    .set_index("relation")[column]
                    .reindex(relations)
                    .to_numpy(dtype=float),
                    colors[graph],
                )
                for graph in trained
            ]
            draw_grouped_columns(ax, relations, series, top=1.05)
            ax.set_yticks([0, 0.25, 0.5, 0.75, 1], ["0", "0.25", "0.5", "0.75", "1"])
            ax.tick_params(axis="x", labelsize=8.5)
            ax.set_title(
                f"{model} · {label}",
                loc="left",
                color=INK,
                fontsize=10.5,
                fontweight="bold",
                pad=8,
            )

    save_figure(fig, path)
    plt.close(fig)


def p_value(p: float) -> str:
    """A p-value as journals print it: "p=0.034", or "p<0.001" below 0.001."""
    if pd.isna(p):
        return "p=n/a"
    return "p<0.001" if p < 0.001 else f"p={p:.3f}"


def graph_notes(
    runs: pd.DataFrame, comparison: pd.DataFrame, graphs: list[str]
) -> list[tuple[str, tuple[str, ...], bool]]:
    """
    The number of held-out predictions of every graph in one relation's `runs`,
    with its Cliff's delta against the reference and the Holm-adjusted
    Mann-Whitney p from the relation's `compare_relations` rows, as (graph,
    cells, bold) triples in the order of `graphs`, where the cells are the
    name, the count, the delta and the p-value, e.g. ("pygraft", ("PyGraft",
    "n=190", "δ −0.95", "p<0.001"), False), and bold marks a p of
    SIGNIFICANCE or more: no detectable difference from the reference. The
    reference is not compared, so its delta and p-value cells are empty.
    """
    counts = runs.groupby("graph").size()
    rows = comparison.set_index("compared")
    notes = []
    for graph in graphs:
        if graph not in counts.index:
            continue
        delta, p, bold = "", "", False
        if graph in rows.index:
            row = rows.loc[graph]
            delta = "δ " + f"{row['cliffs_delta']:+.2f}".replace("-", "−")
            p = p_value(row["mannwhitney_p_holm"])
            bold = row["mannwhitney_p_holm"] >= SIGNIFICANCE
        cells = (folder_name(graph), f"n={counts[graph]:,}", delta, p)
        notes.append((graph, cells, bold))
    return notes


def note_columns(
    fig, notes: list[tuple[str, tuple, bool]], start: float
) -> list[float]:
    """
    The x offsets in points, from a panel's left edge, where the columns of
    `notes` (`graph_notes` triples) start, drawn in NOTE_FONT_SIZE from
    `start`, followed by where the last one ends. Each column is as wide as
    its widest cell, measured in bold for bold lines, plus NOTE_COLUMN_GAP_PT.
    """
    renderer = fig.canvas.get_renderer()
    probe = fig.text(0, 0, "", fontsize=NOTE_FONT_SIZE)
    widths = [0.0] * max(len(cells) for _, cells, _ in notes)
    for _, cells, bold in notes:
        probe.set_fontweight("bold" if bold else "normal")
        for i, cell in enumerate(cells):
            probe.set_text(cell)
            pixels = probe.get_window_extent(renderer).width if cell else 0
            widths[i] = max(widths[i], pixels * 72 / fig.dpi)
    probe.remove()
    offsets = [start]
    for width in widths[:-1]:
        offsets.append(offsets[-1] + width + NOTE_COLUMN_GAP_PT)
    return offsets + [offsets[-1] + widths[-1]]


def plot_hits_curves(
    predictions: pd.DataFrame,
    comparison: pd.DataFrame,
    graphs: list[str],
    reference: str,
    path: Path,
) -> None:
    """
    Plot the Hits@k curves of one KG and model (`predictions` holds only its
    rows, with the `kg` and `graph` columns of `split_dataset`): a first
    panel over all relations, then one panel per relation, and one line per
    graph, giving the share of predictions ranked within the top k for every
    k, so the height at k = 1 and k = 10 is Hits@1 and Hits@10. Lines are
    colored in the order of `graphs`. Each panel lists every graph's number
    of held-out predictions and its Cliff's delta against the reference from
    `comparison`, the `compare_relations` rows of this KG and model.
    """
    kg, model = predictions["kg"].iat[0], predictions["model"].iat[0]
    colors = dict(zip(graphs, CATEGORICAL))
    trained = [graph for graph in graphs if graph in set(predictions["graph"])]
    k_max = predictions["candidates"].max()
    # (title, predictions, comparison rows) of each panel.
    panels = [
        (
            ALL_RELATIONS_TITLE,
            predictions,
            comparison[comparison["relation"] == ALL_RELATIONS],
        )
    ] + [
        (
            relation,
            predictions[predictions["relation"] == relation],
            comparison[comparison["relation"] == relation],
        )
        for relation in sorted(predictions["relation"].unique())
    ]
    notes = [graph_notes(runs, deltas, graphs) for _, runs, deltas in panels]
    lines = max(len(n) for n in notes)

    # Heights in inches: the header, one row of panels per 4 panels, each
    # under its title and δ lines, and the bottom axis.
    columns = 4
    rows = -(-len(panels) // columns)
    header, panel, footer = CAPTION_HEIGHT + 0.95, 1.85, 0.6
    notes_height = lines * DELTA_LINE_PT / 72
    gap = 0.45 * panel + notes_height  # between rows of panels
    height = header + notes_height + rows * panel + (rows - 1) * gap + footer
    fig, axes = plt.subplots(
        rows, columns, figsize=(13, height), dpi=PLOT_DPI, squeeze=False
    )
    fig.patch.set_facecolor(SURFACE)
    # The same columns in every panel, after the 11-point dash of each line.
    note_x = note_columns(fig, [note for n in notes for note in n], 11)
    # Widen the figure, 13 inches at least, until the δ lines fit their panels.
    left, right, wspace = 0.06, 0.98, 0.18
    panel_share = (right - left) / (columns + (columns - 1) * wspace)
    fig.set_size_inches(max(13, note_x[-1] / 72 / panel_share), height)
    fig.subplots_adjust(
        left=left,
        right=right,
        top=1 - (header + notes_height) / height,
        bottom=footer / height,
        wspace=wspace,
        hspace=gap / panel,
    )

    ref_name = folder_name(reference)
    subtitle = (
        f"Cumulative distribution of the rank of the correct entity in the held-out predictions "
        f"of {model} on a logarithmic rank axis, over all relations (first panel) and per "
        f"relation, so that the height of a curve at k is its Hits@k. {HITS_DEFINITION} "
        "Curves that rise earlier indicate better link "
        "prediction. Under each panel title, n is the number of held-out predictions of each "
        "graph"
    )
    if comparison.empty:
        subtitle += "."
    else:
        subtitle += (
            f", δ its Cliff's delta against {ref_name} on candidate-normalized ranks, from −1 "
            "to 1, negative when the graph ranks worse, and p the Holm-corrected p-value of a "
            "two-sided Mann–Whitney U\u00a0test. Bold entries show no significant difference "
            f"(p\u00a0≥\u00a0{SIGNIFICANCE})."
        )
    draw_caption(
        fig, height, 0.06, f"{kg_name(kg, model)}: Hits@k per relation", subtitle
    )
    fig.legend(
        handles=[
            Line2D(
                [], [], color=colors[graph], linewidth=2 * PX, label=folder_name(graph)
            )
            for graph in trained
        ],
        loc="upper left",
        bbox_to_anchor=(0.06, 1 - (CAPTION_HEIGHT + 0.25) / height),
        ncols=len(trained),
        frameon=False,
        fontsize=9,
        labelcolor=INK_SECONDARY,
        handlelength=1.6,
        borderaxespad=0,
        borderpad=0,
    )

    for i, ax in enumerate(axes.flat):
        if i >= len(panels):
            ax.set_visible(False)
            continue
        title, runs, _ = panels[i]
        style_axes(ax)
        # The reference graph last, so that its line is drawn on top.
        for graph in reversed(trained):
            ranks = runs.loc[runs["graph"] == graph, "rank"].to_numpy()
            if len(ranks) == 0:
                continue
            values, counts = np.unique(ranks, return_counts=True)
            shares = np.cumsum(counts) / len(ranks)
            if values[0] > 1:  # no answer ranked first, so the curve starts at 0
                values, shares = np.r_[1, values], np.r_[0, shares]
            ax.step(
                np.r_[values, k_max],
                np.r_[shares, 1],
                where="post",
                color=colors[graph],
                linewidth=2 * PX,
                solid_joinstyle="round",
                solid_capstyle="round",
            )
        ax.set_xscale("log")
        ax.minorticks_off()
        ax.set_xlim(1, k_max)
        ax.set_ylim(0, 1.02)
        ax.set_xticks([1, 10, 100, 1000], ["1", "10", "100", "1,000"])
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1], ["0", "0.25", "0.5", "0.75", "1"])
        ax.grid(color=GRIDLINE, linewidth=PX)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set(visible=True, color=BASELINE, linewidth=PX)
        ax.set_title(
            title,
            loc="left",
            color=INK,
            fontsize=10.5,
            fontweight="bold",
            pad=6 + lines * DELTA_LINE_PT,
        )
        # One line per graph under the title, the first graph on top, each
        # keyed by a dash in its line color, in the columns of `note_x`.
        for j, (graph, cells, bold) in enumerate(reversed(notes[i])):
            weight = "bold" if bold else "normal"
            for text, color, x, text_weight in [
                ("━", colors[graph], 0, "normal"),
                *((cell, INK_SECONDARY, x, weight) for cell, x in zip(cells, note_x)),
            ]:
                ax.annotate(
                    text,
                    xy=(0, 1),
                    xycoords="axes fraction",
                    xytext=(x, 4 + j * DELTA_LINE_PT),
                    textcoords="offset points",
                    color=color,
                    fontsize=NOTE_FONT_SIZE,
                    fontweight=text_weight,
                    va="bottom",
                )
        if i % columns == 0:
            ax.set_ylabel("Share ranked ≤ k", color=INK_SECONDARY, fontsize=9.5)
        if i + columns >= len(panels):  # no panel below this one
            ax.set_xlabel("Rank threshold k", color=INK_SECONDARY, fontsize=9.5)

    save_figure(fig, path)
    plt.close(fig)


def line_grid(
    panels: int,
    title: str,
    subtitle: str,
    colors: dict[str, str],
    xlabel: str,
    ylabel: str,
):
    """
    Start a figure of `panels` line panels, 4 per row, under a title, a
    subtitle and a line legend of `colors` (label to color), with `xlabel`
    under the bottom panels and `ylabel` beside the first column. Returns the
    figure and its visible axes; the panels left over in the last row are
    hidden.
    """
    columns = min(4, panels)
    rows = -(-panels // columns)
    # Heights in inches: the caption band and legend, one row of panels, the
    # bottom axis.
    header, row_height, footer = CAPTION_HEIGHT + 0.95, 2.7, 0.6
    height = header + row_height * rows + footer
    fig, axes = plt.subplots(
        rows, columns, figsize=(13, height), dpi=PLOT_DPI, squeeze=False
    )
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(
        left=0.06,
        right=0.98,
        top=1 - header / height,
        bottom=footer / height,
        wspace=0.18,
        hspace=0.45,
    )
    draw_caption(fig, height, 0.06, title, subtitle)
    fig.legend(
        handles=[
            Line2D([], [], color=color, linewidth=2 * PX, label=label)
            for label, color in colors.items()
        ],
        loc="upper left",
        bbox_to_anchor=(0.06, 1 - (CAPTION_HEIGHT + 0.25) / height),
        ncols=len(colors),
        frameon=False,
        fontsize=9,
        labelcolor=INK_SECONDARY,
        handlelength=1.6,
        borderaxespad=0,
        borderpad=0,
    )

    axes = list(axes.flat)
    for ax in axes[panels:]:
        ax.set_visible(False)
    for i, ax in enumerate(axes[:panels]):
        if i % columns == 0:
            ax.set_ylabel(ylabel, color=INK_SECONDARY, fontsize=9.5)
        if i + columns >= panels:  # no panel below this one
            ax.set_xlabel(xlabel, color=INK_SECONDARY, fontsize=9.5)
    return fig, axes[:panels]


def draw_hits_lines(
    ax, predictions: pd.DataFrame, by: str, colors: dict[str, str], title: str
) -> None:
    """
    Draw one Hits@k panel titled `title`: one line per value of the `by`
    column ("model" or "graph") that is in `colors` and has predictions in
    `predictions`, through its Hits@k at every k of HITS_AT, on a 0 to 1
    scale shared by every panel. Under the title, the number of test
    predictions behind each line, since a small panel's lines rest on few
    of them.
    """
    style_axes(ax)
    grouped = predictions.groupby(by)
    hits = grouped[HITS_COLUMNS].mean()
    # The first entry of `colors` last, so that its line (the reference
    # graph's, when comparing graphs) is drawn on top.
    for key, color in reversed(colors.items()):
        if key in hits.index:
            ax.plot(
                HITS_AT,
                hits.loc[key].to_numpy(),
                color=color,
                linewidth=2 * PX,
                marker="o",
                markersize=6 * PX,
                solid_joinstyle="round",
                solid_capstyle="round",
            )
    ax.set_xlim(min(HITS_AT) - 0.4, max(HITS_AT) + 0.4)
    ax.set_ylim(0, 1.02)
    ax.set_xticks([1, 5, 10, 15], ["1", "5", "10", "15"])
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1], ["0", "0.25", "0.5", "0.75", "1"])
    ax.grid(color=GRIDLINE, linewidth=PX)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set(visible=True, color=BASELINE, linewidth=PX)
    ax.set_title(title, loc="left", color=INK, fontsize=10.5, fontweight="bold", pad=20)
    # The models of one graph share its held-out set, but graphs have their own.
    counts = grouped.size()
    low, high = int(counts.min()), int(counts.max())
    count = f"{low:,}" if low == high else f"{low:,} to {high:,}"
    ax.text(
        0,
        1.03,
        f"{count} held-out predictions per {by}",
        transform=ax.transAxes,
        color=INK_SECONDARY,
        fontsize=8,
        va="bottom",
    )


def present(
    colors: dict[str, str], predictions: pd.DataFrame, by: str
) -> dict[str, str]:
    """The entries of `colors` whose value of the `by` column has predictions."""
    keys = set(predictions[by])
    return {key: color for key, color in colors.items() if key in keys}


def plot_model_hits(
    predictions: pd.DataFrame, graphs: list[str], colors: dict[str, str], path: Path
) -> None:
    """
    Plot the Hits@k of every model of one KG (`predictions` holds only its
    rows, with the `kg` and `graph` columns of `split_dataset`): one panel per
    graph, in the order of `graphs`, and one line per model, colored by
    `colors`.
    """
    kg = predictions["kg"].iat[0]
    shown = [graph for graph in graphs if graph in set(predictions["graph"])]
    name = kg_name(kg)
    fig, axes = line_grid(
        len(shown),
        f"{name}: {HITS_RANGE} per model" if name else f"{HITS_RANGE} per model",
        f"Hits@k of each knowledge graph embedding model on each graph. {HITS_DEFINITION} "
        "Hits@k cannot decrease with k, and a steep segment means that many correct "
        "entities rank around that k. Under each panel title, the number of held-out "
        "predictions behind its lines; the models of a graph share its held-out set.",
        present(colors, predictions, "model"),
        "k",
        "Hits@k",
    )
    for ax, graph in zip(axes, shown):
        draw_hits_lines(
            ax,
            predictions[predictions["graph"] == graph],
            "model",
            colors,
            folder_name(graph),
        )
    save_figure(fig, path)
    plt.close(fig)


def plot_relation_hits(
    predictions: pd.DataFrame, colors: dict[str, str], path: Path
) -> None:
    """
    Plot the Hits@k of one model on every graph of one KG (`predictions`
    holds only its rows, with the `kg` and `graph` columns of
    `split_dataset`): a first panel over all relations, then one panel per
    relation, with one line per graph, colored by `colors` (graph to color,
    in the plots' graph order). A graph without a line in a panel doesn't
    have that relation.
    """
    kg, model = predictions["kg"].iat[0], predictions["model"].iat[0]
    relations = sorted(predictions["relation"].unique())
    panels = [(ALL_RELATIONS_TITLE, predictions)] + [
        (relation, predictions[predictions["relation"] == relation])
        for relation in relations
    ]
    fig, axes = line_grid(
        len(panels),
        f"{kg_name(kg, model)}: {HITS_RANGE} per relation",
        f"Hits@k of {model} on each graph, over all relations (first panel) and per "
        f"relation. {HITS_DEFINITION} Hits@k cannot decrease with k, and a steep segment "
        "means that many correct entities rank around that k. Under each panel title, the "
        "number of held-out predictions behind its lines; lines resting on few predictions are "
        "less reliable. A graph without a line in a panel does not contain that relation.",
        {
            folder_name(graph): color
            for graph, color in present(colors, predictions, "graph").items()
        },
        "k",
        "Hits@k",
    )
    for ax, (label, runs) in zip(axes, panels):
        draw_hits_lines(ax, runs, "graph", colors, label)
    save_figure(fig, path)
    plt.close(fig)


def plot_training_losses(losses: pd.DataFrame, graphs: list[str], path: Path) -> None:
    """
    Plot the training loss of every model of one KG (`losses` holds only its
    `build_losses` rows, with the `kg` and `graph` columns of
    `split_dataset`): one panel per model and one line per graph, colored in
    the order of `graphs`, on a log scale, since the loss falls by orders of
    magnitude. Each model has its own loss function, so each panel has its
    own scale.
    """
    kg = losses["kg"].iat[0]
    models = sorted(losses["model"].unique())
    colors = dict(zip(graphs, CATEGORICAL))
    trained = [graph for graph in graphs if graph in set(losses["graph"])]
    name = kg_name(kg)
    fig, axes = line_grid(
        len(models),
        f"{name}: training loss per model" if name else "Training loss per model",
        "Training loss per epoch on a logarithmic scale, averaged over the training triples "
        "as reported by PyKEEN. The models use different loss functions, so the values can "
        "be compared between the graphs of a panel but not between panels.",
        {folder_name(graph): colors[graph] for graph in trained},
        "Epoch",
        "Loss",
    )
    for ax, model in zip(axes, models):
        style_axes(ax)
        runs = losses[losses["model"] == model]
        # The reference graph last, so that its line is drawn on top.
        for graph in reversed(trained):
            run = runs[runs["graph"] == graph]
            if not run.empty:
                ax.plot(
                    run["epoch"],
                    run["loss"],
                    color=colors[graph],
                    linewidth=2 * PX,
                    solid_joinstyle="round",
                    solid_capstyle="round",
                )
        ax.set_yscale("log")
        # 1, 2 and 5 of every decade, written plainly (0.02 rather than 2×10⁻²),
        # so that a loss that stays within one decade still gets several ticks.
        ax.yaxis.set_major_locator(LogLocator(subs=(1, 2, 5)))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.minorticks_off()
        ax.set_xlim(1, runs["epoch"].max())
        ax.grid(color=GRIDLINE, linewidth=PX)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set(visible=True, color=BASELINE, linewidth=PX)
        ax.set_title(
            model, loc="left", color=INK, fontsize=10.5, fontweight="bold", pad=8
        )
    save_figure(fig, path)
    plt.close(fig)


def write_cropped(plot: Path, out: Path) -> Path:
    """
    Save a copy of `plot`, a plot of the analysis folder `out`, without its
    caption band, the top CAPTION_HEIGHT inches, to the same place under
    `out`'s CROPPED_FOLDER, for a publication, where that text goes in the
    caption. Returns the copy's path.
    """
    cropped = out / CROPPED_FOLDER / plot.relative_to(out)
    cropped.parent.mkdir(parents=True, exist_ok=True)
    band = round(CAPTION_HEIGHT * PLOT_DPI)
    with Image.open(plot) as image:
        with atomic_path(cropped) as temporary:
            image.crop((0, band, image.width, image.height)).save(
                temporary, format="PNG", dpi=(PLOT_DPI, PLOT_DPI)
            )
    return cropped


def write_analysis(
    rows: list[dict],
    predictions: pd.DataFrame,
    losses: pd.DataFrame,
    output: Path,
    reference: str,
    progress: Progress,
    crop: bool = False,
) -> None:
    """
    Write each family's analysis (see the module docstring) to
    `<output>/<family>/`, since the variants of a family are only compared with
    each other, and the summary of every run to `<output>/SUMMARY_NAME`. With
    `crop`, every plot also gets a copy without its caption band in its
    family's CROPPED_FOLDER.
    """
    if predictions.empty:
        print("warning: no run could be ranked, so no analysis was written")
        return

    summary = [
        {**dict(zip(("family", "variant"), row["dataset"].split("/"))), **row}
        for row in rows
    ]
    write_csv(summary, output / SUMMARY_NAME, SUMMARY_FIELDS)
    print(f"Wrote {len(summary)} row(s) to {output / SUMMARY_NAME}")

    runs = split_dataset(predictions)
    losses = split_dataset(losses)
    families = runs["kg"].nunique()
    for number, (kg, kg_runs) in enumerate(runs.groupby("kg"), start=1):
        progress.stage(f"analysis {number}/{families}", kg)
        print(f"[analysis {number}/{families}] {kg}", flush=True)
        out = output / kg
        kg_rows = [row for row in rows if row["dataset"].split("/")[0] == kg]
        write_csv(kg_rows, out / REPORT_NAME)
        print(f"Wrote {len(kg_rows)} row(s) to {out / REPORT_NAME}")
        write_kg_analysis(
            kg_runs,
            losses[losses["kg"] == kg],
            pd.DataFrame(kg_rows, columns=CSV_FIELDS),
            out,
            reference,
            crop,
        )


def write_kg_analysis(
    runs: pd.DataFrame,
    losses: pd.DataFrame,
    metrics: pd.DataFrame,
    out: Path,
    reference: str,
    crop: bool = False,
) -> None:
    """
    Write the analysis of one family to `out`: from its `runs` (the
    `build_predictions` rows of its variants, with the `kg` and `graph`
    columns of `split_dataset`), the training `losses` of its variants and its
    rows of the metrics table (`metrics`), the CSV files and plots of the
    module docstring, and with `crop` a copy of every plot without its caption
    band (`write_cropped`).
    """
    kg = runs["kg"].iat[0]
    predictions = runs[PREDICTION_COLUMNS]
    relation_comparison = compare_relations(runs, reference)
    rankings = ranking_agreement(split_dataset(metrics), reference, AGREEMENT_METRICS)
    report = relation_report(predictions)
    tables = {
        RELATIONS_NAME: report,
        "training_losses.csv": losses[["dataset", "model", "epoch", "loss"]],
    }
    if relation_comparison.empty:
        print(f"note: {kg} has no '{reference}' variant, so no relations were compared")
    else:
        tables["relation_comparison.csv"] = relation_comparison
    if rankings.empty:
        print(
            f"note: {kg} has no '{reference}' variant or no other variant, so no model "
            "rankings were compared"
        )
    else:
        tables["ranking_agreement.csv"] = rankings

    out.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        write_table(table, out / name)
        print(f"Wrote {len(table)} row(s) to {out / name}")

    # One variant order per family, so that each variant keeps its color in every plot.
    graphs = graph_order(runs["graph"])
    if len(graphs) > len(CATEGORICAL):
        print(
            f"warning: {kg} has {len(graphs)} variants, the relation plots show "
            f"the first {len(CATEGORICAL)}"
        )
        graphs = graphs[: len(CATEGORICAL)]
    hits_range = f"hits_at_{min(HITS_AT)}-{max(HITS_AT)}"
    plots = []

    report = split_dataset(report)
    plots.append(out / "relations.png")
    plot_relations(report[report["graph"].isin(graphs)], graphs, plots[-1])
    shown = runs[runs["graph"].isin(graphs)]
    # The plots of one model go to its own folder under PLOTS_FOLDER.
    for model, model_runs in shown.groupby("model"):
        model_plots = out / PLOTS_FOLDER / model
        model_plots.mkdir(parents=True, exist_ok=True)
        plots.append(model_plots / f"{model}_hits_at_k.png")
        deltas = relation_comparison[relation_comparison["model"] == model]
        plot_hits_curves(model_runs, deltas, graphs, reference, plots[-1])
        plots.append(model_plots / f"{model}_relations_{hits_range}.png")
        plot_relation_hits(model_runs, dict(zip(graphs, CATEGORICAL)), plots[-1])

    # One model order per family, so that each model keeps its color in the Hits@k line plots.
    models = sorted(shown["model"].unique())
    if len(models) > len(CATEGORICAL):
        print(
            f"warning: {kg} has {len(models)} models, the Hits@k line plots show "
            f"the first {len(CATEGORICAL)}"
        )
        models = models[: len(CATEGORICAL)]
    plots.append(out / f"models_{hits_range}.png")
    plot_model_hits(
        shown[shown["model"].isin(models)],
        graphs,
        dict(zip(models, CATEGORICAL)),
        plots[-1],
    )

    shown_losses = losses[losses["graph"].isin(graphs)]
    if not shown_losses.empty:
        plots.append(out / "training_loss.png")
        plot_training_losses(shown_losses, graphs, plots[-1])

    for plot in plots:
        print(f"Wrote {plot}")
        if crop:
            print(f"Wrote {write_cropped(plot, out)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rank every held-out prediction of the models train_kge.py trained, "
        "and compare the variants of each dataset family.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--results",
        type=Path,
        default=DEFAULT_RESULTS,
        help="train_kge.py's output, with <dataset>/<model>/results.json",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Folder for the ranks and the analysis of every family",
    )
    p.add_argument(
        "--families",
        nargs="+",
        default=None,
        help="Families to analyse, by their original dataset's name (default: all)",
    )
    p.add_argument(
        "--variants",
        nargs="+",
        default=None,
        choices=VARIANT_ORDER,
        help="Variants to analyse (default: all)",
    )
    p.add_argument(
        "--reference",
        default=REFERENCE_VARIANT,
        help="Variant whose relations and model ranking the others are compared with",
    )
    p.add_argument(
        "--device",
        default="cuda" if torch.cuda.is_available() else "cpu",
        help="Device that scores the held-out predictions",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=f"Rank every run again, even those with a {PREDICTIONS_FILE}",
    )
    p.add_argument(
        "--crop",
        action="store_true",
        help="Also write every plot without its caption band (title and explanation) to "
        f"a {CROPPED_FOLDER}/ subfolder of its family's folder, for a publication",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.results.is_dir():
        print(f"error: results directory not found: {args.results}")
        return 1
    runs = find_runs(args.results, args.families, args.variants)
    if not runs:
        print(f"error: no finished run (results.json) under {args.results}")
        return 1
    print(f"Found {len(runs)} finished run(s) under {args.results}")

    # Ctrl-C and SIGTERM (e.g. kill) stop the validation cleanly. SIGINT is set
    # explicitly: a process started in the background inherits it ignored.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    with validation_lock(args.output) as locked:
        if not locked:
            print(
                f"error: another validation is writing to {args.output} (it holds "
                f"{args.output / LOCK_NAME}); see scripts/validation_eta.py"
            )
            return 1
        progress = Progress(args.output, runs, args.force, args.families, args.variants)
        try:
            predictions = build_predictions(
                runs, args.output, torch.device(args.device), progress, args.force
            )
            rows = build_report(predictions)
            losses = build_losses(runs)
            write_analysis(
                rows,
                predictions,
                losses,
                args.output,
                args.reference,
                progress,
                args.crop,
            )
        except KeyboardInterrupt:
            progress.interrupted()
            print(
                "Interrupted. The runs ranked so far are kept; run the same command to "
                "resume.",
                flush=True,
            )
            return INTERRUPTED_STATUS
        progress.stage("done")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
