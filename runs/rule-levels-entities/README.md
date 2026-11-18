# kbgen's level copies with the source's entity count

The kbgen copies of `runs/rule-levels/` were synthesized with `synthesize.py -s 1.0` and no `-ne`, so kbgen made as many entities as the subjects of the graph it loads (`n_entities`, the rows of its type matrix), fewer than the source has. PyGraft was given the source's unique entities as `num_entities`, and this project's generator reads every entity by name from its profiles. These copies give kbgen the same number: `-ne` = the unique entities of the distinct triples of `<dataset>.unified.tsv`. Everything else is as in `runs/rule-levels/`: same code (upstream `e5d06bc` + `baselines/kbgen.patch`), model M3, `-s 1.0`, the rules this generator used at each level (`--rules-from-ours`), and the same tensor, M1 and M2, hard-linked from `runs/rule-levels/kbgen-work/<dataset>/` so only M3 and the synthesis were run again. The copies are in `exp_datasets_kbgen_entities/<dataset>_kbgen_<level>/`, each with its logs and records next to it (`.log`, `.base.log`, `.stats.json`, `.rules.txt`).

## Runs (18 November 2026, Hefesto)

Code at commit `c9a3d05e` (`--entities source`); `french_kbgen_pca1` at `c1b6e57c` (`--used-rules-dir`). `machine.txt` has the machine; the load average was about 10 of 20 threads, from other jobs.

```bash
# the five French datasets, levels std1 and r25 to r100 (21 copies), 13:34 to 14:03
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --datasets french french1_1 french1_N frenchN_1 frenchN_M --levels std1 r25 r50 r75 r100 --rules-from-ours --entities source --jobs 6 --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results
# french at pca1: the rules this generator uses in the PCA setting (complete = PCA confidence 1, after cycle removal)
python runs/rule-levels-entities/select_rules.py --datasets french --completeness pca --level pca1
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --rule-levels exp_datasets_pca/rule_levels.tsv --used-rules-dir runs/rule-levels-entities/used-rules --datasets french --levels pca1 --rules-from-ours --entities source --jobs 1 --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results
```

Both ran in tmux sessions; their output is in `kbgen-results/logs/`. `kbgen-results/rule-levels/summary.csv` has one row per copy (`entities_mode`, `desired_entities`, `rule_facts`, `given_rule_ids`, seconds of each step).

At pca1, french keeps 16 rules with PCA confidence 1, and this generator, treating them as complete, keeps 3 after cycle removal: 2 (`?b predecessor ?a => ?a successor ?b`), 3 (`?a father ?b => ?a parent ?b`) and 4 (`?a mother ?b => ?a parent ?b`), each with std and PCA confidence 1. They are the rules of std1, so `french_kbgen_pca1` and `french_kbgen_std1` have the same input. `french_kbgen_pca1`: 6,572 facts, 1,175 by rules, 2,606 of the 2,644 entities, 2.8 s of synthesis.

## Checks

`scripts/check_kbgen_levels.py --rules-from-ours --level-root exp_datasets_kbgen_entities` passes for the 21 copies (`exp_datasets/rule_levels.tsv`) and for `french_kbgen_pca1` (`exp_datasets_pca/rule_levels.tsv`): kbgen got the rule IDs this generator used, and each copy has as many facts as kbgen kept from its dataset.

## Comparison with the earlier copies

`python runs/rule-levels-entities/compare.py`. "Before" is the copy in `exp_datasets/`, "now" the one here; degrees are counted on distinct triples. Rule facts are kbgen's `count_rule_facts`; sampled facts are the rest.

