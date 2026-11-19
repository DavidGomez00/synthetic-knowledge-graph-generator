"""Compares the confidence of the mined rules on each graph and on its copies.

For each family (a folder of `--root` with `<family>.unified.tsv` and
`<family>.csv`), reads every mined rule of `<family>.csv` and counts, on the
source and on each copy that exists, the rule's support (`positive_examples`),
body size and PCA body size, as AMIE and the summary of `cli/main.py` do, and
from them its std and PCA confidence. The copies are this generator's and
kbgen's rule-level copies (`<family>_ours_<level>`, `<family>_kbgen_<level>`,
for the levels of `exp_datasets/rule_levels.tsv` that have a copy of their own,
including pca1, the rules with PCA confidence 1; std1 copies, made by the std
setting the generator had until 2026-11-19, are ignored)
and PyGraft's copy (`<family>_pygraft`), made without rules, which is compared
at every level. Everything is computed from the `.tsv` files, with no graph
database.

Each rule gets a role at each level: `used` if it is in the rules this
generator used there (`<family>_ours_<level>_used.csv`, the rules kbgen was
given too), `removed` if the level keeps it (by the threshold in
`rule_levels.tsv`, or PCA confidence 1 for pca1) but cycle removal dropped it,
and `not_given` otherwise. A used rule is `complete` (PCA confidence 1,
forward-chained by this generator) or `partial`.

Before using a source's counts, the script checks them against the rules CSV:
on the graph as AMIE read it (`rule_set_table.as_amie_reads`), the support,
body size and PCA body size must equal `positive_examples`, `body_size` and
`pca_body_size`. The confidences compared are those on the graph as it is.
`--check-logs` also compares this generator's copies with the summary of the
run that made them, the log next to each copy (`<copy>/<copy>.log`, or
`<folder>/<copy>.log` with `--check-logs FOLDER`), to 3 decimals.

Writes to `--output-dir` (default `exp_analysis/rule_confidence`):

- `rules.csv`: one row per family, level, generator and rule, with the rule's
  role and kind, its counts and confidences on the source and on the copy, and
  the differences.
- `summary.csv`: per family, level, generator and group of rules (`used`,
  `used_complete`, `used_partial`, `not_given`, `all`), for std and PCA
  confidence: the rules, those whose confidence is undefined on the copy (body
  never grounded), those within `WITHIN` of the source, the median and mean
  absolute difference, the mean difference (copy minus source), the root mean
  squared error and Spearman's rho between the source and copy confidences.
  The undefined confidences are left out of every statistic but their count.
- `summary_<level>.md`, `.tex` and `.csv`: for the used rules, the share within
  `WITHIN`, the median absolute difference and Spearman's rho of PCA
  confidence, one row per family, one column per generator, the closest
  generator in bold.
- `plots/<family>_<level>.png`: each rule's PCA confidence on the source
  against the copy, one panel per generator, used rules filled.

Needs the `skgg` package (`pip install -e .`).

    python scripts/rule_confidence.py
    python scripts/rule_confidence.py --families french french1_1 --check-logs
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import re
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import pandas as pd
from results_table import HEADER, MISSING, latex_name
from rule_set_table import TripleIndex, as_amie_reads, path_atoms, rule_predictions
from scipy.stats import spearmanr

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

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

log = logging.getLogger("rule_confidence")

LEVELS = ("pca1", "r25", "r50", "r75", "r100")
# The level that keeps the rules with PCA confidence 1. A `rule_levels.tsv` of the
# former std setting has no row for it, so its threshold is set here.
PCA1 = "pca1"
# Generator key, the name of its copies' folders and its column name, in table
# order (the order of results_table.GENERATORS).
GENERATORS = (("pygraft", "PyGraft"), ("kbgen", "KBgen"), ("ours", "NAME"))
LABELS = {"pygraft": "PyGraft", "kbgen": "kbgen", "ours": "Ours"}
COLORS = {"pygraft": CATEGORICAL[2], "kbgen": CATEGORICAL[1], "ours": CATEGORICAL[0]}
CONFIDENCES = ("std", "pca")
GROUPS = ("used", "used_complete", "used_partial", "not_given", "all")
# A copy's confidence is "within" the source's when they differ by at most this.
WITHIN = 0.05
DECIMALS = 3

Triple = tuple[str, str, str]
Atom = tuple[str, str, str]
Counts = tuple[int, int, int]  # support, body size, PCA body size


@dataclass(frozen=True)
class Rule:
    rule_id: str
    body: str
    head: str
    functional_variable: str
    std_confidence: float
    pca_confidence: float
    positive_examples: int
    body_size: int
    pca_body_size: int

    @property
    def text(self) -> str:
        return f"{self.body} => {self.head}"


def read_rules(path: Path) -> list[Rule]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            Rule(
                rule_id=r["rule_id"],
                body=r["body"],
                head=r["head"],
                functional_variable=r["functional_variable"],
                std_confidence=float(r["std_confidence"]),
                pca_confidence=float(r["pca_confidence"]),
                positive_examples=int(float(r["positive_examples"])),
                body_size=int(float(r["body_size"])),
                pca_body_size=int(float(r["pca_body_size"])),
            )
            for r in csv.DictReader(f)
        ]


def read_triples(path: Path) -> set[Triple]:
    """The distinct triples of a `.tsv` file; lines that aren't a triple are
    skipped."""
    triples: set[Triple] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for line in f:
            terms = line.rstrip("\r\n").split("\t")
            if len(terms) == 3 and all(terms):
                triples.add((terms[0], terms[1], terms[2]))
    return triples


def parse_atoms(text: str) -> list[Atom]:
    tokens = text.split()
    return [(tokens[i], tokens[i + 1], tokens[i + 2]) for i in range(0, len(tokens), 3)]


def groundings(
    atoms: list[Atom], binding: dict[str, str], index: TripleIndex
) -> Iterator[dict[str, str]]:
    """Every binding of the atoms' variables that extends `binding` and makes all
    the atoms hold. Atoms with a bound variable are grounded first."""
    if not atoms:
        yield binding
        return
    atom = max(atoms, key=lambda a: (a[0] in binding) + (a[2] in binding))
    rest = [a for a in atoms if a is not atom]
    s_var, p, o_var = atom
    s, o = binding.get(s_var), binding.get(o_var)
    if s is not None and o is not None:
        if o in index.objects[p].get(s, ()):
            yield from groundings(rest, binding, index)
        return
    if s is not None:
        candidates = [(s, x) for x in index.objects[p].get(s, ())]
    elif o is not None:
        candidates = [(x, o) for x in index.subjects[p].get(o, ())]
    else:
        candidates = index.pairs[p]
    for cs, co in candidates:
        if s_var == o_var and cs != co:
            continue
        yield from groundings(rest, {**binding, s_var: cs, o_var: co}, index)


def body_counts(rule: Rule, index: TripleIndex) -> tuple[int, int]:
    """The rule's body size, the distinct bindings of the head variables for
    which the body holds, and its PCA body size, those of them whose functional
    variable has some fact of the head relation (the denominators of AMIE's std
    and PCA confidence)."""
    atoms = parse_atoms(rule.body)
    x, r, y = rule.head.split()
    fv = rule.functional_variable
    if fv not in (x, y):
        raise ValueError(f"rule {rule.rule_id}: functional variable {fv} not in head")
    head_subjects = index.objects[r]
    head_objects = index.subjects[r]

    def has_head(xv: str, yv: str) -> bool:
        return xv in head_subjects if fv == x else yv in head_objects

    path = path_atoms(atoms, x, y)
    if path is not None:
        # x - z - y: the y values of an x are the union, over the z values reached
        # from x, of the y values reached from z.
        (px, x_is_subject), (py, y_is_subject) = path
        empty: frozenset[str] = frozenset()
        from_x = index.objects[px] if x_is_subject else index.subjects[px]
        z_to_y = index.subjects[py] if y_is_subject else index.objects[py]
        body = pca = 0
        for xv, zs in from_x.items():
            ys: set[str] = set()
            for z in zs:
                ys.update(z_to_y.get(z, empty))
            body += len(ys)
            if fv == x:
                pca += len(ys) if xv in head_subjects else 0
            else:
                pca += sum(1 for yv in ys if yv in head_objects)
        return body, pca
    pairs = {(b[x], b[y]) for b in groundings(atoms, {}, index)}
    return len(pairs), sum(1 for xv, yv in pairs if has_head(xv, yv))


def rule_counts(rule: Rule, index: TripleIndex) -> Counts:
    support = len(rule_predictions(rule.body, rule.head, index))
    body, pca = body_counts(rule, index)
    return support, body, pca


def graph_counts(path: Path, rules: list[Rule], check: bool) -> dict[str, Counts]:
    """Each rule's counts on the triples of `path`. With `check`, also checks
    them, on the triples as AMIE read them, against the rules CSV."""
    start = time.monotonic()
    triples = read_triples(path)
    index = TripleIndex(triples)
    counts = {rule.rule_id: rule_counts(rule, index) for rule in rules}
    if check:
        amie = as_amie_reads(triples)
        amie_index = index if amie == triples else TripleIndex(amie)
        for rule in rules:
            found = (
                counts[rule.rule_id]
                if amie_index is index
                else rule_counts(rule, amie_index)
            )
            expected = (rule.positive_examples, rule.body_size, rule.pca_body_size)
            if found != expected:
                raise ValueError(
                    f"{path}: rule {rule.rule_id} ({rule.text}) has support, body "
                    f"size and PCA body size {found}, the rules CSV {expected}"
                )
    log.info(
        "%s: %d triples, %d rules in %.1f s",
        path.name,
        len(triples),
        len(rules),
        time.monotonic() - start,
    )
    return counts


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True)
class Level:
    name: str
    kept: frozenset[str]
    used: frozenset[str]
    copies: dict[str, Path]  # generator -> triple file


def read_levels(root: Path, family: str, rules: list[Rule]) -> list[Level]:
    """The levels of `rule_levels.tsv` at which the family has a copy of its own
    by this generator or kbgen, with the rules each keeps and the rules this
    generator used."""
    rows: dict[str, dict[str, str]] = {}
    path = root / "rule_levels.tsv"
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row["dataset"] == family:
                    rows[row["level"]] = row
    pygraft = root / f"{family}_pygraft" / f"{family}_pygraft.tsv"
    levels = []
    for name in LEVELS:
        if name not in rows and name != PCA1:
            continue
        level_row = rows.get(name)
        copies = {
            key: root / f"{family}_{key}_{name}" / f"{family}_{key}_{name}.tsv"
            for key in ("kbgen", "ours")
        }
        copies = {k: p for k, p in copies.items() if p.is_file()}
        if not copies:
            continue
        if pygraft.is_file():
            copies["pygraft"] = pygraft
        if level_row is None:
            kept = {r.rule_id for r in rules if r.pca_confidence >= 1.0}
        elif level_row.get("std_threshold"):
            threshold = float(level_row["std_threshold"])
            kept = {r.rule_id for r in rules if r.std_confidence >= threshold}
        else:
            threshold = float(level_row["pca_threshold"])
            kept = {r.rule_id for r in rules if r.pca_confidence >= threshold}
        used_file = root / f"{family}_ours_{name}" / f"{family}_ours_{name}_used.csv"
        if used_file.is_file():
            used = {r.rule_id for r in read_rules(used_file)}
        else:
            log.warning(
                "%s %s: no %s, every kept rule counts as used",
                family,
                name,
                used_file.name,
            )
            used = kept
        levels.append(Level(name, frozenset(kept), frozenset(used), copies))
    return levels


def families(root: Path, names: list[str] | None) -> list[str]:
    found = sorted(
        (
            d.name
            for d in root.iterdir()
            if (d / f"{d.name}.unified.tsv").is_file()
            and (d / f"{d.name}.csv").is_file()
        ),
        key=lambda n: (root / n / f"{n}.unified.tsv").stat().st_size,
    )
    if names is None:
        return found
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f"Not a family under {root}: {', '.join(sorted(missing))}")
    return [n for n in found if n in names]


def rule_rows(
    family: str,
    rules: list[Rule],
    levels: list[Level],
    counts: dict[Path, dict[str, Counts]],
    source: Path,
) -> list[dict[str, object]]:
    rows = []
    for level in levels:
        for key, path in level.copies.items():
            for rule in rules:
                rid = rule.rule_id
                if rid in level.used:
                    role = "used"
                elif rid in level.kept:
                    role = "removed"
                else:
                    role = "not_given"
                s_sup, s_body, s_pca = counts[source][rid]
                c_sup, c_body, c_pca = counts[path][rid]
                row: dict[str, object] = {
                    "family": family,
                    "level": level.name,
                    "generator": key,
                    "copy": path.parent.name,
                    "rule_id": rid,
                    "rule": rule.text,
                    "role": role,
                    "kind": "complete" if rule.pca_confidence >= 1 else "partial",
                    "source_support": s_sup,
                    "source_body_size": s_body,
                    "source_pca_body_size": s_pca,
                    "copy_support": c_sup,
                    "copy_body_size": c_body,
                    "copy_pca_body_size": c_pca,
                }
                for conf, s_den, c_den in (
                    ("std", s_body, c_body),
                    ("pca", s_pca, c_pca),
                ):
                    s_val, c_val = ratio(s_sup, s_den), ratio(c_sup, c_den)
                    row[f"source_{conf}"] = s_val
                    row[f"copy_{conf}"] = c_val
                    row[f"delta_{conf}"] = (
                        c_val - s_val
                        if s_val is not None and c_val is not None
                        else None
                    )
                rows.append(row)
    return rows


def group_mask(frame: pd.DataFrame, group: str) -> pd.Series:
    if group == "all":
        return pd.Series(True, index=frame.index)
    if group == "used":
        return frame["role"] == "used"
    if group == "not_given":
        return frame["role"] == "not_given"
    kind = group.removeprefix("used_")
    return (frame["role"] == "used") & (frame["kind"] == kind)


def statistics_of(group: pd.DataFrame, conf: str) -> dict[str, object]:
    defined = group.dropna(subset=[f"source_{conf}", f"copy_{conf}"])
    deltas = defined[f"delta_{conf}"].astype(float)
    absolute = deltas.abs()
    stats: dict[str, object] = {
        f"{conf}_undefined": int(group[f"copy_{conf}"].isna().sum()),
        f"{conf}_compared": len(defined),
        f"{conf}_within": int((absolute <= WITHIN + 1e-9).sum()),
        f"{conf}_within_share": (absolute <= WITHIN + 1e-9).mean()
        if len(defined)
        else None,
        f"{conf}_median_abs": absolute.median() if len(defined) else None,
        f"{conf}_mean_abs": absolute.mean() if len(defined) else None,
        f"{conf}_mean_delta": deltas.mean() if len(defined) else None,
        f"{conf}_rmse": math.sqrt((deltas**2).mean()) if len(defined) else None,
        f"{conf}_spearman_rho": None,
        f"{conf}_spearman_p": None,
    }
    if (
        len(defined) >= 3
        and defined[f"source_{conf}"].nunique() > 1
        and defined[f"copy_{conf}"].nunique() > 1
    ):
        rho = spearmanr(defined[f"source_{conf}"], defined[f"copy_{conf}"])
        if not math.isnan(rho.statistic):
            stats[f"{conf}_spearman_rho"] = float(rho.statistic)
            stats[f"{conf}_spearman_p"] = float(rho.pvalue)
    return stats


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    keys = ["family", "level", "generator", "copy"]
    for values, part in frame.groupby(keys, sort=False):
        for group in GROUPS:
            selected = part[group_mask(part, group)]
            row: dict[str, object] = dict(zip(keys, values, strict=True))
            row.update(group=group, rules=len(selected))
            for conf in CONFIDENCES:
                row.update(statistics_of(selected, conf))
            rows.append(row)
    return pd.DataFrame(rows)


# Columns of the per-level tables: header, column of summary.csv, whether the
# best value is the largest, and the format of a value.
TABLE_MEASURES = (
    ("Within 0.05", "pca_within_share", True),
    ("Median abs. diff.", "pca_median_abs", False),
    ("Spearman", "pca_spearman_rho", True),
)


def level_cells(
    summary: pd.DataFrame, level: str
) -> list[tuple[str, dict[tuple[str, str], tuple[str, bool]]]]:
    """Per family with a copy at `level`, the text of each (measure, generator)
    cell of the used rules and whether it is the best of its measure."""
    part = summary[(summary["level"] == level) & (summary["group"] == "used")]
    table = []
    for family, rows in part.groupby("family", sort=False):
        by_key = rows.set_index("generator")
        cells: dict[tuple[str, str], tuple[str, bool]] = {}
        for _, column, larger in TABLE_MEASURES:
            values = {
                key: round(float(by_key.at[key, column]), DECIMALS)  # type: ignore[arg-type]
                for key, _ in GENERATORS
                if key in by_key.index and pd.notna(by_key.at[key, column])
            }
            best = (max if larger else min)(values.values()) if values else None
            for key, _ in GENERATORS:
                if key in values:
                    cells[(column, key)] = (
                        f"{values[key]:.{DECIMALS}f}",
                        values[key] == best,
                    )
                else:
                    cells[(column, key)] = (MISSING, False)
        rules = int(rows["rules"].iloc[0])
        table.append((f"{family} ({rules})", cells))
    return table


def level_markdown(summary: pd.DataFrame, level: str) -> str:
    rows = []
    for name, cells in level_cells(summary, level):
        row = {"graph (used rules)": name}
        for header, column, _ in TABLE_MEASURES:
            for key, _ in GENERATORS:
                text, best = cells[(column, key)]
                row[f"{header} {LABELS[key]}"] = f"**{text}**" if best else text
        rows.append(row)
    title = (
        f"## PCA confidence of the used rules on each copy at level {level}\n\n"
        f"Share of the rules whose PCA confidence on the copy is within {WITHIN} of "
        "the source's, median absolute difference, and Spearman's rho between the "
        "source and copy confidences. The closest generator is in bold.\n\n"
    )
    return title + pd.DataFrame(rows).to_markdown(index=False) + "\n"


def level_latex(summary: pd.DataFrame, level: str) -> str:
    names = [name for _, name in GENERATORS]
    columns = "|".join("c" * len(names) for _ in TABLE_MEASURES)
    lines = [
        "% Generated by scripts/rule_confidence.py",
        "\\begin{table*}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        f"\\caption{{PCA confidence of the rules each generator was given at level "
        f"{level}, on its copy against the original graph: share of the rules within "
        f"{WITHIN} of their original PCA confidence, median absolute difference, and "
        "Spearman's $\\rho$ between the original and copy confidences over the rules. "
        "PyGraft's copy, made without rules, is measured on the same rules. The "
        "number of rules is in parentheses; the closest generator is in bold.}",
        f"\\label{{tab:rule-confidence-{level}}}",
        "\\resizebox{\\textwidth}{!}{%",
        f"\\begin{{tabular}}{{l||{columns}}}",
        "\\hline",
        " & "
        + " & ".join(
            f"\\multicolumn{{{len(names)}}}{{c{separator}}}{{{header}}}"
            for header, separator in zip(
                (h for h, _, _ in TABLE_MEASURES),
                ["|"] * (len(TABLE_MEASURES) - 1) + [""],
                strict=True,
            )
        )
        + " \\\\",
        " & ".join([HEADER, *(names * len(TABLE_MEASURES))]) + " \\\\",
        "\\hline\\hline",
    ]
    for name, cells in level_cells(summary, level):
        row = [latex_name(name)]
        for _, column, _ in TABLE_MEASURES:
            for key, _ in GENERATORS:
                text, best = cells[(column, key)]
                row.append(f"\\textbf{{{text}}}" if best else text)
        lines.append(" & ".join(row) + " \\\\")
    lines += ["\\hline", "\\end{tabular}%", "}", "\\end{table*}"]
    return "\n".join(lines) + "\n"


def level_csv(summary: pd.DataFrame, level: str) -> pd.DataFrame:
    part = summary[(summary["level"] == level) & (summary["group"] == "used")]
    return part[
        [
            "family",
            "generator",
            "rules",
            "pca_undefined",
            *(c for _, c, _ in TABLE_MEASURES),
        ]
    ]


def scatter(frame: pd.DataFrame, family: str, level: str, output: Path) -> None:
    """Source against copy PCA confidence of every rule, one panel per generator."""
    keys = [k for k, _ in GENERATORS if k in set(frame["generator"])]
    fig, axes = plt.subplots(
        1, len(keys), figsize=(3.2 * len(keys), 3.7), dpi=PLOT_DPI, squeeze=False
    )
    fig.patch.set_facecolor(SURFACE)
    for ax, key in zip(axes[0], keys, strict=True):
        part = frame[frame["generator"] == key].dropna(
            subset=["source_pca", "copy_pca"]
        )
        used = part["role"] == "used"
        ax.set_facecolor(SURFACE)
        ax.plot([0, 1], [0, 1], color=BASELINE, linewidth=1, zorder=1)
        ax.scatter(
            part.loc[~used, "source_pca"],
            part.loc[~used, "copy_pca"],
            s=16,
            facecolors="none",
            edgecolors=INK_MUTED,
            linewidths=1,
            label="other mined rules",
            zorder=2,
        )
        ax.scatter(
            part.loc[used, "source_pca"],
            part.loc[used, "copy_pca"],
            s=20,
            color=COLORS[key],
            edgecolors=SURFACE,
            linewidths=2 * PX,
            label="rules given",
            zorder=3,
        )
        undefined = int(frame[(frame["generator"] == key)]["copy_pca"].isna().sum())
        title = LABELS[key] + (
            f" ({undefined} rules with no PCA body)" if undefined else ""
        )
        ax.set_title(title, color=INK, fontsize=9)
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_aspect("equal")
        ax.grid(color=GRIDLINE, linewidth=1)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color(BASELINE)
        ax.tick_params(colors=INK_SECONDARY, labelsize=7)
        ax.set_xlabel("PCA confidence, original", color=INK_SECONDARY, fontsize=8)
    axes[0][0].set_ylabel("PCA confidence, copy", color=INK_SECONDARY, fontsize=8)
    # The two kinds of points, drawn in neutral ink: each panel fills the given
    # rules with its generator's color.
    handles = [
        Line2D([], [], linestyle="none", marker="o", markersize=6, color=INK_SECONDARY),
        Line2D(
            [],
            [],
            linestyle="none",
            marker="o",
            markersize=6,
            markerfacecolor="none",
            markeredgecolor=INK_MUTED,
        ),
    ]
    fig.legend(
        handles,
        [f"rules given at level {level}", "other mined rules"],
        loc="lower center",
        ncol=2,
        fontsize=8,
        frameon=False,
        labelcolor=INK_SECONDARY,
    )
    fig.suptitle(f"{family}, level {level}", color=INK, fontsize=10)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor=SURFACE)
    plt.close(fig)


LOG_ROW = re.compile(
    r"^\s+(?P<rid>\d+)\s+.*?std (?P<s0>n/a|[\d.]+) -> (?P<s1>n/a|[\d.]+).*?"
    r"pca (?P<p0>n/a|[\d.]+) -> (?P<p1>n/a|[\d.]+)"
)


def check_logs(frame: pd.DataFrame, root: Path, logs: Path | None) -> int:
    """Compares this generator's rows with the summary of each copy's run log,
    `<root>/<copy>/<copy>.log` or `<logs>/<copy>.log`, and returns the number of
    mismatches."""
    mismatches = checked = 0
    ours = frame[frame["generator"] == "ours"]
    for copy, part in ours.groupby("copy"):
        path = (root / copy if logs is None else logs) / f"{copy}.log"
        if not path.is_file():
            log.warning("no run log %s", path)
            continue
        by_id = part.set_index("rule_id")
        for line in path.read_text(encoding="utf-8").splitlines():
            match = LOG_ROW.match(line)
            if not match or match["rid"] not in by_id.index:
                continue
            row = by_id.loc[match["rid"]]
            for conf, original, copied in (("std", "s0", "s1"), ("pca", "p0", "p1")):
                for side, group in (("source", original), ("copy", copied)):
                    value = row[f"{side}_{conf}"]
                    text: str = "n/a" if pd.isna(value) else f"{value:.3f}"
                    checked += 1
                    if text != match[group]:
                        mismatches += 1
                        log.error(
                            "%s rule %s: %s %s confidence %s, log %s",
                            copy,
                            match["rid"],
                            side,
                            conf,
                            text,
                            match[group],
                        )
    log.info(
        "Checked %d values against the run logs: %d mismatches", checked, mismatches
    )
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument("--families", nargs="+", help="family folders (default all)")
    parser.add_argument("--levels", nargs="+", choices=LEVELS, default=list(LEVELS))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("exp_analysis/rule_confidence")
    )
    parser.add_argument("--workers", type=int, default=1, help="graphs counted at once")
    parser.add_argument(
        "--check-logs",
        nargs="?",
        const="",
        metavar="FOLDER",
        help="compare our copies with their run logs: the log next to each copy, "
        "or <FOLDER>/<copy>.log",
    )
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s"
    )

    plan = []
    for family in families(args.root, args.families):
        rules = read_rules(args.root / family / f"{family}.csv")
        levels = [
            lv for lv in read_levels(args.root, family, rules) if lv.name in args.levels
        ]
        if not levels:
            log.info("%s: no copy at the levels asked for", family)
            continue
        source = args.root / family / f"{family}.unified.tsv"
        graphs = [
            source,
            *dict.fromkeys(p for lv in levels for p in lv.copies.values()),
        ]
        plan.append((family, rules, levels, source, graphs))

    jobs = [
        (g, rules, g == source) for _, rules, _, source, graphs in plan for g in graphs
    ]
    log.info("%d families, %d graphs to count", len(plan), len(jobs))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(graph_counts, *zip(*jobs, strict=True)))
    counts = {job[0]: result for job, result in zip(jobs, results, strict=True)}

    rows = []
    for family, rules, levels, source, _ in plan:
        rows += rule_rows(family, rules, levels, counts, source)
    frame = pd.DataFrame(rows)
    summary = summarize(frame)

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "rules.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)
    written = [out / "rules.csv", out / "summary.csv"]
    for level in args.levels:
        if not (summary["level"] == level).any():
            continue
        (out / f"summary_{level}.md").write_text(
            level_markdown(summary, level), encoding="utf-8"
        )
        (out / f"summary_{level}.tex").write_text(
            level_latex(summary, level), encoding="utf-8"
        )
        level_csv(summary, level).to_csv(out / f"summary_{level}.csv", index=False)
        written += [out / f"summary_{level}.{ext}" for ext in ("md", "tex", "csv")]
    if not args.no_plots:
        for keys, part in frame.groupby(["family", "level"], sort=False):
            graph, at = (str(k) for k in keys)
            scatter(part, graph, at, out / "plots" / f"{graph}_{at}.png")
        written.append(out / "plots")
    for path in written:
        print(f"Wrote {path}")

    if args.check_logs is not None and check_logs(
        frame, args.root, Path(args.check_logs) if args.check_logs else None
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
