"""Picks the PCA confidence thresholds of the rule-level experiment for every dataset.

The experiment generates each benchmark dataset with this project's generator and
with kbgen at several rule levels: no rules (`r0`), only the rules with PCA
confidence 1 (`pca1`, the complete rules), a share of the dataset's mined rules
(`r25`, `r50`, `r75` by default) and all of them (`r100`). A share level's
threshold is chosen per dataset, from its rules file `<folder>/<folder>.csv`, so
that it keeps about the same share of rules in every dataset:

1. The N rules are sorted by PCA confidence, descending. For share s, the k-th rule,
   with k = ceil(s * N), gives the boundary value b.
2. The threshold is b rounded down to the fewest decimals that stay above the next
   lower PCA confidence of the file, so `pca_confidence >= threshold` keeps exactly
   the rules with PCA confidence >= b: the first k and any rule tied with the k-th.
   Ties can raise the share kept above s.
3. `r100` has threshold 0 and `r0` keeps no rule.

`pca1` has PCA threshold 1, the rules `cli/main.py` keeps without `--pca-conf`, and
every share level keeps these rules too.

For each level the output also counts the rules each generator can use. Ours, as
`cli/main.py` runs it: the complete rules (PCA confidence 1) left after
`core/cycles.remove_minimal_cyclic_rules`, with the symmetric rules of
`engine/edb.get_undirected_rule_ids` exempt, plus every partial rule (PCA confidence
below 1), which cycle removal leaves alone. kbgen: the
rules whose relations all keep a triple once kbgen's `load_tensor.py` has dropped
every triple whose object is never a subject. The profiles both need are read from
`<folder>.unified.tsv`, so no graph database is used.

The column `completeness` is always `pca`: `cli/generate_datasets.py` refuses a
levels file without it, written for the std setting that the generator had until
2026-11-19 (levels `std1` instead of `pca1`, columns `std_threshold` and
`complete_changed`; commit 2c7dfd5d has it).

A level whose rules, for one generator, equal those of another level gets that
level's name in `ours_same_as` or `kbgen_same_as`: its copy would be generated from
the same rules, so it is neither generated nor trained, and the named level's copy
stands in for it. An empty rule set points to `r0`, any other to the highest level
with the same rules, so for kbgen it is `r100` (the full `<folder>_kbgen` copy until it
was removed on 17 November 2026).

    python -m skgg.cli.rule_levels          # -> exp_datasets/rule_levels.tsv
    python -m skgg.cli.rule_levels --datasets french family -o /tmp/levels.tsv
    python -m skgg.cli.rule_levels --shares 0.1 0.25 0.5 0.75 0.9
"""

import argparse
import csv
import logging
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from skgg.core.cycles import remove_minimal_cyclic_rules
from skgg.core.rules import parse_rule_set, rule_sort_key
from skgg.core.utils import build_term_mapping, format_term, setup_logging
from skgg.engine.edb import get_undirected_rule_ids
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)

DEFAULT_SHARES = (0.25, 0.5, 0.75)
# The level that keeps only the complete rules, PCA confidence >=
# PCA_LEVEL_THRESHOLD, as cli/main.py does without --pca-conf.
PCA_LEVEL = "pca1"
PCA_LEVEL_THRESHOLD = Decimal(1)
# The value of the `completeness` column, which cli/generate_datasets.py checks.
COMPLETENESS = "pca"
# Suffixes of the copies' folders, which hold no rules of their own.
COPY_SUFFIXES = ("_ours", "_kbgen", "_pygraft")
# The rule-level copies, e.g. `french_kbgen_r25` (see docs/rule-levels.md).
LEVEL_COPY = re.compile(r"_(ours|kbgen|pygraft)_(r\d+|std1|pca1)$")
# Namespace of a dataset's graphs in cli/generate_datasets.py.
NAMESPACE = "http://skgg.org/{folder}/"
COLUMNS = (
    "dataset",
    "level",
    "target_share",
    "pca_threshold",
    "rules_mined",
    "rules_kept",
    "share_kept",
    "rules_ours",
    "rules_kbgen",
    "ours_same_as",
    "kbgen_same_as",
    "completeness",
)


@dataclass
class Level:
    """One rule level of one dataset."""

    name: str
    target_share: float
    # PCA threshold; None for r0, which keeps no rule.
    threshold: Decimal | None
    kept: set[str]
    ours: set[str]
    kbgen: set[str]
    ours_same_as: str = ""
    kbgen_same_as: str = ""


def level_name(share: float) -> str:
    """`r25` for share 0.25."""
    return f"r{round(share * 100)}"


