# Rule levels

kbgen (Melo and Paulheim, ESWC 2017) made its copies, `<dataset>_kbgen`, from every rule mined from a dataset, and this generator made `<dataset>_ours` from the rules with std confidence 1 only. 11 of the 24 datasets have no such rule: Family1_1, Family1_N, FamilyM_N, FamilyN_1, french1_N, frenchN_M, WN18RR1_1, WN18RR1_N, WN18RRN_1, YAGO3-10_1_1 and YAGO3-10_N_1. To measure how the amount of rules changes the copies and the models trained on them, both generators make each dataset again at six rule levels:

| Level | Rules given to the generator |
|---|---|
| `r0` | none |
| `std1` | the rules with std confidence 1, those `<dataset>_ours` uses |
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

`std1` has no PCA threshold: its column `std_threshold` is 1, and it keeps the rules with std confidence 1, as `cli/main.py` does without `--pca-conf`. PCA confidence is never below std confidence, so every share level keeps these rules too.

Ties can raise the share kept above the target. The four SGKG datasets have only rules of PCA confidence 1, so their `r25`, `r50` and `r75` keep every rule; WN18RR and WN18RRN_M keep 88% at `r75`. The analysis uses `share_kept`, not the level's name.

## Rules each generator uses

A generator does not use every rule it is given. This generator removes the fewest rules that leave the relation graph with no cycle (`core/cycles.remove_minimal_cyclic_rules`, see "Cyclic rule removal" in [`architecture.md`](architecture.md)), and recursive rules always go unless they are symmetric rules of an undirected relation. kbgen drops every triple whose object is never a subject before learning, and with it the rules over relations left without triples ([`benchmark-copies.md`](benchmark-copies.md), kbgen section). `skgg.cli.rule_levels` counts both, from `<dataset>.unified.tsv` without a graph database: `rules_ours` counts the rules as `cli/main.py` uses them: the complete rules (std confidence 1) left after its cycle removal, with the symmetric rules of `engine/edb.get_undirected_rule_ids` exempt, plus every partial rule (std confidence below 1), which cycle removal leaves alone (section 3.2.4 of [`algorithm.md`](algorithm.md)), and `rules_kbgen` keeps the rules whose relations all keep a triple. Its `rules_kbgen` at `r100` equals the rules kbgen parsed for the existing copies (`EXP_DATASETS.md` in the kbgen repository) for every dataset.

Each cell gives the threshold (none for `std1`), then the rules kept / used by this generator / used by kbgen when given every kept rule, with the code of 16 November 2026:

