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
with two tables, each as LaTeX (`.tex`), Markdown (`.md`) and CSV (`.csv`):

- `metrics`: Hits@1, 3, 5, 10, 15 and MRR of every model on the original and on
  each copy;
- `agreement`: Spearman's rho between the model rankings of the original and of
  each copy, for each metric, with its p-value and the number of models;

and `ranking.png`, a bump chart of the model ranking on the original and on each
copy: one panel per metric, the versions in table order on the x axis, the rank
(1 = best, ties sharing the best rank) on the y axis, one line per model, with a
fixed color and marker shape per model in every chart (style of
kgc_benchmark/plot_style.py).

For each original graph with copies at the rule thresholds (MSE_THRESHOLDS: std1,
r25, r50, r75, r100), it writes `<analysis>/results_tables/<family>/mse`, also as
LaTeX, Markdown and CSV: one row per threshold, with the mean squared error of each
metric between the original and kbgen's and our copy at that threshold, computed
metric by metric over the models trained on both; a threshold without copies yet
shows "-".

The LaTeX tables are built by scripts/results_table.py's own functions, in the
format of its tables: columns Original, then the copies under its generator names
(PyGraft, KBgen, NAME), the best value of each column or metric in bold, $^*$ for
p < 0.005 and $^\\dagger$ for an error over fewer than 7 models. The Markdown and
CSV tables are written from pandas DataFrames, the CSV unrounded. A run not ranked
yet is missing ("-" in LaTeX, empty in Markdown and CSV).

    python scripts/compare_results.py                  # every graph version
    python scripts/compare_results.py --family french1_N --thresholds r25
"""

import argparse
import sys
import textwrap
from pathlib import Path

import matplotlib
import pandas as pd
import results_table

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

# The plot style shared with kgc_benchmark/report_results.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kgc_benchmark"))
from plot_style import (  # noqa: E402
    CATEGORICAL,
    GRIDLINE,
    INK,
    INK_SECONDARY,
    PLOT_DPI,
    PX,
    SURFACE,
)

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
# The rows of each graph's MSE table, in order.
MSE_THRESHOLDS = ("std1", "r25", "r50", "r75", "r100")
# The generators of the MSE table's columns, in results_table's order.
MSE_GENERATORS = ("kbgen", "ours")
# A marker shape per model of MODELS, beside its color: in a bump chart every pair
# of lines can cross, and past three series colors alone don't tell them apart.
MARKERS = ("o", "s", "D", "^", "v", "P", "X")
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
    """The metrics and agreement tables of one graph version, made by
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
    tables = {
        "metrics": results_table.build_table(
            metrics,
            [(results_table.latex_name(family), family)],
            results_table.HEADER,
            f"Link prediction results on {caption} and its synthetic copies {made}.",
            f"tab:{stem}_metrics",
            bold=False,
        ),
        "agreement": results_table.build_agreement_table(
            results_table.read_agreement(analysis)
        )[0]
        .replace(
            "on each original graph and on the synthetic copy of each generator",
            f"on {caption} and on the synthetic copy of each generator {made}",
        )
        .replace("\\label{tab:ranking_agreement}", f"\\label{{tab:{stem}_agreement}}"),
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


def mse_variant(generator_key: str, threshold: str) -> str:
    """The variant of a generator's copy at a threshold: `ours_r25`."""
    return f"{generator_key}_{threshold}"


def graph_mse_frame(analysis: Path, family: str) -> pd.DataFrame:
    """One row per threshold, copy and metric: the MSE with the original and the
    number of models trained on both (none for a copy not ranked yet)."""
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    rows = []
    for threshold in MSE_THRESHOLDS:
        for key in MSE_GENERATORS:
            for name, metric in METRICS:
                value = results_table.metric_mse(
                    metrics, family, mse_variant(key, threshold), metric
                )
                rows.append(
                    {
                        "threshold": threshold,
                        "copy": LABELS[key],
                        "metric": name,
                        "mse": value[0] if value else None,
                        "models": value[1] if value else 0,
                    }
                )
    return pd.DataFrame(rows)


def graph_mse_latex(analysis: Path, family: str, caption: str) -> str:
    """The MSE table of one graph, one row per threshold, made by results_table."""
    results_table.GENERATORS = (
        ("Original", REFERENCE),
        *((name, key) for name, key in GENERATORS if key in MSE_GENERATORS),
    )
    results_table.AGREEMENT_METRICS = METRICS
    keys = [key for name, key in results_table.GENERATORS if key != REFERENCE]
    rows = [
        [(t, family, [mse_variant(key, t) for key in keys]) for t in MSE_THRESHOLDS]
    ]
    table, _, _ = results_table.build_mse_table(
        results_table.read_metrics(analysis / "metrics.csv"),
        rows,
        header="Threshold",
        caption=(
            f"Mean squared error of {results_table.metric_list(METRICS)} between "
            f"{caption} and the synthetic copy of each generator at each rule "
            "threshold (std1: the rules with std confidence 1; r25 to r100: about 25 "
            "to 100\\% of the mined rules, those of highest PCA confidence), computed "
            f"metric by metric over the models trained on both (all {len(MODELS)} "
            "unless marked $^\\dagger$). The smallest error of each threshold and "
            "metric is in bold."
        ),
        label=f"tab:{family.replace('_', '-')}_mse",
    )
    header = f"% Generated by scripts/compare_results.py from {analysis}"
    return header + "\n" + table.split("\n", 1)[1]


def bump_chart(
    frame: pd.DataFrame, versions: list[str], title: str, path: Path
) -> None:
    """Draws how the models rank on the original graph and on each copy: one
    panel per metric, the versions on the x axis in table order and the rank on the
    y axis, 1 (best) at the top, one line per model. The ranks come from pandas'
    `rank` of each version's column of `frame` (`metrics_frame`), tied models
    sharing the best of their ranks; a run not ranked yet leaves a gap."""
    names = ["Original" if v == REFERENCE else generator(v)[1] for v in versions]
    models = [m for m in MODELS if m in frame.index]
    fig, axes = plt.subplots(
        2, 3, figsize=(13, 7.6), dpi=PLOT_DPI, sharey=True, facecolor=SURFACE
    )
    for ax, (metric, _) in zip(axes.flat, METRICS, strict=True):
        columns = [f"{label(v)} {metric}" for v in versions]
        ranks = frame[columns].rank(ascending=False, method="min")
        for model in models:
            index = MODELS.index(model)
            ax.plot(
                range(len(versions)),
                ranks.loc[model].to_numpy(dtype=float),
                color=CATEGORICAL[index],
                linewidth=2 * PX,
                solid_capstyle="round",
                solid_joinstyle="round",
                marker=MARKERS[index],
                markersize=14 * PX,
                markeredgecolor=SURFACE,
                markeredgewidth=2 * PX,
                label=model,
                zorder=3,
            )
        ax.set_facecolor(SURFACE)
        ax.set_title(metric, color=INK, fontsize=11, loc="left")
        ax.set_xticks(range(len(versions)), names)
        ax.set_xlim(-0.25, len(versions) - 0.75)
        ax.set_yticks(range(1, len(models) + 1))
        ax.set_ylim(len(models) + 0.5, 0.5)
        ax.grid(axis="y", color=GRIDLINE, linewidth=PX)
        ax.tick_params(colors=INK_SECONDARY, length=0, labelsize=9)
        for spine in ax.spines.values():
            spine.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel("Rank (1 = best)", color=INK_SECONDARY, fontsize=9)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(models),
        frameon=False,
        fontsize=9,
        labelcolor=INK,
        handlelength=2.5,
    )
    # A long threshold description wraps rather than running off the figure.
    title = textwrap.fill(title, width=120)
    fig.suptitle(title, color=INK, fontsize=12, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1 - 0.035 * (title.count("\n") + 1)))
    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
    plt.close(fig)


