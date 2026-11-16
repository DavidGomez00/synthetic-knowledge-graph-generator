"""Compares the link prediction results of a dataset and its copies, per threshold.

Reads what kgc_benchmark/report_results.py writes for one family: the summary
`<analysis>/metrics.csv` (Hits@1/3/5/10/15 and MRR of every run, from the ranks
of its held-out triples) and `<analysis>/<family>/ranking_agreement.csv`
(Kendall's tau between the model rankings of the original and of each copy, per
metric). Run report_results.py first, and again when more runs finish:

    python kgc_benchmark/report_results.py --families french

A copy's variant is its generator and the threshold of the rules it was made from,
as `ours_std1` (rules with std confidence 1) or `kbgen_r25` (rule level r25, see
docs/rule-levels.md). The copies are grouped by threshold, and each threshold gets
its own tables, comparing the original with the generators' copies at that
threshold. The thresholds are every tag found in metrics.csv for the family, or
those of --thresholds.

For each threshold `<t>` it writes to `<analysis>/results_tables/` (--output-dir):

- `vanilla_<family>_<t>.md`, three Markdown tables: the metrics (one row per model,
  one group of columns per variant, the best of each column in bold, a run not
  ranked yet as "-"), the ranking of the models on each variant by each metric,
  and the agreement of each copy with the original per metric, Kendall's tau-b
  (with its p-value; * for p < 0.05) and the mean absolute difference;
- `vanilla_<family>_<t>.tex` and `vanilla_<family>_<t>_agreement.tex`, the metrics
  and agreement tables in the format of scripts/results_table.py, built by its own
  functions: columns Original, KBgen and NAME, the agreement table with Kendall's
  tau for every metric, the best tau of each metric in bold and $^*$ for p < 0.05;
- `vanilla_<family>_<t>_distance.tex` and `vanilla_<family>_<t>_distance_relative.tex`,
  the tables of results_table.py's metric_distance.tex and
  metric_distance_relative.tex for every metric: the mean absolute difference
  between the original's and the copy's scores over the models trained on both,
  and that difference summed and divided by the sum of the original's scores.

It prints the Markdown tables (the LaTeX ones with --latex). Only the Python
standard library is used.

    python scripts/compare_results.py
    python scripts/compare_results.py --thresholds std1 --latex
"""

import argparse
import csv
import sys
from pathlib import Path

import results_table

