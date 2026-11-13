"""Writes the LaTeX tables of link prediction results from `exp_analysis/metrics.csv`.

The datasets are the 1:N, N:1 and N:M variants of French Royalty, Family,
YAGO3-10 and WN18RR, and the base graph each was taken from (DATASETS), plus
SGKG1 to SGKG4. The script writes one table per dataset family, with a block of
rows for the base graph and each variant (for SGKG, one block per graph), and
one table per variant (1:N, N:1, N:M), with a
block of rows per family. Each table has one row per model of MODELS and
one group of columns (Hits@1/3/5/10/15 and MRR) for the original dataset and
each generator of GENERATORS. A run missing from `metrics.csv` (not trained or
not ranked yet, or a generator without copies of the dataset, such as PyGraft
on YAGO3-10) is shown as "-". In each block, the highest value of each metric
of each variant over the models is bold (all of them on a tie); a column whose
models all show the same value has no bold.

Each table goes to its own file in `--output-dir` (default
`exp_analysis/results_tables/`), named after its label: `family_french.tex`,
..., `variant_1N.tex`, ..., to be included with `\\input`. Run it again after
`report_results.py` to update the tables.

`ranking_agreement.tex` has one row per graph (every base graph, its 1:1, 1:N,
N:1 and N:M sub-graphs, and SGKG1-4) and, per generator, Kendall's tau between
the ranking of the models on the original graph and on the copy, by Hits@1 and
by MRR, read from the `ranking_agreement.csv` that `report_results.py` writes
in each graph's folder. `metric_distance.tex` has the same rows and columns,
with the mean absolute difference of Hits@1 and of MRR between the original
graph and the copy over the models trained on both, the smallest of each row
in bold, and `metric_distance_relative.tex` divides that difference by the
original's mean score over the same models (a copy on which every model scores
0 gets 1). In `ranking_agreement.tex`, the highest tau of each row is bold,
separately for Hits@1 and MRR, and ties are all bold. A tau with p < 0.05 is
marked with an asterisk, and an undefined tau (every model with the same
value, e.g. Hits@1 = 0) is "-". Only the Python standard library is used.

    python scripts/results_table.py
    python scripts/results_table.py --output-dir paper/tables
"""

import argparse
import csv
from pathlib import Path

# Name, file/label key and caption name of each dataset family, in table order.
FAMILIES = (
    ("French", "french", "French Royalty"),
    ("Family", "family", "Family"),
    ("YAGO", "yago", "YAGO3-10"),
    ("WN18RR", "wn18rr", "WN18RR"),
)
# Name and file/label key of each variant, in table order.
VARIANTS = (
    ("1:N", "1N"),
    ("N:1", "N1"),
    ("N:M", "NM"),
)
# Row label of the base graph of each family, the first block of its table.
BASE = "Base"
# Folder of each dataset, by family and variant name.
DATASETS = {
    ("French", BASE): "french",
    ("Family", BASE): "family",
    ("YAGO", BASE): "YAGO3-10",
    ("WN18RR", BASE): "WN18RR",
    ("French", "1:N"): "french1_N",
    ("French", "N:1"): "frenchN_1",
    ("French", "N:M"): "frenchN_M",
    ("Family", "1:N"): "Family1_N",
    ("Family", "N:1"): "FamilyN_1",
    ("Family", "N:M"): "FamilyM_N",
    ("YAGO", "1:N"): "YAGO3-10_1_N",
    ("YAGO", "N:1"): "YAGO3-10_N_1",
    ("YAGO", "N:M"): "YAGO3-10_N_M",
    ("WN18RR", "1:N"): "WN18RR1_N",
    ("WN18RR", "N:1"): "WN18RRN_1",
    ("WN18RR", "N:M"): "WN18RRN_M",
}
# The SGKG graphs, which have no sub-graphs and get a family table of their own.
SGKG_GRAPHS = ["SGKG1", "SGKG2", "SGKG3", "SGKG4"]
# The models trained by scripts/train_kge.py, in table order.
MODELS = ("TransE", "TransH", "TransD", "RotatE", "ComplEx", "TuckER", "CompGCN")
# Column header and variant in metrics.csv of the original dataset and of each
# generator; None for a generator with no results, whose columns are all "-".
GENERATORS = (
    ("Original", "original"),
    ("PyGraft", "pygraft"),
    ("KBgen", "kbgen"),
    ("NAME", "ours"),
)
METRICS = (
    ("Hits@1", "hits_at_1"),
    ("Hits@3", "hits_at_3"),
    ("Hits@5", "hits_at_5"),
    ("Hits@10", "hits_at_10"),
    ("Hits@15", "hits_at_15"),
    ("MRR", "mrr"),
)
# Header of the first column, whose blocks are labelled with the dataset folder.
HEADER = "Knowledge Graph"
DECIMALS = 3
MISSING = "-"