| Dataset | Rules | std1 | r25 | r50 | r75 | r100 |
|---|---:|---|---|---|---|---|
| family | 370 | 37 / 18 / 37 | 0.796: 93 / 74 / 93 | 0.5059: 185 / 166 / 185 | 0.412: 278 / 259 / 278 | 0: 370 / 351 / 370 |
| Family1_1 | 126 | 0 / 0 / 0 | 0.46: 33 / 33 / 33 | 0.273: 63 / 63 / 63 | 0.205: 95 / 95 / 95 | 0: 126 / 126 / 126 |
| Family1_N | 70 | 0 / 0 / 0 | 0.293: 18 / 18 / 18 | 0.229: 35 / 35 / 35 | 0.1897: 53 / 53 / 53 | 0: 70 / 70 / 70 |
| FamilyM_N | 92 | 0 / 0 / 0 | 0.29: 23 / 23 / 23 | 0.2371: 46 / 46 / 46 | 0.197: 69 / 69 / 69 | 0: 92 / 92 / 92 |
| FamilyN_1 | 61 | 0 / 0 / 0 | 0.27: 16 / 16 / 16 | 0.21: 31 / 31 / 31 | 0.1506: 46 / 46 / 46 | 0: 61 / 61 / 61 |
| french | 240 | 12 / 3 / 9 | 0.8882: 60 / 51 / 52 | 0.553: 120 / 111 / 100 | 0.409: 180 / 171 / 151 | 0: 240 / 231 / 210 |
| french1_1 | 223 | 6 / 0 / 3 | 0.8148: 56 / 50 / 44 | 0.504: 112 / 106 / 86 | 0.34: 168 / 162 / 138 | 0: 223 / 217 / 193 |
| french1_N | 15 | 0 / 0 / 0 | 0.47: 4 / 4 / 4 | 0.28: 8 / 8 / 8 | 0.26: 12 / 12 / 12 | 0: 15 / 15 / 15 |
| frenchN_1 | 132 | 2 / 0 / 0 | 0.61: 33 / 31 / 21 | 0.48: 66 / 64 / 43 | 0.33: 102 / 100 / 76 | 0: 132 / 130 / 106 |
| frenchN_M | 54 | 0 / 0 / 0 | 0.5: 14 / 14 / 14 | 0.36: 27 / 27 / 27 | 0.27: 41 / 41 / 41 | 0: 54 / 54 / 54 |
| SGKG1 | 4 | 4 / 2 / 4 | 1: 4 / 2 / 4 | 1: 4 / 2 / 4 | 1: 4 / 2 / 4 | 0: 4 / 2 / 4 |
| SGKG2 | 3 | 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 0: 3 / 1 / 3 |
| SGKG3 | 3 | 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 1: 3 / 1 / 3 | 0: 3 / 1 / 3 |
| SGKG4 | 6 | 6 / 4 / 6 | 1: 6 / 4 / 6 | 1: 6 / 4 / 6 | 1: 6 / 4 / 6 | 0: 6 / 4 / 6 |
| WN18RR | 24 | 2 / 2 / 2 | 0.8: 6 / 6 / 6 | 0.7: 12 / 12 / 12 | 0.3: 21 / 21 / 21 | 0: 24 / 24 / 24 |
| WN18RR1_1 | 17 | 0 / 0 / 0 | 0.85: 5 / 5 / 5 | 0.7: 9 / 9 / 9 | 0.3: 13 / 13 / 13 | 0: 17 / 17 / 17 |
| WN18RR1_N | 10 | 0 / 0 / 0 | 0.6: 3 / 3 / 3 | 0.2: 5 / 5 / 5 | 0.17: 8 / 8 / 8 | 0: 10 / 10 / 10 |
| WN18RRN_1 | 15 | 0 / 0 / 0 | 0.7: 4 / 4 / 4 | 0.2: 8 / 8 / 8 | 0.126: 12 / 12 / 12 | 0: 15 / 15 / 15 |
| WN18RRN_M | 24 | 2 / 2 / 2 | 0.8: 6 / 6 / 6 | 0.7: 12 / 12 / 12 | 0.3: 21 / 21 / 21 | 0: 24 / 24 / 24 |
| YAGO3-10 | 149 | 3 / 0 / 3 | 0.47: 38 / 35 / 28 | 0.3102: 75 / 72 / 63 | 0.19: 112 / 109 / 94 | 0: 149 / 146 / 125 |
| YAGO3-10_1_1 | 88 | 0 / 0 / 0 | 0.58: 22 / 22 / 15 | 0.397: 44 / 44 / 33 | 0.25: 66 / 66 / 54 | 0: 88 / 88 / 74 |
| YAGO3-10_1_N | 79 | 1 / 0 / 1 | 0.362: 20 / 19 / 18 | 0.28: 40 / 39 / 34 | 0.18: 60 / 59 / 49 | 0: 79 / 78 / 67 |
| YAGO3-10_N_1 | 119 | 0 / 0 / 0 | 0.5: 30 / 30 / 21 | 0.298: 60 / 60 / 50 | 0.172: 90 / 90 / 71 | 0: 119 / 119 / 95 |
| YAGO3-10_N_M | 133 | 3 / 0 / 3 | 0.46: 34 / 31 / 31 | 0.308: 67 / 64 / 63 | 0.177: 100 / 97 / 88 | 0: 133 / 130 / 117 |

Cycle removal only removes complete rules, so this generator drops few rules at the share levels: 9 of 240 on french at every level from `r25`, and none on the datasets without a complete rule. At `std1` it drops most of them: every one on french1_1, frenchN_1 and the YAGO3-10 datasets, and 9 of 12 on french. kbgen's loading drops more on the datasets with relations whose objects are never subjects, such as french (30 of 240 at `r100`) and the YAGO3-10 datasets. If a change to the generator changes which rules survive cycle removal, run `python -m skgg.cli.rule_levels` again before generating, so that `rules_ours` and `ours_same_as` follow it.

## Same rules for both generators

The two generators must see the same rules at each level, so kbgen is given the rules this generator used, not every rule the level keeps. `cli/generate_datasets.py --levels` saves the rules each run used next to the copy, as `<dataset>_ours_<level>/<dataset>_ours_<level>_used.csv` (written by `cli/main.py`, tracked by git), and kbgen's driver with `--rules-from-ours` reads the rule_ids of that file for each level, so this generator's copy of a level is made first. kbgen then drops the rules over the relations its loading empties, as for every copy, so it parses a subset of them; its summary gives both counts (`given_rules`, `parsed_rules`). kbgen's `r100` is then a level copy, `<dataset>_kbgen_r100`, made from the rules this generator used at `r100`, and the full copy `<dataset>_kbgen`, made from every rule, is not part of the comparison.

