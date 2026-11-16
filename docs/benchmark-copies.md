# How the benchmark copies were made

Every benchmark dataset in `exp_datasets/` has synthetic copies made by three generators: this project's (`<dataset>_ours/`, 24 copies), kbgen (Melo and Paulheim, ESWC 2017; `<dataset>_kbgen/`, 24 copies) and PyGraft (Hubert et al., ESWC 2024; `<dataset>_pygraft/`, 19 copies, none of the five YAGO3-10 datasets). All three sets were made between 9 and 11 November 2026 on the development laptop (Intel i5-11300H with 4 cores and 8 threads, 15 GB of RAM), with the `NeSy` pyenv environment (Python 3.10.12). This page records the code, settings and commands behind each set and the checks run on it. [`generation-times.md`](generation-times.md) gives the time of each copy, and [`generate-benchmarks.md`](generate-benchmarks.md) the steps to make this generator's copies again on another machine.

## At a glance

| | Ours | kbgen | PyGraft |
|---|---|---|---|
| Code | `cli/generate_datasets.py` at commit `c6e5b35` | kbgen's original code with Python 3 fixes, `~/GitHub/kbgen`, local branch `exp-datasets` (`69c7480`) | PyGraft 0.0.3, unmodified, `~/GitHub/pygraft` (`a5f8a5a`) |
| Driver | `python -m skgg.cli.generate_datasets` | `synthesize_exp_datasets.py` in the kbgen repository | `synthesize_exp_datasets.py` in the pygraft repository |
| Input | `<dataset>.unified.tsv` and its rules, `<dataset>.csv` | the same | `<dataset>.unified.tsv`; the rules are only counted |
| Kept from the source | each entity's degree in each relation, self-loop counts, the rules of std confidence 1 left after cycle removal | relation shares, functional, inverse-functional and non-reflexive flags, every parsed rule with its PCA confidence | entity and triple counts, each relation's characteristics that hold for every triple |
| Entity names | the source's | `Entity_0` to `Entity_N` | `E1` to `En` |
| Seed | none | none | numpy seed 1010 and `PYTHONHASHSEED=0` |
| Made | 11 November, 08:25 to 11:36 | 10 November, 13:55 to 23:29 | 11 November, 00:16 to 02:10 |
| Datasets at a time | 1, then up to 3 for the YAGO3-10 datasets | up to 6 | 4 |
| Copies | 24 | 24 | 19 |

## This generator (`_ours`)

The copies come from `cli/generate_datasets.py` at commit `c6e5b35` on the branch `edb-self-loops`, which includes the self-loop fix `35a1b81`; `logs/ours/commit.txt` records the commit. For each dataset the script creates a GraphDB repository `skgg_ours_<dataset>`, runs `cli/graph.py upload`, `cli/main.py` and `cli/graph.py download` as separate processes, moves the downloaded graph to `<dataset>_ours/<dataset>_ours.tsv` and deletes the repository. The graphs are under `http://skgg.org/<dataset>/`. `cli/main.py` runs with the default rule filter, std confidence 1, and without the fill step.

```bash
git rev-parse HEAD > logs/ours/commit.txt
python -m skgg.cli.generate_datasets > logs/ours/batch.log 2>&1                                              # 08:25
python -m skgg.cli.generate_datasets --datasets YAGO3-10 > logs/ours/batch_YAGO3-10.log 2>&1                 # 10:08
python -m skgg.cli.generate_datasets --datasets YAGO3-10_N_1 --force > logs/ours/batch_YAGO3-10_N_1.log 2>&1  # 10:18
```

The main batch ran one dataset at a time, smallest first, up to `YAGO3-10_1_1`. To save time, `YAGO3-10` and `YAGO3-10_N_1` were then started in batches of their own while the main batch ran `YAGO3-10_1_N` and `YAGO3-10_N_M`, and empty placeholder copies made the main batch skip those two. Up to three datasets shared GraphDB and the CPU, so the times of those four are not those of a dataset alone (last section of [`generation-times.md`](generation-times.md)). The first attempt at `YAGO3-10_N_1` failed after 115 s: no object for `Puntland` in `dealsWith` passed the solvability check in 1,000 draws. The second attempt succeeded. The pipeline is not seeded, so two runs give different copies.

Of the 2,057 mined rules, 84 have std confidence 1 and 33 are left after cycle removal (the `Kept` and `Removed rule` lines of `logs/ours/<dataset>.log`). 16 of the 24 copies used no rule, so their relations come from the profiles alone. The other eight used family 18 rules, SGKG4 4, french 3, SGKG1, WN18RR and WN18RRN_M 2 each, and SGKG2 and SGKG3 1 each. french's three rules have one body atom, the WN18RR rules are symmetric rules generated as undirected edges, and each SGKG copy used one rule with a two-atom body.

`scripts/check_outputs.py` finds 22 of the 24 copies equal to their source in every relation: the triple count, each entity's in- and out-degree and the number of self-loops. `french` is 47 `parent` triples short (1,192 of 1,239), because only the fill step adds the `parent` triples that its rules don't derive. `family` has 86,649 triples against 28,358, with `nephew` at 43,100 against 3,633, and 104 self-loops that its source doesn't have, because completion has no cap.

