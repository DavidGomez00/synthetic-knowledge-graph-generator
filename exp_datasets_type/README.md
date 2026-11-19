# Benchmark datasets with their type triples

`scripts/merge_splits.py` left the `type` triples of the french datasets out of their unified files in `exp_datasets/`, so every split, rule set and copy made since has none. This folder holds the datasets with those triples kept. Nothing in `exp_datasets/` was changed.

Only three datasets had `type` triples in their original splits (commit `8c91d046`, unchanged until the unified files were written in `2757cf90`): `french`, `french1_1` and `frenchN_1`, 2,222 each. `french1_N`, `frenchN_M` and the other 19 datasets never had any.

| Dataset | Triples | `type` triples | Train | Test |
|---|---:|---:|---:|---:|
| french | 12,554 | 2,222 | 10,043 | 2,511 |
| french1_1 | 10,179 | 2,222 | 8,143 | 2,036 |
| frenchN_1 | 7,393 | 2,222 | 5,914 | 1,479 |

The `type` triples are 2,212 `<entity> type Person`, one for every subject of the graph, and 10 schema triples `<relation> type http://www.w3.org/1999/02/22_rdf_syntax_ns#Property` (for `child`, `father`, `gender`, `mother`, `name`, `parent`, `predecessor`, `spouse`, `successor` and `type`), kept as they were in the original splits.

## How they were made (2026-11-17)

```bash
for d in french french1_1 frenchN_1; do
  mkdir -p exp_datasets_type/$d
  for s in train valid test; do git show 8c91d046:exp_datasets/$d/$s > exp_datasets_type/$d/$s; done
done
python scripts/merge_splits.py exp_datasets_type --keep-types              # <d>/<d>.unified.tsv
python scripts/split_vanilla.py --root exp_datasets_type exp_datasets_type/*/*.unified.tsv   # 80/20 train and test, removes valid
```

Check: each `<d>.unified.tsv` here without its `type` lines is byte for byte the `<d>.unified.tsv` of `exp_datasets/`.

## Rules and copies (2026-11-19)

No rules were mined on these graphs. `<d>/<d>.csv` is a copy of `exp_datasets/<d>/<d>.csv`, mined without the `type` triples, so no rule uses `type`, and `rule_levels.tsv` has the rows of `exp_datasets/rule_levels.tsv` for these three datasets.

Next to each dataset are its rule-level copies by this generator and by kbgen, `<d>_ours_<level>/` and `<d>_kbgen_<level>/`, at pca1 and r25 to r100 (13 per generator; `french1_1` and `frenchN_1` use no rule at pca1). This generator copies the source's `type` triples (`cli/main.py --copy-relations type`); kbgen gives its own entities classes learned from them, and its copies keep every line of its output, its schema included. `runs/rule-levels-type/` has the script, the logs, the checks and a table of the copies. The copies are split 80/20 like the datasets (`python scripts/split_vanilla.py --root exp_datasets_type exp_datasets_type/*_ours_*/*.tsv exp_datasets_type/*_kbgen_*/*.tsv`, 2026-11-19).
