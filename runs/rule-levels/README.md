# Rule-level copies of the benchmark datasets

This folder holds everything that made the rule-level copies of the 24 benchmark datasets in `exp_datasets/` with this project's generator (ours) and with kbgen, and the record of the run: commands, code, parameters, machine and times. [`REPORT.md`](REPORT.md), written by `make_report.py`, gives the tables of every copy: rules, triples, time, when and from which code.

## What was made

The datasets are french, family, WN18RR and YAGO3-10, each complete and as its 1_1, 1_N, N_1 and N_M variants, and SGKG1 to SGKG4: 24 datasets, each with its rules mined by AMIE in `exp_datasets/<d>/<d>.csv`. Each is copied by both methods at five rule levels:

| Level | Rules |
|---|---|
| `std1` | the rules with std confidence 1 |
| `r25` | about 25% of the mined rules, those with the highest PCA confidence |
| `r50` | about 50% |
| `r75` | about 75% |
| `r100` | all of them |

The PCA threshold of each dataset at each level is in `exp_datasets/rule_levels.tsv`, written by `python -m skgg.cli.rule_levels`; [`docs/rule-levels.md`](../../docs/rule-levels.md) explains how it is chosen.

The copies are `exp_datasets/<d>_ours_<L>/<d>_ours_<L>.tsv` and `exp_datasets/<d>_kbgen_<L>/<d>_kbgen_<L>.tsv`, one triple per line, with no splits; `scripts/split_vanilla.py` makes VANILLA's 80/20 `train` and `test` files on the machine that trains.

## Both methods get the same rules

Our generator does not use every rule it is given: it removes the fewest complete rules (std confidence 1) that leave the graph of relations with no cycle, and recursive rules always go unless they are symmetric rules of an undirected relation ("Cyclic rule removal" in [`docs/architecture.md`](../../docs/architecture.md)). kbgen must not get the rules our generator removed, so the copies are made in this order:

1. Our generator makes the copy and writes the rules it used, the rows of `<d>.csv` it kept, to `exp_datasets/<d>_ours_<L>/<d>_ours_<L>_used.csv`.
2. kbgen's driver, with `--rules-from-ours`, gives kbgen exactly the rule IDs of that file, written in AMIE's output format, and records them in the level's `stats.json` and `kbgen-results/rule-levels/summary.csv` (`given_rule_ids`).
3. `check_copies.py` checks both: the IDs in `_used.csv` must equal the rules our generator selects at the level (computed again with `skgg.cli.rule_levels.dataset_levels`, as `cli/main.py` selects them), and the rules in the file kbgen learned from, mapped back to their IDs, must equal them too.

kbgen can apply fewer rules than it is given: it drops every triple whose object never appears as a subject, and a rule over a relation left without triples is dropped. `REPORT.md` gives these as *kbgen parsed*.

No copy is made at a level where our generator uses no rule (std1 of most variants, which have no std confidence 1 rule left after cycle removal), nor at a level whose rules are those of another level (SGKG std1 to r75, whose rules all have PCA confidence 1, are the r100 copy). `REPORT.md` names these levels.

## Triples produced by rules

Both methods record how many triples of a copy the rules produced, so the copies can be compared on deduced against instantiated triples (table "Triples produced by rules" of `REPORT.md`):

- Our generator forward-chains the complete rules (std confidence 1) in completion; the log gives the triples each pass added (`+N triples in M passes`). A partial rule (std confidence below 1) is never forward-chained: the EDB places its head together with its body, as one grounding, round(PCA confidence × support) times, and the log gives the head triples placed this way (`[Partial] Head triples placed by the groundings of partial rules: N`, logged since commit `226bb8c`). Both count as produced by rules; the rest of the copy is instantiated.
- kbgen fires a rule each time it adds a fact of the rule's body relation, with the rule's PCA confidence as probability, and adds the facts the rule then produces. Model M3 counts them (`count_rule_facts`); upstream logs that number only in debug mode, so `synthesize.py` here writes it to a counts file and the driver records it as `rule_facts` (since commit `77849c8`).

A copy made before those commits has no count, and `check_copies.py` treats it as invalid, so `reproduce.sh` makes it again.

## How to reproduce

`reproduce.sh` runs every step; each can run on its own, and `DATASETS` limits a run to some datasets:

```bash
docker compose --profile graphdb up -d                        # our generator needs GraphDB
runs/rule-levels/reproduce.sh                                 # record check ours kbgen verify report
DATASETS="french1_N frenchN_M" runs/rule-levels/reproduce.sh ours kbgen
```