Metrics = dict[tuple[str, str, str], dict[str, str]]


def read_metrics(path: Path) -> Metrics:
    """The rows of metrics.csv by family (dataset folder), variant and model."""
    with path.open(newline="", encoding="utf-8") as f:
        return {(r["family"], r["variant"], r["model"]): r for r in csv.DictReader(f)}


def latex_name(dataset: str) -> str:
    """A dataset folder name as LaTeX text."""
    return dataset.replace("_", "\\_")


def best_values(metrics: Metrics, dataset: str) -> dict[tuple[str, str], float]:
    """The highest value, rounded as shown, of each metric of each variant of a
    dataset over the models of MODELS, keyed by (variant, metric). A column whose
    models all show the same value has no best and is left out."""
    best = {}
    for _, variant in GENERATORS:
        for _, key in METRICS:
            values = [
                round(float(row[key]), DECIMALS)
                for model in MODELS
                if variant and (row := metrics.get((dataset, variant, model)))
            ]
            if len(set(values)) > 1:
                best[variant, key] = max(values)
    return best


def cells(
    metrics: Metrics,
    dataset: str,
    model: str,
    best: dict[tuple[str, str], float],
) -> list[str]:
    """A model's row of values, the best of each column (`best_values`) in bold."""
    out = []
    for _, variant in GENERATORS:
        row = metrics.get((dataset, variant, model)) if variant else None
        for _, key in METRICS:
            if not row:
                out.append(MISSING)
                continue
            value = round(float(row[key]), DECIMALS)
            text = f"{value:.{DECIMALS}f}"
            if best.get((variant, key)) == value:
                text = f"\\textbf{{{text}}}"
            out.append(text)
    return out


