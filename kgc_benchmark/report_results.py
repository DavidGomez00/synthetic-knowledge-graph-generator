"""
Collect Hits@k / MRR from pykeen results.json files and export them to CSV,
then analyse every single test prediction behind those numbers.

`KGC.py` trains each model with pykeen's `pipeline()` and saves its output
with `results.save_to_directory(...)`, which writes a `results.json`
alongside the trained model, one per `<results_path>/<model>/` directory
(e.g. `Output/french_royalty/skgg.std=1.filled/TuckER/results.json`). That file's
`"metrics"` key holds pykeen's full evaluation report, nested as
`metrics[rank_type][filtering]`, where `rank_type` is "head", "tail" or
"both" and `filtering` is "optimistic", "pessimistic" or "realistic".

This script walks a results directory recursively
(`<root>/<dataset...>/<model>/results.json`, where `<dataset...>` is one or
more folders such as `french_royalty/skgg.std=1.filled`, named with its KG folder
even when `<root>` is that KG's folder), pulls Hits@1/3/5/10 and MRR out
of the "both"/"realistic" slice of each one -- "both" combines head and
tail prediction, "realistic" is the standard filtered-ranking evaluation
reported in the KG completion literature -- and writes one row per
dataset/model pair to a CSV. The CSV holds Hits@k for every k from 1 to
1000: the k that results.json lacks are counted from the recomputed ranks
below, so they stay empty for a run that couldn't be ranked.

The per-prediction analysis goes below those averages. `results.json` only
stores aggregates, so the rank of every test prediction is recomputed from
the run's `trained_model.pkl` and the `test` split KGC.py saves next to the
model folders (`save_splits: true`), the same way pykeen ranked it. These
ranks average to the numbers in the table; a run whose recomputed MRR
doesn't match its results.json (its splits were rewritten by a later run)
is left out of the analysis.

The reference KG (`--reference`, `source` by default) is then compared
prediction by prediction with every sibling dataset matching `--compare`
(`skgg*` by default, e.g. `skgg.std=1.filled`): synthetic graphs generated from it
that keep its entity names. They share no test triples with it  so
predictions are paired by query: a tail query `(h, r, ?)` or a head query
`(?, r, t)` held out in both graphs, each graph
ranking its own held-out answers. PyGraft renames every entity, so its
queries never pair.

Each relation is also compared between the reference and every other graph
of its KG, PyGraft included, over all the relation's test predictions: the
difference in MRR, and tests and an effect size for the rank distributions.

The reference is also compared with every other graph of its KG as a
benchmark: how the two rank the models trained on both, by Hits@k and by
MRR, as a Kendall's tau per metric (see kendall_tau.py).

The plots name each graph by its FOLDER_NAMES entry, or by its folder name
when it has none. The CSV files and the plot file names keep folder names.

Each KG's analysis is written to its own folder, `<root>/<kg>/prediction_analysis`
(or `<analysis-dir>/<kg>` with `--analysis-dir`), since a KG's graphs are
only compared with each other:

    metrics_report.csv               the metrics table's rows of this KG
    predictions.csv                  one row per test prediction, every run
    relation_report.csv              the table's metrics per relation
    training_losses.csv              one row per training epoch, every run
    relation_comparison.csv          each relation's ranks, reference vs every graph
    query_comparison.csv             one row per query paired between two graphs
    query_comparison_summary.csv     correlation and agreement per stratum and k
    ranking_agreement.csv            Kendall's tau of the model rankings, reference vs every graph
    relations.png                    MRR and Hits@10 per relation, model and graph
    models_hits_at_1-15.png          Hits@1 to Hits@15 per graph, one line per model
    training_loss.png                training loss per model, one line per graph
    <model>/                         the plots of one model:
        <model>_hits_at_k.png        Hits@k curves per relation, one line per graph
        <model>_relations_hits_at_1-15.png
                                     Hits@1 to Hits@15 per relation, one line per graph
        <compared>_<model>.png       rank distributions and rank transitions
    cropped/                         with --crop: every plot without its caption
                                     band, in the same folders

Usage:

    python report_results.py
    python report_results.py --root Output/french_royalty
    python report_results.py --reference source --compare "skgg*" --analysis-dir analysis
    python report_results.py --crop
"""

from __future__ import annotations

import argparse
import csv
import json
import textwrap
from collections import defaultdict
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd
import torch
from matplotlib import path as mpath
from matplotlib import pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.ticker import FuncFormatter, LogLocator, PercentFormatter
from PIL import Image
from pykeen.triples import TriplesFactory
from scipy.stats import ks_2samp, mannwhitneyu, spearmanr

from cliffs_delta import cliffs_delta, effect_size
from kendall_tau import ranking_agreement

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR / "Output"
# The metrics table, written to `<root>/REPORT_NAME` (or --output) for every
# KG and to each KG's analysis folder for that KG.
REPORT_NAME = "metrics_report.csv"
# The folder of each KG's per-prediction analysis, inside the KG's folder.
ANALYSIS_FOLDER = "prediction_analysis"
# Subfolder of a KG's analysis folder for the plots without their caption band
# (--crop).
CROPPED_FOLDER = "cropped"