def markdown(
    frame: pd.DataFrame, title: str, index: bool = False, decimals: int = DECIMALS
) -> str:
    """A table as Markdown under a title, numbers rounded to `decimals`."""
    shown = frame.copy()
    if "spearman_rho" in shown:
        stars = shown["spearman_p"].lt(SIGNIFICANCE).map({True: "*", False: ""})
        shown["spearman_rho"] = shown["spearman_rho"].round(DECIMALS).map(
            lambda v: "" if pd.isna(v) else f"{v:.{DECIMALS}f}"
        ) + stars.where(shown["spearman_rho"].notna(), "")
    # tabulate takes one number format per column: the MSE keeps more decimals.
    formats = [f".{decimals}f"] * (len(shown.columns) + index)
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
            }
            titles = {
                "metrics": f"{family}, {threshold}: Hits@k and MRR (copies {made})",
                "agreement": f"{family}, {threshold}: Spearman's rho with the original",
            }
            latex = latex_tables(args.analysis, family, threshold, copies, caption)
            chart = folder / "ranking.png"
            bump_chart(
                frames["metrics"],
                [REFERENCE, *copies],
                f"{caption}, copies {made}: model ranking by metric",
                chart,
            )
            written.append(chart)
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

        if any(
            mse_variant(k, t) in set(runs["variant"])
            for k in MSE_GENERATORS
            for t in MSE_THRESHOLDS
        ):
            folder = output / family
            folder.mkdir(parents=True, exist_ok=True)
            frame = graph_mse_frame(args.analysis, family)
            frame.to_csv(folder / "mse.csv", index=False)
            wide = frame.pivot_table(
                index="threshold",
                columns=["copy", "metric"],
                values="mse",
                dropna=False,
                sort=False,
            )
            wide.columns = [f"{c} {m}" for c, m in wide.columns]
            wide = wide.reindex(list(MSE_THRESHOLDS))
            title = f"{family}: mean squared error with the original, by threshold"
            text = markdown(
                wide, title, index=True, decimals=results_table.MSE_DECIMALS
            )
            (folder / "mse.md").write_text(text, encoding="utf-8")
            tex = graph_mse_latex(args.analysis, family, caption)
            (folder / "mse.tex").write_text(tex, encoding="utf-8")
            written += [folder / f"mse.{ext}" for ext in ("csv", "md", "tex")]
            printed.append(tex if args.latex else text)

    print("\n".join(printed), end="")
    print(f"\nWrote {len(written)} files under {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