| Step | What it does |
|---|---|
| `record` | writes `machine.txt`: CPU, RAM, OS, Python versions, GraphDB version, commit of this repository, checksum of kbgen's driver |
| `check` | `check_copies.py`: which copies exist and were made as intended (writes `status.tsv`) |
| `ours` | `python -m skgg.cli.generate_datasets --levels <levels> --datasets <d> --force --log-dir runs/rule-levels/ours --times runs/rule-levels/ours/times.tsv` for the copies still missing or invalid |
| `kbgen` | `kbgen/synthesize_exp_datasets.py --levels <levels> --datasets <d> --rules-from-ours --force --jobs $KBGEN_JOBS --work-dir runs/rule-levels/kbgen-work --results-dir runs/rule-levels/kbgen-results` for the copies still missing or invalid |
| `verify` | `check_copies.py`, `scripts/check_outputs.py` (our copies against their source: well formed, relations kept) and `scripts/check_kbgen_levels.py --rules-from-ours` (kbgen's copies against its summary) |
| `report` | `make_report.py`: writes `REPORT.md` |

The `ours` and `kbgen` steps refuse to run with uncommitted changes in `src/`, `runs/rule-levels/kbgen/` or `scripts/format_change.py`, so every copy comes from committed code. A copy that `check_copies.py` finds valid is not made again.

Environment: this repository's `requirements.txt` with `pip install -e .` for our generator (`SKGG_PYTHON`, default `python`), and `kbgen/requirements.txt` for kbgen (`KBGEN_PYTHON`, default `~/.pyenv/versions/KG/bin/python`), both Python 3.10.22.

## Parameters

Our generator: `cli/main.py` with `--pca-conf <threshold>` at `r25` to `r100` and without it at `std1` (std confidence 1), the default rule filter otherwise, no fill step, self-loops placed as in the source. `cli/generate_datasets.py` runs it in a temporary GraphDB repository per dataset: upload the source, `cli/main.py`, download the synthetic graph. A rule with std confidence below 1 is a partial rule: the EDB grounds it with its head round(PCA confidence × support) times and completion never applies it ([`docs/algorithm.md`](../../docs/algorithm.md), section 8).

kbgen: the code in `kbgen/`, the authors' commit `e5d06bc` with `baselines/kbgen.patch` (Python 3 fixes and our driver), described in [`kbgen/UPSTREAM.md`](kbgen/UPSTREAM.md). Model M3 (M2 plus the rules), `synthesize.py -s 1.0` (as many facts as the graph it learned from). The driver makes the N-Triples file, the tensor and the models M1 and M2 once per dataset and shares them between the dataset's levels; each level learns its own M3 from its rules. `--jobs` levels run at a time.

## Files

| File | Content |
|---|---|
| `reproduce.sh` | the steps above |
| `check_copies.py` | checks every copy; `--plan ours` or `--plan kbgen` lists what is left to make |
| `make_report.py` | writes `REPORT.md` |
| `kbgen/` | kbgen's code as run, with `UPSTREAM.md` and `requirements.txt` |
| `ours/` | one log per copy of our generator (`<d>_ours_<L>.log`: the rules kept, removed and used, and each rule's support and confidences in the source and in the copy) and `times.tsv` (one row per copy: rules used, triples, seconds, start, end, commit, log) |
| `kbgen-results/` | kbgen's `rule-levels/summary.csv` (one row per copy: rules given and parsed, triples, the seconds of each step, `given_rule_ids`), `relation_counts.csv`, `degree_stats.csv`, and the driver's logs in `logs/` |
| `machine.txt` | the machine and code of the run |
| `status.tsv` | the last verdict of `check_copies.py` per dataset and level |
| `steps.log` | start and end of every step run, with its datasets |
| `REPORT.md` | the tables |

kbgen's tensors and models go to `kbgen-work/`, which git ignores.

## History

The french datasets were made first, on 16 November 2026 between 16:19 and 17:20, on the same machine, with the same procedure before this folder existed: our generator at branch `pca-partial-rules` commit `7a55aa9` plus the changes later committed as `8c40aae` (the std1 level and the used rules saved at every level), and kbgen with `--rules-from-ours` from the clone `~/kbgen` with the same code as `kbgen/` except the two records added since (`given_rule_ids`, `finished`). `check_copies.py --import-legacy ~/kbgen/work` checked those copies and imported their records, so they are not made again; their `ours/times.tsv` rows were written from that run's own times file.

kbgen's copies of the other datasets at `r25` to `r75` were made earlier the same day without `--rules-from-ours`, given every rule above the threshold. Where those were exactly the rules our generator uses (Family1_1, Family1_N, FamilyM_N, FamilyN_1, the five WN18RR datasets, YAGO3-10_1_1 and YAGO3-10_N_1), `check_copies.py` accepted them by comparing the rules in the file kbgen learned from with our used rules, and imported their records and their dataset's tensor, M1 and M2, so that the levels made later for that dataset use the same M2. The others are made again: family, YAGO3-10, YAGO3-10_1_N and YAGO3-10_N_M, whose kbgen copies had 1 to 19 rules more than ours used, and french1_N and frenchN_M, whose `r25` to `r75` copies had the right rules but were made on another machine, which left no record here. Our copies of french1_1, french1_N, frenchN_1 and frenchN_M are made again too: their earlier run left no times.
