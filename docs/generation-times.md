# Generation times of the benchmark copies

This page gives the wall-clock time it took to make each synthetic copy of the 24 benchmark datasets in `exp_datasets/`, with this project's generator (`<dataset>_ours`) and with the two baselines, kbgen (`<dataset>_kbgen`) and PyGraft (`<dataset>_pygraft`). All three ran on the development machine, a laptop with an Intel i5-11300H (4 cores, 8 threads) and 15 GB of RAM. PyGraft has no copy of the five YAGO3-10 datasets yet.

## What each time covers

The three batches ran under different conditions, so the times are only roughly comparable.

| | Ours | kbgen | PyGraft |
|---|---|---|---|
| Script | `cli/generate_datasets.py` at commit `bbb358c` | `synthesize_exp_datasets.py` in the kbgen repository | `synthesize_exp_datasets.py` in the pygraft repository |
| Steps timed | upload of the source to GraphDB, `cli/main.py`, download of the copy | TSV to N-Triples, rules to AMIE's text format, `load_tensor.py`, models M1, M2 and M3, `synthesize.py`, writing the copy | writing the schema, PyGraft's KG generation with its RDF export, writing and measuring the copy |
| Datasets at a time | 1 | up to 6 | 4 (french on its own) |
| When | 9 November 2026, 21:07, to 10 November, 00:52 | 10 November 2026, copies written from 14:15 to 23:29 | 11 November 2026, 01:32 to 02:10 (french at 00:17) |
| Times read from | `logs/ours_2026-11-09/batch.log` and, for french, `logs/ours_2026-11-09/french.log` | `results/summary.csv` in the kbgen repository | `results/summary.csv` in the pygraft repository |

Ours ran one dataset at a time, so its times are those of a dataset with the machine to itself, apart from GraphDB. french was generated on its own just before the batch. These times come from the run before the self-loop fix (commit `35a1b81`). The copies in `exp_datasets/<dataset>_ours/` were made again on 11 November 2026 at commit `c6e5b35`, but that run had four YAGO3-10 datasets going two or three at a time, so its times are less precise; the last section gives them. The baselines ran several datasets at once on four cores, which makes each one slower than it would be alone. For PyGraft, the triple generation of WN18RR took 12 minutes in a timing run on its own, without the RDF export, and the whole dataset took 16 minutes in the batch.

Other differences:

- The kbgen times of family, YAGO3-10_N_1 and YAGO3-10_N_M include about 15 minutes during which the laptop was suspended.
- The copies don't all have the size of their source. kbgen keeps only the triples whose object also appears as a subject, so its YAGO3-10 copies have 21% to 55% of the source's triples. PyGraft stopped early on the SGKG datasets, at 56% to 84%, because every SGKG relation is functional and inverse-functional and PyGraft gives up after 11 rejected candidate triples in a row. Ours made 86,649 triples for family's 28,358.

## Times per dataset

*Copy* is the number of triples in the copy in `exp_datasets/`. The copies of ours made on 9 November, whose times the table gives, have the same number of triples as those of 11 November, except family's, which had 87,110.

