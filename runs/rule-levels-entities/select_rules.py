#!/usr/bin/env python3
"""Writes the rules this project's generator would use at one rule level, without
generating its copy, so that kbgen can be given them (synthesize_exp_datasets.py
--rules-from-ours --used-rules-dir).

    python runs/rule-levels-entities/select_rules.py --datasets french --completeness pca --level pca1

The selection is `skgg.cli.rule_levels.dataset_levels`, the one `check_copies.py` checks
the generator's `_used.csv` files against: the level's threshold, completeness read
from std or PCA confidence (`--completeness`), and `remove_minimal_cyclic_rules` over
the complete rules, with the symmetric rules of undirected relations exempt. The rows
of `<root>/<D>/<D>.csv` with those rule IDs go to `<output>/<D>_ours_<level>_used.csv`
(`write_used_rules`, same columns and values). For `pca1` it checks that every rule
has PCA confidence 1, and for every level that the count equals `rules_ours` of the
setting's `rule_levels.tsv`.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path

from skgg.cli.rule_levels import DEFAULT_SHARES, PCA_ROOT, dataset_levels
from skgg.core.rules import rule_sort_key, write_used_rules

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--completeness", choices=["std", "pca"], default="pca")
    parser.add_argument("--level", default="pca1")
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument("--output", type=Path, default=HERE / "used-rules")
    args = parser.parse_args()

    levels_file = (PCA_ROOT if args.completeness == "pca" else args.root) / "rule_levels.tsv"
    with levels_file.open(encoding="utf-8", newline="") as f:
        expected = {(r["dataset"], r["level"]): r for r in csv.DictReader(f, delimiter="\t")}

    args.output.mkdir(parents=True, exist_ok=True)
    for name in args.datasets:
        folder = args.root / name
        _, levels = dataset_levels(folder, list(DEFAULT_SHARES), args.completeness)
        level = {lv.name: lv for lv in levels}[args.level]
        ids = sorted(level.ours, key=rule_sort_key)

        rules_file = folder / f"{name}.csv"
        with rules_file.open(encoding="utf-8", newline="") as f:
            rows = {r["rule_id"].strip(): r for r in csv.DictReader(f)}
        if args.level == "pca1":
            not_one = [i for i in ids if Decimal(rows[i]["pca_confidence"]) != 1]
            if not_one:
                raise SystemExit(f"{name}: rules without PCA confidence 1: {not_one}")
        row = expected[(name, args.level)]
        if len(ids) != int(row["rules_ours"]):
            raise SystemExit(f"{name}: {len(ids)} rules, {levels_file} says rules_ours {row['rules_ours']}")

        output = args.output / f"{name}_ours_{args.level}_used.csv"
        write_used_rules(rules_file, ids, output)
        print(f"{name} {args.level} ({args.completeness} setting): {len(level.kept)} rules kept, "
              f"{len(ids)} used -> {output}")
        for i in ids:
            r = rows[i]
            print(f"  {i}: {r['rule']}  std {r['std_confidence']}  pca {r['pca_confidence']}  "
                  f"support {r['positive_examples']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
