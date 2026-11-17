"""Writes the results tables of every graph version: an original graph and the
copies the generators made of it at one threshold.

Reads what kgc_benchmark/report_results.py writes: the summary
`<analysis>/metrics.csv` (Hits@1/3/5/10/15 and MRR of every run, from the ranks
of its held-out triples) and `<analysis>/<family>/ranking_agreement.csv`
(Spearman's rho between the model rankings of the original and of each copy, per
metric). Run report_results.py first, and again when more runs finish:

    python kgc_benchmark/report_results.py

A copy's variant is its generator and the threshold of the rules it was made from,
as `ours_std1` (rules with std confidence 1) or `kbgen_r25` (rule level r25).
PyGraft's copies (`pygraft`) are made without rules, so they are in the version of
every threshold. A graph version is a family at one threshold: the original, every
generator's copy at that threshold and PyGraft's copy, such as `french1_N` at
`r25`. The full copies `ours` and `kbgen`, made from different rules, were dropped
on 17 November 2026 and have no version.

For each graph version it writes the folder
`<analysis>/results_tables/<family>_<threshold>/` (--output-dir for another root)
with two tables, each as LaTeX (`.tex`), Markdown (`.md`) and CSV (`.csv`):

- `metrics`: Hits@1, 3, 5, 10, 15 and MRR of every model on the original and on
  each copy;
- `agreement`: Spearman's rho between the model rankings of the original and of
  each copy, for each metric, with its p-value and the number of models;

`ranking` (each model's rank by each metric, 1 = best and tied models sharing the
best rank, on the original and on KBgen's and NAME's copy, PyGraft left out; in
LaTeX a copy's rank is bold where it equals the model's rank on the original; also
drawn as `ranking_table.png`, those ranks tinted and bold), and `ranking.png`, a
bump chart of the model ranking on the original and on each copy: one panel per
metric, the versions in table order on the x axis, the rank (1 = best, ties sharing
the best rank) on the y axis, one line per model, with a fixed color and marker
shape per model in every chart (style of kgc_benchmark/plot_style.py).

For each original graph with copies at the rule thresholds (MSE_THRESHOLDS: std1,
r25, r50, r75, r100), it writes `<analysis>/results_tables/<family>/mse`, also as
LaTeX, Markdown and CSV: one row per threshold, with the mean squared error of each
metric between the original and kbgen's and our copy at that threshold, computed
metric by metric over the models trained on both; a threshold without copies yet
shows "-". Next to it, `<family>/agreement` has the same layout with Spearman's rho of
the model rankings of each metric, copy against original (the higher in bold, $^*$
for p < 0.005).

With `--summary NAME`, it also writes `<analysis>/results_tables/<NAME>_summary/`,
the comparison of KBgen's and NAME's copies with the original over every family
of `--family` and every rule threshold of MSE_THRESHOLDS where both have runs,
one row per family and threshold, PyGraft left out: `agreement` (Spearman's rho
of each metric, the higher of the two in bold, $^*$ for p < 0.005) and `mse` (the
mean squared error of each metric, the smaller in bold), each as LaTeX, Markdown
and CSV.

    python scripts/compare_results.py --family french french1_1 french1_N \
        frenchN_1 frenchN_M --summary french

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
    BASELINE,
    CATEGORICAL,
    GRIDLINE,
    INK,
    INK_MUTED,
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
    **{
        f"r{share}": f"made from about {share}\\% of the mined rules, those of highest "
        f"PCA confidence (level r{share})"
        for share in (25, 50, 75)
    },
    "r100": "made from every mined rule (level r100)",
}
# The tag of a copy made without a threshold: PyGraft's, which uses no rules and
# goes in the version of every threshold.
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
    if any(generator(c)[2] == FULL for c in copies):
        made += " (PyGraft's made without rules)"
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


def ranking_frame(metrics: pd.DataFrame, versions: list[str]) -> pd.DataFrame:
    """Each model's rank on each version by each metric (1 = best, tied models
    sharing the best of their ranks, as in `bump_chart`), from `metrics_frame`:
    one row per model, one column per metric and version."""
    frame = pd.DataFrame(index=metrics.index)
    for name, _ in METRICS:
        for v in versions:
            column = f"{label(v)} {name}"
            frame[column] = metrics[column].rank(ascending=False, method="min")
    return frame.astype("Int64")


def ranking_latex(
    ranks: pd.DataFrame, versions: list[str], caption: str, label_: str
) -> str:
    """The ranking table in results_table's style: one column group per metric,
    one column per version, a copy's rank bold where it equals the original's."""
    names = ["Original" if v == REFERENCE else generator(v)[1] for v in versions]
    n = len(versions)
    groups = [
        f"\\multicolumn{{{n}}}{{c{'||' if i < len(METRICS) - 1 else ''}}}{{{metric}}}"
        for i, (metric, _) in enumerate(METRICS)
    ]
    lines = [
        "% Generated by scripts/compare_results.py",
        "\\begin{table*}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        f"\\caption{{{caption}}}",
        f"\\label{{{label_}}}",
        "\\resizebox{\\textwidth}{!}{%",
        f"\\begin{{tabular}}{{l||{'||'.join(['c' * n] * len(METRICS))}}}",
        "\\hline",
        "\\multirow{2}{*}{Model} & " + "\n & ".join(groups) + " \\\\",
        " & " + " & ".join(names * len(METRICS)) + " \\\\",
        "\\hline\\hline",
    ]
    for model, row in ranks.iterrows():
        cells = [str(model)]
        for metric, _ in METRICS:
            original = row[f"{label(REFERENCE)} {metric}"]
            for v in versions:
                rank = row[f"{label(v)} {metric}"]
                if pd.isna(rank):
                    cells.append(results_table.MISSING)
                elif v != REFERENCE and not pd.isna(original) and rank == original:
                    cells.append(f"\\textbf{{{rank}}}")
                else:
                    cells.append(str(rank))
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\hline", "\\end{tabular}%", "}", "\\end{table*}"]
    return "\n".join(lines) + "\n"


def tint(color: str, share: float) -> str:
    """`color` mixed with SURFACE, `share` of it: a light fill of the same hue."""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(SURFACE[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(share * x + (1 - share) * y) for x, y in zip(a, b, strict=True)]
    return "#" + "".join(f"{v:02x}" for v in mixed)


def ranking_table_png(
    ranks: pd.DataFrame, versions: list[str], title: str, path: Path
) -> None:
    """Draws the ranking table (`ranking_frame`) as a PNG: one column group per
    metric, one column per version, one row per model. A copy's rank that equals
    the model's rank on the original is bold on a light tint, so the match does not
    rest on color alone; a run not ranked yet is "-"."""
    names = ["Original" if v == REFERENCE else generator(v)[1] for v in versions]
    models = list(ranks.index)
    n = len(versions)
    columns = 1 + n * len(METRICS)
    match_fill = tint(CATEGORICAL[0], 0.18)
    width = 1.5 + 0.55 * n * len(METRICS)
    height = 1.6 + 0.36 * (len(models) + 2)
    fig, ax = plt.subplots(figsize=(width, height), dpi=PLOT_DPI, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, columns + 1)
    ax.set_ylim(len(models) + 2, 0)
    ax.axis("off")

    def x_of(column: int) -> float:
        # The model column is twice as wide as a rank column.
        return 1.0 if column == 0 else column + 1.5

    for i, (metric, _) in enumerate(METRICS):
        first = 1 + i * n
        center = (x_of(first) + x_of(first + n - 1)) / 2
        ax.text(center, 0.5, metric, ha="center", va="center", color=INK, fontsize=10)
        for j, name in enumerate(names):
            ax.text(
                x_of(first + j),
                1.5,
                name,
                ha="center",
                va="center",
                color=INK_SECONDARY,
                fontsize=8,
            )
        if i:
            ax.plot(
                [x_of(first) - 0.5] * 2,
                [0.1, len(models) + 1.9],
                color=BASELINE,
                linewidth=2 * PX,
            )
    ax.text(0.2, 1.5, "Model", ha="left", va="center", color=INK_SECONDARY, fontsize=8)
    ax.plot([0.1, columns + 0.9], [2] * 2, color=BASELINE, linewidth=2 * PX)

    for r, model in enumerate(models):
        y = 2.5 + r
        if r:
            ax.plot([0.1, columns + 0.9], [y - 0.5] * 2, color=GRIDLINE, linewidth=PX)
        ax.text(0.2, y, model, ha="left", va="center", color=INK, fontsize=9)
        for i, (metric, _) in enumerate(METRICS):
            original = ranks.at[model, f"{label(REFERENCE)} {metric}"]
            for j, v in enumerate(versions):
                rank = ranks.at[model, f"{label(v)} {metric}"]
                x = x_of(1 + i * n + j)
                match = (
                    v != REFERENCE
                    and not pd.isna(rank)
                    and not pd.isna(original)
                    and rank == original
                )
                if match:
                    ax.add_patch(
                        plt.Rectangle(
                            (x - 0.42, y - 0.4),
                            0.84,
                            0.8,
                            facecolor=match_fill,
                            edgecolor="none",
                            zorder=0,
                        )
                    )
                ax.text(
                    x,
                    y,
                    "-" if pd.isna(rank) else str(rank),
                    ha="center",
                    va="center",
                    fontsize=9,
                    color=INK_MUTED if pd.isna(rank) else INK,
                    fontweight="bold" if match else "normal",
                )
    fig.suptitle(
        textwrap.fill(title, width=int(width * 11)),
        color=INK,
        fontsize=12,
        x=0.01,
        ha="left",
    )
    fig.text(
        0.01,
        0.02,
        "Rank 1 = best; tied models share the best rank. Bold on a tint: the copy "
        "ranks the model where the original does.",
        color=INK_SECONDARY,
        fontsize=8,
        ha="left",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1 - 0.06 * (title.count("\n") + 1)))
    fig.savefig(path, dpi=PLOT_DPI, facecolor=SURFACE)
    plt.close(fig)


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
            f"{caption} and the synthetic copy of each generator using different "
            "amounts of rules (std1: the rules with std confidence 1; r25 to r100: 25 "
            "to 100\\% of the mined rules, those of highest PCA confidence), computed "
            f"over the models trained on both (all {len(MODELS)} "
            "unless marked $^\\dagger$). The smallest error of each threshold and "
            "metric is in bold."
        ),
        label=f"tab:{family.replace('_', '-')}_mse",
    )
    header = f"% Generated by scripts/compare_results.py from {analysis}"
    return header + "\n" + table.split("\n", 1)[1]


def graph_agreement_latex(analysis: Path, family: str, caption: str) -> str:
    """The agreement table of one graph, one row per threshold, made by
    results_table: Spearman's rho of each metric for kbgen's and our copy."""
    results_table.GENERATORS = (
        ("Original", REFERENCE),
        *((name, key) for name, key in GENERATORS if key in MSE_GENERATORS),
    )
    results_table.AGREEMENT_METRICS = METRICS
    results_table.AGREEMENT_GRAPHS = ((family,),)
    keys = [key for name, key in results_table.GENERATORS if key != REFERENCE]
    rows = [
        [(t, family, [mse_variant(key, t) for key in keys]) for t in MSE_THRESHOLDS]
    ]
    table, _, _ = results_table.build_agreement_table(
        results_table.read_agreement(analysis),
        rows,
        header="Threshold",
        caption=(
            "Spearman's $\\rho$ between the ranking of the models on "
            f"{caption} and on the synthetic copy of each generator using different "
            "amounts of rules (std1: the rules with std confidence 1; r25 to r100: 25 "
            "to 100\\% of the mined rules, those of highest PCA confidence), for "
            f"metrics {results_table.metric_list(METRICS)}. The higher $\\rho$ of "
            "each threshold and metric is in bold, and $^*$ means a p-value of less "
            f"than {SIGNIFICANCE}."
        ),
        label=f"tab:{family.replace('_', '-')}_agreement",
    )
    header = f"% Generated by scripts/compare_results.py from {analysis}"
    return header + "\n" + table.split("\n", 1)[1]


# The caption text of the rule thresholds, for the tables of several thresholds.
THRESHOLD_LEGEND = (
    "std1: the rules with std confidence 1; r25 to r100: about 25 to 100\\% of the "
    "mined rules, those of highest PCA confidence; both generators were given the "
    "same rules"
)


def summary_rows(scores: pd.DataFrame, families: list[str]) -> list[tuple[str, str]]:
    """(family, threshold) of every family of `families` and threshold of
    MSE_THRESHOLDS where every generator of MSE_GENERATORS has runs."""
    variants = scores.groupby("family")["variant"].agg(set)
    return [
        (family, t)
        for family in families
        for t in MSE_THRESHOLDS
        if all(mse_variant(k, t) in variants.get(family, set()) for k in MSE_GENERATORS)
    ]


def summary_frames(
    analysis: Path, rows: list[tuple[str, str]]
) -> dict[str, pd.DataFrame]:
    """The agreement and MSE of every (family, threshold) of `rows`, one row per
    family, threshold, copy and metric."""
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    results_table.AGREEMENT_GRAPHS = (tuple(dict.fromkeys(f for f, _ in rows)),)
    agreement = results_table.read_agreement(analysis)
    rho, mse = [], []
    for family, t in rows:
        for key in MSE_GENERATORS:
            variant = mse_variant(key, t)
            for name, metric in METRICS:
                base = {"graph": family, "threshold": t, "copy": LABELS[key]}
                found = agreement.get((family, variant, metric))
                rho.append(
                    {
                        **base,
                        "metric": name,
                        "spearman_rho": results_table.rho_value(found)
                        if found
                        else None,
                        "spearman_p": float(found["spearman_p"])
                        if found and found["spearman_p"]
                        else None,
                        "models": int(found["models"]) if found else 0,
                    }
                )
                value = results_table.metric_mse(metrics, family, variant, metric)
                mse.append(
                    {
                        **base,
                        "metric": name,
                        "mse": value[0] if value else None,
                        "models": value[1] if value else 0,
                    }
                )
    return {"agreement": pd.DataFrame(rho), "mse": pd.DataFrame(mse)}


def summary_markdown(frame: pd.DataFrame, value: str, title: str) -> str:
    """A summary frame as a wide Markdown table, one row per graph and threshold,
    the closer copy of each metric in bold: the higher rho or the smaller MSE."""
    decimals = DECIMALS if value == "spearman_rho" else results_table.MSE_DECIMALS
    better = max if value == "spearman_rho" else min
    copies = [LABELS[k] for k in MSE_GENERATORS]
    table = []
    for (graph, t), group in frame.groupby(["graph", "threshold"], sort=False):
        row = {"graph": graph, "threshold": t}
        for name, _ in METRICS:
            cells = group[group["metric"] == name].set_index("copy")
            shown = {
                c: round(float(cells.at[c, value]), decimals)
                for c in copies
                if c in cells.index and pd.notna(cells.at[c, value])
            }
            best = better(shown.values()) if shown else None
            for c in copies:
                if c not in shown:
                    row[f"{c} {name}"] = ""
                    continue
                text = f"{shown[c]:.{decimals}f}"
                if value == "spearman_rho":
                    p = cells.at[c, "spearman_p"]
                    text += "*" if pd.notna(p) and p < SIGNIFICANCE else ""
                row[f"{c} {name}"] = f"**{text}**" if shown[c] == best else text
        table.append(row)
    return f"## {title}\n\n{pd.DataFrame(table).to_markdown(index=False)}\n"


def summary_latex(
    analysis: Path, name: str, rows: list[tuple[str, str]]
) -> dict[str, str]:
    """The agreement and MSE tables of the summary, made by results_table, one
    row group per family."""
    results_table.GENERATORS = (
        ("Original", REFERENCE),
        *((n, key) for n, key in GENERATORS if key in MSE_GENERATORS),
    )
    results_table.AGREEMENT_METRICS = METRICS
    keys = [key for _, key in results_table.GENERATORS if key != REFERENCE]
    grouped: dict[str, list[results_table.MseRow]] = {}
    for family, t in rows:
        grouped.setdefault(family, []).append(
            (
                f"{results_table.latex_name(family)} {t}",
                family,
                [mse_variant(k, t) for k in keys],
            )
        )
    table_rows = list(grouped.values())
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    results_table.AGREEMENT_GRAPHS = (tuple(grouped),)
    stem = name.replace("_", "-")
    names = results_table.metric_list(METRICS)
    agreement, _, _ = results_table.build_agreement_table(
        results_table.read_agreement(analysis),
        table_rows,
        header="Graph and level",
        caption=(
            "Spearman's $\\rho$ between the ranking of the models on each original "
            "graph and on KBgen's and NAME's copy at each rule level "
            f"({THRESHOLD_LEGEND}), for metrics {names}. The higher $\\rho$ of each "
            f"row and metric is in bold, and $^*$ means a p-value of less than "
            f"{SIGNIFICANCE}."
        ),
        label=f"tab:{stem}-summary-agreement",
    )
    mse, _, _ = results_table.build_mse_table(
        metrics,
        table_rows,
        header="Graph and level",
        caption=(
            f"Mean squared error of {names} between each original graph and KBgen's "
            f"and NAME's copy at each rule level ({THRESHOLD_LEGEND}), computed metric "
            f"by metric over the models trained on both (all {len(MODELS)} unless "
            "marked $^\\dagger$). The smaller error of each row and metric is in bold."
        ),
        label=f"tab:{stem}-summary-mse",
    )
    header = (
        f"% Generated by scripts/compare_results.py --summary {name} from {analysis}"
    )
    # Twelve columns of numbers need the width of the page.
    return {
        table_name: header
        + "\n"
        + table.split("\n", 1)[1]
        .replace("\\begin{table}[t]", "\\begin{table*}[t]")
        .replace("\\end{table}", "\\end{table*}")
        .replace("\\resizebox{\\columnwidth}", "\\resizebox{\\textwidth}")
        for table_name, table in (("agreement", agreement), ("mse", mse))
    }


def write_summary(
    analysis: Path,
    output: Path,
    name: str,
    scores: pd.DataFrame,
    families: list[str],
    latex: bool,
) -> tuple[list[Path], list[str]]:
    """Writes `<output>/<name>_summary/` (see the module docstring) and returns the
    files written and the tables to print."""
    rows = summary_rows(scores, families)
    if not rows:
        return [], []
    folder = output / f"{name}_summary"
    folder.mkdir(parents=True, exist_ok=True)
    frames = summary_frames(analysis, rows)
    tex = summary_latex(analysis, name, rows)
    values = {"agreement": "spearman_rho", "mse": "mse"}
    titles = {
        "agreement": f"{name}: Spearman's rho of KBgen's and our copies with the "
        "original, by graph and rule level",
        "mse": f"{name}: mean squared error of KBgen's and our copies with the "
        "original, by graph and rule level",
    }
    written, printed = [], []
    for table, frame in frames.items():
        frame.to_csv(folder / f"{table}.csv", index=False)
        text = summary_markdown(frame, values[table], titles[table])
        (folder / f"{table}.md").write_text(text, encoding="utf-8")
        (folder / f"{table}.tex").write_text(tex[table], encoding="utf-8")
        written += [folder / f"{table}.{ext}" for ext in ("csv", "md", "tex")]
        printed.append(tex[table] if latex else text)
    return written, printed


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
        "--summary",
        metavar="NAME",
        help="also write <output-dir>/NAME_summary/: KBgen's and NAME's copies "
        "against the original over every family and rule threshold",
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
        # PyGraft's copies, made without rules, join the version of every threshold.
        rule_free = groups.pop(FULL, [])
        for t, copies in groups.items():
            groups[t] = sorted(copies + rule_free, key=lambda v: generator(v)[0])
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
            # The ranking table leaves out PyGraft, made without rules.
            ranked = [REFERENCE, *(c for c in copies if generator(c)[2] != FULL)]
            frames["ranking"] = ranking_frame(frames["metrics"], ranked)
            titles["ranking"] = (
                f"{family}, {threshold}: rank of each model by metric (1 = best)"
            )
            latex["ranking"] = ranking_latex(
                frames["ranking"],
                ranked,
                f"Rank of each model by each metric (1 = best, tied models sharing "
                f"the best rank) on {caption} and on KBgen's and NAME's copies "
                f"{THRESHOLDS.get(threshold, threshold)}. "
                "A copy's rank is in bold where it equals the model's rank on the "
                "original graph.",
                f"tab:{family.replace('_', '-')}_{threshold}_ranking",
            )
            table_chart = folder / "ranking_table.png"
            ranking_table_png(
                frames["ranking"],
                ranked,
                f"{caption}, copies {made}: rank of each model by metric",
                table_chart,
            )
            written.append(table_chart)
            chart = folder / "ranking.png"
            bump_chart(
                frames["metrics"],
                [REFERENCE, *copies],
                f"{caption}, copies {made}: model ranking by metric",
                chart,
            )
            written.append(chart)
            for name, frame in frames.items():
                index = name in ("metrics", "ranking")
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

            # The same layout for the agreement: Spearman's rho of each metric.
            rho = summary_frames(
                args.analysis, [(family, t) for t in MSE_THRESHOLDS]
            )["agreement"]
            rho.drop(columns="graph").to_csv(folder / "agreement.csv", index=False)
            title = f"{family}: Spearman's rho with the original, by threshold"
            text = summary_markdown(rho, "spearman_rho", title)
            (folder / "agreement.md").write_text(text, encoding="utf-8")
            tex = graph_agreement_latex(args.analysis, family, caption)
            (folder / "agreement.tex").write_text(tex, encoding="utf-8")
            written += [folder / f"agreement.{ext}" for ext in ("csv", "md", "tex")]
            printed.append(tex if args.latex else text)

    if args.summary:
        files, tables = write_summary(
            args.analysis, output, args.summary, scores, families, args.latex
        )
        written += files
        printed += tables

    print("\n".join(printed), end="")
    print(f"\nWrote {len(written)} files under {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