A first batch ran on 9 November at commit `bbb358c`, before the self-loop fix, one dataset at a time. Its copies were in `exp_datasets/ours-no-reflexivity-09-11/`, removed on 16 November 2026 and still in commit `2b9edf9`, and the main tables of [`generation-times.md`](generation-times.md) use its times because every dataset ran alone. Checking it on 10 November showed that self-loops were not preserved, for example 122 in french's `name` became 0, and 0 in YAGO3-10 became 65. That led to the fix.

## kbgen (`_kbgen`)

kbgen is in `~/GitHub/kbgen`, a clone of the authors' repository `aolimelo/kbgen`. Its code was changed only so that it runs under Python 3, never in its generation logic, known bugs included. Commit `1fd8b55` puts `list()` around dict views and the results of `filter()` and `zip()`, loads the `.npz` with `allow_pickle=True`, opens rules files in text mode and wraps URIRefs in `str()` inside SPARQL patterns. Commits `703d542` and `69c7480` add the driver `synthesize_exp_datasets.py`, the report `EXP_DATASETS.md` and `results/`. The three commits are on the local branch `exp-datasets`, which is not pushed anywhere.

For each dataset `D`, the driver runs kbgen's own pipeline in `work/D/`:

1. `D.unified.tsv` to N-Triples, with every bare term under `http://example.org/`, through this repository's `scripts/format_change.py`.
2. `D.csv` to AMIE's text format with full relation IRIs, the only format kbgen's rule parser reads.
3. `load_tensor.py` builds the tensor kbgen learns from.
4. `learn_model.py` learns M1, M2 and M3, the last with the rules.
5. `synthesize.py D-M3.pkl … -s 1.0` asks for as many facts as kbgen learned from.
6. The driver writes the output as `Entity_N <TAB> relation <TAB> Entity_M`, with the relations under their original names and without kbgen's schema triples (`relation_i rdf:type owl:ObjectProperty`).

```bash
cd ~/GitHub/kbgen
python synthesize_exp_datasets.py --datasets french                                       # test, 13:55
python synthesize_exp_datasets.py --jobs 4 --datasets <the 19 datasets without YAGO3-10> > work/batch_small.log 2>&1   # 14:16 to 20:15
python synthesize_exp_datasets.py --jobs 2 --datasets YAGO3-10 YAGO3-10_1_1 YAGO3-10_1_N YAGO3-10_N_1 YAGO3-10_N_M > work/batch_yago.log 2>&1   # 14:32 to 23:29
```

The second command skipped `french`, whose copy already existed. The two batches ran at the same time, so up to six datasets shared the CPU. The laptop was suspended for about 15 minutes (17:51 to 18:07) while `family`, `YAGO3-10_N_1` and `YAGO3-10_N_M` were running. No run failed.

kbgen's code shapes the copies in five ways:

- `load_tensor.py` treats only subjects as entities, so every triple whose object is never a subject is dropped before learning: none of SGKG's triples, 36% of french's, 65% of frenchN_1's and 79% of YAGO3-10_1_N's. The french datasets lose `gender` and `hasSpouse`, WN18RR1_N loses `_similar_to` and the YAGO3-10 datasets lose five to seven relations, and every rule that uses a lost relation is skipped (french keeps 210 of its 240). A copy has the size of this kept graph: 16 copies have exactly its fact count and 8 one fact more, because the rules fired by the last random fact are not counted against the target.
- No dataset has type triples, so the type model reduces to the relation shares and M3 draws entities uniformly. The EM models, which add an entity bias, were not used.
- M3 lets each relation reach 110% of its kept count, and `choice(relations, 1, weights)` in `kb_models/model_m3.py` passes the weights as numpy's `replace` argument, so the relation of each random fact is drawn uniformly. In most copies the small relations end near 110% and the largest ones short of 100%; the total variation distance between the relation shares of the kept graph and the copy is 0.021 to 0.077.
- After every fact, each rule with that relation in its body fires with probability equal to its PCA confidence, and so do the rules of the facts it makes. Rule-made facts link entities that already have facts, so the Family and french copies use only 40 to 78% of the entities kbgen creates and gain hubs: Family1_N's largest degree goes from 21 to 195.
- kbgen's sampling is not seeded, so a new run gives a different copy.

`EXP_DATASETS.md` in the kbgen repository has the tables per dataset: triples dropped, rules parsed, relation shares and degrees.

These copies use every rule. [`rule-levels.md`](rule-levels.md) describes the copies of both generators at fewer rules, made from the same driver with `--levels`.

## PyGraft (`_pygraft`)

PyGraft is in `~/GitHub/pygraft`, a fork of `nicolas-hbt/pygraft` at version 0.0.3, with its library code unmodified. PyGraft generates a graph from a schema and a few size parameters instead of learning from a graph, so the driver derives the schema from each dataset, following the method of `docs/notes/Replicating_FR_with_pygraft.md` in that repository. The driver `synthesize_exp_datasets.py`, its report `EXP_DATASETS.md` and `results/` are untracked files of that repository.