| Copy | Desired before | Desired now | Entities source / before / now | Triples kept / before / now | Relations source / now | Rule facts before / now | Sampled facts before / now | Max degree source / before / now | Median degree source / before / now | Top 1% share source / before / now | Synthesis s before / now |
|---|---:|---:|---|---|---|---|---|---|---|---|---|
| french_kbgen_std1 | 2212 | 2644 | 2644 / 2196 / 2602 | 6572 / 6572 / 6572 | 11 / 9 | 1158 / 1161 | 5414 / 5411 | 1218 / 16 / 17 | 3 / 6 / 5 | 0.194 / 0.022 / 0.026 | 3 / 4 |
| french_kbgen_r25 | 2212 | 2644 | 2644 / 2148 / 2540 | 6572 / 6572 / 6572 | 11 / 9 | 2414 / 2334 | 4158 / 4238 | 1218 / 22 / 22 | 3 / 6 / 5 | 0.194 / 0.030 / 0.033 | 301 / 369 |
| french_kbgen_r50 | 2212 | 2644 | 2644 / 1968 / 2201 | 6572 / 6572 / 6572 | 11 / 9 | 4251 / 4282 | 2321 / 2290 | 1218 / 136 / 60 | 3 / 4 / 4 | 0.194 / 0.117 / 0.065 | 661 / 921 |
| french_kbgen_r75 | 2212 | 2644 | 2644 / 1523 / 1622 | 6572 / 6572 / 6572 | 11 / 9 | 5312 / 5337 | 1260 / 1235 | 1218 / 120 / 71 | 3 / 3 / 4 | 0.194 / 0.121 / 0.072 | 1079 / 1365 |
| french_kbgen_r100 | 2212 | 2644 | 2644 / 1520 / 1702 | 6572 / 6572 / 6572 | 11 / 9 | 5338 / 5285 | 1234 / 1287 | 1218 / 81 / 77 | 3 / 4 / 3 | 0.194 / 0.082 / 0.079 | 1068 / 1232 |
| french1_1_kbgen_r25 | 2212 | 2639 | 2639 / 2073 / 2392 | 4202 / 4202 / 4202 | 11 / 9 | 1386 / 1228 | 2816 / 2974 | 1218 / 21 / 17 | 2 / 4 / 3 | 0.232 / 0.031 / 0.032 | 277 / 285 |
| french1_1_kbgen_r50 | 2212 | 2639 | 2639 / 1800 / 2025 | 4202 / 4202 / 4202 | 11 / 9 | 2463 / 2419 | 1739 / 1783 | 1218 / 21 / 23 | 2 / 4 / 3 | 0.232 / 0.037 / 0.042 | 422 / 442 |
| french1_1_kbgen_r75 | 2212 | 2639 | 2639 / 1459 / 1640 | 4202 / 4202 / 4204 | 11 / 9 | 3119 / 3022 | 1083 / 1182 | 1218 / 24 / 26 | 2 / 4 / 3 | 0.232 / 0.036 / 0.041 | 638 / 671 |
| french1_1_kbgen_r100 | 2212 | 2639 | 2639 / 1488 / 1610 | 4202 / 4203 / 4203 | 11 / 9 | 3113 / 3083 | 1090 / 1120 | 1218 / 27 / 25 | 2 / 4 / 3 | 0.232 / 0.036 / 0.038 | 646 / 653 |
| french1_N_kbgen_r25 | 616 | 1325 | 1325 / 533 / 821 | 881 / 881 / 881 | 6 / 6 | 259 / 244 | 622 / 637 | 20 / 14 / 9 | 1 / 3 / 2 | 0.055 / 0.033 / 0.033 | 3 / 4 |
| french1_N_kbgen_r50 | 616 | 1325 | 1325 / 500 / 750 | 881 / 881 / 881 | 6 / 6 | 361 / 324 | 520 / 557 | 20 / 22 / 18 | 1 / 3 / 2 | 0.055 / 0.054 / 0.052 | 6 / 7 |
| french1_N_kbgen_r75 | 616 | 1325 | 1325 / 479 / 750 | 881 / 881 / 881 | 6 / 6 | 412 / 355 | 469 / 526 | 20 / 21 / 12 | 1 / 3 / 2 | 0.055 / 0.039 / 0.039 | 8 / 9 |
| french1_N_kbgen_r100 | 616 | 1325 | 1325 / 484 / 727 | 881 / 881 / 881 | 6 / 6 | 420 / 367 | 461 / 514 | 20 / 21 / 11 | 1 / 3 / 2 | 0.055 / 0.044 / 0.036 | 8 / 10 |
| frenchN_1_kbgen_r25 | 2212 | 2259 | 2259 / 1590 / 1579 | 1796 / 1796 / 1796 | 10 / 8 | 428 / 433 | 1368 / 1363 | 1218 / 14 / 12 | 2 / 2 / 2 | 0.350 / 0.043 / 0.040 | 45 / 49 |
| frenchN_1_kbgen_r50 | 2212 | 2259 | 2259 / 1140 / 1136 | 1796 / 1796 / 1796 | 10 / 8 | 983 / 1012 | 813 / 784 | 1218 / 34 / 41 | 2 / 2 / 2 | 0.350 / 0.079 / 0.094 | 79 / 78 |
| frenchN_1_kbgen_r75 | 2212 | 2259 | 2259 / 964 / 952 | 1796 / 1796 / 1796 | 10 / 8 | 1163 / 1185 | 633 / 611 | 1218 / 45 / 43 | 2 / 2 / 2 | 0.350 / 0.094 / 0.094 | 111 / 104 |
| frenchN_1_kbgen_r100 | 2212 | 2259 | 2259 / 981 / 951 | 1796 / 1796 / 1796 | 10 / 8 | 1178 / 1196 | 618 / 600 | 1218 / 36 / 45 | 2 / 2 / 2 | 0.350 / 0.085 / 0.092 | 123 / 116 |
| frenchN_M_kbgen_r25 | 1230 | 1675 | 1675 / 1122 / 1420 | 2887 / 2887 / 2887 | 5 / 5 | 1351 / 1287 | 1536 / 1600 | 24 / 23 / 17 | 3 / 4 / 3 | 0.043 / 0.036 / 0.034 | 59 / 65 |
| frenchN_M_kbgen_r50 | 1230 | 1675 | 1675 / 912 / 1084 | 2887 / 2889 / 2889 | 5 / 5 | 2082 / 2050 | 807 / 839 | 24 / 57 / 42 | 3 / 3 / 2 | 0.043 / 0.084 / 0.070 | 107 / 137 |
| frenchN_M_kbgen_r75 | 1230 | 1675 | 1675 / 841 / 1016 | 2887 / 2888 / 2887 | 5 / 5 | 2183 / 2105 | 705 / 782 | 24 / 64 / 64 | 3 / 3 / 3 | 0.043 / 0.082 / 0.097 | 166 / 186 |
| frenchN_M_kbgen_r100 | 1230 | 1675 | 1675 / 792 / 955 | 2887 / 2888 / 2888 | 5 / 5 | 2242 / 2176 | 646 / 712 | 24 / 59 / 65 | 3 / 3 / 3 | 0.043 / 0.068 / 0.098 | 178 / 220 |

