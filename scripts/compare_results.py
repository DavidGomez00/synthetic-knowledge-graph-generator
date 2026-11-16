"""Writes the results tables of every graph version: an original graph and the
copies the generators made of it at one threshold.

Reads what kgc_benchmark/report_results.py writes: the summary
`<analysis>/metrics.csv` (Hits@1/3/5/10/15 and MRR of every run, from the ranks
of its held-out triples) and `<analysis>/<family>/ranking_agreement.csv`
(Spearman's rho between the model rankings of the original and of each copy, per
metric). Run report_results.py first, and again when more runs finish:

    python kgc_benchmark/report_results.py

A copy's variant is its generator and the threshold of the rules it was made from,
as `ours_std1` (rules with std confidence 1) or `kbgen_r25` (rule level r25); the
full copies of the main benchmark (`ours`, `kbgen`, `pygraft`) have the threshold
`full`. A graph version is a family at one threshold: the original and every
generator's copy at that threshold, such as `french1_N` at `r25`.

For each graph version it writes the folder
`<analysis>/results_tables/<family>_<threshold>/` (--output-dir for another root)
with three tables, each as LaTeX (`.tex`), Markdown (`.md`) and CSV (`.csv`):

- `metrics`: Hits@1, 3, 5, 10, 15 and MRR of every model on the original and on
  each copy;
- `agreement`: Spearman's rho between the model rankings of the original and of
  each copy, for each metric, with its p-value and the number of models;
- `mse`: the mean squared error of each metric between the original and each
  copy, computed metric by metric over the models trained on both.

The LaTeX tables are built by scripts/results_table.py's own functions, in the
format of its tables: columns Original, then the copies under its generator names
(PyGraft, KBgen, NAME), the best value of each column or metric in bold, $^*$ for
p < 0.05 and $^\\dagger$ for an error over fewer than 7 models. The Markdown and
CSV tables are written from pandas DataFrames, the CSV unrounded. A run not ranked
yet is missing ("-" in LaTeX, empty in Markdown and CSV).

    python scripts/compare_results.py                  # every graph version
    python scripts/compare_results.py --family french1_N --thresholds r25
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import results_table

MODELS = results_table.MODELS
# Header and column of metrics.csv and ranking_agreement.csv of each metric.
METRICS = results_table.METRICS
REFERENCE = "original"
# results_table's generators as it defines them; latex_tables changes its copy.
GENERATORS = results_table.GENERATORS
# The Markdown and CSV name of each generator's copies.
LABELS = {"ours": "Ours", "kbgen": "kbgen", "pygraft": "PyGraft"}
# Caption text and order of each threshold. A threshold not listed here is
# named in the caption as it is and comes after these.
THRESHOLDS = {
    "std1": "made from the rules with std confidence 1",
    "full": "of the main benchmark (NAME from the rules with std confidence 1, KBgen "
    "from every mined rule, PyGraft from the relation characteristics)",
    **{
        f"r{share}": f"made from about {share}\\% of the mined rules, those of highest "
        f"PCA confidence (level r{share})"
        for share in (25, 50, 75)
    },
    "r100": "made from every mined rule (level r100)",
}
# The tag of a copy made without a threshold (`ours`, `kbgen`).
FULL = "full"
# Decimals shown in Markdown.
DECIMALS = 3
SIGNIFICANCE = results_table.SIGNIFICANCE


def generator(variant: str) -> tuple[int, str, str]:
    """A copy's place in results_table's generator order, its column name there
    (`ours` is NAME, `kbgen` KBgen) and its threshold (`r25` for `ours_r25`,
    FULL for `ours`)."""
    for i, (name, key) in enumerate(GENERATORS):
        if key != REFERENCE and (variant == key or variant.startswith(f"{key}_")):
            return i, name, variant.removeprefix(key).lstrip("_") or FULL
    return len(GENERATORS), variant, FULL


def label(variant: str) -> str:
    """The Markdown and CSV name of a variant: "Original", "Ours", "kbgen"."""
    if variant == REFERENCE:
        return "Original"
    base = variant.partition("_")[0]
    return LABELS.get(base, base)


def caption_names() -> dict[str, str]:
    """The caption name of each dataset folder that results_table names, as
    "French Royalty 1:N" for `french1_N`."""
    names = {key: caption for _, key, caption in results_table.FAMILIES}
    family_captions = {name: caption for name, _, caption in results_table.FAMILIES}
    for (family, variant), folder in results_table.DATASETS.items():
        caption = family_captions.get(family, family)
        names[folder] = (
            caption if variant == results_table.BASE else f"{caption} {variant}"
        )
    return names


def latex_tables(
    analysis: Path, family: str, threshold: str, copies: list[str], caption: str
) -> dict[str, str]:
    """The metrics, agreement and MSE tables of one graph version, made by
    results_table, by table name."""
    results_table.GENERATORS = (
        ("Original", REFERENCE),
        *((generator(c)[1], c) for c in copies),
    )
    results_table.AGREEMENT_GRAPHS = ((family,),)
    results_table.AGREEMENT_METRICS = METRICS
    made = THRESHOLDS.get(threshold, f"made at threshold {threshold}")
    stem = f"{family.replace('_', '-')}_{threshold}"
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    each = (
        f"on {caption} and on the synthetic copy of each generator {made}",
        f"between {caption} and the synthetic copy of each generator {made}",
    )
    tables = {
        "metrics": results_table.build_table(
            metrics,
            [(results_table.latex_name(family), family)],
            results_table.HEADER,
            f"Link prediction results on {caption} and its synthetic copies {made}, "
            "trained and tested on VANILLA's 80/20 split.",
            f"tab:{stem}_metrics",
        ),
        "agreement": results_table.build_agreement_table(
            results_table.read_agreement(analysis)
        )[0]
        .replace(
            "on each original graph and on the synthetic copy of each generator",
            each[0],
        )
        .replace("\\label{tab:ranking_agreement}", f"\\label{{tab:{stem}_agreement}}"),
        "mse": results_table.build_mse_table(metrics)[0]
        .replace(
            "between each original graph and the synthetic copy of each generator",
            each[1],
        )
        .replace("\\label{tab:metric_mse}", f"\\label{{tab:{stem}_mse}}"),
    }
    header = f"% Generated by scripts/compare_results.py from {analysis}"
    return {
        name: header + "\n" + table.split("\n", 1)[1] for name, table in tables.items()
    }


def metrics_frame(scores: pd.DataFrame, variants: list[str]) -> pd.DataFrame:
    """One row per model, one column per version and metric."""
    frame = pd.DataFrame(index=pd.Index([], name="model"))
    for variant in variants:
        runs = scores[scores["variant"] == variant].set_index("model")
        for name, key in METRICS:
            frame[f"{label(variant)} {name}"] = runs[key]
    models = [m for m in MODELS if m in frame.index]
    return frame.reindex(models)


def agreement_frame(agreement: pd.DataFrame, copies: list[str]) -> pd.DataFrame:
    """One row per copy and metric: Spearman's rho, its p-value, the models."""
    rows = agreement[agreement["compared"].isin(copies)]
    copy_order = {copy: i for i, copy in enumerate(copies)}
    metric_order = {key: i for i, (_, key) in enumerate(METRICS)}
    rows = rows.assign(
        copy_rank=rows["compared"].map(copy_order),
        metric_rank=rows["metric"].map(metric_order),
    ).sort_values(["copy_rank", "metric_rank"])
    return rows.assign(
        copy=rows["compared"].map(label),
        metric=rows["metric"].map({key: name for name, key in METRICS}),
    )[["copy", "metric", "spearman_rho", "spearman_p", "models"]].reset_index(drop=True)