For each dataset `D`, the driver:

1. Reads `D.unified.tsv` and tests, for each relation, which characteristics hold for every triple.
2. Writes the schema to `output/exp_datasets/D/`:
    - one class, `Entity`, since no dataset has type triples;
    - for each relation, the characteristics that hold for every triple, never reflexive. Characteristics that PyGraft's `combinations.json` rejects together are dropped: transitive and symmetric first, which the generator never reads, then irreflexive where asymmetric is declared, which already rejects self-loops. Dropping therefore changes no generated triple;
    - `config.yml`, with `num_entities` and `num_triples` equal to the input's unique entities and triples, `relation_balance_ratio` 1.0, `fast_gen` off and `kg_check_reasoner` off.
3. Runs the steps of `pygraft.generate_kg` in a child process with numpy seed 1010 and `PYTHONHASHSEED=0`. The hash seed is needed because PyGraft builds its entity lists by iterating sets of strings, so the numpy seed alone does not reproduce a run.
4. Writes `E<i> <TAB> relation <TAB> E<j>`, sorted, without type triples.

Three choices differ from the note: one fixed seed instead of the best of 20 by topological similarity, `fast_gen` off (above 30,000 entities it types entities in batches and loses up to four of them) and no HermiT check of the copies.

```bash
cd ~/GitHub/pygraft
python synthesize_exp_datasets.py --datasets french                                          # test, 00:16
python synthesize_exp_datasets.py --jobs 4 --datasets <the 19 datasets without YAGO3-10> > output/exp_datasets/batch.log 2>&1   # 01:32 to 02:10
```

A first batch of all 24 datasets, largest first, started at 00:17 with four of the YAGO3-10 datasets. Generating the first 2,000 triples of each one again in a separate test gave about 8 triples per second, so the batch was stopped at 01:31 before it wrote any copy (`output/exp_datasets/batch_yago_stopped.log`), and the 19 other datasets ran instead. PyGraft has no copy of the YAGO3-10 datasets; [`generation-times.md`](generation-times.md) estimates 8 to 30 hours for each.

The copies differ from their sources in these ways, all of which follow from how PyGraft works:

- 15 of the 19 copies have exactly their source's triple count. The four SGKG copies stop at 56 to 84%: every SGKG relation is functional and inverse-functional, and PyGraft gives up after 11 rejected candidates in a row.
- The SGKG1 and SGKG2 copies are byte-identical, and so are the SGKG3 and SGKG4 copies. Each pair has the same relations and 36,450 entities, PyGraft ignores the per-relation counts, and both runs of a pair stop early at the same point. Their splits have the same `test` and differ by one triple in `train` and `valid`, so each pair is in effect one benchmark.
- Every copy uses all its entities, or all but one or two.
- The relations have equal shares. The total variation distance from the source's shares is 0.004 to 0.676, within 0.02 of what a uniform mix predicts.
- Symmetric relations are declared but not generated as such: `_derivationally_related_form`, `_similar_to` and `_verb_group` in WN18RR and WN18RRN_M. The transitive `name` of french, french1_1 and french1_N is not transitive in the copies.

## Splits for training

A copy is one triples file. `scripts/split_copies.py` writes its `train`, `valid` and `test` with seed 1235, giving `valid` and `test` the shares they have in the source's splits ([`kge_training.md`](kge_training.md), "Training the copies"). The splits are tracked in git.

## Times

| Datasets | Ours | kbgen | PyGraft |
|---|---:|---:|---:|
| The 19 without YAGO3-10 | 30 min | 10 h 58 min | 2 h 24 min |
| The 5 YAGO3-10 datasets | 3 h 12 min | 14 h 15 min | not made |
| All 24 | 3 h 42 min | 25 h 13 min | |

The times are summed over datasets; [`generation-times.md`](generation-times.md) gives each one and the conditions of each batch. Ours are the times of the 9 November batch, in which every dataset ran alone. The copies of 11 November took 1.10 to 1.56 times as long on the datasets over 30 seconds that ran alone.

- kbgen spends 98.9% of its summed time in `synthesize.py`, and that time grows with the number of rules per relation more than with the size of the graph. family, with 31 rules per relation, took 5 h 54 min for 28,262 facts, longer than YAGO3-10_N_M took for 532,316.
- PyGraft spends 99.9% in generation. Its time grows with the number of entities and with the functional and inverse-functional relations, whose checks scan every triple generated so far.

## Where the code and the records are

| Set | Copies | Driver and report | Logs and results |
|---|---|---|---|
| Ours | `exp_datasets/<dataset>_ours/`, tracked | this repository | `logs/ours/`, ignored by git |
| kbgen | `exp_datasets/<dataset>_kbgen/`, tracked | `~/GitHub/kbgen`, local branch `exp-datasets` | `work/`, ignored by git; `results/` on the branch |
| PyGraft | `exp_datasets/<dataset>_pygraft/`, tracked | `~/GitHub/pygraft`, untracked | `output/exp_datasets/`, ignored by git; `results/`, untracked |

The drivers, reports and results of the two baselines exist only on the development laptop.