# pykeen's evaluation report is sliced by rank_type ("head"/"tail"/"both")
# and filtering ("optimistic"/"pessimistic"/"realistic"). "both" combines
# head- and tail-prediction ranks; "realistic" is the standard filtered
# evaluation reported in the KG completion literature (see module
# docstring), so that's the slice pulled out here.
RANK_TYPE = "both"
FILTERING = "realistic"

# The dataset whose predictions the others are compared with, and
# shell-style patterns naming its sibling datasets to compare: graphs
# generated from that KG that keep its entity names (PyGraft's don't).
DEFAULT_REFERENCE = "source"
DEFAULT_COMPARED = ["skgg*"]

# Names shown in the plots for the folders of a results_path: the KG folders
# (e.g. french_royalty) and each graph's own folder (e.g. source). A folder
# that isn't listed is shown by its folder name. The CSV files and the plot
# file names keep the folder names.
FOLDER_NAMES = {
    "french_royalty": "French Royalty",
    "source": "Original KG",
    "skgg.pca=0.9.filled": "SKGG (PCA 0.9)",
    "skgg.std=1.filled": "SKGG",
    "pygraft": "PyGraft",
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
# The `relation` of the comparisons over all of a graph's test predictions,
# and the title of their plot panels.
ALL_RELATIONS = "all"
ALL_RELATIONS_TITLE = "all relations"
# The k of the headline Hits@k in the relation plots, the relation comparison
# and the comparison plots.
HEADLINE_K = 10
# The k compared query by query, kept to the usual four so that the query
# comparison tables stay readable.
COMPARISON_HITS_AT = (1, 3, 5, 10)
COMPARISON_HITS_COLUMNS = [f"hits_at_{k}" for k in COMPARISON_HITS_AT]
# The metrics of the metrics table that the models are ranked by in each graph,
# to compare the graphs' rankings (ranking_agreement.csv). Hits@15 is not in
# results.json, so a run without recomputed ranks has none.
AGREEMENT_HITS_AT = (1, 3, 5, 10, 15)
AGREEMENT_METRICS = [*(f"hits_at_{k}" for k in AGREEMENT_HITS_AT), "mrr"]

CSV_FIELDS = ["dataset", "model", *REPORT_HITS_COLUMNS, "mrr", "count"]
# A query is "accurate at k" in a graph when at least this share of its
# held-out answers rank within the top k. Most queries have one answer, so
# this is usually just a hit or a miss.
ACCURATE_SHARE = 0.5
# Largest gap allowed between the MRR recomputed from a run's saved model
# and splits and the MRR in its results.json. A bigger gap means the splits
# on disk were written by another run.
MRR_TOLERANCE = 1e-3
# Test triples scored per forward pass when recomputing ranks.
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
    "side",
    "head",
    "relation",
    "tail",
    "rank",
    "candidates",
    "reciprocal_rank",
    *HITS_COLUMNS,
]

# Rank buckets of the plots, as (lower, upper] edges. Realistic ranks are
# fractional when scores tie, e.g. 1.5 falls in "2–3".
BUCKET_EDGES = [0, 1, 3, 10, 100, np.inf]
BUCKET_LABELS = ["1", "2–3", "4–10", "11–100", ">100"]

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
SERIES_COLORS = CATEGORICAL[:2]  # reference graph, compared graph
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "blue",
    [
        "#cde2fb",
        "#b7d3f6",
        "#9ec5f4",
        "#86b6ef",
        "#6da7ec",
        "#5598e7",
        "#3987e5",
        "#2a78d6",
        "#256abf",
        "#1c5cab",
        "#184f95",
        "#104281",
        "#0d366b",
    ],
)
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
    "Hits@k is the fraction of test predictions (head and tail pooled) whose correct "
    "entity ranks in the top k of all candidates, under filtered ranking with ties at "
    "their mean rank."
)


def dataset_base(root: Path) -> Path:
    """
    The folder that datasets are named relative to: `root`, or its parent
    when `root` is one KG's folder, with every run in a graph folder right
    under it (`<root>/<graph>/<model>/results.json`). A dataset is then
    named with its KG either way, e.g. `french_royalty/source`, and the CSV
    files are the same for a run over one KG as for a run over all of them.
    """
    depths = {len(p.relative_to(root).parts) for p in root.rglob("results.json")}
    return root.absolute().parent if depths == {3} else root


def find_results(root: Path, base: Path):
    """
    Yield (dataset, model, results_json_path) for every pykeen
    `results.json` found under `root`, assuming the
    `<root>/<dataset...>/<model>/results.json` layout that `KGC.py` produces
    (`<dataset...>` is the `results_path` folder chain relative to `base`,
    see `dataset_base`, e.g. `french_royalty/skgg.std=1.filled`, reported
    with "/").
    """
    for results_json in sorted(root.absolute().rglob("results.json")):
        model_dir = results_json.parent
        dataset = model_dir.parent.relative_to(base.absolute()).as_posix()
        if dataset == ".":
            continue
        yield dataset, model_dir.name, results_json


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