| Dataset | Source triples | Ours: copy | Ours: time | kbgen: copy | kbgen: time | PyGraft: copy | PyGraft: time |
|---|---:|---:|---:|---:|---:|---:|---:|
| family | 28,358 | 86,649 | 36 s | 28,262 | 5 h 54 min | 28,358 | 44 s |
| Family1_1 | 5,720 | 5,720 | 5 s | 5,362 | 6 min 35 s | 5,720 | 9 s |
| Family1_N | 3,462 | 3,462 | 4 s | 3,068 | 1 min 53 s | 3,462 | 2 s |
| FamilyM_N | 5,963 | 5,963 | 4 s | 5,361 | 6 min 0 s | 5,963 | 4 s |
| FamilyN_1 | 3,397 | 3,397 | 4 s | 2,783 | 1 min 9 s | 3,397 | 2 s |
| french | 10,332 | 10,285 | 5 s | 6,572 | 20 min | 10,332 | 12 s |
| french1_1 | 7,957 | 7,957 | 6 s | 4,203 | 12 min | 7,957 | 16 s |
| french1_N | 1,696 | 1,696 | 3 s | 881 | 12 s | 1,696 | 1 s |
| frenchN_1 | 5,171 | 5,171 | 4 s | 1,796 | 2 min 3 s | 5,171 | 5 s |
| frenchN_M | 3,375 | 3,375 | 4 s | 2,887 | 3 min 17 s | 3,375 | 2 s |
| SGKG1 | 72,720 | 72,720 | 34 s | 72,720 | 24 min | 40,639 | 20 min |
| SGKG2 | 54,585 | 54,585 | 2 min 33 s | 54,585 | 21 min | 40,639 | 18 min |
| SGKG3 | 54,585 | 54,585 | 1 min 6 s | 54,585 | 19 min | 46,119 | 21 min |
| SGKG4 | 72,720 | 72,720 | 34 s | 72,721 | 21 min | 46,119 | 22 min |
| WN18RR | 93,003 | 93,003 | 5 min 32 s | 92,550 | 54 min | 93,003 | 16 min |
| WN18RR1_1 | 71,015 | 71,015 | 7 min 4 s | 70,699 | 37 min | 71,015 | 13 min |
| WN18RR1_N | 38,938 | 38,938 | 45 s | 21,809 | 41 s | 38,938 | 3 min 35 s |
| WN18RRN_1 | 65,379 | 65,379 | 4 min 42 s | 63,745 | 20 min | 65,379 | 11 min |
| WN18RRN_M | 93,003 | 93,003 | 5 min 37 s | 92,550 | 54 min | 93,003 | 16 min |
| YAGO3-10 | 1,089,040 | 1,089,040 | 48 min | 530,839 | 4 h 44 min | | |
| YAGO3-10_1_1 | 340,991 | 340,991 | 43 min | 188,150 | 1 h 36 min | | |
| YAGO3-10_1_N | 915,478 | 915,478 | 23 min | 192,151 | 1 h 1 min | | |
| YAGO3-10_N_1 | 1,042,791 | 1,042,791 | 43 min | 478,138 | 2 h 35 min | | |
| YAGO3-10_N_M | 1,002,950 | 1,002,950 | 36 min | 532,316 | 4 h 18 min | | |

Summed over the datasets:

| Datasets | Ours | kbgen | PyGraft |
|---|---:|---:|---:|
| The 19 without YAGO3-10 | 30 min | 10 h 58 min | 2 h 24 min |
| The 5 YAGO3-10 datasets | 3 h 12 min | 14 h 15 min | |
| All 24 | 3 h 42 min | 25 h 13 min | |

Ours ran one dataset at a time, so its sum is also the wall-clock time of its batch, apart from french's 5 seconds. The PyGraft batch of 18 datasets, four at a time, took 38 minutes, plus 12 seconds for french.

## Comparison

Ours was the fastest on 13 of the 19 datasets without YAGO3-10 and never the slowest. PyGraft was faster on five datasets under 6,000 triples (Family1_N, FamilyM_N, FamilyN_1, french1_N and frenchN_M), by at most 2 seconds. kbgen was the fastest only on WN18RR1_N, 41 s against 45 s, with a copy of 21,809 of the 38,938 triples. On the YAGO3-10 datasets, ours took 23 to 48 minutes, and kbgen 2.3 to 7.3 times as long for copies with 21% to 55% of the triples.

kbgen was the slowest on 16 of the 19 datasets. Its synthesis time grows with the number of rules per relation more than with the size of the graph, so family, with 31 rules per relation, took 5 h 54 min for 28,262 triples (see `EXP_DATASETS.md` in the kbgen repository).

PyGraft's time grows with the number of entities and with the functional and inverse-functional relations. For every triple it draws the subject and the object with `np.random.choice` from a Python list of entities, which converts the whole list to an array on each call. Each check of a functional or inverse-functional relation scans every triple generated so far. The SGKG datasets have 36,450 entities and only such relations, and took 18 to 22 minutes for 40,639 to 46,119 triples, longer than WN18RR's 16 minutes for 93,003 triples. Ours made the SGKG copies in 34 s to 2 min 33 s.

## PyGraft on YAGO3-10

A PyGraft batch with four of the YAGO3-10 datasets at a time was stopped on 11 November 2026 after about 70 minutes, without any of them finished. A replay of the first 2,000 triples of each dataset, five at a time and with the batch's seeds, generated 10 to 12 triples per second per dataset. At that rate the copies would take:

| Dataset | Source triples | Estimated time |
|---|---:|---:|
| YAGO3-10 | 1,089,040 | 25 to 30 h |
| YAGO3-10_1_1 | 340,991 | 8 to 9.5 h |
| YAGO3-10_1_N | 915,478 | 21 to 25 h |
| YAGO3-10_N_1 | 1,042,791 | 24 to 29 h |
| YAGO3-10_N_M | 1,002,950 | 23 to 28 h |

