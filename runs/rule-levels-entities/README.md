# kbgen's level copies with the source's entity count

The kbgen copies of `runs/rule-levels/` were synthesized with `synthesize.py -s 1.0` and no `-ne`, so kbgen made as many entities as the subjects of the graph it loads (`n_entities`, the rows of its type matrix), fewer than the source has. PyGraft was given the source's unique entities as `num_entities`, and this project's generator reads every entity by name from its profiles. These copies give kbgen the same number: `-ne` = the unique entities of the distinct triples of `<dataset>.unified.tsv`. Everything else is as in `runs/rule-levels/`: same code (upstream `e5d06bc` + `baselines/kbgen.patch`), model M3, `-s 1.0`, the rules this generator used at each level (`--rules-from-ours`), and the same tensor, M1 and M2, hard-linked from `runs/rule-levels/kbgen-work/<dataset>/` so only M3 and the synthesis were run again.

The copies are in `exp_datasets_kbgen_entities/<dataset>_kbgen_<level>/`, 89 in all: the 24 datasets at r25 to r100 (SGKG only at r100, its other levels have the same rules), family's std1, and pca1 for french, family, WN18RR and WN18RRN_M. Each has its logs and records next to it (`.log`, `.base.log`, `.stats.json`, `.rules.txt`). kbgen's records of every level are also in `kbgen-work/rule-levels/<dataset>-<level>/` and the driver's output in `kbgen-results/logs/`.

## pca1 and std1

pca1 is the level of the rules with PCA confidence 1 that this generator uses in its PCA setting (complete = PCA confidence 1, after cycle removal). `select_rules.py` picks them as `skgg.cli.rule_levels.dataset_levels` does, without making this generator's copy, checks that each has PCA confidence 1, and writes them to `used-rules/<dataset>_ours_pca1_used.csv`; the driver reads them with `--used-rules-dir`.