MODELS = results_table.MODELS
# Header and column of metrics.csv and ranking_agreement.csv of each metric.
METRICS = results_table.METRICS
REFERENCE = "original"
# results_table's generators as it defines them; latex_tables changes its copy.
GENERATORS = results_table.GENERATORS
# Caption text and order of each threshold. A threshold not listed here is
# named in the caption as it is and comes after these.
THRESHOLDS = {
    "r0": "made with no rules",
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def markdown(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join(
        [
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
            *("| " + " | ".join(r) + " |" for r in rows),
        ]
    )


def generator(variant: str) -> tuple[int, str, str]:
    """A copy's place in results_table's generator order, its column name there
    (`ours` is NAME, `kbgen` KBgen) and its threshold (`r0` for `ours_r0`,
    FULL for `ours`)."""
    for i, (name, key) in enumerate(GENERATORS):
        if key != REFERENCE and (variant == key or variant.startswith(f"{key}_")):
            return i, name, variant.removeprefix(key).lstrip("_") or FULL
    return len(GENERATORS), variant, FULL


def markdown_label(variant: str) -> str:
    """The Markdown name of a copy: `ours_std1` is "Ours"."""
    base = variant.partition("_")[0]
    return {"ours": "Ours", "kbgen": "kbgen", "pygraft": "PyGraft"}.get(base, base)


def latex_tables(
    analysis: Path, family: str, threshold: str, copies: list[str], caption_name: str
) -> tuple[str, str, str, str]:
    """The metrics, agreement, distance and relative distance tables of one
    family and threshold, made by
    results_table."""
    copies = sorted(copies, key=lambda c: generator(c)[0])
    results_table.GENERATORS = (
        ("Original", REFERENCE),
        *((generator(c)[1], c) for c in copies),
    )
    results_table.AGREEMENT_GRAPHS = ((family,),)
    results_table.AGREEMENT_METRICS = METRICS
    name = results_table.latex_name(family)
    stem = f"{family.replace('_', '-')}_{threshold}"
    made = THRESHOLDS.get(threshold, f"made at threshold {threshold}")

    metrics_table = results_table.build_table(
        results_table.read_metrics(analysis / "metrics.csv"),
        [(name, family)],
        results_table.HEADER,
        f"Link prediction results on {caption_name} and its synthetic copies {made}, "
        "trained and tested on VANILLA's 80/20 split.",
        f"tab:vanilla_{stem}",
    )
    agreement_table, _, _ = results_table.build_agreement_table(
        results_table.read_agreement(analysis)
    )
    agreement_table = agreement_table.replace(
        "on each original graph and on the synthetic copy of each generator, by "
        "Hits@1 and MRR. The highest $\\tau$ of each row is in bold, separately for "
        "Hits@1 and MRR.",
        f"on {caption_name} and on the synthetic copy of each generator {made}, by "
        "each metric. The highest $\\tau$ of each metric is in bold.",
    ).replace(
        "\\label{tab:ranking_agreement}", f"\\label{{tab:vanilla_{stem}_agreement}}"
    )
    metrics = results_table.read_metrics(analysis / "metrics.csv")
    distance_tables = []
    for relative, suffix in ((False, "distance"), (True, "distance_relative")):
        table, _, _ = results_table.build_distance_table(metrics, relative)
        table = (
            table.replace(
                "of Hits@1 and MRR between each original graph and the synthetic "
                "copy of each generator",
                f"of each metric between {caption_name} and the synthetic copy of "
                f"each generator {made}",
            )
            .replace(", separately for Hits@1 and MRR", "")
            .replace(
                "smallest difference of each row", "smallest difference of each metric"
            )
            .replace("smallest value of each row", "smallest value of each metric")
            .replace(
                f"\\label{{tab:metric_{suffix}}}",
                f"\\label{{tab:vanilla_{stem}_{suffix}}}",
            )
        )
        distance_tables.append(table)
    header = f"% Generated by scripts/compare_results.py from {analysis}"
    metrics_tex, agreement_tex, distance_tex, relative_tex = (
        header + "\n" + table.split("\n", 1)[1]
        for table in (metrics_table, agreement_table, *distance_tables)
    )
    return metrics_tex, agreement_tex, distance_tex, relative_tex


def markdown_tables(
    family: str,
    threshold: str,
    copies: list[str],
    scores: dict[str, dict[str, dict[str, float]]],
    taus: dict[tuple[str, str], dict[str, str]],
) -> str:
    """The metrics, ranking and agreement tables of one threshold, in Markdown."""
    variants = [REFERENCE, *copies]
    label = {REFERENCE: "Original", **{c: markdown_label(c) for c in copies}}
    models = [m for m in MODELS if any(m in scores.get(v, {}) for v in variants)]
    made = THRESHOLDS.get(threshold, threshold).replace("\\", "")
    tables = [f"## {family}, {threshold}: copies {made}"]

    # 1. Metrics.
    header = ["Model", *(f"{label[v]} {name}" for v in variants for name, _ in METRICS)]
    rows = []
    for model in models:
        cells = [model]
        for v in variants:
            for _, key in METRICS:
                if model not in scores.get(v, {}):
                    cells.append("-")
                    continue
                value = scores[v][model][key]
                best = max(s[key] for s in scores[v].values())
                cells.append(f"**{value:.3f}**" if value == best else f"{value:.3f}")
        rows.append(cells)
    tables.append(markdown(header, rows))

    # 2. Rankings, best first.
    header = ["Metric", *(label[v] for v in variants)]
    rows = []
    for name, key in METRICS:
        cells = [name]
        for v in variants:
            vs = scores.get(v, {})
            ranked = sorted(vs, key=lambda m: (-vs[m][key], MODELS.index(m)))
            cells.append(" > ".join(ranked) if ranked else "-")
        rows.append(cells)
    tables.append(markdown(header, rows))

    # 3. Agreement with the original.
    header = [
        "Metric",
        *(f"{label[c]} {h}" for c in copies for h in ("tau", "p", "mean diff")),
    ]
    rows = []
    for name, key in METRICS:
        cells = [name]
        for c in copies:
            row = taus.get((c, key))
            tau = row["kendall_tau"] if row else ""
            p = row["kendall_p"] if row else ""
            if tau:
                cells += [
                    f"{float(tau):.3f}" + ("*" if p and float(p) < 0.05 else ""),
                    f"{float(p):.3f}" if p else "-",
                ]
            else:
                cells += ["-", "-"]
            original, copy = scores.get(REFERENCE, {}), scores.get(c, {})
            shared = [m for m in MODELS if m in original and m in copy]
            diffs = [abs(original[m][key] - copy[m][key]) for m in shared]
            cells.append(f"{sum(diffs) / len(diffs):.3f}" if diffs else "-")
        rows.append(cells)
    tables.append(markdown(header, rows))
    return "\n\n".join(tables) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--analysis",
        type=Path,
        default=Path("exp_analysis"),
        help="report_results.py's --output (default: %(default)s)",
    )
    parser.add_argument("--family", default="french")
    parser.add_argument(
        "--thresholds",
        nargs="+",
        help="thresholds to write tables for (default: every one in metrics.csv)",
    )
    parser.add_argument(
        "--caption-name",
        default="French Royalty",
        help="name of the graph in the LaTeX captions (default: %(default)s)",
    )
    parser.add_argument(
        "--latex",
        action="store_true",
        help="print the LaTeX tables instead of the Markdown ones",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="folder of the table files (default: <analysis>/results_tables)",
    )
    args = parser.parse_args()

    summary = args.analysis / "metrics.csv"
    agreement = args.analysis / args.family / "ranking_agreement.csv"
    for path in (summary, agreement):
        if not path.is_file():
            parser.error(f"No {path}: run kgc_benchmark/report_results.py first.")

    scores: dict[str, dict[str, dict[str, float]]] = {}
    for row in read_csv(summary):
        if row["family"] == args.family:
            scores.setdefault(row["variant"], {})[row["model"]] = {
                key: float(row[key]) for _, key in METRICS
            }
    taus = {
        (row["compared"], row["metric"]): row
        for row in read_csv(agreement)
        if row["reference"] == REFERENCE
    }
    groups: dict[str, list[str]] = {}
    for variant in sorted(scores, key=lambda v: generator(v)[0]):
        if variant != REFERENCE:
            groups.setdefault(generator(variant)[2], []).append(variant)
    order = list(THRESHOLDS)
    thresholds = args.thresholds or sorted(
        groups, key=lambda t: (order.index(t) if t in order else len(order), t)
    )
    missing = [t for t in thresholds if t not in groups]
    if missing:
        parser.error(
            f"No copy of {args.family} at {', '.join(missing)} in {summary}; "
            f"found {', '.join(groups) or 'none'}."
        )

    output = args.output_dir or args.analysis / "results_tables"
    output.mkdir(parents=True, exist_ok=True)
    written, printed = [], []
    for threshold in thresholds:
        copies = groups[threshold]
        md = markdown_tables(args.family, threshold, copies, scores, taus)
        metrics_tex, agreement_tex, distance_tex, relative_tex = latex_tables(
            args.analysis, args.family, threshold, copies, args.caption_name
        )
        stem = f"vanilla_{args.family}_{threshold}"
        for name, text in (
            (f"{stem}.md", md),
            (f"{stem}.tex", metrics_tex),
            (f"{stem}_agreement.tex", agreement_tex),
            (f"{stem}_distance.tex", distance_tex),
            (f"{stem}_distance_relative.tex", relative_tex),
        ):
            (output / name).write_text(text, encoding="utf-8")
            written.append(output / name)
        printed.append(
            "\n".join((metrics_tex, agreement_tex, distance_tex, relative_tex))
            if args.latex
            else md
        )
    print("\n".join(printed), end="")
    print("\nWrote " + ", ".join(str(p) for p in written), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