def pick_threshold(pca: list[Decimal], share: float) -> Decimal:
    """Returns the threshold that keeps the first ceil(share * N) rules by PCA
    confidence and any rule tied with the last of them (see the module docstring).

    Args:
        pca: PCA confidences of the dataset's rules, sorted descending.
        share: Share of the rules to keep, in (0, 1).
    """
    k = max(1, math.ceil(share * len(pca)))
    boundary = pca[k - 1]
    below = next((value for value in pca[k:] if value < boundary), Decimal(0))
    decimals = 1
    while True:
        step = Decimal(1).scaleb(-decimals)
        rounded = (boundary / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if rounded > below:
            return rounded.normalize()
        decimals += 1


def read_rule_confidences(
    rules_file: Path, column: str = "pca_confidence"
) -> dict[str, Decimal]:
    """A confidence column of a rules CSV, PCA by default, by rule_id. Decimal keeps
    the CSV's digits, so a threshold equal to a rule's value keeps that rule."""
    with rules_file.open(encoding="utf-8", newline="") as f:
        return {
            row["rule_id"].strip(): Decimal(row[column])
            for row in csv.DictReader(f)
        }


def read_graph(
    triple_file: Path, term_mapping: dict[str, str]
) -> tuple[dict[str, PredicateProfile], set[str]]:
    """Reads a `.tsv` graph into the profiles cycle removal needs (domain and range
    counts per predicate) and the predicates kbgen keeps: those with a triple whose
    object is also the subject of some triple. Predicates are formatted with
    `term_mapping`, as `parse_rule_set` formats the rules' predicates."""
    domain: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    range_: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    subjects: set[str] = set()
    objects: dict[str, set[str]] = defaultdict(set)
    with triple_file.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            terms = line.rstrip("\r\n").split("\t")
            if len(terms) != 3:
                logger.warning(
                    "%s:%d is not a triple, skipped.", triple_file, line_number
                )
                continue
            s, p, o = terms
            domain[p][s] += 1
            range_[p][o] += 1
            subjects.add(s)
            objects[p].add(o)
    profiles = {
        format_term(p, term_mapping): PredicateProfile(
            domain=dict(domain[p]), range=dict(range_[p])
        )
        for p in domain
    }
    kbgen_preds = {
        format_term(p, term_mapping)
        for p, objs in objects.items()
        if not objs.isdisjoint(subjects)
    }
    return profiles, kbgen_preds


def dataset_levels(folder: Path, shares: list[float]) -> tuple[int, list[Level]]:
    """Computes every level of one dataset.

    Returns:
        The number of mined rules and the levels: r0, pca1, the share levels and
        r100.

    Raises:
        ValueError: If a rule uses a predicate that the graph lacks.
    """
    rules_file = folder / f"{folder.name}.csv"
    term_mapping = build_term_mapping({}, NAMESPACE.format(folder=folder.name))
    confidences = read_rule_confidences(rules_file)
    pca = sorted(confidences.values(), reverse=True)
    profiles, kbgen_preds = read_graph(
        folder / f"{folder.name}.unified.tsv", term_mapping
    )

    thresholds: list[tuple[str, float, Decimal | None]] = [
        ("r0", 0.0, None),
        (PCA_LEVEL, 0.0, PCA_LEVEL_THRESHOLD),
    ]
    thresholds += [
        (level_name(s), s, pick_threshold(pca, s) if pca else None) for s in shares
    ]
    thresholds.append(("r100", 1.0, Decimal(0)))

    levels = []
    for name, share, threshold in thresholds:
        if threshold is None:
            levels.append(Level(name, share, None, set(), set(), set()))
            continue
        rules = parse_rule_set(
            rules_file, term_mapping, pca_threshold=float(threshold)
        )
        kept = {r_id for r_id, value in confidences.items() if value >= threshold}
        condition = f"PCA >= {threshold}"
        if set(rules) != kept:
            raise RuntimeError(
                f"{folder.name} {name}: parse_rule_set kept {len(rules)} rules at "
                f"{condition}, the CSV has {len(kept)}."
            )
        used = {p for rule in rules.values() for p in rule.get_predicates()}
        missing = sorted(used - set(profiles))
        if missing:
            raise ValueError(
                f"{folder.name}: the rules use predicates the graph lacks: "
                f"{', '.join(missing)}."
            )
        kbgen = {
            r_id
            for r_id, rule in rules.items()
            if rule.get_predicates() <= kbgen_preds
        }
        # As cli/main.py: cycle removal only sees the complete rules (PCA confidence
        # 1), and the partial ones are all used.
        complete = {r_id: rule for r_id, rule in rules.items() if rule.is_complete}
        partial = set(rules) - set(complete)
        remove_minimal_cyclic_rules(
            complete, exempt=get_undirected_rule_ids(complete, profiles)
        )
        ours = set(complete) | partial
        levels.append(Level(name, share, threshold, kept, ours, kbgen))

    for level in levels:
        level.ours_same_as = stand_in(level, levels, "ours")
        level.kbgen_same_as = stand_in(level, levels, "kbgen")
    return len(confidences), levels


def stand_in(level: Level, levels: list[Level], generator: str) -> str:
    """Name of the level whose copy stands in for `level` with one generator, or ""
    if `level` gets a copy of its own: `r0` for an empty rule set, otherwise the
    highest level with the same rules."""
    rules = getattr(level, generator)
    same = [other.name for other in levels if getattr(other, generator) == rules]
    representative = "r0" if not rules else same[-1]
    return "" if representative == level.name else representative


def find_datasets(root: Path, names: list[str] | None) -> list[Path]:
    """Dataset folders under `root` with `<folder>.unified.tsv` and `<folder>.csv`,
    sorted by name, leaving out the copies' folders.

    Raises:
        ValueError: If a name is not such a folder, or `root` has none.
    """
    datasets = {
        folder.name: folder
        for folder in sorted(root.iterdir(), key=lambda p: p.name.lower())
        if folder.is_dir()
        and not folder.name.endswith(COPY_SUFFIXES)
        and not LEVEL_COPY.search(folder.name)
        and (folder / f"{folder.name}.unified.tsv").is_file()
        and (folder / f"{folder.name}.csv").is_file()
    }
    if not datasets:
        raise ValueError(f"No folder under {root} has a .unified.tsv and a .csv file.")
    if names is None:
        return list(datasets.values())
    missing = sorted(set(names) - set(datasets))
    if missing:
        raise ValueError(f"Not datasets under {root}: {', '.join(missing)}.")
    return [datasets[name] for name in datasets if name in names]


def format_share(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def threshold_text(level: Level) -> str:
    """The level's PCA threshold as written in the output, empty for r0."""
    return "" if level.threshold is None else str(level.threshold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="folder with one subfolder per dataset (default: %(default)s)",
    )
    parser.add_argument(
        "--datasets", nargs="+", help="dataset folders to include (default: every one)"
    )
    parser.add_argument(
        "--shares",
        type=float,
        nargs="+",
        default=list(DEFAULT_SHARES),
        help="shares of the rules kept by the levels between r0 and r100 "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output .tsv file (default: <root>/rule_levels.tsv)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    setup_logging(args.log_level)
    if logging.getLogger().level > logging.DEBUG:
        # parse_rule_set and the cycle removal log every rule they keep or remove.
        logging.getLogger("skgg.core").setLevel(logging.WARNING)

    shares = sorted(set(args.shares))
    if any(not 0 < s < 1 for s in shares):
        parser.error("--shares must be between 0 and 1, both excluded.")
    if len({level_name(s) for s in shares}) != len(shares):
        parser.error("--shares must differ by at least 0.01.")
    output = args.output or args.root / "rule_levels.tsv"

    try:
        folders = find_datasets(args.root, args.datasets)
    except ValueError as error:
        parser.error(str(error))

    rows = []
    for folder in folders:
        n_rules, levels = dataset_levels(folder, shares)
        for level in levels:
            rows.append(
                {
                    "dataset": folder.name,
                    "level": level.name,
                    "target_share": format_share(level.target_share),
                    "pca_threshold": threshold_text(level),
                    "rules_mined": n_rules,
                    "rules_kept": len(level.kept),
                    "share_kept": format_share(len(level.kept) / n_rules)
                    if n_rules
                    else "0",
                    "rules_ours": len(level.ours),
                    "rules_kbgen": len(level.kbgen),
                    "ours_same_as": level.ours_same_as,
                    "kbgen_same_as": level.kbgen_same_as,
                    "completeness": COMPLETENESS,
                }
            )
        logger.info(
            "%s (%d rules): %s",
            folder.name,
            n_rules,
            "; ".join(
                f"{level.name} PCA >= {threshold_text(level) or '-'} "
                f"{len(level.kept)}/{len(level.ours)}/{len(level.kbgen)}"
                for level in levels
            ),
        )
        removed = {
            level.name: sorted(level.kept - level.ours, key=rule_sort_key)
            for level in levels
        }
        logger.debug("%s rules removed from ours by level: %s", folder.name, removed)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    logger.info(
        "Wrote %d levels of %d datasets to %s (rules kept/ours/kbgen per level).",
        len(rows),
        len(folders),
        output,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