| Dataset | Rules used at pca1 | Same as std1 | Copy |
|---|---|---|---|
| french | 2, 3, 4 | yes | `french_kbgen_pca1` |
| WN18RR | 1, 2 | yes | `WN18RR_kbgen_pca1` |
| WN18RRN_M | 1, 2 | yes | `WN18RRN_M_kbgen_pca1` |
| family | 26 (std1's 18 plus 39, 41, 47, 54, 201, 202, 209, 210) | no | `family_kbgen_pca1`, and `family_kbgen_std1` |

The std1 copies of french, WN18RR and WN18RRN_M were also made, from the same rules as their pca1 copies, and removed (as in `exp_datasets/`, see "std1 copies renamed pca1" in `docs/rule-levels.md`); their records stay in `kbgen-work/rule-levels/`, and `summary.csv` leaves out a level whose copy no longer exists.

## Runs (18 November 2026, Hefesto)

`machine.txt` has the machine and the commit of each run. Hefesto was shared with another user's job during the runs.

```bash
# French, std1 and r25 to r100 (21 levels), 13:34 to 14:03, commit c9a3d05e, 6 processes
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --datasets french french1_1 french1_N frenchN_1 frenchN_M --levels std1 r25 r50 r75 r100 --rules-from-ours --entities source --jobs 6 --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results
# french pca1, 13:49, commit c1b6e57c
python runs/rule-levels-entities/select_rules.py --datasets french --completeness pca --level pca1
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --rule-levels exp_datasets_pca/rule_levels.tsv --used-rules-dir runs/rule-levels-entities/used-rules --datasets french --levels pca1 --rules-from-ours --entities source --jobs 1 --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results
# the other 19 datasets (67 levels), 16:44 to 23:50, commit c247bae4, 15 processes, longest levels first
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --rules-from-ours --entities source --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results --datasets family Family1_1 Family1_N FamilyM_N FamilyN_1 SGKG1 SGKG2 SGKG3 SGKG4 WN18RR WN18RR1_1 WN18RR1_N WN18RRN_1 WN18RRN_M YAGO3-10 YAGO3-10_1_1 YAGO3-10_1_N YAGO3-10_N_1 YAGO3-10_N_M --levels std1 r25 r50 r75 r100 --jobs 15 --order-from runs/rule-levels/kbgen-results/rule-levels/summary.csv
# pca1 of family, WN18RR and WN18RRN_M, 16:44 to 17:18, at the same time, 1 process
python runs/rule-levels-entities/select_rules.py --datasets family WN18RR WN18RRN_M --completeness pca --level pca1
~/.pyenv/versions/KG/bin/python runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets --level-root exp_datasets_kbgen_entities --rules-from-ours --entities source --work-dir runs/rule-levels-entities/kbgen-work --results-dir runs/rule-levels-entities/kbgen-results --datasets family WN18RR WN18RRN_M --levels pca1 --rule-levels exp_datasets_pca/rule_levels.tsv --used-rules-dir runs/rule-levels-entities/used-rules --jobs 1
```

The command of the 19 datasets exited with status 1 after every level had finished: `french_kbgen_std1` had been removed meanwhile and writing `degree_stats.csv` failed on it. The summary was rebuilt on 19 November with `--summary-only`, which now leaves out removed copies.

pca1 copies: family 28,262 facts (10,442 by rules), 3,007 of 3,007 entities, 1,350 s; WN18RR and WN18RRN_M 92,550 facts (18,198 and 18,197 by rules), 39,850 and 39,835 of 40,943 entities, 330 and 357 s; french 6,572 facts (1,175 by rules), 2,606 of 2,644 entities, 3 s.

## Checks

`scripts/check_kbgen_levels.py --rules-from-ours --level-root exp_datasets_kbgen_entities` with `exp_datasets/rule_levels.tsv` (std1, r25 to r100) and with `exp_datasets_pca/rule_levels.tsv` (pca1): kbgen got the rule IDs this generator used, and each copy has as many facts as kbgen kept from its dataset, within 0.1%, except `Family1_N_kbgen_r100`, 3,075 facts for 3,068 kept (0.23% more). The rules fired by the last random fact are not counted against kbgen's target; the copy of `runs/rule-levels/` of that level had 3,079. Every row of `summary.csv` has `desired_entities` equal to `input_entities`.

## Comparison with the earlier copies

`python runs/rule-levels-entities/compare.py`. "Before" is the copy in `exp_datasets/`, "now" the one here, for the levels made in both runs; degrees are counted on distinct triples. Rule facts are kbgen's `count_rule_facts`; sampled facts are the rest.

| Copy | Desired before | Desired now | Entities source / before / now | Triples kept / before / now | Relations source / now | Rule facts before / now | Sampled facts before / now | Max degree source / before / now | Median degree source / before / now | Top 1% share source / before / now | Synthesis s before / now |
|---|---:|---:|---|---|---|---|---|---|---|---|---|
| family_kbgen_std1 | 2994 | 3007 | 3007 / 2994 / 3007 | 28262 / 28262 / 28262 | 12 / 12 | 10148 / 10195 | 18114 / 18067 | 105 / 71 / 68 | 12 / 17 / 18 | 0.046 / 0.025 / 0.026 | 708 / 1125 |
| family_kbgen_r25 | 2994 | 3007 | 3007 / 2979 / 2982 | 28262 / 28262 / 28262 | 12 / 12 | 20468 / 21620 | 7794 / 6642 | 105 / 659 / 467 | 12 / 9 / 8 | 0.046 / 0.163 / 0.163 | 1929 / 4666 |
| family_kbgen_r50 | 2994 | 3007 | 3007 / 2541 / 2524 | 28262 / 28264 / 28262 | 12 / 12 | 25593 / 25591 | 2671 / 2671 | 105 / 467 / 447 | 12 / 4 / 4 | 0.046 / 0.183 / 0.164 | 30079 / 25564 |
| family_kbgen_r75 | 2994 | 3007 | 3007 / 2110 / 2028 | 28262 / 28262 / 28262 | 12 / 12 | 26511 / 26575 | 1751 / 1687 | 105 / 482 / 378 | 12 / 3 / 3 | 0.046 / 0.162 / 0.129 | 26268 / 21472 |
| family_kbgen_r100 | 2994 | 3007 | 3007 / 2061 / 2140 | 28262 / 28262 / 28262 | 12 / 12 | 26578 / 26436 | 1684 / 1826 | 105 / 232 / 387 | 12 / 3 / 2 | 0.046 / 0.078 / 0.135 | 26722 / 22416 |
| Family1_1_kbgen_r25 | 2430 | 2715 | 2715 / 2358 / 2592 | 5361 / 5361 / 5361 | 12 / 12 | 1191 / 1197 | 4170 / 4164 | 18 / 19 / 15 | 4 / 4 / 4 | 0.033 / 0.030 / 0.030 | 113 / 158 |
| Family1_1_kbgen_r50 | 2430 | 2715 | 2715 / 2256 / 2478 | 5361 / 5361 / 5361 | 12 / 12 | 2409 / 2313 | 2952 / 3048 | 18 / 28 / 23 | 4 / 4 / 3 | 0.033 / 0.044 / 0.042 | 204 / 315 |
| Family1_1_kbgen_r75 | 2430 | 2715 | 2715 / 2050 / 2168 | 5361 / 5361 / 5361 | 12 / 12 | 3295 / 3324 | 2066 / 2037 | 18 / 28 / 28 | 4 / 3 / 3 | 0.033 / 0.045 / 0.047 | 325 / 491 |
| Family1_1_kbgen_r100 | 2430 | 2715 | 2715 / 1673 / 1748 | 5361 / 5361 / 5361 | 12 / 12 | 4005 / 4029 | 1356 / 1332 | 18 / 36 / 37 | 4 / 3 / 3 | 0.033 / 0.047 / 0.049 | 374 / 527 |
| Family1_N_kbgen_r25 | 1310 | 1592 | 1592 / 1274 / 1538 | 3068 / 3068 / 3068 | 12 / 12 | 630 / 484 | 2438 / 2584 | 21 / 21 / 18 | 3 / 4 / 4 | 0.035 / 0.032 / 0.032 | 28 / 43 |
| Family1_N_kbgen_r50 | 1310 | 1592 | 1592 / 1189 / 1432 | 3068 / 3068 / 3068 | 12 / 12 | 1523 / 1236 | 1545 / 1832 | 21 / 142 / 115 | 3 / 3 / 3 | 0.035 / 0.196 / 0.151 | 44 / 58 |
| Family1_N_kbgen_r75 | 1310 | 1592 | 1592 / 802 / 864 | 3068 / 3068 / 3068 | 12 / 12 | 2430 / 2449 | 638 / 619 | 21 / 184 / 222 | 3 / 2 / 1 | 0.035 / 0.221 / 0.268 | 91 / 87 |
| Family1_N_kbgen_r100 | 1310 | 1592 | 1592 / 887 / 860 | 3068 / 3079 / 3075 | 12 / 12 | 2343 / 2447 | 736 / 628 | 21 / 152 / 169 | 3 / 2 / 1 | 0.035 / 0.183 / 0.200 | 82 / 91 |
| FamilyM_N_kbgen_r25 | 2134 | 2578 | 2578 / 1740 / 2093 | 5361 / 5361 / 5361 | 10 / 10 | 3535 / 3244 | 1826 / 2117 | 23 / 151 / 155 | 3 / 2 / 2 | 0.037 / 0.215 / 0.231 | 124 / 140 |
| FamilyM_N_kbgen_r50 | 2134 | 2578 | 2578 / 1573 / 1795 | 5361 / 5361 / 5363 | 10 / 10 | 3949 / 3819 | 1412 / 1544 | 23 / 146 / 134 | 3 / 2 / 2 | 0.037 / 0.175 / 0.170 | 231 / 352 |
| FamilyM_N_kbgen_r75 | 2134 | 2578 | 2578 / 1512 / 1654 | 5361 / 5361 / 5361 | 10 / 10 | 4045 / 4042 | 1316 / 1319 | 23 / 134 / 147 | 3 / 2 / 2 | 0.037 / 0.176 / 0.192 | 310 / 398 |
| FamilyM_N_kbgen_r100 | 2134 | 2578 | 2578 / 1365 / 1578 | 5361 / 5384 / 5361 | 10 / 10 | 4301 / 4150 | 1083 / 1211 | 23 / 253 / 106 | 3 / 2 / 2 | 0.037 / 0.274 / 0.140 | 431 / 472 |
| FamilyN_1_kbgen_r25 | 1218 | 1463 | 1463 / 1193 / 1400 | 2783 / 2784 / 2783 | 10 / 10 | 457 / 328 | 2327 / 2455 | 21 / 15 / 14 | 4 / 4 / 4 | 0.034 / 0.026 / 0.030 | 28 / 39 |
| FamilyN_1_kbgen_r50 | 1218 | 1463 | 1463 / 1125 / 1319 | 2783 / 2783 / 2783 | 10 / 10 | 1189 / 1085 | 1594 / 1698 | 21 / 51 / 33 | 4 / 3 / 3 | 0.034 / 0.077 / 0.059 | 45 / 57 |
| FamilyN_1_kbgen_r75 | 1218 | 1463 | 1463 / 948 / 1100 | 2783 / 2783 / 2783 | 10 / 10 | 1859 / 1707 | 924 / 1076 | 21 / 87 / 69 | 4 / 2 / 2 | 0.034 / 0.123 / 0.111 | 50 / 62 |
| FamilyN_1_kbgen_r100 | 1218 | 1463 | 1463 / 807 / 997 | 2783 / 2858 / 2784 | 10 / 10 | 2161 / 1932 | 697 / 852 | 21 / 84 / 78 | 4 / 2 / 2 | 0.034 / 0.104 / 0.111 | 53 / 80 |
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
| SGKG1_kbgen_r100 | 36450 | 36450 | 36450 / 36152 / 36135 | 72720 / 72721 / 72720 | 4 / 4 | 18516 / 18489 | 54205 / 54231 | 4 / 8 / 8 | 4 / 4 / 4 | 0.010 / 0.018 / 0.018 | 655 / 780 |
| SGKG2_kbgen_r100 | 36450 | 36450 | 36450 / 35430 / 35438 | 54585 / 54585 / 54585 | 4 / 4 | 2247 / 2199 | 52338 / 52386 | 3 / 8 / 8 | 3 / 3 / 3 | 0.010 / 0.022 / 0.022 | 644 / 783 |
| SGKG3_kbgen_r100 | 36450 | 36450 | 36450 / 35141 / 35145 | 54585 / 54585 / 54585 | 6 / 6 | 2125 / 2132 | 52460 / 52453 | 3 / 10 / 10 | 3 / 3 / 3 | 0.010 / 0.024 / 0.024 | 579 / 670 |
| SGKG4_kbgen_r100 | 36450 | 36450 | 36450 / 35211 / 35208 | 72720 / 72720 / 72720 | 6 / 6 | 21886 / 21841 | 50834 / 50879 | 4 / 12 / 12 | 4 / 4 / 4 | 0.010 / 0.023 / 0.023 | 559 / 609 |
| WN18RR_kbgen_r25 | 40701 | 40943 | 40943 / 39516 / 39814 | 92550 / 92550 / 92550 | 11 / 11 | 19588 / 19560 | 72962 / 72990 | 521 / 22 / 19 | 3 / 4 / 4 | 0.120 / 0.028 / 0.028 | 746 / 998 |
| WN18RR_kbgen_r50 | 40701 | 40943 | 40943 / 39531 / 39736 | 92550 / 92550 / 92550 | 11 / 11 | 20019 / 19971 | 72531 / 72579 | 521 / 19 / 19 | 3 / 4 / 4 | 0.120 / 0.027 / 0.028 | 1345 / 1877 |
| WN18RR_kbgen_r75 | 40701 | 40943 | 40943 / 39483 / 39647 | 92550 / 92550 / 92550 | 11 / 11 | 21334 / 21338 | 71216 / 71212 | 521 / 23 / 20 | 3 / 4 / 4 | 0.120 / 0.030 / 0.029 | 2199 / 3476 |
| WN18RR_kbgen_r100 | 40701 | 40943 | 40943 / 39465 / 39694 | 92550 / 92550 / 92550 | 11 / 11 | 21293 / 21307 | 71257 / 71243 | 521 / 21 / 22 | 3 / 4 / 4 | 0.120 / 0.029 / 0.029 | 2139 / 3557 |
| WN18RR1_1_kbgen_r25 | 40701 | 40835 | 40835 / 39325 / 39516 | 70699 / 70699 / 70699 | 11 / 11 | 1168 / 1145 | 69531 / 69554 | 425 / 13 / 14 | 2 / 3 / 3 | 0.130 / 0.026 / 0.027 | 658 / 1009 |
| WN18RR1_1_kbgen_r50 | 40701 | 40835 | 40835 / 39389 / 39527 | 70699 / 70699 / 70699 | 11 / 11 | 1496 / 1559 | 69203 / 69140 | 425 / 15 / 16 | 2 / 3 / 3 | 0.130 / 0.027 / 0.027 | 1249 / 1566 |
| WN18RR1_1_kbgen_r75 | 40701 | 40835 | 40835 / 38601 / 38640 | 70699 / 70699 / 70699 | 11 / 11 | 10912 / 10859 | 59787 / 59840 | 425 / 18 / 16 | 2 / 3 / 3 | 0.130 / 0.030 / 0.030 | 1355 / 1757 |
| WN18RR1_1_kbgen_r100 | 40701 | 40835 | 40835 / 38458 / 38628 | 70699 / 70699 / 70699 | 11 / 11 | 11236 / 11116 | 59463 / 59583 | 425 / 17 / 18 | 2 / 3 / 3 | 0.130 / 0.030 / 0.030 | 1549 / 2070 |
| WN18RR1_N_kbgen_r25 | 11582 | 26506 | 26506 / 10556 / 17199 | 21808 / 21809 / 21809 | 11 / 10 | 7932 / 7946 | 13877 / 13863 | 495 / 19 / 11 | 1 / 4 / 2 | 0.094 / 0.030 / 0.032 | 13 / 34 |
| WN18RR1_N_kbgen_r50 | 11582 | 26506 | 26506 / 10523 / 17219 | 21808 / 21809 / 21809 | 11 / 10 | 7965 / 7969 | 13844 / 13840 | 495 / 20 / 11 | 1 / 4 / 2 | 0.094 / 0.029 / 0.032 | 21 / 43 |
| WN18RR1_N_kbgen_r75 | 11582 | 26506 | 26506 / 10462 / 17131 | 21808 / 21808 / 21808 | 11 / 10 | 7953 / 7970 | 13855 / 13838 | 495 / 17 / 13 | 1 / 4 / 2 | 0.094 / 0.030 / 0.032 | 25 / 51 |
| WN18RR1_N_kbgen_r100 | 11582 | 26506 | 26506 / 10527 / 17167 | 21808 / 21808 / 21809 | 11 / 10 | 7961 / 7955 | 13847 / 13854 | 495 / 19 / 12 | 1 / 4 / 2 | 0.094 / 0.030 / 0.032 | 27 / 49 |
| WN18RRN_1_kbgen_r25 | 38151 | 38623 | 38623 / 35568 / 36018 | 63745 / 63745 / 63745 | 11 / 11 | 11884 / 11934 | 51861 / 51811 | 514 / 16 / 18 | 2 / 3 / 3 | 0.146 / 0.030 / 0.030 | 471 / 622 |
| WN18RRN_1_kbgen_r50 | 38151 | 38623 | 38623 / 35587 / 36022 | 63745 / 63745 / 63745 | 11 / 11 | 12160 / 12204 | 51585 / 51541 | 514 / 16 / 16 | 2 / 3 / 3 | 0.146 / 0.029 / 0.030 | 739 / 1197 |
| WN18RRN_1_kbgen_r75 | 38151 | 38623 | 38623 / 35505 / 35901 | 63745 / 63745 / 63745 | 11 / 11 | 12259 / 12245 | 51486 / 51500 | 514 / 17 / 16 | 2 / 3 / 3 | 0.146 / 0.030 / 0.030 | 746 / 1160 |
| WN18RRN_1_kbgen_r100 | 38151 | 38623 | 38623 / 35566 / 36002 | 63745 / 63745 / 63745 | 11 / 11 | 12219 / 12186 | 51526 / 51559 | 514 / 16 / 16 | 2 / 3 / 3 | 0.146 / 0.029 / 0.030 | 747 / 1054 |
| WN18RRN_M_kbgen_r25 | 40701 | 40943 | 40943 / 39593 / 39761 | 92550 / 92550 / 92550 | 11 / 11 | 19613 / 19608 | 72937 / 72942 | 521 / 20 / 19 | 3 / 4 / 4 | 0.120 / 0.027 / 0.027 | 588 / 921 |
| WN18RRN_M_kbgen_r50 | 40701 | 40943 | 40943 / 39584 / 39793 | 92550 / 92550 / 92550 | 11 / 11 | 20052 / 19996 | 72498 / 72554 | 521 / 20 / 20 | 3 / 4 / 4 | 0.120 / 0.028 / 0.027 | 1216 / 1926 |
| WN18RRN_M_kbgen_r75 | 40701 | 40943 | 40943 / 39479 / 39695 | 92550 / 92550 / 92550 | 11 / 11 | 21362 / 21373 | 71188 / 71177 | 521 / 22 / 23 | 3 / 4 / 4 | 0.120 / 0.030 / 0.030 | 1998 / 3251 |
| WN18RRN_M_kbgen_r100 | 40701 | 40943 | 40943 / 39421 / 39693 | 92550 / 92550 / 92550 | 11 / 11 | 21459 / 21376 | 71091 / 71174 | 521 / 21 / 21 | 3 / 4 / 4 | 0.120 / 0.030 / 0.030 | 2015 / 3450 |
| YAGO3-10_kbgen_r25 | 112981 | 123182 | 123182 / 112897 / 123008 | 530838 / 530838 / 530838 | 37 / 30 | 136498 / 136520 | 394340 / 394318 | 61604 / 30 / 28 | 10 / 9 / 8 | 0.251 / 0.022 / 0.023 | 16863 / 13559 |
| YAGO3-10_kbgen_r50 | 112981 | 123182 | 123182 / 112879 / 123002 | 530838 / 530838 / 530838 | 37 / 30 | 143456 / 143378 | 387382 / 387460 | 61604 / 31 / 29 | 10 / 9 / 8 | 0.251 / 0.023 / 0.023 | 18754 / 16324 |
| YAGO3-10_kbgen_r75 | 112981 | 123182 | 123182 / 112854 / 122934 | 530838 / 530838 / 530839 | 37 / 30 | 159111 / 157809 | 371727 / 373030 | 61604 / 31 / 33 | 10 / 9 / 8 | 0.251 / 0.023 / 0.024 | 20342 / 18417 |
| YAGO3-10_kbgen_r100 | 112981 | 123182 | 123182 / 112823 / 122863 | 530838 / 530838 / 530839 | 37 / 30 | 172436 / 170519 | 358402 / 360320 | 61604 / 41 / 42 | 10 / 9 / 8 | 0.251 / 0.025 / 0.026 | 21570 / 19537 |
| YAGO3-10_1_1_kbgen_r25 | 112760 | 122404 | 122404 / 108565 / 116521 | 188150 / 188150 / 188150 | 37 / 30 | 1999 / 1964 | 186151 / 186186 | 61601 / 15 / 14 | 4 / 3 / 3 | 0.267 / 0.027 / 0.027 | 8772 / 5690 |
| YAGO3-10_1_1_kbgen_r50 | 112760 | 122404 | 122404 / 108587 / 116482 | 188150 / 188150 / 188150 | 37 / 30 | 2668 / 2677 | 185482 / 185473 | 61601 / 14 / 14 | 4 / 3 / 3 | 0.267 / 0.027 / 0.027 | 9882 / 6429 |
| YAGO3-10_1_1_kbgen_r75 | 112760 | 122404 | 122404 / 108203 / 116216 | 188150 / 188150 / 188150 | 37 / 30 | 6068 / 6056 | 182082 / 182094 | 61601 / 17 / 14 | 4 / 3 / 3 | 0.267 / 0.027 / 0.028 | 11458 / 7551 |
| YAGO3-10_1_1_kbgen_r100 | 112760 | 122404 | 122404 / 107980 / 115687 | 188150 / 188150 / 188150 | 37 / 30 | 10003 / 9987 | 178147 / 178163 | 61601 / 25 / 16 | 4 / 3 / 3 | 0.267 / 0.029 / 0.029 | 12440 / 7742 |
| YAGO3-10_1_N_kbgen_r25 | 93053 | 114444 | 114444 / 89498 / 106458 | 192150 / 192150 / 192150 | 34 / 29 | 39913 / 39890 | 152237 / 152260 | 6976 / 19 / 19 | 9 / 4 / 3 | 0.241 / 0.028 / 0.030 | 3234 / 2460 |
| YAGO3-10_1_N_kbgen_r50 | 93053 | 114444 | 114444 / 89343 / 106114 | 192150 / 192150 / 192150 | 34 / 29 | 42373 / 42256 | 149777 / 149894 | 6976 / 20 / 18 | 9 / 4 / 3 | 0.241 / 0.028 / 0.030 | 5859 / 3041 |
| YAGO3-10_1_N_kbgen_r75 | 93053 | 114444 | 114444 / 87072 / 102544 | 192150 / 192162 / 192150 | 34 / 29 | 64907 / 62735 | 127255 / 129415 | 6976 / 61 / 29 | 9 / 4 / 3 | 0.241 / 0.045 / 0.038 | 7722 / 4335 |
| YAGO3-10_1_N_kbgen_r100 | 93053 | 114444 | 114444 / 87038 / 102440 | 192150 / 192150 / 192150 | 34 / 29 | 64588 / 62895 | 127562 / 129255 | 6976 / 37 / 38 | 9 / 4 / 3 | 0.241 / 0.039 / 0.039 | 8391 / 4880 |
| YAGO3-10_N_1_kbgen_r25 | 111990 | 121840 | 121840 / 111775 / 121446 | 478137 / 478137 / 478137 | 37 / 30 | 125918 / 125857 | 352219 / 352280 | 61601 / 31 / 27 | 10 / 8 / 8 | 0.261 / 0.023 / 0.024 | 10680 / 6975 |
| YAGO3-10_N_1_kbgen_r50 | 111990 | 121840 | 121840 / 111783 / 121404 | 478137 / 478137 / 478137 | 37 / 30 | 130470 / 130302 | 347667 / 347835 | 61601 / 29 / 28 | 10 / 8 / 8 | 0.261 / 0.023 / 0.024 | 13291 / 9074 |
| YAGO3-10_N_1_kbgen_r75 | 111990 | 121840 | 121840 / 111702 / 121368 | 478137 / 478138 / 478138 | 37 / 30 | 140171 / 139713 | 337967 / 338425 | 61601 / 34 / 27 | 10 / 8 / 8 | 0.261 / 0.024 / 0.024 | 14414 / 10263 |
| YAGO3-10_N_1_kbgen_r100 | 111990 | 121840 | 121840 / 111573 / 121220 | 478137 / 478138 / 478137 | 37 / 30 | 161846 / 159848 | 316292 / 318289 | 61601 / 66 / 70 | 10 / 8 / 7 | 0.261 / 0.032 / 0.032 | 15856 / 11861 |
| YAGO3-10_N_M_kbgen_r25 | 111508 | 121681 | 121681 / 111391 / 121466 | 532315 / 532316 / 532316 | 34 / 29 | 145803 / 145561 | 386513 / 386755 | 12471 / 31 / 29 | 10 / 9 / 8 | 0.237 / 0.023 / 0.023 | 17368 / 13285 |
| YAGO3-10_N_M_kbgen_r50 | 111508 | 121681 | 121681 / 111374 / 121467 | 532315 / 532316 / 532315 | 34 / 29 | 153415 / 153392 | 378901 / 378923 | 12471 / 35 / 33 | 10 / 9 / 8 | 0.237 / 0.023 / 0.024 | 19397 / 15849 |
| YAGO3-10_N_M_kbgen_r75 | 111508 | 121681 | 121681 / 111343 / 121413 | 532315 / 532316 / 532319 | 34 / 29 | 163333 / 162083 | 368983 / 370236 | 12471 / 36 / 36 | 10 / 9 / 8 | 0.237 / 0.023 / 0.024 | 21003 / 17636 |
| YAGO3-10_N_M_kbgen_r100 | 111508 | 121681 | 121681 / 111305 / 121322 | 532315 / 532316 / 532316 | 34 / 29 | 181409 / 179552 | 350907 / 352764 | 12471 / 48 / 40 | 10 / 9 / 8 | 0.237 / 0.025 / 0.025 | 21177 / 18621 |

- The copies use more entities, most where kbgen's own count was lowest: YAGO3-10, YAGO3-10_N_1 and YAGO3-10_N_M reach almost all of the source's (about 92% before), YAGO3-10_1_1 95% (89%), YAGO3-10_1_N 90 to 93% (76 to 78%), WN18RR1_N 65% (40%), french1_N 55 to 62% (36 to 40%). In the Family and French datasets the copies at r75 and r100 still use only 42 to 71% of the entities: rule facts link entities that already have facts.
- The facts are the same: kbgen makes as many as it kept, and the split between rule and sampled facts moves by a few percent.
- The degrees hardly change. The source's largest degrees (`No` with 1,218 in french, `male` with 61,604 in YAGO3-10) belong to the objects of relations kbgen drops when it loads the graph (`hasSpouse` and `gender` in french, `hasGender` and six more in YAGO3-10); `-ne` cannot bring them back. This is a limitation of the baseline, left unchanged.

## Time

Synthesis time now / before, levels of at least 30 s: median 1.13 (min 0.52, max 2.42, 75 levels)

Every level is made. Synthesis time per dataset, levels made in both runs:

| Dataset | Levels | Before | Now |
|---|---:|---:|---:|
| family | 5 | 23.8 h | 20.9 h |
| YAGO3-10 | 4 | 21.5 h | 18.8 h |
| YAGO3-10_N_M | 4 | 21.9 h | 18.2 h |
| YAGO3-10_N_1 | 4 | 15.1 h | 10.6 h |
| YAGO3-10_1_1 | 4 | 11.8 h | 7.6 h |
| YAGO3-10_1_N | 4 | 7.0 h | 4.1 h |
| WN18RR | 4 | 1.8 h | 2.8 h |
| WN18RRN_M | 4 | 1.6 h | 2.7 h |
| WN18RR1_1 | 4 | 1.3 h | 1.8 h |
| WN18RRN_1 | 4 | 45 min | 1.1 h |
| french | 4 | 52 min | 1.1 h |
| french1_1 | 4 | 33 min | 34 min |
| Family1_1 | 4 | 17 min | 25 min |
| FamilyM_N | 4 | 18 min | 23 min |
| SGKG2 | 1 | 11 min | 13 min |
| SGKG1 | 1 | 11 min | 13 min |
| SGKG3 | 1 | 10 min | 11 min |
| SGKG4 | 1 | 9 min | 10 min |
| frenchN_M | 4 | 8 min | 10 min |
| frenchN_1 | 4 | 6 min | 6 min |
| Family1_N | 4 | 4 min | 5 min |
| FamilyN_1 | 4 | 3 min | 4 min |
| WN18RR1_N | 4 | 1 min | 3 min |
| french1_N | 4 | 0 min | 0 min |

Total: 109.7 h before, 92.2 h now.

Wall time: 29 minutes for French with 6 processes, 7 h 06 min for the other 19 datasets with 15 processes (the longest level, family r50, took 7.1 h), and 34 minutes for the three pca1 levels.