So far only french is made this way. The kbgen copies of the other datasets, made from every rule each level keeps, don't meet this and must be made again from this generator's rules.

## Levels that are not generated

A level whose rules, for one generator, equal those of another level would give a copy from the same input, so it is neither generated nor trained. `ours_same_as` and `kbgen_same_as` name the level whose copy stands in for it: `r0` for an empty rule set, otherwise the highest level with the same rules. With the code of 16 November 2026, this generator:

- `std1` stands in as `r0` on the 16 datasets where no complete rule survives cycle removal: the 11 without a std=1 rule, plus french1_1, frenchN_1, YAGO3-10, YAGO3-10_1_N and YAGO3-10_N_M.
- SGKG1 to SGKG4: `std1`, `r25`, `r50` and `r75` stand in as `r100`, since every rule has std confidence 1.

With the same rules for both generators, kbgen's driver (`--rules-from-ours`) skips the rows with `ours_same_as` too, so both generators make the same 112 level copies, 87 of them on the 19 datasets other than YAGO3-10. `kbgen_same_as` applies only to kbgen given every kept rule, where `r100` is the full copy `<dataset>_kbgen`.

## Layout

The copies of a level sit in `exp_datasets/` next to their dataset and its full copies, as `<dataset>_<generator>_<level>/<dataset>_<generator>_<level>.tsv`, so a folder listing reads `french`, `french_kbgen`, `french_kbgen_r0`, `french_kbgen_r25`, ..., `french_ours`, `french_ours_r0`, .... kbgen's `r100` has no folder of its own: it is the full copy, `french_kbgen`, which already uses every rule. This generator's full copy, `french_ours`, keeps only the rules with std confidence 1, so its `r100` is a level copy, `french_ours_r100`. The level is part of the name because `scripts/upload_results.py` keys the runs of the `kge-results` branch by dataset name and skips a run the branch already has, so a level copy named `french_ours` would never be uploaded.

The scripts that tell copies from datasets by their folder name handle the level copies as their own sets. `train_kge.py --set` and `kge_eta.py --set` take `ours-levels` and `kbgen-levels`, while `--set ours` and `--set kbgen` stay the full copies. `report_results.py` and `validation_eta.py` put a level copy in the family of its level, as the `kbgen` or `ours` variant of `french-r25` for `french_kbgen_r25`, so a level's plots compare the two generators at that level. `cli/rule_levels.py`, `cli/generate_datasets.py`, `mine_rules/mine_datasets.py` and kbgen's driver don't take a level copy for a dataset: the first and the driver skip its name, and the others need a `<folder>.unified.tsv` that it doesn't have.

## Making the copies

This generator: `cli/generate_datasets.py --levels` passes each dataset's `pca_threshold` from `rule_levels.tsv` to `cli/main.py --pca-conf` (0 for `r100`), runs `cli/main.py` without `--pca-conf` for `std1`, and for `r0` gives the pipeline a rules file with no rule. It writes each copy to `exp_datasets/<dataset>_ours_<level>/`, copies the rules the run used next to it as `<dataset>_ours_<level>_used.csv`, which git tracks, and skips a row with `ours_same_as`. The rules a level keeps are used as section 3.2.4 of `algorithm.md` describes: the rules with std confidence 1 are complete and forward-chained, after cycle removal; every other kept rule is partial, grounded round(PCA confidence × support) times after the complete rules and never forward-chained. The french dataset, as made on 16 November 2026:

```bash
python -m skgg.cli.generate_datasets --levels r0 std1 r25 r50 r75 r100 --force --log-dir logs/ours-levels --datasets french
python scripts/check_outputs.py --suffix _ours_r25 --datasets french
```

french's copies were made again on 16 November 2026 on Hefesto, at all six levels, each with the rules it used (`runs/french-rule-levels/` has the commands, logs and times). The copies of french1_1, french1_N, frenchN_1 and frenchN_M were made earlier that day on the development laptop, at the five levels other than `std1` and without their used rules, in 1 to 22 s each (logs in `logs/ours-levels/`, ignored by git), and split with `split_copies.py --suffix _ours_<level>`. `check_outputs.py` finds them well formed, with the source's entities, relations and self-loops. Every relation keeps its source frequency and degrees except `parent`, which the complete rules derive in completion. french's copies of Hefesto match the source exactly at `r0`; `parent` ends 47 triples short at `std1`, as in `french_ours`, 1 short at `r25` and `r75`, 3 short at `r100`, and has the right count but other degrees at `r50`. On the laptop's copies, frenchN_1 ends one `predecessor` triple short at r25.

Partial rules within 0.05 of their source PCA confidence, of the partial rules of each copy, and the median absolute difference:

| Dataset | r25 | r50 | r75 | r100 |
|---|---|---|---|---|
| french | 25 of 48 (0.029) | 38 of 108 (0.138) | 44 of 168 (0.240) | 58 of 228 (0.225) |
| french1_1 | 23 of 50 (0.068) | 30 of 106 (0.237) | 33 of 162 (0.260) | 43 of 217 (0.243) |
| french1_N | 0 of 4 (0.210) | 1 of 8 (0.214) | 1 of 12 (0.204) | 1 of 15 (0.196) |
| frenchN_1 | 4 of 31 (0.252) | 2 of 64 (0.255) | 6 of 100 (0.237) | 4 of 130 (0.215) |
| frenchN_M | 0 of 14 (0.214) | 5 of 27 (0.218) | 2 of 41 (0.214) | 1 of 54 (0.194) |

The rules of french1_N, frenchN_1 and frenchN_M have lower PCA confidences (their r25 thresholds are 0.47, 0.61 and 0.5), so even their r25 rules lose about 0.2 (section 8 of `algorithm.md`, item 11).

kbgen: the driver in `~/GitHub/kbgen` (local branch `exp-datasets`) takes `--levels`. For each dataset it makes the N-Triples file, tensor, M1 and M2 once in `work/<dataset>/`, which already holds them from the `_kbgen` copies, and links the tensor and M2 into `work/rule-levels/<dataset>-<level>/`. There it learns M3 from the rules of the level only and synthesizes the copy, so every level uses the M2 of the existing copy. It skips a row with `kbgen_same_as` and a level that keeps every rule, checks each level's rule count against `rules_kept`, writes each copy to `exp_datasets/<dataset>_kbgen_<level>/` (`--level-root` picks another folder), and writes its summary to `results/rule-levels/`, where a copy's row is named `<dataset>-<level>`. A test on french1_N gave the expected rule counts (0, 4, 8 and 12 parsed) and 881 facts at each level, as many as kbgen keeps from the dataset. `baselines/kbgen.patch` rebuilds that code from the authors' repository, and [`generate-kbgen-levels.md`](generate-kbgen-levels.md) gives the steps to make the copies on another machine, which datasets each machine makes, and `scripts/check_kbgen_levels.py`, which checks the copies against `rule_levels.tsv`. The 19 datasets other than YAGO3-10, on the development laptop:

```bash
cd ~/GitHub/kbgen
python synthesize_exp_datasets.py --levels r0 r25 r50 r75 --jobs 4 --datasets family Family1_1 Family1_N FamilyM_N FamilyN_1 french french1_1 french1_N frenchN_1 frenchN_M SGKG1 SGKG2 SGKG3 SGKG4 WN18RR WN18RR1_1 WN18RR1_N WN18RRN_1 WN18RRN_M
```

kbgen's time grows with the rules per relation (see "Times" in [`benchmark-copies.md`](benchmark-copies.md)), so each level should take less than the 10 h 58 min the all-rules copies of these 19 datasets took, summed over datasets.

## Splits and training

`scripts/split_copies.py` finds a copy's source by removing the suffix, so a suffix that includes the level resolves `french_ours_r50` to `french` without a code change. `--suffix _ours` doesn't match `french_ours_r50`, so the full copies and the level copies are split separately.

```bash
for level in r0 r25 r50 r75 r100; do
  python scripts/split_copies.py --suffix _ours_${level}
done
for level in r0 r25 r50 r75; do
  python scripts/split_copies.py --suffix _kbgen_${level}
done
```

The splits use seed 1235 and the source's shares of valid and test triples at every level, so the levels of a dataset differ only in their graph. Training runs on the training machine, with the usual settings:

```bash
python scripts/train_kge.py --set ours-levels --skip-models CompGCN --workers 4 --upload
python scripts/train_kge.py --set kbgen-levels --skip-models CompGCN --workers 4 --upload
```

`train_kge.py` trains only the folders that have splits, so the YAGO3-10 levels are left out until their copies are made. With 4 workers on an RTX 3090 a set of the 19 datasets other than YAGO3-10 takes about 11 hours ([`kge_training.md`](kge_training.md)), so the nine new sets take about 4 days, less with the levels that are not generated. Each set of the five YAGO3-10 datasets adds about 3.2 days.

## Reading the results

For each dataset and generator, the metrics of its copies (MRR, Hits@1, Hits@10) are compared across `share_kept`, from `r0` to `r100`, with the source dataset's own results as reference, and a second view against `rules_ours` and `rules_kbgen`. A level that is not generated takes the results of the level it names in `ours_same_as` or `kbgen_same_as`. The thresholds keep the rules of highest PCA confidence first, so the amount of rules and their confidence change together: a lower level has fewer rules and more confident ones.