def build_table(
    metrics: Metrics,
    blocks: list[tuple[str, str]],
    block_header: str,
    caption: str,
    label: str,
) -> str:
    """A table with one block of rows per (row label, dataset folder) of
    `blocks`, under the first column's header `block_header`."""
    n = len(METRICS)
    groups = [
        f"\\multicolumn{{{n}}}{{c{'||' if i < len(GENERATORS) - 1 else ''}}}{{{name}}}"
        for i, (name, _) in enumerate(GENERATORS)
    ]
    columns = "||".join(["c" * n] * len(GENERATORS))
    lines = [
        "% Generated by scripts/results_table.py from exp_analysis/metrics.csv",
        "\\begin{table*}[p]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        f"\\caption{{{caption} In each graph, the best model of each metric is "
        "in bold, for the original graph and each generator.}",
        f"\\label{{{label}}}",
        "\\resizebox{\\textwidth}{!}{%",
        f"\\begin{{tabular}}{{l|l||{columns}}}",
        "\\hline",
        f"\\multirow{{2}}{{*}}{{{block_header}}} & \\multirow{{2}}{{*}}{{Model}} & "
        + "\n & ".join(groups)
        + " \\\\",
        " & & "
        + " & ".join(label for _ in GENERATORS for label, _ in METRICS)
        + " \\\\",
        "\\hline\\hline",
    ]
    for block_label, dataset in blocks:
        best = best_values(metrics, dataset)
        for i, model in enumerate(MODELS):
            first = (
                f"\\multirow{{{len(MODELS)}}}{{*}}{{{block_label}}}" if i == 0 else ""
            )
            row = [first, model, *cells(metrics, dataset, model, best)]
            lines.append(" & ".join(row) + " \\\\")
        lines.append("\\hline")
    lines += ["\\end{tabular}%", "}", "\\end{table*}"]
    return "\n".join(lines) + "\n"


def tables(metrics: Metrics) -> list[tuple[str, str, list[str]]]:
    """Each table as (file stem, LaTeX source, dataset folders)."""
    out = []
    for family, key, caption_name in FAMILIES:
        blocks = [
            (latex_name(DATASETS[family, variant]), DATASETS[family, variant])
            for variant in (BASE, *(v for v, _ in VARIANTS))
        ]
        caption = (
            f"Link prediction results on {caption_name} and its 1:N, N:1 and N:M "
            "sub-graphs, for the original graphs and the synthetic copies of each "
            "generator."
        )
        stem = f"family_{key}"
        source = build_table(metrics, blocks, HEADER, caption, f"tab:{stem}")
        out.append((stem, source, [d for _, d in blocks]))
    blocks = [(latex_name(graph), graph) for graph in SGKG_GRAPHS]
    caption = (
        "Link prediction results on SGKG1 to SGKG4, for the original graphs and "
        "the synthetic copies of each generator."
    )
    source = build_table(metrics, blocks, HEADER, caption, "tab:family_sgkg")
    out.append(("family_sgkg", source, SGKG_GRAPHS))
    for variant, key in VARIANTS:
        blocks = [
            (latex_name(DATASETS[family, variant]), DATASETS[family, variant])
            for family, _, _ in FAMILIES
        ]
        caption = (
            f"Link prediction results on the {variant} sub-graphs of the four "
            "benchmarks, for the original graphs and the synthetic copies of each "
            "generator."
        )
        stem = f"variant_{key}"
        source = build_table(metrics, blocks, HEADER, caption, f"tab:{stem}")
        out.append((stem, source, [d for _, d in blocks]))
    return out


# Every graph of the agreement table, in order: per family the base graph and
# its 1:1, 1:N, N:1 and N:M sub-graphs, then the SGKG graphs.
AGREEMENT_GRAPHS = (
    ("french", "french1_1", "french1_N", "frenchN_1", "frenchN_M"),
    ("family", "Family1_1", "Family1_N", "FamilyN_1", "FamilyM_N"),
    ("YAGO3-10", "YAGO3-10_1_1", "YAGO3-10_1_N", "YAGO3-10_N_1", "YAGO3-10_N_M"),
    ("WN18RR", "WN18RR1_1", "WN18RR1_N", "WN18RRN_1", "WN18RRN_M"),
    tuple(SGKG_GRAPHS),
)
AGREEMENT_METRICS = (("Hits@1", "hits_at_1"), ("MRR", "mrr"))
# A tau with a p-value below this is marked with an asterisk.
SIGNIFICANCE = 0.05

Agreement = dict[tuple[str, str, str], dict[str, str]]


def read_agreement(analysis: Path) -> Agreement:
    """The rows of every `<graph>/ranking_agreement.csv` under `analysis`, by
    graph, compared variant and metric."""
    rows: Agreement = {}
    for graphs in AGREEMENT_GRAPHS:
        for graph in graphs:
            path = analysis / graph / "ranking_agreement.csv"
            if path.is_file():
                with path.open(newline="", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        rows[graph, r["compared"], r["metric"]] = r
    return rows


def tau_value(row: dict[str, str] | None) -> float | None:
    """A row's tau rounded as shown, or None when it is missing or undefined."""
    if not row or not row["kendall_tau"]:
        return None
    return round(float(row["kendall_tau"]), DECIMALS)


def tau_cell(row: dict[str, str] | None, best: float | None) -> str:
    """A tau as shown in the table: bold if it is the row's best for its
    metric, with an asterisk if p < SIGNIFICANCE."""
    value = tau_value(row)
    if value is None or row is None:
        return MISSING
    text = f"{value:.{DECIMALS}f}"
    if value == best:
        text = f"\\textbf{{{text}}}"
    if row["kendall_p"] and float(row["kendall_p"]) < SIGNIFICANCE:
        text += "$^*$"
    return text


def build_agreement_table(agreement: Agreement) -> tuple[str, int, int]:
    """The table of Kendall's tau between the model rankings of each graph
    and of each generator's copy, with the number of taus filled and in all."""
    copies = [(name, variant) for name, variant in GENERATORS if variant != "original"]
    n = len(AGREEMENT_METRICS)
    groups = [
        f"\\multicolumn{{{n}}}{{c{'||' if i < len(copies) - 1 else ''}}}{{{name}}}"
        for i, (name, _) in enumerate(copies)
    ]
    columns = "||".join(["c" * n] * len(copies))
    lines = [
        "% Generated by scripts/results_table.py from "
        "exp_analysis/*/ranking_agreement.csv",
        "\\begin{table}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        "\\caption{Kendall's $\\tau$ between the ranking of the models on each "
        "original graph and on the synthetic copy of each generator, by Hits@1 and "
        f"MRR. The highest $\\tau$ of each row is in bold, separately for Hits@1 and "
        f"MRR. $^*$: $p < {SIGNIFICANCE}$.}}",
        "\\label{tab:ranking_agreement}",
        "\\resizebox{\\columnwidth}{!}{%",
        f"\\begin{{tabular}}{{l||{columns}}}",
        "\\hline",
        f"\\multirow{{2}}{{*}}{{{HEADER}}} & " + "\n & ".join(groups) + " \\\\",
        " & "
        + " & ".join(label for _ in copies for label, _ in AGREEMENT_METRICS)
        + " \\\\",
        "\\hline\\hline",
    ]
    filled = total = 0
    for graphs in AGREEMENT_GRAPHS:
        for graph in graphs:
            best = {
                metric: max(
                    (
                        v
                        for _, variant in copies
                        if (v := tau_value(agreement.get((graph, variant, metric))))
                        is not None
                    ),
                    default=None,
                )
                for _, metric in AGREEMENT_METRICS
            }
            row = [latex_name(graph)]
            for _, variant in copies:
                for _, metric in AGREEMENT_METRICS:
                    cell = tau_cell(
                        agreement.get((graph, variant, metric)), best[metric]
                    )
                    row.append(cell)
                    total += 1
                    filled += cell != MISSING
            lines.append(" & ".join(row) + " \\\\")
        lines.append("\\hline")
    lines += ["\\end{tabular}%", "}", "\\end{table}"]
    return "\n".join(lines) + "\n", filled, total


def mean_distance(
    metrics: Metrics, graph: str, variant: str, key: str, relative: bool = False
) -> tuple[float, int] | None:
    """The mean absolute difference of a metric between the original graph and
    a variant's copy, over the models trained on both, and how many models that
    is. With `relative`, the difference is divided by the original's mean score
    over the same models, sum |copy - original| / sum original: a copy on which
    every model scores 0 gets 1. None when no model was trained on both, or,
    with `relative`, when every original score is 0."""
    pairs = [
        (float(original[key]), float(copy[key]))
        for model in MODELS
        if (original := metrics.get((graph, "original", model)))
        and (copy := metrics.get((graph, variant, model)))
    ]
    if not pairs:
        return None
    difference = sum(abs(c - o) for o, c in pairs)
    if relative:
        scale = sum(o for o, _ in pairs)
        return (difference / scale, len(pairs)) if scale else None
    return difference / len(pairs), len(pairs)


# Caption and label of the distance tables, absolute (False) and relative (True).
DISTANCE_CAPTIONS = {
    False: (
        "\\caption{Mean absolute difference of Hits@1 and MRR between each "
        "original graph and the synthetic copy of each generator, over the models "
        f"trained on both (all {len(MODELS)} unless marked $^\\dagger$). 0 means "
        "the copy gives every model the score it has on the original graph. The "
        "smallest difference of each row is in bold, separately for Hits@1 and "
        "MRR.}",
        "\\label{tab:metric_distance}",
    ),
    True: (
        "\\caption{Relative difference of Hits@1 and MRR between each original "
        "graph and the synthetic copy of each generator: the sum over the models "
        "trained on both (all "
        f"{len(MODELS)} unless marked $^\\dagger$) of $|\\text{{copy}} - "
        "\\text{original}|$, divided by the sum of the original scores. 0 means "
        "the copy gives every model the score it has on the original graph; a "
        "copy on which every model scores 0 gets 1. The smallest value of each "
        "row is in bold, separately for Hits@1 and MRR.}",
        "\\label{tab:metric_distance_relative}",
    ),
}


def build_distance_table(
    metrics: Metrics, relative: bool = False
) -> tuple[str, int, int]:
    """The table of the mean absolute difference of Hits@1 and MRR between each
    original graph and the copy of each generator (with `relative`, divided by
    the original's mean score, see `mean_distance`), with the number of cells
    filled and in all."""
    copies = [(name, variant) for name, variant in GENERATORS if variant != "original"]
    n = len(AGREEMENT_METRICS)
    groups = [
        f"\\multicolumn{{{n}}}{{c{'||' if i < len(copies) - 1 else ''}}}{{{name}}}"
        for i, (name, _) in enumerate(copies)
    ]
    columns = "||".join(["c" * n] * len(copies))
    lines = [
        "% Generated by scripts/results_table.py from exp_analysis/metrics.csv",
        "\\begin{table}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        *DISTANCE_CAPTIONS[relative],
        "\\resizebox{\\columnwidth}{!}{%",
        f"\\begin{{tabular}}{{l||{columns}}}",
        "\\hline",
        f"\\multirow{{2}}{{*}}{{{HEADER}}} & " + "\n & ".join(groups) + " \\\\",
        " & "
        + " & ".join(label for _ in copies for label, _ in AGREEMENT_METRICS)
        + " \\\\",
        "\\hline\\hline",
    ]
    filled = total = 0
    for graphs in AGREEMENT_GRAPHS:
        for graph in graphs:
            values = {
                (variant, key): mean_distance(metrics, graph, variant, key, relative)
                for _, variant in copies
                for _, key in AGREEMENT_METRICS
            }
            best = {
                key: min(
                    (
                        round(v[0], DECIMALS)
                        for (_, k), v in values.items()
                        if k == key and v is not None
                    ),
                    default=None,
                )
                for _, key in AGREEMENT_METRICS
            }
            row = [latex_name(graph)]
            for _, variant in copies:
                for _, key in AGREEMENT_METRICS:
                    total += 1
                    value = values[variant, key]
                    if value is None:
                        row.append(MISSING)
                        continue
                    filled += 1
                    distance = round(value[0], DECIMALS)
                    text = f"{distance:.{DECIMALS}f}"
                    if distance == best[key]:
                        text = f"\\textbf{{{text}}}"
                    if value[1] < len(MODELS):
                        text += "$^\\dagger$"
                    row.append(text)
            lines.append(" & ".join(row) + " \\\\")
        lines.append("\\hline")
    lines += ["\\end{tabular}%", "}", "\\end{table}"]
    return "\n".join(lines) + "\n", filled, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("exp_analysis/metrics.csv"),
        help="summary written by kgc_benchmark/report_results.py; the "
        "ranking_agreement.csv files are read from the folders next to it",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exp_analysis/results_tables"),
        help="folder of the LaTeX files, one per table",
    )
    args = parser.parse_args()

    metrics = read_metrics(args.metrics)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    variants = [v for _, v in GENERATORS if v]
    for stem, source, datasets in tables(metrics):
        path = args.output_dir / f"{stem}.tex"
        path.write_text(source, encoding="utf-8")
        filled = sum(
            (d, v, m) in metrics for d in datasets for v in variants for m in MODELS
        )
        total = len(datasets) * len(variants) * len(MODELS)
        print(f"Wrote {path}: {filled} of {total} runs with results")

    source, filled, total = build_agreement_table(read_agreement(args.metrics.parent))
    path = args.output_dir / "ranking_agreement.tex"
    path.write_text(source, encoding="utf-8")
    print(f"Wrote {path}: {filled} of {total} taus")

    source, filled, total = build_distance_table(metrics)
    path = args.output_dir / "metric_distance.tex"
    path.write_text(source, encoding="utf-8")
    print(f"Wrote {path}: {filled} of {total} distances")

    source, filled, total = build_distance_table(metrics, relative=True)
    path = args.output_dir / "metric_distance_relative.tex"
    path.write_text(source, encoding="utf-8")
    print(f"Wrote {path}: {filled} of {total} distances")


if __name__ == "__main__":
    main()
