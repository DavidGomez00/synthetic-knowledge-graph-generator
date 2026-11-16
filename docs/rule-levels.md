# Rule levels

kbgen (Melo and Paulheim, ESWC 2017) made its copies, `<dataset>_kbgen`, from every rule mined from a dataset, and this generator made `<dataset>_ours` from the rules with std confidence 1 only. 11 of the 24 datasets have no such rule: Family1_1, Family1_N, FamilyM_N, FamilyN_1, french1_N, frenchN_M, WN18RR1_1, WN18RR1_N, WN18RRN_1, YAGO3-10_1_1 and YAGO3-10_N_1. To measure how the amount of rules changes the copies and the models trained on them, both generators make each dataset again at five rule levels:

| Level | Rules given to the generator |
|---|---|
| `r0` | none |
| `r25` | about 25% of the dataset's mined rules, those with the highest PCA confidence |
| `r50` | about 50% |
| `r75` | about 75% |
| `r100` | all of them (PCA confidence >= 0) |

`exp_datasets/rule_levels.tsv` holds each dataset's threshold at each level, and `python -m skgg.cli.rule_levels` writes it from the rules files.

## Choosing a threshold

A single PCA threshold for every dataset keeps very different shares of their rules: at PCA >= 0.5, Family1_N keeps 4 of its 70 rules (6%) and family 206 of its 370 (56%), and Family1_N, FamilyN_1 and french1_N have no rule above 0.72. So each level has its own threshold per dataset, chosen to keep the level's share of that dataset's rules. The rules are those of `<dataset>/<dataset>.csv`, the same input for both generators:

1. Sort the N rules by PCA confidence, descending. For share s, the k-th rule, with k = ceil(s × N), gives the boundary value b.
2. The threshold is b rounded down to the fewest decimals that stay above the next lower PCA confidence in the file. `pca_confidence >= threshold` then keeps exactly the rules with PCA confidence >= b: the first k, plus any rule tied with the k-th.
3. `r100` has threshold 0, and `r0` keeps no rule.

Ties can raise the share kept above the target. The four SGKG datasets have only rules of PCA confidence 1, so their `r25`, `r50` and `r75` keep every rule; WN18RR and WN18RRN_M keep 88% at `r75`. The analysis uses `share_kept`, not the level's name.

## Rules each generator uses

A generator does not use every rule it is given. This generator removes the fewest rules that leave the relation graph with no cycle (`core/cycles.remove_minimal_cyclic_rules`, see "Cyclic rule removal" in [`architecture.md`](architecture.md)), and recursive rules always go unless they are symmetric rules of an undirected relation. kbgen drops every triple whose object is never a subject before learning, and with it the rules over relations left without triples ([`benchmark-copies.md`](benchmark-copies.md), kbgen section). `skgg.cli.rule_levels` counts both, from `<dataset>.unified.tsv` without a graph database: `rules_ours` runs the cycle removal of `cli/main.py` on the level's rules, with the symmetric rules of `engine/edb.get_undirected_rule_ids` exempt, and `rules_kbgen` keeps the rules whose relations all keep a triple. Its `rules_kbgen` at `r100` equals the rules kbgen parsed for the existing copies (`EXP_DATASETS.md` in the kbgen repository) for every dataset.

Each cell gives the threshold, then the rules kept / used by this generator / used by kbgen, with the code of 16 November 2026:

| Dataset | Rules | r25 | r50 | r75 | r100 |
|---|---:|---|---|---|---|
| family | 370 | 0.796: 93 / 34 / 93 | 0.5059: 185 / 78 / 185 | 0.412: 278 / 111 / 278 | 0: 370 / 126 / 370 |
| Family1_1 | 126 | 0.46: 33 / 22 / 33 | 0.273: 63 / 33 / 63 | 0.205: 95 / 50 / 95 | 0: 126 / 62 / 126 |
| Family1_N | 70 | 0.293: 18 / 9 / 18 | 0.229: 35 / 16 / 35 | 0.1897: 53 / 18 / 53 | 0: 70 / 21 / 70 |
| FamilyM_N | 92 | 0.29: 23 / 10 / 23 | 0.2371: 46 / 22 / 46 | 0.197: 69 / 34 / 69 | 0: 92 / 38 / 92 |
| FamilyN_1 | 61 | 0.27: 16 / 8 / 16 | 0.21: 31 / 13 / 31 | 0.1506: 46 / 15 / 46 | 0: 61 / 20 / 61 |
| french | 240 | 0.8882: 60 / 16 / 52 | 0.553: 120 / 40 / 100 | 0.409: 180 / 59 / 151 | 0: 240 / 74 / 210 |
| french1_1 | 223 | 0.8148: 56 / 12 / 44 | 0.504: 112 / 30 / 86 | 0.34: 168 / 54 / 138 | 0: 223 / 67 / 193 |
| french1_N | 15 | 0.47: 4 / 2 / 4 | 0.28: 8 / 4 / 8 | 0.26: 12 / 7 / 12 | 0: 15 / 8 / 15 |
| frenchN_1 | 132 | 0.61: 33 / 13 / 21 | 0.48: 66 / 21 / 43 | 0.33: 102 / 35 / 76 | 0: 132 / 46 / 106 |
| frenchN_M | 54 | 0.5: 14 / 6 / 14 | 0.36: 27 / 14 / 27 | 0.27: 41 / 19 / 41 | 0: 54 / 22 / 54 |
| SGKG1 | 4 | 1: 4 / 2 / 4 | 1: 4 / 2 / 4 | 1: 4 / 2 / 4 | 0: 4 / 2 / 4 |
| SGKG2 | 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 0: 3 / 1 / 3 |
| SGKG3 | 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 0: 3 / 1 / 3 |
| SGKG4 | 6 | 1: 6 / 4 / 6 | 1: 6 / 4 / 6 | 1: 6 / 4 / 6 | 0: 6 / 4 / 6 |
| WN18RR | 24 | 0.8: 6 / 2 / 6 | 0.7: 12 / 2 / 12 | 0.3: 21 / 2 / 21 | 0: 24 / 2 / 24 |
| WN18RR1_1 | 17 | 0.85: 5 / 0 / 5 | 0.7: 9 / 0 / 9 | 0.3: 13 / 0 / 13 | 0: 17 / 0 / 17 |
| WN18RR1_N | 10 | 0.6: 3 / 0 / 3 | 0.2: 5 / 0 / 5 | 0.17: 8 / 0 / 8 | 0: 10 / 0 / 10 |
| WN18RRN_1 | 15 | 0.7: 4 / 0 / 4 | 0.2: 8 / 0 / 8 | 0.126: 12 / 0 / 12 | 0: 15 / 0 / 15 |
| WN18RRN_M | 24 | 0.8: 6 / 2 / 6 | 0.7: 12 / 2 / 12 | 0.3: 21 / 2 / 21 | 0: 24 / 2 / 24 |
| YAGO3-10 | 149 | 0.47: 38 / 6 / 28 | 0.3102: 75 / 17 / 63 | 0.19: 112 / 25 / 94 | 0: 149 / 35 / 125 |
| YAGO3-10_1_1 | 88 | 0.58: 22 / 4 / 15 | 0.397: 44 / 7 / 33 | 0.25: 66 / 17 / 54 | 0: 88 / 22 / 74 |
| YAGO3-10_1_N | 79 | 0.362: 20 / 4 / 18 | 0.28: 40 / 8 / 34 | 0.18: 60 / 12 / 49 | 0: 79 / 18 / 67 |
| YAGO3-10_N_1 | 119 | 0.5: 30 / 4 / 21 | 0.298: 60 / 15 / 50 | 0.172: 90 / 20 / 71 | 0: 119 / 29 / 95 |
| YAGO3-10_N_M | 133 | 0.46: 34 / 7 / 31 | 0.308: 67 / 17 / 63 | 0.177: 100 / 24 / 88 | 0: 133 / 31 / 117 |

At `r100` this generator uses 30 to 53% of the rules it is given on the French and Family datasets, and 23 to 25% on the YAGO3-10 datasets. On WN18RR1_1, WN18RR1_N and WN18RRN_1 it uses no rule at any level, and on WN18RR and WN18RRN_M the same 2 rules from `r25` to `r100`, so those datasets cannot show an effect of the amount of rules on this generator. If a change to the generator changes which rules survive cycle removal, run `python -m skgg.cli.rule_levels` again before generating, so that `rules_ours` and `ours_same_as` follow it.

## Levels that are not generated

A level whose rules, for one generator, equal those of another level would give a copy from the same input, so it is neither generated nor trained. `ours_same_as` and `kbgen_same_as` name the level whose copy stands in for it: `r0` for an empty rule set, otherwise the highest level with the same rules. For kbgen that is `r100`, the existing `<dataset>_kbgen` copy, which is never made again. With the code of 16 November 2026:

- SGKG1 to SGKG4: `r25`, `r50` and `r75` stand in as `r100`, for both generators.
- This generator on WN18RR1_1, WN18RR1_N and WN18RRN_1: `r25` to `r100` stand in as `r0`.
- This generator on WN18RR and WN18RRN_M: `r25`, `r50` and `r75` stand in as `r100`.

For the 19 datasets other than YAGO3-10, that leaves 129 of the 171 copies of the nine new sets (this generator at five levels, kbgen at four) to generate and train.