These are lower bounds. The checks of functional and inverse-functional relations get slower as the graph grows, and each run ends with an RDF export of about a million triples. Ours took 23 to 48 minutes for the same datasets.

## Ours after the self-loop fix

The copies in `exp_datasets/<dataset>_ours/` were made on 11 November 2026, from 08:25 to 11:36, at commit `c6e5b35`, which includes the self-loop fix (`35a1b81`). The times are in `logs/ours/batch.log`, `batch_YAGO3-10.log` and `batch_YAGO3-10_N_1.log`. The batch ran one dataset at a time up to YAGO3-10_1_1. Then, to save time, YAGO3-10 was started in a second batch at 10:08 and YAGO3-10_N_1 in a third at 10:18, while the first batch ran YAGO3-10_1_N and YAGO3-10_N_M. Those four datasets ran two or three at a time, sharing GraphDB and the CPU, so their times are not those of a dataset alone, and the tables above keep the times of 9 November.

The copies of 9 November are in `exp_datasets/ours-no-reflexivity-09-11/<dataset>_ours/`. Their logs, in `logs/ours_2026-11-09/`, and their `<dataset>_used.csv` files, in `exp_datasets/ours-no-reflexivity-09-11/used_rules/`, are only on the development machine, because git ignores both.

| Dataset | 9 November, before `35a1b81` | 11 November, at `c6e5b35` | Ratio |
|---|---:|---:|---:|
| family | 36 s | 40 s | 1.11 |
| Family1_1 | 5 s | 5 s | 1.00 |
| Family1_N | 4 s | 4 s | 1.00 |
| FamilyM_N | 4 s | 10 s | 2.50 |
| FamilyN_1 | 4 s | 7 s | 1.75 |
| french | 5 s | 16 s | 3.20 |
| french1_1 | 6 s | 5 s | 0.83 |
| french1_N | 3 s | 3 s | 1.00 |
| frenchN_1 | 4 s | 9 s | 2.25 |
| frenchN_M | 4 s | 4 s | 1.00 |
| SGKG1 | 34 s | 53 s | 1.56 |
| SGKG2 | 2 min 33 s | 2 min 54 s | 1.14 |
| SGKG3 | 1 min 6 s | 1 min 27 s | 1.32 |
| SGKG4 | 34 s | 53 s | 1.56 |
| WN18RR | 5 min 32 s | 6 min 24 s | 1.16 |
| WN18RR1_1 | 7 min 4 s | 8 min 1 s | 1.13 |
| WN18RR1_N | 45 s | 54 s | 1.20 |
| WN18RRN_1 | 4 min 42 s | 5 min 10 s | 1.10 |
| WN18RRN_M | 5 min 37 s | 6 min 27 s | 1.15 |
| YAGO3-10 | 48 min | 1 h 23 min | 1.72 |
| YAGO3-10_1_1 | 43 min | 50 min | 1.17 |
| YAGO3-10_1_N | 23 min | 36 min | 1.57 |
| YAGO3-10_N_1 | 43 min | 1 h 15 min | 1.75 |
| YAGO3-10_N_M | 36 min | 1 h 2 min | 1.74 |

On the datasets that took more than 30 seconds and ran alone in both batches, the new code took 1.10 to 1.56 times as long. The datasets under 20 seconds take a few seconds either way, part of which goes to starting three processes and creating the GraphDB repository, so their ratios say little about the generator. The ratios of 1.57 to 1.75 for YAGO3-10, YAGO3-10_1_N, YAGO3-10_N_1 and YAGO3-10_N_M also include running two or three datasets at a time. The run of 11 November took 3 h 11 min of wall-clock time; the sum of its times is 5 h 40 min.

Two more things affect the times of 11 November:

- The first attempt at YAGO3-10_N_1 failed after 115 s: no object for `Puntland` in `dealsWith` passed the solvability check of `engine/edb.py` in 1,000 draws (`logs/ours/YAGO3-10_N_1.failed-1.log`). The pipeline is not seeded, and the second attempt succeeded. The table gives the time of the second attempt.
- The laptop suspended itself from 08:52 to about 08:59, during WN18RR. The batch measures times with `time.monotonic`, which stops during a suspension, so WN18RR's 6 min 24 s leaves it out.

`scripts/check_outputs.py` finds that 22 of the 24 new copies match their source on every relation, including the number of self-loops. french and family differ as `docs/generate-benchmarks.md` describes: french's `parent` is 47 triples short, and family has more triples than its source and 104 self-loops that its source doesn't have.
