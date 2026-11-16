"""Checks the kbgen copies of the rule-level experiment against rule_levels.tsv.

For every dataset and level that kbgen's driver should have made (every row of
`exp_datasets/rule_levels.tsv` except r100 and the rows with `kbgen_same_as`), of the
datasets named with --datasets or else of those in kbgen's summary, this checks:

- the copy `<level root>/<dataset>-<level>_kbgen/<dataset>-<level>_kbgen.tsv` exists
  and has as many lines as the summary's `synthetic_triples`;
- kbgen was given `rules_kept` rules and parsed `rules_kbgen` of them;
- the copy has as many facts as kbgen kept from the source, give or take 0.1%
  (the all-rules copies have exactly that many or one more), and loses no relation;
- with --splits, the copy has its `train`, `valid` and `test` files.

The summary is `results/rule-levels/summary.csv` in the kbgen repository, written by
`synthesize_exp_datasets.py --levels`. Exits with status 1 if a check fails.

    S=~/kbgen/results/rule-levels/summary.csv
    python scripts/check_kbgen_levels.py --summary $S
    python scripts/check_kbgen_levels.py --summary $S --splits --datasets french

Needs only the Python standard library.
"""

import argparse
import csv
import sys
from pathlib import Path

SPLITS = ("train", "valid", "test")
TOLERANCE = 0.001


def check(
    row: dict[str, str], stats: dict[str, str] | None, copy: Path, splits: bool
) -> list[str]:
    """Problems of one level copy, empty if it passes."""
    problems = []
    if not copy.is_file():
        problems.append(f"no copy {copy}")
    if stats is None:
        return [*problems, "not in the summary"]
    if int(stats["given_rules"]) != int(row["rules_kept"]):
        problems.append(
            f"given {stats['given_rules']} rules, rule_levels.tsv keeps "
            f"{row['rules_kept']}"
        )
    if int(stats["parsed_rules"]) != int(row["rules_kbgen"]):
        problems.append(
            f"parsed {stats['parsed_rules']} rules, rule_levels.tsv expects "
            f"{row['rules_kbgen']}"
        )
    ratio = float(stats["synthetic_vs_kept"])
    if abs(ratio - 1) > TOLERANCE:
        problems.append(
            f"{stats['synthetic_triples']} facts for {stats['kept_triples']} kept "
            f"({ratio:.4f})"
        )
    if stats["relations_lost_in_synthesis"]:
        lost = stats["relations_lost_in_synthesis"]
        problems.append(f"relations lost in synthesis: {lost}")
    if copy.is_file():
        with copy.open(encoding="utf-8") as f:
            lines = sum(1 for _ in f)
        facts = int(stats["synthetic_triples"])
        if lines != facts:
            problems.append(f"copy has {lines} lines, the summary {facts} facts")
    if splits:
        missing = [s for s in SPLITS if not (copy.parent / s).is_file()]
        if missing:
            problems.append(f"no {', '.join(missing)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="kbgen's results/rule-levels/summary.csv",
    )
    parser.add_argument(
        "--levels-file", type=Path, default=Path("exp_datasets/rule_levels.tsv")
    )
    parser.add_argument(
        "--level-root", type=Path, default=Path("exp_datasets/rule-levels")
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        help="datasets to check (default: those in the summary)",
    )
    parser.add_argument(
        "--splits",
        action="store_true",
        help="also require the train, valid and test files",
    )
    args = parser.parse_args()

    with args.levels_file.open(encoding="utf-8", newline="") as f:
        levels = list(csv.DictReader(f, delimiter="\t"))
    with args.summary.open(encoding="utf-8", newline="") as f:
        summary = {r["dataset"]: r for r in csv.DictReader(f)}
    known = {r["dataset"] for r in levels}
    if args.datasets:
        unknown = sorted(set(args.datasets) - known)
        if unknown:
            parser.error(f"Not in {args.levels_file}: {', '.join(unknown)}.")
        datasets = set(args.datasets)
    else:
        datasets = {d for d in known if any(t.startswith(f"{d}-r") for t in summary)}

    failed = checked = 0
    for row in levels:
        if (
            row["dataset"] not in datasets
            or row["level"] == "r100"
            or row["kbgen_same_as"]
        ):
            continue
        tag = f"{row['dataset']}-{row['level']}"
        copy = args.level_root / f"{tag}_kbgen" / f"{tag}_kbgen.tsv"
        problems = check(row, summary.get(tag), copy, args.splits)
        checked += 1
        if problems:
            failed += 1
            print(f"FAIL {tag}: {'; '.join(problems)}")
        else:
            stats = summary[tag]
            facts, rules = stats["synthetic_triples"], stats["parsed_rules"]
            print(f"ok   {tag}: {facts} facts, {rules} rules")
    print(f"{checked - failed} of {checked} copies pass.")
    return 1 if failed or not checked else 0


if __name__ == "__main__":
    sys.exit(main())