def build_report(root: Path, base: Path) -> list[dict]:
    rows = []
    for dataset, model, results_json in find_results(root, base):
        metrics = extract_metrics(results_json)
        if metrics is None:
            continue
        rows.append({"dataset": dataset, "model": model, **metrics})
    return rows


def build_losses(base: Path, rows: list[dict]) -> pd.DataFrame:
    """
    The training loss of every run in the table (`rows` from `build_report`,
    their datasets relative to `base`), one row per epoch, from the "losses" pykeen's `results.json` keeps: the
    loss of each epoch, averaged over the training triples.
    """
    frames = []
    for row in rows:
        results_json = base / row["dataset"] / row["model"] / "results.json"
        with results_json.open(encoding="utf-8") as f:
            losses = json.load(f).get("losses") or []
        frames.append(
            pd.DataFrame(
                {
                    "dataset": row["dataset"],
                    "model": row["model"],
                    "epoch": np.arange(1, len(losses) + 1),
                    "loss": losses,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def write_csv(rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_split(path: Path) -> np.ndarray:
    """Read a split KGC.py saved with `save_splits`: one tab-separated labeled triple per line."""
    return pd.read_csv(
        path, sep="\t", header=None, dtype=str, keep_default_na=False
    ).to_numpy()


def rank_predictions(dataset_dir: Path, model_dir: Path) -> pd.DataFrame | None:
    """
    Rank both predictions of every test triple of one run, as pykeen did.

    Each test triple (h, r, t) gives a tail prediction (h, r, ?) and a head
    prediction (?, r, t). Like `pipeline()`'s filtered evaluation, the other
    answers known to be true -- the training triples saved in
    `training_triples/`, the `test` split KGC.py saved in `dataset_dir`, and a
    `valid` split if the folder has one -- are removed before ranking, and
    ties get the "realistic" rank: the mean of the best and worst position
    among equal scores.

    Returns one row per prediction (side, head, relation, tail, rank,
    candidates), where `side` is the entity being predicted and `candidates`
    the number of entities it was ranked among (all but the filtered
    answers, pykeen's "number of options"), or None (and prints a warning)
    if a file is missing or the test labels aren't in the model's vocabulary.
    """
    needed = [
        dataset_dir / "test",
        model_dir / "trained_model.pkl",
        model_dir / "training_triples",
    ]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        print(
            f"warning: no prediction analysis for {model_dir} (missing {', '.join(missing)})"
        )
        return None

    test = read_split(dataset_dir / "test")
    training = TriplesFactory.from_path_binary(model_dir / "training_triples")
    # PyKEEN reloads a column of all-numeric labels as integers; test labels are strings.
    entity_to_id = {str(label): i for label, i in training.entity_to_id.items()}
    relation_to_id = {str(label): i for label, i in training.relation_to_id.items()}
    # Runs trained with a validation split (not KGC.py's) filter it too.
    valid_path = dataset_dir / "valid"
    valid = read_split(valid_path) if valid_path.is_file() else []
    try:
        test_ids, valid_ids = (
            torch.tensor(
                [[entity_to_id[h], relation_to_id[r], entity_to_id[t]] for h, r, t in split],
                dtype=torch.long,
            ).reshape(-1, 3)
            for split in (test, valid)
        )
    except KeyError as e:
        print(
            f"warning: no prediction analysis for {model_dir} (label {e} is not in the "
            "model's vocabulary, so its splits were written by another run)"
        )
        return None

    # Every answer known to be true, per tail query (h, r) and head query (r, t).
    tails, heads = defaultdict(list), defaultdict(list)
    for h, r, t in torch.cat([training.mapped_triples, test_ids, valid_ids]).tolist():
        tails[h, r].append(t)
        heads[r, t].append(h)

    # The pickle holds the whole model object, not just its weights.
    model = torch.load(
        model_dir / "trained_model.pkl", map_location="cpu", weights_only=False
    )
    model.eval()
    ranks, candidates = {}, {}
    with torch.inference_mode():
        for side, column in (("head", 0), ("tail", 2)):
            side_ranks, side_candidates = [], []
            for batch in torch.split(test_ids, SCORE_BATCH_SIZE):
                scores = model.predict(hrt_batch=batch, target=side)
                rows = torch.arange(len(batch))
                true_scores = scores[rows, batch[:, column]]
                for i, (h, r, t) in enumerate(batch.tolist()):
                    scores[i, heads[r, t] if side == "head" else tails[h, r]] = float(
                        "nan"
                    )
                scores[rows, batch[:, column]] = true_scores
                # Filtered (NaN) scores compare False, so they never count.
                better = (scores > true_scores[:, None]).sum(dim=1)
                not_worse = (scores >= true_scores[:, None]).sum(dim=1)
                side_ranks.append((better + 1 + not_worse) / 2)
                side_candidates.append(torch.isfinite(scores).sum(dim=1))
            ranks[side] = torch.cat(side_ranks).double().numpy()
            candidates[side] = torch.cat(side_candidates).numpy()

    return pd.concat(
        [
            pd.DataFrame(
                {
                    "side": side,
                    "head": test[:, 0],
                    "relation": test[:, 1],
                    "tail": test[:, 2],
                    "rank": ranks[side],
                    "candidates": candidates[side],
                }
            )
            for side in ("head", "tail")
        ],
        ignore_index=True,
    )


def build_predictions(base: Path, rows: list[dict]) -> pd.DataFrame:
    """
    Recompute the per-prediction ranks of every run in the table (`rows`
    from `build_report`, their datasets relative to `base`), keeping only the
    runs whose ranks reproduce the MRR in their results.json.
    """
    frames = []
    for row in rows:
        dataset, model = row["dataset"], row["model"]
        ranks = rank_predictions(base / dataset, base / dataset / model)
        if ranks is None:
            continue
        mrr = (1 / ranks["rank"]).mean()
        if abs(mrr - row["mrr"]) > MRR_TOLERANCE:
            print(
                f"warning: no prediction analysis for {dataset}/{model} (MRR {mrr:.4f} from its "
                f"saved model and splits vs {row['mrr']:.4f} in results.json, so the splits on "
                "disk were written by another run)"
            )
            continue
        print(f"Ranked {len(ranks)} test predictions of {dataset}/{model}")
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


def add_hits_from_ranks(rows: list[dict], predictions: pd.DataFrame) -> None:
    """
    Fill Hits@k for every k of REPORT_HITS_AT into the table's `rows`, from
    the recomputed ranks of each run (head and tail pooled, like the "both"
    slice). A run that couldn't be ranked keeps the k that results.json
    holds, and the other columns stay empty.
    """
    hits = hits_by(predictions, ["dataset", "model"])
    for row in rows:
        key = (row["dataset"], row["model"])
        if key in hits.index:
            row.update(hits.loc[key].to_dict())


def relation_report(predictions: pd.DataFrame) -> pd.DataFrame:
    """The table's metrics per relation, head and tail predictions pooled like the "both" slice."""
    by = ["dataset", "model", "relation"]
    grouped = predictions.groupby(by)
    report = hits_by(predictions, by)
    report["mrr"] = grouped["reciprocal_rank"].mean()
    report["count"] = grouped.size()
    return report.reset_index()


def query_table(predictions: pd.DataFrame) -> pd.DataFrame:
    """
    Group predictions by query, one row per (dataset, model, side, relation,
    entity), where `entity` is the query's known entity: the head of a tail
    query (h, r, ?) and the tail of a head query (?, r, t). A query has one
    prediction per held-out answer; `answers` counts them, and `mrr` and
    `hits_at_k` (the share of answers within the top k) average over them.
    """
    entity = predictions["head"].where(
        predictions["side"] == "tail", predictions["tail"]
    )
    grouped = predictions.assign(entity=entity).groupby(
        ["dataset", "model", "side", "relation", "entity"]
    )
    queries = grouped[["reciprocal_rank", *COMPARISON_HITS_COLUMNS]].mean()
    queries = queries.rename(columns={"reciprocal_rank": "mrr"})
    queries.insert(0, "answers", grouped.size())
    return queries.reset_index()


def split_dataset(table: pd.DataFrame) -> pd.DataFrame:
    """
    Add the `kg` and `graph` columns: the dataset's parent folder and its own
    name, e.g. `french_royalty` and `skgg.std=1.filled`.
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


def is_compared(graph: str, reference: str, patterns: list[str]) -> bool:
    """Whether `graph` is compared with the reference graph: it matches a pattern."""
    return graph != reference and any(fnmatchcase(graph, p) for p in patterns)


def graph_order(graphs, reference: str, patterns: list[str]) -> list[str]:
    """
    Sort a KG's graphs for the plots: the reference first, then the graphs
    compared with it, then the rest, so the colors follow the graphs.
    """
    return sorted(
        set(graphs),
        key=lambda g: (g != reference, not is_compared(g, reference, patterns), g),
    )


def compare_queries(
    queries: pd.DataFrame, reference: str, patterns: list[str]
) -> pd.DataFrame:
    """
    Pair the queries of `<kg>/<reference>` with those of every sibling
    dataset `<kg>/<compared>` whose name matches one of `patterns`, for the
    models trained on both. A query pairs when it is held out in both
    graphs; each graph keeps its own answers and values, in the
    `reference_*` and `compared_*` columns.
    """
    queries = split_dataset(queries)
    keys = ["kg", "model", "side", "relation", "entity"]
    values = ["answers", "mrr", *COMPARISON_HITS_COLUMNS]
    compared_rows = queries["graph"].map(
        lambda graph: is_compared(graph, reference, patterns)
    )
    ref = (
        queries[queries["graph"] == reference]
        .set_index(keys)[values]
        .add_prefix("reference_")
    )
    cmp = queries[compared_rows].set_index(keys)[["graph", *values]]
    cmp = cmp.rename(
        columns={"graph": "compared", **{v: f"compared_{v}" for v in values}}
    )
    paired = ref.join(cmp, how="inner").reset_index()
    paired.insert(1, "reference", reference)
    paired.insert(2, "compared", paired.pop("compared"))
    paired.insert(
        len(keys) + 2,
        "query",
        np.where(
            paired["side"] == "tail",
            "(" + paired["entity"] + ", " + paired["relation"] + ", ?)",
            "(?, " + paired["relation"] + ", " + paired["entity"] + ")",
        ),
    )
    return paired.sort_values(
        ["kg", "compared", "model", "relation", "side", "entity"], ignore_index=True
    )


def spearman(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Spearman's rho and its p-value; NaN under 3 pairs or when a side is constant."""
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return float("nan"), float("nan")
    result = spearmanr(x, y)
    return float(result.statistic), float(result.pvalue)


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def agreement(paired: pd.DataFrame, k: int) -> dict:
    """
    Cross-tabulate the paired queries by whether they are accurate at k in
    each graph, and how often the compared graph is accurate after the
    reference graph is (or isn't).
    """
    ref = paired[f"reference_hits_at_{k}"] >= ACCURATE_SHARE
    cmp = paired[f"compared_hits_at_{k}"] >= ACCURATE_SHARE
    both, only_ref = int((ref & cmp).sum()), int((ref & ~cmp).sum())
    only_cmp, neither = int((~ref & cmp).sum()), int((~ref & ~cmp).sum())
    # Cohen's kappa: the observed agreement corrected for the agreement
    # expected by chance from how often each graph is accurate.
    observed = (both + neither) / len(paired)
    expected = ref.mean() * cmp.mean() + (1 - ref.mean()) * (1 - cmp.mean())
    return {
        "both_accurate": both,
        "only_reference": only_ref,
        "only_compared": only_cmp,
        "neither": neither,
        "compared_accurate_when_reference_accurate": ratio(both, both + only_ref),
        "compared_accurate_when_reference_inaccurate": ratio(
            only_cmp, only_cmp + neither
        ),
        "cohen_kappa": ratio(observed - expected, 1 - expected),
    }


def summarize_comparison(paired: pd.DataFrame) -> pd.DataFrame:
    """
    Compare each pair of graphs per KG and model, over all paired queries,
    head and tail queries apart, and each relation (the strata), with one
    row per k: the per-query MRR of each graph, their rank correlation, and
    the agreement on which queries are accurate at k.
    """
    rows = []
    for (kg, reference, compared, model), group in paired.groupby(
        ["kg", "reference", "compared", "model"]
    ):
        strata = [("all", "both", group)]
        strata += [("all", side, g) for side, g in group.groupby("side")]
        strata += [(relation, "both", g) for relation, g in group.groupby("relation")]
        for relation, side, g in strata:
            rho, p = spearman(g["reference_mrr"], g["compared_mrr"])
            for k in COMPARISON_HITS_AT:
                rows.append(
                    {
                        "kg": kg,
                        "reference": reference,
                        "compared": compared,
                        "model": model,
                        "relation": relation,
                        "side": side,
                        "k": k,
                        "paired_queries": len(g),
                        "reference_mrr": g["reference_mrr"].mean(),
                        "compared_mrr": g["compared_mrr"].mean(),
                        "spearman_rho": rho,
                        "spearman_p": p,
                        **agreement(g, k),
                    }
                )
    return pd.DataFrame(rows)


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
    Compare each relation's test predictions in the reference graph with
    those in every other graph of the same KG (`predictions` with the `kg`
    and `graph` columns of `split_dataset`), for each model trained on both,
    and all their test predictions as one more relation, ALL_RELATIONS.
    Unlike `compare_queries`, this needs no shared entities, so PyGraft is
    compared too, and it uses every test prediction.

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
        low, high = bootstrap_difference(x["reciprocal_rank"], y["reciprocal_rank"], rng)
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


def rank_buckets(ranks: pd.Series) -> pd.Series:
    """Label each rank with its plot bucket ("1", "2–3", ..., ">100")."""
    return pd.cut(ranks, bins=BUCKET_EDGES, labels=BUCKET_LABELS)


def ink_on(fill) -> str:
    """Ink or white, whichever has the higher WCAG contrast against `fill`."""

    def luminance(color) -> float:
        c = np.array(to_rgb(color))
        c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        return float(c @ [0.2126, 0.7152, 0.0722])

    fill_luminance = luminance(fill)
    on_white = 1.05 / (fill_luminance + 0.05)
    on_ink = (fill_luminance + 0.05) / (luminance(INK) + 0.05)
    return "#ffffff" if on_white > on_ink else INK


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


def fmt(value: float, spec: str) -> str:
    return "n/a" if pd.isna(value) else format(value, spec)


def plot_comparison(runs: pd.DataFrame, paired: pd.DataFrame, path: Path) -> None:
    """
    Plot one comparison of two graphs with one model (`paired` holds its
    `compare_queries` rows only, `runs` the KG's predictions with the `graph`
    column of `split_dataset`). Left: how the ranks of all test predictions
    of each graph spread over the rank buckets. Right: for the
    paired queries, where the queries of each reference bucket land in the
    compared graph, as a share of the row and a count. A query is bucketed
    by 1 / its MRR, its rank when it has a single held-out answer.
    """
    kg, reference, compared, model = paired[
        ["kg", "reference", "compared", "model"]
    ].iloc[0]
    # Heights in inches: the caption band, the panel titles and legend, the
    # panels, the bottom axis.
    header, panel, footer = CAPTION_HEIGHT + 0.8, 3.36, 0.67
    height = header + panel + footer
    fig, (left, right) = plt.subplots(
        1,
        2,
        figsize=(12, height),
        dpi=PLOT_DPI,
        gridspec_kw={"width_ratios": [1.2, 1]},
    )
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(
        left=0.07,
        right=0.97,
        top=1 - header / height,
        bottom=footer / height,
        wspace=0.28,
    )
    for ax in (left, right):
        style_axes(ax)

    # Headline: does accurate in the reference graph mean accurate in the other?
    k = HEADLINE_K
    rho, _ = spearman(paired["reference_mrr"], paired["compared_mrr"])
    rates = agreement(paired, k)
    when_accurate = rates["compared_accurate_when_reference_accurate"]
    when_inaccurate = rates["compared_accurate_when_reference_inaccurate"]
    ref_name, cmp_name = folder_name(reference), folder_name(compared)
    draw_caption(
        fig,
        height,
        0.07,
        f"{kg_name(kg, model)}: {ref_name} vs {cmp_name}",
        f"Agreement between the link predictions of {model} on the two graphs. Left: "
        "filtered rank of the correct entity over all test predictions of each graph, in "
        f"rank buckets. Right: for the {len(paired):,} queries (h, r, ?) or (?, r, t) held "
        f"out in both graphs, the rank bucket in {cmp_name} of the queries in each rank "
        f"bucket of {ref_name}; a query with several held-out answers is bucketed by its "
        "inverse MRR. The per-query MRRs of the two graphs have a Spearman correlation of "
        f"ρ\u00a0=\u00a0{fmt(rho, '.2f')}. At Hits@{k}, {cmp_name} is accurate on "
        f"{fmt(when_accurate, '.0%')} of the queries on which {ref_name} is accurate and on "
        f"{fmt(when_inaccurate, '.0%')} of the others, a query being accurate when at least "
        f"{ACCURATE_SHARE:.0%} of its answers rank in the top {k}.",
    )

    # Left: rank distribution of every test prediction, one column per graph and bucket.
    shares, totals = {}, {}
    for graph in (reference, compared):
        run = (runs["graph"] == graph) & (runs["model"] == model)
        totals[graph] = int(run.sum())
        shares[graph] = rank_buckets(runs.loc[run, "rank"]).value_counts(
            normalize=True, sort=False
        )
    draw_grouped_columns(
        left,
        BUCKET_LABELS,
        [
            (shares[graph].reindex(BUCKET_LABELS).to_numpy(dtype=float), color)
            for graph, color in zip((reference, compared), SERIES_COLORS)
        ],
        top=max(s.max() for s in shares.values()) * 1.12,
    )
    left.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    left.set_xlabel("Rank of the true answer", color=INK_SECONDARY, fontsize=9.5)
    left.set_ylabel("Share of test predictions", color=INK_SECONDARY, fontsize=9.5)
    left.set_title(
        "All test predictions",
        loc="left",
        color=INK,
        fontsize=10.5,
        fontweight="bold",
        pad=32,
    )
    left.legend(
        handles=[
            Patch(
                facecolor=color,
                label=f"{folder_name(graph)} ({totals[graph]:,} predictions)",
            )
            for graph, color in zip((reference, compared), SERIES_COLORS)
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.03),
        ncols=2,
        frameon=False,
        fontsize=9,
        labelcolor=INK_SECONDARY,
        handlelength=0.9,
        handleheight=0.9,
        borderaxespad=0.1,
        borderpad=0,
    )

    # Right: rank transitions of the paired queries, one row per reference bucket.
    counts = pd.crosstab(
        rank_buckets(1 / paired["reference_mrr"]),
        rank_buckets(1 / paired["compared_mrr"]),
        dropna=False,
    ).reindex(index=BUCKET_LABELS, columns=BUCKET_LABELS, fill_value=0)
    row_shares = counts.div(counts.sum(axis=1), axis=0)
    right.pcolormesh(
        np.ma.masked_invalid(row_shares.to_numpy(dtype=float)),
        cmap=SEQUENTIAL,
        vmin=0,
        vmax=1,
        edgecolors=SURFACE,
        linewidth=BAR_GAP_PX * PX,
    )
    right.set_aspect("equal")
    right.invert_yaxis()
    for i in range(len(BUCKET_LABELS)):
        for j in range(len(BUCKET_LABELS)):
            share = row_shares.iat[i, j]
            if np.isnan(share):
                continue
            color = ink_on(SEQUENTIAL(share))
            right.text(
                j + 0.5,
                i + 0.47,
                f"{share:.0%}",
                ha="center",
                va="bottom",
                color=color,
                fontsize=10,
                fontweight="bold",
            )
            right.text(
                j + 0.5,
                i + 0.55,
                f"n={counts.iat[i, j]:,}",
                ha="center",
                va="top",
                color=color,
                fontsize=8,
            )
    ticks = np.arange(len(BUCKET_LABELS)) + 0.5
    right.set_xticks(ticks, BUCKET_LABELS)
    right.set_yticks(ticks, BUCKET_LABELS)
    right.set_xlabel(
        f"Rank in {folder_name(compared)}", color=INK_SECONDARY, fontsize=9.5
    )
    right.set_ylabel(
        f"Rank in {folder_name(reference)}", color=INK_SECONDARY, fontsize=9.5
    )
    right.set_title(
        "Paired queries, by rank in each graph",
        loc="left",
        color=INK,
        fontsize=10.5,
        fontweight="bold",
        pad=32,
    )
    right.text(
        0,
        1.035,
        "Each cell: share of its row, and number of queries",
        transform=right.transAxes,
        color=INK_SECONDARY,
        fontsize=9,
    )

    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
    plt.close(fig)


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
        "of the correct entity over the test predictions of the relation (head and tail "
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

    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
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
    The number of test predictions of every graph in one relation's `runs`,
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


def note_columns(fig, notes: list[tuple[str, tuple, bool]], start: float) -> list[float]:
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
    of test predictions and its Cliff's delta against the reference from
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
        f"Cumulative distribution of the rank of the correct entity in the test predictions "
        f"of {model} on a logarithmic rank axis, over all relations (first panel) and per "
        f"relation, so that the height of a curve at k is its Hits@k. {HITS_DEFINITION} "
        "Curves that rise earlier indicate better link "
        "prediction. Under each panel title, n is the number of test predictions of each "
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

    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
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
    # The models of one graph share its test split, but graphs have their own.
    counts = grouped.size()
    low, high = int(counts.min()), int(counts.max())
    count = f"{low:,}" if low == high else f"{low:,} to {high:,}"
    ax.text(
        0,
        1.03,
        f"{count} test predictions per {by}",
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
        "entities rank around that k. Under each panel title, the number of test "
        "predictions behind its lines; the models of a graph share its test split.",
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
    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
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
        "number of test predictions behind its lines; lines resting on few predictions are "
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
    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
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
    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
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
        image.crop((0, band, image.width, image.height)).save(
            cropped, dpi=(PLOT_DPI, PLOT_DPI)
        )
    return cropped


def write_analysis(
    rows: list[dict],
    predictions: pd.DataFrame,
    losses: pd.DataFrame,
    base: Path,
    analysis_dir: Path | None,
    reference: str,
    patterns: list[str],
    crop: bool = False,
) -> None:
    """
    Write the per-prediction analysis (see the module docstring) of the
    `build_predictions` ranks, with the training losses of `build_losses`
    and the KG's rows of the metrics table (`rows`), one folder per KG, since the graphs of a KG are only compared with each
    other: `<base>/<kg>/prediction_analysis` (`base` from `dataset_base`), or
    `<analysis_dir>/<kg>` when
    `analysis_dir` is given. With `crop`, every plot also gets a copy without
    its caption band in that folder's CROPPED_FOLDER.
    """
    if predictions.empty:
        print("warning: no run could be ranked, so no prediction analysis was written")
        return

    runs = split_dataset(predictions)
    losses = split_dataset(losses)
    for kg, kg_runs in runs.groupby("kg"):
        out = analysis_dir / kg if analysis_dir else base / kg / ANALYSIS_FOLDER
        kg_rows = [
            row for row in rows if PurePosixPath(row["dataset"]).parent.as_posix() == kg
        ]
        write_csv(kg_rows, out / REPORT_NAME)
        print(f"Wrote {len(kg_rows)} row(s) to {out / REPORT_NAME}")
        write_kg_analysis(
            kg_runs,
            losses[losses["kg"] == kg],
            pd.DataFrame(kg_rows, columns=CSV_FIELDS),
            out,
            reference,
            patterns,
            crop,
        )


def write_kg_analysis(
    runs: pd.DataFrame,
    losses: pd.DataFrame,
    metrics: pd.DataFrame,
    out: Path,
    reference: str,
    patterns: list[str],
    crop: bool = False,
) -> None:
    """
    Write the analysis of one KG to `out`: its `runs` (the `build_predictions`
    rows of its graphs, with the `kg` and `graph` columns of `split_dataset`),
    the training `losses` of its graphs and its rows of the metrics table
    (`metrics`), as CSV files and plots, and with `crop` a copy of every plot
    without its caption band (`write_cropped`).
    """
    kg = runs["kg"].iat[0]
    predictions = runs[PREDICTION_COLUMNS]
    relation_comparison = compare_relations(runs, reference)
    rankings = ranking_agreement(split_dataset(metrics), reference, AGREEMENT_METRICS)
    paired = compare_queries(query_table(predictions), reference, patterns)
    report = relation_report(predictions)
    tables = {
        "predictions.csv": predictions,
        "relation_report.csv": report,
        "training_losses.csv": losses[["dataset", "model", "epoch", "loss"]],
    }
    if relation_comparison.empty:
        print(f"note: {kg} has no '{reference}' graph, so no relations were compared")
    else:
        tables["relation_comparison.csv"] = relation_comparison
    if rankings.empty:
        print(
            f"note: {kg} has no '{reference}' graph or no other graph, so no model rankings "
            "were compared"
        )
    else:
        tables["ranking_agreement.csv"] = rankings
    if paired.empty:
        print(
            f"note: no query of {kg} is held out in both '{reference}' and a graph matching "
            f"{' '.join(patterns)} with the same model, so no graphs were compared"
        )
    else:
        tables["query_comparison.csv"] = paired
        tables["query_comparison_summary.csv"] = summarize_comparison(paired)

    out.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(out / name, index=False)
        print(f"Wrote {len(table)} row(s) to {out / name}")

    # One graph order per KG, so that each graph keeps its color in every plot.
    graphs = graph_order(runs["graph"], reference, patterns)
    if len(graphs) > len(CATEGORICAL):
        print(
            f"warning: {kg} has {len(graphs)} graphs, the relation plots show "
            f"the first {len(CATEGORICAL)}"
        )
        graphs = graphs[: len(CATEGORICAL)]
    hits_range = f"hits_at_{min(HITS_AT)}-{max(HITS_AT)}"
    plots = []

    report = split_dataset(report)
    plots.append(out / "relations.png")
    plot_relations(report[report["graph"].isin(graphs)], graphs, plots[-1])
    shown = runs[runs["graph"].isin(graphs)]
    # The plots of one model go to its own folder, those of the whole KG to `out`.
    for model, model_runs in shown.groupby("model"):
        (out / model).mkdir(exist_ok=True)
        plots.append(out / model / f"{model}_hits_at_k.png")
        deltas = relation_comparison[relation_comparison["model"] == model]
        plot_hits_curves(model_runs, deltas, graphs, reference, plots[-1])
        plots.append(out / model / f"{model}_relations_{hits_range}.png")
        plot_relation_hits(model_runs, dict(zip(graphs, CATEGORICAL)), plots[-1])

    # One model order per KG, so that each model keeps its color in the Hits@k line plots.
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

    for (compared, model), group in paired.groupby(["compared", "model"]):
        (out / model).mkdir(exist_ok=True)
        plots.append(out / model / f"{compared}_{model}.png")
        plot_comparison(runs, group, plots[-1])
    for plot in plots:
        print(f"Wrote {plot}")
        if crop:
            print(f"Wrote {write_cropped(plot, out)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Collect Hits@k / MRR from pykeen results.json files into one CSV, "
        "and analyse every test prediction behind them.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Results directory to scan, expected to contain <dataset...>/<model>/results.json",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=f"Output CSV path; when unset, <root>/{REPORT_NAME}",
    )
    p.add_argument(
        "--analysis-dir",
        type=Path,
        default=None,
        help="Directory to write each KG's per-prediction analysis to, as "
        f"<analysis-dir>/<kg>/; when unset, <root>/<kg>/{ANALYSIS_FOLDER}/",
    )
    p.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE,
        help="Dataset folder whose predictions are compared with its siblings'",
    )
    p.add_argument(
        "--compare",
        nargs="+",
        default=DEFAULT_COMPARED,
        metavar="PATTERN",
        help="Sibling dataset folders compared with --reference query by query, as "
        "shell-style patterns (quote them): graphs that keep the reference's entity names",
    )
    p.add_argument(
        "--crop",
        action="store_true",
        help="Also write every plot without its caption band (title and explanation) to "
        f"a {CROPPED_FOLDER}/ subfolder of its KG's analysis folder, for a publication",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.root.exists():
        print(f"error: results directory not found: {args.root}")
        return 1

    base = dataset_base(args.root)
    rows = build_report(args.root, base)
    if not rows:
        print(f"error: no results.json files found under {args.root}")
        return 1

    # The ranks come first, since the table's Hits@k beyond results.json's come from them.
    predictions = build_predictions(base, rows)
    add_hits_from_ranks(rows, predictions)
    output = args.output or args.root / REPORT_NAME
    write_csv(rows, output)
    print(f"Wrote {len(rows)} row(s) to {output}")

    losses = build_losses(base, rows)
    write_analysis(
        rows,
        predictions,
        losses,
        base,
        args.analysis_dir,
        args.reference,
        args.compare,
        args.crop,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