- Each copy now uses more entities, closest to the source where kbgen's own count was lowest: french1_N 484-533 before, 727-821 now (source 1,325), french std1 2,196 before, 2,602 now (source 2,644). frenchN_1 hardly changes: its desired count only goes from 2,212 to 2,259.
- The facts are the same: kbgen still makes as many as it kept, and the split between rule and sampled facts moves by at most 10%.
- With more entities to draw from, the hubs made by rules shrink in french r50 and r75 (largest degree 136 and 120 before, 60 and 71 now) and french1_N; in frenchN_M they do not.
- kbgen still loses the relations whose objects are never subjects (`gender` and `hasSpouse` in french, french1_1 and frenchN_1), and with them the source's largest degrees (`No` 1,218, `Yes` 994 and `male` 578, objects of `hasSpouse` and `gender`). `-ne` cannot change this; it is a limitation of the baseline, left unchanged.

## Time for the other datasets

Synthesis time now / before, levels of at least 30 s: median 1.10 (min 0.94, max 1.39, 16 levels)

| Dataset | Levels | Synthesis, all levels | Longest level |
|---|---:|---:|---:|
| family | 5 | 26.1 h | 9.2 h |
| YAGO3-10_N_M | 4 | 24.1 h | 6.5 h |
| YAGO3-10 | 4 | 23.6 h | 6.6 h |
| YAGO3-10_N_1 | 4 | 16.5 h | 4.8 h |
| YAGO3-10_1_1 | 4 | 13.0 h | 3.8 h |
| YAGO3-10_1_N | 4 | 7.7 h | 2.6 h |
| WN18RR | 5 | 2.0 h | 40 min |
| WN18RRN_M | 5 | 1.8 h | 37 min |
| WN18RR1_1 | 4 | 1.5 h | 28 min |
| WN18RRN_1 | 4 | 49 min | 14 min |
| FamilyM_N | 4 | 20 min | 8 min |
| Family1_1 | 4 | 19 min | 7 min |
| SGKG1 | 1 | 12 min | 12 min |
| SGKG2 | 1 | 12 min | 12 min |
| SGKG3 | 1 | 11 min | 11 min |
| SGKG4 | 1 | 10 min | 10 min |
| Family1_N | 4 | 4 min | 2 min |
| FamilyN_1 | 4 | 3 min | 1 min |
| WN18RR1_N | 4 | 2 min | 0 min |

Total: 67 levels, 118.7 h of synthesis; longest level 9.2 h.
Wall time with 6 processes, longest level first: 19.8 h
Wall time with 8 processes, longest level first: 14.8 h
Wall time with 10 processes, longest level first: 12.2 h

The estimate scales each level's synthesis time of `runs/rule-levels/` by the median ratio measured on French. family at pca1 (26 rules against 18 at std1) is not in it; its std1 level took 12 minutes. family and the YAGO3-10 datasets dominate, so start each in its own command, longest levels first.