## Layout

The copies of a level go to `exp_datasets/rule-levels/<dataset>-<level>_<generator>/<dataset>-<level>_<generator>.tsv`, for example `french-r50_ours/` and `french-r0_kbgen/`. The level is part of the name because `scripts/upload_results.py` keys the runs of the `kge-results` branch by dataset name and skips a run the branch already has, so a level copy named `french_ours` would never be uploaded. The names still end in `_ours` and `_kbgen`, so `train_kge.py --set`, `kge_eta.py` and `validation_eta.py` handle them as copies.

## Making the copies

This generator: `cli/generate_datasets.py` does not take a level yet. It needs to pass each dataset's `pca_threshold` from `rule_levels.tsv` to `cli/main.py --pca-conf` (and no rule at all for `r0`), write to the layout above, skip a row with `ours_same_as`, and keep each copy's used rules: `core/rules.write_used_rules` writes `<dataset>/<dataset>_used.csv`, which every level overwrites, so it should be copied next to the level's copy.

kbgen: the driver in `~/GitHub/kbgen` (local branch `exp-datasets`) takes `--levels`. For each dataset it makes the N-Triples file, tensor, M1 and M2 once in `work/<dataset>/`, which already holds them from the `_kbgen` copies, and links the tensor and M2 into `work/rule-levels/<dataset>-<level>/`. There it learns M3 from the rules of the level only and synthesizes the copy, so every level uses the M2 of the existing copy. It skips a row with `kbgen_same_as` and a level that keeps every rule, checks each level's rule count against `rules_kept`, and writes its summary to `results/rule-levels/`. A test on french1_N gave the expected rule counts (0, 4, 8 and 12 parsed) and 881 facts at each level, as many as kbgen keeps from the dataset. `baselines/kbgen.patch` rebuilds that code from the authors' repository, and [`generate-kbgen-levels.md`](generate-kbgen-levels.md) gives the steps to make the copies on another machine, which datasets each machine makes, and `scripts/check_kbgen_levels.py`, which checks the copies against `rule_levels.tsv`. The 19 datasets other than YAGO3-10, on the development laptop:

```bash
cd ~/GitHub/kbgen
python synthesize_exp_datasets.py --levels r0 r25 r50 r75 --jobs 4 --datasets family Family1_1 Family1_N FamilyM_N FamilyN_1 french french1_1 french1_N frenchN_1 frenchN_M SGKG1 SGKG2 SGKG3 SGKG4 WN18RR WN18RR1_1 WN18RR1_N WN18RRN_1 WN18RRN_M
```

kbgen's time grows with the rules per relation (see "Times" in [`benchmark-copies.md`](benchmark-copies.md)), so each level should take less than the 10 h 58 min the all-rules copies of these 19 datasets took, summed over datasets.

## Splits and training

`scripts/split_copies.py` finds a copy's source by removing the suffix, so a suffix that includes the level resolves `french-r50_ours` to `french` without a code change. The suffix starts with a dash, so it must be passed as `--suffix=-r50_ours`: with a space, argparse takes it for an option.

```bash
for level in r0 r25 r50 r75 r100; do
  python scripts/split_copies.py --root exp_datasets/rule-levels --sources exp_datasets --suffix=-${level}_ours
done
for level in r0 r25 r50 r75; do
  python scripts/split_copies.py --root exp_datasets/rule-levels --sources exp_datasets --suffix=-${level}_kbgen
done
```

The splits use seed 1235 and the source's shares of valid and test triples at every level, so the levels of a dataset differ only in their graph. Training runs on the training machine, with the usual settings:

```bash
python scripts/train_kge.py --root exp_datasets/rule-levels --set ours --skip-models CompGCN --workers 4 --upload
python scripts/train_kge.py --root exp_datasets/rule-levels --set kbgen --skip-models CompGCN --workers 4 --upload
```

`train_kge.py` trains only the folders that have splits, so the YAGO3-10 levels are left out until their copies are made. With 4 workers on an RTX 3090 a set of the 19 datasets other than YAGO3-10 takes about 11 hours ([`kge_training.md`](kge_training.md)), so the nine new sets take about 4 days, less with the levels that are not generated. Each set of the five YAGO3-10 datasets adds about 3.2 days.

## Reading the results

For each dataset and generator, the metrics of its copies (MRR, Hits@1, Hits@10) are compared across `share_kept`, from `r0` to `r100`, with the source dataset's own results as reference, and a second view against `rules_ours` and `rules_kbgen`. A level that is not generated takes the results of the level it names in `ours_same_as` or `kbgen_same_as`. The thresholds keep the rules of highest PCA confidence first, so the amount of rules and their confidence change together: a lower level has fewer rules and more confident ones.
