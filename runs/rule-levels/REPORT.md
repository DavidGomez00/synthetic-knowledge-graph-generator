# Rule-level copies: results

Written by `make_report.py` on 2026-11-16 19:48 from `status.tsv`, `ours/times.tsv`, `kbgen-results/rule-levels/summary.csv` and `kbgen-work/<dataset>/timings.json`. README.md says how the copies were made.

## Machine

```
machine.txt missing: run reproduce.sh record
```

## Copies

*Filter* is what a rule needs to be kept at the level. *Used* is the rules our generator used, the rules kbgen was given; *parsed* is those kbgen could apply (a rule whose relation kbgen's tensor lacks is dropped). A time covers one copy: for ours the upload of the source to GraphDB, `cli/main.py` and the download; for kbgen M3, `synthesize.py` and writing the copy, without the shared steps of the next table.

| Dataset | Level | Filter | Rules kept | Used | kbgen parsed | Source triples | Ours triples | Ours time | kbgen triples | kbgen time |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| family | std1 | std = 1 | 37 | 18 |  |  |  |  |  |  |
| family | r25 | PCA ≥ 0.796 | 93 | 74 |  |  |  |  | 28,262 |  |
| family | r50 | PCA ≥ 0.5059 | 185 | 166 |  |  |  |  |  |  |
| family | r75 | PCA ≥ 0.412 | 278 | 259 |  |  |  |  | 28,265 |  |
| family | r100 | PCA ≥ 0 | 370 | 351 |  |  |  |  |  |  |
| Family1_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| Family1_1 | r25 | PCA ≥ 0.46 | 33 | 33 | 33 |  |  |  | 5,361 | 1 min 44 s |
| Family1_1 | r50 | PCA ≥ 0.273 | 63 | 63 | 63 |  |  |  | 5,361 | 3 min 16 s |
| Family1_1 | r75 | PCA ≥ 0.205 | 95 | 95 | 95 |  |  |  | 5,361 | 5 min 8 s |
| Family1_1 | r100 | PCA ≥ 0 | 126 | 126 |  |  |  |  |  |  |
| Family1_N | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| Family1_N | r25 | PCA ≥ 0.293 | 18 | 18 | 18 |  |  |  | 3,068 | 30 s |
| Family1_N | r50 | PCA ≥ 0.229 | 35 | 35 | 35 |  |  |  | 3,068 | 41 s |
| Family1_N | r75 | PCA ≥ 0.1897 | 53 | 53 | 53 |  |  |  | 3,069 | 1 min 2 s |
| Family1_N | r100 | PCA ≥ 0 | 70 | 70 |  |  |  |  |  |  |
| FamilyM_N | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| FamilyM_N | r25 | PCA ≥ 0.29 | 23 | 23 | 23 |  |  |  | 5,361 | 1 min 55 s |
| FamilyM_N | r50 | PCA ≥ 0.2371 | 46 | 46 | 46 |  |  |  | 5,361 | 3 min 30 s |
| FamilyM_N | r75 | PCA ≥ 0.197 | 69 | 69 | 69 |  |  |  | 5,361 | 4 min 42 s |
| FamilyM_N | r100 | PCA ≥ 0 | 92 | 92 |  |  |  |  |  |  |
| FamilyN_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| FamilyN_1 | r25 | PCA ≥ 0.27 | 16 | 16 | 16 |  |  |  | 2,783 | 29 s |
| FamilyN_1 | r50 | PCA ≥ 0.21 | 31 | 31 | 31 |  |  |  | 2,783 | 42 s |
| FamilyN_1 | r75 | PCA ≥ 0.1506 | 46 | 46 | 46 |  |  |  | 2,783 | 1 min 6 s |
| FamilyN_1 | r100 | PCA ≥ 0 | 61 | 61 |  |  |  |  |  |  |
| french | std1 | std = 1 | 12 | 3 | 3 | 10,332 | 10,285 | 17 s | 6,572 | 7 s |
| french | r25 | PCA ≥ 0.8882 | 60 | 51 | 46 | 10,332 | 10,331 | 24 s | 6,573 | 9 min 9 s |
| french | r50 | PCA ≥ 0.553 | 120 | 111 | 94 | 10,332 | 10,332 | 33 s | 6,572 | 21 min 49 s |
| french | r75 | PCA ≥ 0.409 | 180 | 171 | 145 | 10,332 | 10,331 | 40 s | 6,572 | 28 min 50 s |
| french | r100 | PCA ≥ 0 | 240 | 231 | 204 | 10,332 | 10,329 | 46 s | 6,573 | 26 min 23 s |
| french1_1 | std1 | std = 1 | 6 | 0 | | | no rule: no copy | | | |
| french1_1 | r25 | PCA ≥ 0.8148 | 56 | 50 | 41 |  | 7,957 |  | 4,202 | 5 min 9 s |
| french1_1 | r50 | PCA ≥ 0.504 | 112 | 106 | 83 |  | 7,957 |  | 4,202 | 6 min 56 s |
| french1_1 | r75 | PCA ≥ 0.34 | 168 | 162 | 135 |  | 7,957 |  | 4,202 | 11 min 27 s |
| french1_1 | r100 | PCA ≥ 0 | 223 | 217 | 190 |  | 7,957 |  | 4,202 | 11 min 8 s |
| french1_N | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| french1_N | r25 | PCA ≥ 0.47 | 4 | 4 |  |  | 1,696 |  | 881 |  |
| french1_N | r50 | PCA ≥ 0.28 | 8 | 8 |  |  | 1,696 |  | 881 |  |
| french1_N | r75 | PCA ≥ 0.26 | 12 | 12 |  |  | 1,696 |  | 881 |  |
| french1_N | r100 | PCA ≥ 0 | 15 | 15 | 15 |  | 1,696 |  | 881 | 19 s |
| frenchN_1 | std1 | std = 1 | 2 | 0 | | | no rule: no copy | | | |
| frenchN_1 | r25 | PCA ≥ 0.61 | 33 | 31 | 21 |  | 5,170 |  | 1,796 | 53 s |
| frenchN_1 | r50 | PCA ≥ 0.48 | 66 | 64 | 43 |  | 5,171 |  | 1,796 | 1 min 25 s |
| frenchN_1 | r75 | PCA ≥ 0.33 | 102 | 100 | 76 |  | 5,171 |  | 1,796 | 2 min 20 s |
| frenchN_1 | r100 | PCA ≥ 0 | 132 | 130 | 106 |  | 5,171 |  | 1,796 | 2 min 41 s |
| frenchN_M | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| frenchN_M | r25 | PCA ≥ 0.5 | 14 | 14 |  |  | 3,375 |  | 2,887 |  |
| frenchN_M | r50 | PCA ≥ 0.36 | 27 | 27 |  |  | 3,375 |  | 2,888 |  |
| frenchN_M | r75 | PCA ≥ 0.27 | 41 | 41 |  |  | 3,375 |  | 2,887 |  |
| frenchN_M | r100 | PCA ≥ 0 | 54 | 54 | 54 |  | 3,375 |  | 2,887 | 4 min 29 s |
| SGKG1 | std1 | std = 1 | 4 | 2 | | | same rules as r100: no copy | | | |
| SGKG1 | r25 | PCA ≥ 1 | 4 | 2 | | | same rules as r100: no copy | | | |
| SGKG1 | r50 | PCA ≥ 1 | 4 | 2 | | | same rules as r100: no copy | | | |
| SGKG1 | r75 | PCA ≥ 1 | 4 | 2 | | | same rules as r100: no copy | | | |
| SGKG1 | r100 | PCA ≥ 0 | 4 | 2 |  |  |  |  |  |  |
| SGKG2 | std1 | std = 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG2 | r25 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG2 | r50 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG2 | r75 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG2 | r100 | PCA ≥ 0 | 3 | 1 |  |  |  |  |  |  |
| SGKG3 | std1 | std = 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG3 | r25 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG3 | r50 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG3 | r75 | PCA ≥ 1 | 3 | 1 | | | same rules as r100: no copy | | | |
| SGKG3 | r100 | PCA ≥ 0 | 3 | 1 |  |  |  |  |  |  |
| SGKG4 | std1 | std = 1 | 6 | 4 | | | same rules as r100: no copy | | | |
| SGKG4 | r25 | PCA ≥ 1 | 6 | 4 | | | same rules as r100: no copy | | | |
| SGKG4 | r50 | PCA ≥ 1 | 6 | 4 | | | same rules as r100: no copy | | | |
| SGKG4 | r75 | PCA ≥ 1 | 6 | 4 | | | same rules as r100: no copy | | | |
| SGKG4 | r100 | PCA ≥ 0 | 6 | 4 |  |  |  |  |  |  |
| WN18RR | std1 | std = 1 | 2 | 2 |  |  |  |  |  |  |
| WN18RR | r25 | PCA ≥ 0.8 | 6 | 6 | 6 |  |  |  | 92,550 | 19 min 23 s |
| WN18RR | r50 | PCA ≥ 0.7 | 12 | 12 | 12 |  |  |  | 92,550 | 37 min 7 s |
| WN18RR | r75 | PCA ≥ 0.3 | 21 | 21 | 21 |  |  |  | 92,550 | 1 h 1 min |
| WN18RR | r100 | PCA ≥ 0 | 24 | 24 |  |  |  |  |  |  |
| WN18RR1_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| WN18RR1_1 | r25 | PCA ≥ 0.85 | 5 | 5 | 5 |  |  |  | 70,699 | 16 min 8 s |
| WN18RR1_1 | r50 | PCA ≥ 0.7 | 9 | 9 | 9 |  |  |  | 70,699 | 29 min 29 s |
| WN18RR1_1 | r75 | PCA ≥ 0.3 | 13 | 13 | 13 |  |  |  | 70,699 | 31 min 37 s |
| WN18RR1_1 | r100 | PCA ≥ 0 | 17 | 17 |  |  |  |  |  |  |
| WN18RR1_N | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| WN18RR1_N | r25 | PCA ≥ 0.6 | 3 | 3 | 3 |  |  |  | 21,809 | 15 s |
| WN18RR1_N | r50 | PCA ≥ 0.2 | 5 | 5 | 5 |  |  |  | 21,808 | 19 s |
| WN18RR1_N | r75 | PCA ≥ 0.17 | 8 | 8 | 8 |  |  |  | 21,808 | 25 s |
| WN18RR1_N | r100 | PCA ≥ 0 | 10 | 10 |  |  |  |  |  |  |
| WN18RRN_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| WN18RRN_1 | r25 | PCA ≥ 0.7 | 4 | 4 | 4 |  |  |  | 63,745 | 11 min 41 s |
| WN18RRN_1 | r50 | PCA ≥ 0.2 | 8 | 8 | 8 |  |  |  | 63,745 | 20 min 4 s |
| WN18RRN_1 | r75 | PCA ≥ 0.126 | 12 | 12 | 12 |  |  |  | 63,745 | 18 min 26 s |
| WN18RRN_1 | r100 | PCA ≥ 0 | 15 | 15 |  |  |  |  |  |  |
| WN18RRN_M | std1 | std = 1 | 2 | 2 |  |  |  |  |  |  |
| WN18RRN_M | r25 | PCA ≥ 0.8 | 6 | 6 | 6 |  |  |  | 92,550 | 18 min 50 s |
| WN18RRN_M | r50 | PCA ≥ 0.7 | 12 | 12 | 12 |  |  |  | 92,550 | 37 min 34 s |
| WN18RRN_M | r75 | PCA ≥ 0.3 | 21 | 21 | 21 |  |  |  | 92,550 | 1 h 1 min |
| WN18RRN_M | r100 | PCA ≥ 0 | 24 | 24 |  |  |  |  |  |  |
| YAGO3-10 | std1 | std = 1 | 3 | 0 | | | no rule: no copy | | | |
| YAGO3-10 | r25 | PCA ≥ 0.47 | 38 | 35 |  |  |  |  | 530,838 |  |
| YAGO3-10 | r50 | PCA ≥ 0.3102 | 75 | 72 |  |  |  |  | 530,838 |  |
| YAGO3-10 | r75 | PCA ≥ 0.19 | 112 | 109 |  |  |  |  | 530,841 |  |
| YAGO3-10 | r100 | PCA ≥ 0 | 149 | 146 |  |  |  |  |  |  |
| YAGO3-10_1_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| YAGO3-10_1_1 | r25 | PCA ≥ 0.58 | 22 | 22 | 15 |  |  |  | 188,150 | 1 h 10 min |
| YAGO3-10_1_1 | r50 | PCA ≥ 0.397 | 44 | 44 | 33 |  |  |  | 188,150 | 1 h 22 min |
| YAGO3-10_1_1 | r75 | PCA ≥ 0.25 | 66 | 66 | 54 |  |  |  | 188,150 | 1 h 37 min |
| YAGO3-10_1_1 | r100 | PCA ≥ 0 | 88 | 88 |  |  |  |  |  |  |
| YAGO3-10_1_N | std1 | std = 1 | 1 | 0 | | | no rule: no copy | | | |
| YAGO3-10_1_N | r25 | PCA ≥ 0.362 | 20 | 19 |  |  |  |  | 192,150 |  |
| YAGO3-10_1_N | r50 | PCA ≥ 0.28 | 40 | 39 |  |  |  |  | 192,150 |  |
| YAGO3-10_1_N | r75 | PCA ≥ 0.18 | 60 | 59 |  |  |  |  | 192,151 |  |
| YAGO3-10_1_N | r100 | PCA ≥ 0 | 79 | 78 |  |  |  |  |  |  |
| YAGO3-10_N_1 | std1 | std = 1 | 0 | 0 | | | no rule: no copy | | | |
| YAGO3-10_N_1 | r25 | PCA ≥ 0.5 | 30 | 30 | 21 |  |  |  | 478,137 | 1 h 22 min |
| YAGO3-10_N_1 | r50 | PCA ≥ 0.298 | 60 | 60 | 50 |  |  |  | 478,137 | 2 h 0 min |
| YAGO3-10_N_1 | r75 | PCA ≥ 0.172 | 90 | 90 | 71 |  |  |  | 478,138 | 2 h 13 min |
| YAGO3-10_N_1 | r100 | PCA ≥ 0 | 119 | 119 |  |  |  |  |  |  |
| YAGO3-10_N_M | std1 | std = 1 | 3 | 0 | | | no rule: no copy | | | |
| YAGO3-10_N_M | r25 | PCA ≥ 0.46 | 34 | 31 |  |  |  |  | 532,315 |  |
| YAGO3-10_N_M | r50 | PCA ≥ 0.308 | 67 | 64 |  |  |  |  | 532,316 |  |
| YAGO3-10_N_M | r75 | PCA ≥ 0.177 | 100 | 97 |  |  |  |  | 532,315 |  |
| YAGO3-10_N_M | r100 | PCA ≥ 0 | 133 | 130 |  |  |  |  |  |  |

## Totals

Sum of the copies' times; levels of kbgen ran several at a time, so its sum is CPU time, not wall-clock time.

| Method | Copies | Sum of times |
|---|---:|---:|
| ours | 5 | 2 min 40 s |
| kbgen | 48 | 18 h 28 min |

## kbgen's shared steps

Made once per dataset and shared by its levels: the N-Triples file, the tensor and the models M1 and M2.

| Dataset | Time | Made |
|---|---:|---|
| Family1_1 | 5 s | 2026-11-16 13:20 |
| Family1_N | 4 s | 2026-11-16 13:20 |
| FamilyM_N | 4 s | 2026-11-16 13:20 |
| FamilyN_1 | 4 s | 2026-11-16 13:20 |
| french | 7 s | 2026-11-16 16:23 |
| french1_1 | 5 s | 2026-11-16 17:08 |
| french1_N | 4 s | 2026-11-16 17:08 |
| frenchN_1 | 5 s | 2026-11-16 17:08 |
| frenchN_M | 4 s | 2026-11-16 17:08 |
| WN18RR | 18 s | 2026-11-16 13:20 |
| WN18RR1_1 | 15 s | 2026-11-16 13:20 |
| WN18RR1_N | 9 s | 2026-11-16 13:20 |
| WN18RRN_1 | 14 s | 2026-11-16 13:20 |
| WN18RRN_M | 18 s | 2026-11-16 13:21 |
| YAGO3-10_1_1 | 1 min 2 s | 2026-11-16 13:31 |
| YAGO3-10_N_1 | 2 min 5 s | 2026-11-16 13:32 |

## Provenance

When each copy was finished, from which commit of this repository (ours; `+dirty`: with uncommitted changes, listed in README.md), and the verdict of `check_copies.py`.

| Dataset | Level | Ours finished | Ours commit | Ours check | kbgen finished | kbgen check |
|---|---|---|---|---|---|---|
| family | std1 |  |  | missing |  | missing |
| family | r25 |  |  | missing |  | invalid:no run record |
| family | r50 |  |  | missing |  | missing |
| family | r75 |  |  | missing |  | invalid:no run record |
| family | r100 |  |  | missing |  | missing |
| Family1_1 | r25 |  |  | missing | 2026-11-16 13:23 | valid |
| Family1_1 | r50 |  |  | missing | 2026-11-16 13:25 | valid |
| Family1_1 | r75 |  |  | missing | 2026-11-16 13:27 | valid |
| Family1_1 | r100 |  |  | missing |  | missing |
| Family1_N | r25 |  |  | missing | 2026-11-16 13:22 | valid |
| Family1_N | r50 |  |  | missing | 2026-11-16 13:22 | valid |
| Family1_N | r75 |  |  | missing | 2026-11-16 13:22 | valid |
| Family1_N | r100 |  |  | missing |  | missing |
| FamilyM_N | r25 |  |  | missing | 2026-11-16 13:27 | valid |
| FamilyM_N | r50 |  |  | missing | 2026-11-16 13:27 | valid |
| FamilyM_N | r75 |  |  | missing | 2026-11-16 13:26 | valid |
| FamilyM_N | r100 |  |  | missing |  | missing |
| FamilyN_1 | r25 |  |  | missing | 2026-11-16 13:21 | valid |
| FamilyN_1 | r50 |  |  | missing | 2026-11-16 13:21 | valid |
| FamilyN_1 | r75 |  |  | missing | 2026-11-16 13:22 | valid |
| FamilyN_1 | r100 |  |  | missing |  | missing |
| french | std1 | 2026-11-16 16:20 | 7a55aa9+dirty | valid | 2026-11-16 16:23 | valid |
| french | r25 | 2026-11-16 16:20 | 7a55aa9+dirty | valid | 2026-11-16 16:32 | valid |
| french | r50 | 2026-11-16 16:20 | 7a55aa9+dirty | valid | 2026-11-16 16:45 | valid |
| french | r75 | 2026-11-16 16:21 | 7a55aa9+dirty | valid | 2026-11-16 16:52 | valid |
| french | r100 | 2026-11-16 16:22 | 7a55aa9+dirty | valid | 2026-11-16 16:49 | valid |
| french1_1 | r25 |  |  | invalid:no times row | 2026-11-16 17:15 | valid |
| french1_1 | r50 |  |  | invalid:no times row | 2026-11-16 17:16 | valid |
| french1_1 | r75 |  |  | invalid:no times row | 2026-11-16 17:20 | valid |
| french1_1 | r100 |  |  | invalid:no times row | 2026-11-16 17:19 | valid |
| french1_N | r25 |  |  | invalid:no times row |  | invalid:no run record |
| french1_N | r50 |  |  | invalid:no times row |  | invalid:no run record |
| french1_N | r75 |  |  | invalid:no times row |  | invalid:no run record |
| french1_N | r100 |  |  | invalid:no times row | 2026-11-16 17:09 | valid |
| frenchN_1 | r25 |  |  | invalid:no times row | 2026-11-16 17:09 | valid |
| frenchN_1 | r50 |  |  | invalid:no times row | 2026-11-16 17:10 | valid |
| frenchN_1 | r75 |  |  | invalid:no times row | 2026-11-16 17:11 | valid |
| frenchN_1 | r100 |  |  | invalid:no times row | 2026-11-16 17:11 | valid |
| frenchN_M | r25 |  |  | invalid:no times row |  | invalid:no run record |
| frenchN_M | r50 |  |  | invalid:no times row |  | invalid:no run record |
| frenchN_M | r75 |  |  | invalid:no times row |  | invalid:no run record |
| frenchN_M | r100 |  |  | invalid:no times row | 2026-11-16 17:13 | valid |
| SGKG1 | r100 |  |  | missing |  | missing |
| SGKG2 | r100 |  |  | missing |  | missing |
| SGKG3 | r100 |  |  | missing |  | missing |
| SGKG4 | r100 |  |  | missing |  | missing |
| WN18RR | std1 |  |  | missing |  | missing |
| WN18RR | r25 |  |  | missing | 2026-11-16 14:30 | valid |
| WN18RR | r50 |  |  | missing | 2026-11-16 14:43 | valid |
| WN18RR | r75 |  |  | missing | 2026-11-16 14:57 | valid |
| WN18RR | r100 |  |  | missing |  | missing |
| WN18RR1_1 | r25 |  |  | missing | 2026-11-16 14:06 | valid |
| WN18RR1_1 | r50 |  |  | missing | 2026-11-16 14:15 | valid |
| WN18RR1_1 | r75 |  |  | missing | 2026-11-16 14:10 | valid |
| WN18RR1_1 | r100 |  |  | missing |  | missing |
| WN18RR1_N | r25 |  |  | missing | 2026-11-16 13:27 | valid |
| WN18RR1_N | r50 |  |  | missing | 2026-11-16 13:27 | valid |
| WN18RR1_N | r75 |  |  | missing | 2026-11-16 13:27 | valid |
| WN18RR1_N | r100 |  |  | missing |  | missing |
| WN18RRN_1 | r25 |  |  | missing | 2026-11-16 13:46 | valid |
| WN18RRN_1 | r50 |  |  | missing | 2026-11-16 13:51 | valid |
| WN18RRN_1 | r75 |  |  | missing | 2026-11-16 13:49 | valid |
| WN18RRN_1 | r100 |  |  | missing |  | missing |
| WN18RRN_M | std1 |  |  | missing |  | missing |
| WN18RRN_M | r25 |  |  | missing | 2026-11-16 15:02 | valid |
| WN18RRN_M | r50 |  |  | missing | 2026-11-16 15:07 | valid |
| WN18RRN_M | r75 |  |  | missing | 2026-11-16 15:24 | valid |
| WN18RRN_M | r100 |  |  | missing |  | missing |
| YAGO3-10 | r25 |  |  | missing |  | invalid:no run record |
| YAGO3-10 | r50 |  |  | missing |  | invalid:no run record |
| YAGO3-10 | r75 |  |  | missing |  | invalid:no run record |
| YAGO3-10 | r100 |  |  | missing |  | missing |
| YAGO3-10_1_1 | r25 |  |  | missing | 2026-11-16 16:03 | valid |
| YAGO3-10_1_1 | r50 |  |  | missing | 2026-11-16 14:53 | valid |
| YAGO3-10_1_1 | r75 |  |  | missing | 2026-11-16 15:08 | valid |
| YAGO3-10_1_1 | r100 |  |  | missing |  | missing |
| YAGO3-10_1_N | r25 |  |  | missing |  | invalid:no run record |
| YAGO3-10_1_N | r50 |  |  | missing |  | invalid:no run record |
| YAGO3-10_1_N | r75 |  |  | missing |  | invalid:no run record |
| YAGO3-10_1_N | r100 |  |  | missing |  | missing |
| YAGO3-10_N_1 | r25 |  |  | missing | 2026-11-16 16:55 | valid |
| YAGO3-10_N_1 | r50 |  |  | missing | 2026-11-16 15:32 | valid |
| YAGO3-10_N_1 | r75 |  |  | missing | 2026-11-16 15:45 | valid |
| YAGO3-10_N_1 | r100 |  |  | missing |  | missing |
| YAGO3-10_N_M | r25 |  |  | missing |  | invalid:no run record |
| YAGO3-10_N_M | r50 |  |  | missing |  | invalid:no run record |
| YAGO3-10_N_M | r75 |  |  | missing |  | invalid:no run record |
| YAGO3-10_N_M | r100 |  |  | missing |  | missing |