def mse_frame(analysis: Path, family: str, copies: list[str]) -> pd.DataFrame:
    """One row per copy and metric: the MSE with the original and the models."""
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    rows = []
    for copy in copies:
        for name, key in METRICS:
            value = results_table.metric_mse(metrics, family, copy, key)
            rows.append(
                {
                    "copy": label(copy),
                    "metric": name,
                    "mse": value[0] if value else None,
                    "models": value[1] if value else 0,
                }
            )
    return pd.DataFrame(rows)


def markdown(frame: pd.DataFrame, title: str, index: bool = False) -> str:
    """A table as Markdown under a title, numbers rounded to DECIMALS."""
    shown = frame.copy()
    if "spearman_rho" in shown:
        stars = shown["spearman_p"].lt(SIGNIFICANCE).map({True: "*", False: ""})
        shown["spearman_rho"] = shown["spearman_rho"].round(DECIMALS).map(
            lambda v: "" if pd.isna(v) else f"{v:.{DECIMALS}f}"
        ) + stars.where(shown["spearman_rho"].notna(), "")
    # tabulate takes one number format per column: the MSE keeps more decimals.
    formats = [f".{DECIMALS}f"] * (len(shown.columns) + index)
    if "mse" in shown:
        formats[list(shown.columns).index("mse") + index] = (
            f".{results_table.MSE_DECIMALS}f"
        )
    table = shown.to_markdown(index=index, floatfmt=formats, missingval="")
    return f"## {title}\n\n{table}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--analysis",
        type=Path,
        default=Path("exp_analysis"),
        help="report_results.py's --output (default: %(default)s)",
    )
    parser.add_argument(
        "--family", nargs="+", help="families to write (default: every one ranked)"
    )
    parser.add_argument(
        "--thresholds",
        nargs="+",
        help="thresholds to write tables for (default: every one in metrics.csv)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="root of the table folders (default: <analysis>/results_tables)",
    )
    parser.add_argument(
        "--latex",
        action="store_true",
        help="print the LaTeX tables instead of the Markdown ones",
    )
    args = parser.parse_args()

    summary = args.analysis / "metrics.csv"
    if not summary.is_file():
        parser.error(f"No {summary}: run kgc_benchmark/report_results.py first.")
    scores = pd.read_csv(summary)
    families = args.family or sorted(scores["family"].unique())
    missing = sorted(set(families) - set(scores["family"]))
    if missing:
        parser.error(f"No runs of {', '.join(missing)} in {summary}.")
    output = args.output_dir or args.analysis / "results_tables"
    captions = caption_names()
    order = list(THRESHOLDS)
    written, printed = [], []

    for family in families:
        runs = scores[scores["family"] == family]
        path = args.analysis / family / "ranking_agreement.csv"
        agreement = (
            pd.read_csv(path)
            if path.is_file()
            else pd.DataFrame(
                columns=["compared", "metric", "spearman_rho", "spearman_p", "models"]
            )
        )
        groups: dict[str, list[str]] = {}
        for variant in sorted(runs["variant"].unique(), key=lambda v: generator(v)[0]):
            if variant != REFERENCE:
                groups.setdefault(generator(variant)[2], []).append(variant)
        thresholds = [t for t in (args.thresholds or groups) if t in groups]
        thresholds.sort(key=lambda t: (order.index(t) if t in order else len(order), t))
        caption = captions.get(family, results_table.latex_name(family))
        for threshold in thresholds:
            copies = groups[threshold]
            folder = output / f"{family}_{threshold}"
            folder.mkdir(parents=True, exist_ok=True)
            made = THRESHOLDS.get(threshold, threshold).replace("\\", "")
            frames = {
                "metrics": metrics_frame(runs, [REFERENCE, *copies]),
                "agreement": agreement_frame(agreement, copies),
                "mse": mse_frame(args.analysis, family, copies),
            }
            titles = {
                "metrics": f"{family}, {threshold}: Hits@k and MRR (copies {made})",
                "agreement": f"{family}, {threshold}: Spearman's rho with the original",
                "mse": f"{family}, {threshold}: mean squared error with the original",
            }
            latex = latex_tables(args.analysis, family, threshold, copies, caption)
            for name, frame in frames.items():
                index = name == "metrics"
                frame.to_csv(folder / f"{name}.csv", index=index)
                (folder / f"{name}.md").write_text(
                    markdown(frame, titles[name], index), encoding="utf-8"
                )
                (folder / f"{name}.tex").write_text(latex[name], encoding="utf-8")
                written += [folder / f"{name}.{ext}" for ext in ("csv", "md", "tex")]
                printed.append(
                    latex[name] if args.latex else markdown(frame, titles[name], index)
                )

    print("\n".join(printed), end="")
    print(f"\nWrote {len(written)} files under {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
