"""Measures how much of the family structure of `family` a graph keeps.

The generator keeps each relation's frequency and degrees, but draws the
extensional relations independently, so a copy of `family` loses how they line
up: a child's father and mother form a couple, and siblings share their parents.
For each `.tsv` file given, this script prints:

- the couples (a father and a mother of the same child) and the children per
  couple;
- the sibling facts (`brother` and `sister`) whose two people share a parent,
  out of those where both have a parent;
- the `son` and `daughter` facts `(c, p)` where `p` is a father or mother of `c`,
  as in the source, where every such fact points to a parent.

With `--chain RULES`, it also forward-chains the complete rules of the rules CSV
`RULES` (std confidence 1, or PCA confidence 1 with `--completeness pca`) over
each file until nothing is added, and prints the relations that grow. Run on the
source, it shows what the rules derive where the family structure is intact. See
`docs/family-growth.md`. Only the Python standard library is used.

    python scripts/family_structure.py exp_datasets/family/family.unified.tsv \\
        exp_datasets/family_ours_std1/family_ours_std1.tsv
    python scripts/family_structure.py --completeness pca \\
        --chain exp_datasets_pca/family_ours_pca1/family_ours_pca1_used.csv \\
        exp_datasets/family/family.unified.tsv
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from rule_witnesses import Index, body_groundings, parse_atoms, read_index


def structure(index: Index) -> list[str]:
    """Returns the lines of the structure counts of one graph."""
    fathers: dict[str, set[str]] = defaultdict(set)
    mothers: dict[str, set[str]] = defaultdict(set)
    for parent, child in index.get("father", set()):
        fathers[child].add(parent)
    for parent, child in index.get("mother", set()):
        mothers[child].add(parent)

    children_per_couple: dict[tuple[str, str], int] = defaultdict(int)
    for child in fathers.keys() & mothers.keys():
        for father in fathers[child]:
            for mother in mothers[child]:
                children_per_couple[(father, mother)] += 1
    couples = len(children_per_couple)
    children = sum(children_per_couple.values())

    def parents(person: str) -> set[str]:
        return fathers.get(person, set()) | mothers.get(person, set())

    siblings = index.get("brother", set()) | index.get("sister", set())
    with_parents = [(x, y) for x, y in siblings if parents(x) and parents(y)]
    sharing = sum(1 for x, y in with_parents if parents(x) & parents(y))

    lines = [
        f"  couples: {couples:,}, {children / couples if couples else 0:.2f} "
        "children per couple",
        f"  sibling facts sharing a parent: {sharing:,} of {len(with_parents):,} "
        f"with a parent on both sides ({len(siblings):,} sibling facts)",
    ]
    for relation in ("son", "daughter"):
        facts = index.get(relation, set())
        to_parent = sum(1 for child, parent in facts if parent in parents(child))
        lines.append(
            f"  {relation} facts pointing to a parent: {to_parent:,} of {len(facts):,}"
        )
    return lines


def complete_rules(rules_file: Path, completeness: str) -> list[tuple[list, tuple]]:
    """The body and head of each rule of `rules_file` that is complete in the
    setting: confidence 1 on `std_confidence` or `pca_confidence`."""
    column = f"{completeness}_confidence"
    with rules_file.open(encoding="utf-8", newline="") as handle:
        return [
            (parse_atoms(row["body"]), parse_atoms(row["head"])[0])
            for row in csv.DictReader(handle)
            if float(row[column]) >= 1
        ]


def chain(index: Index, rules: list[tuple[list, tuple]]) -> None:
    """Adds every head the rules derive, pass after pass, until none is new."""
    added = True
    while added:
        added = False
        for body, (subject, relation, obj) in rules:
            for binding in body_groundings(index, body):
                pair = (binding.get(subject, subject), binding.get(obj, obj))
                if pair not in index[relation]:
                    index[relation].add(pair)
                    added = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("files", type=Path, nargs="+", help=".tsv graphs to measure")
    parser.add_argument(
        "--chain",
        type=Path,
        help="rules CSV whose complete rules are forward-chained over each graph",
    )
    parser.add_argument(
        "--completeness",
        choices=("std", "pca"),
        default="std",
        help="confidence that makes a rule of --chain complete (default: %(default)s)",
    )
    args = parser.parse_args()

    rules = complete_rules(args.chain, args.completeness) if args.chain else []
    for path in args.files:
        index = read_index(path)
        total = sum(len(pairs) for pairs in index.values())
        print(f"{path}: {total:,} triples")
        print("\n".join(structure(index)))
        if args.chain:
            before = {relation: len(pairs) for relation, pairs in index.items()}
            chain(index, rules)
            after = {relation: len(pairs) for relation, pairs in index.items()}
            grown = [r for r in sorted(after) if after[r] != before.get(r, 0)]
            print(
                f"  forward-chaining the {len(rules)} complete rules "
                f"({args.completeness} setting) of {args.chain}: {total:,} -> "
                f"{sum(after.values()):,} triples"
                + (": " if grown else "")
                + ", ".join(f"{r} {before.get(r, 0):,} -> {after[r]:,}" for r in grown)
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
