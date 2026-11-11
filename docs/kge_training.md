# Training the KGE benchmark

`scripts/train_kge.py` trains six models of the KGC-Benchmark project (TransE, TransH, TransD, RotatE, ComplEx, TuckER) on every dataset under `exp_datasets/`. CompGCN, the seventh, is left out with `--skip-models CompGCN`: at batch size 32 it takes about 106 days on the base set, 104 of them on the five YAGO3-10 datasets. The benchmark is trained three times with the same settings: on the base datasets, then on the datasets whose names end in `_kbgen`, then on those ending in `_ours`.

Since 2026-11-11 hyperparameter tuning is not part of the benchmark. Every model trains with the fixed settings of KGC-Benchmark's `input.json`, which `train_kge.py` uses by default, so the three sets can be compared run by run. [`kge_tuning.md`](kge_tuning.md) describes the search that was stopped.

## Settings

| Setting | Value |
|---|---|
| Training loop | PyKEEN's sLCWA, inverse triples, filtered negative sampling |
| Epochs | 100, no early stopping |
| Batch size | 32 |
| Embedding dimension | 50 |
| Optimizer | PyKEEN's default, Adam with learning rate 0.001 |
| Seed | 1235 |
| Splits | the dataset's `train`, `valid` and `test` files; models train on `train` and are evaluated on `test`, and `valid` only adds known answers to the ranking filter |

`train_kge.py` writes these settings and checksums of the split files to `exp_results/<dataset>/settings.json`, and refuses a dataset whose recorded settings differ from the command's. Results with other settings need another `--output`.

## Why batch size 32

A larger batch would be faster, but it changes which models come out ahead. Six models were trained at batch size 512, with the other settings unchanged, on four datasets of different sizes, and compared with their batch-32 results (test MRR, one run each):

| Dataset | Training triples | ComplEx | RotatE | TransD | TransE | TransH | TuckER |
|---|---|---|---|---|---|---|---|
| FamilyN_1 | 2,718 | 0.006 → 0.006 | 0.094 → 0.017 | 0.118 → 0.066 | 0.063 → 0.018 | 0.181 → 0.154 | 0.055 → 0.006 |
| french | 10,043 | 0.061 → 0.014 | 0.371 → 0.312 | 0.171 → 0.192 | 0.259 → 0.182 | 0.261 → 0.311 | 0.313 → 0.214 |
| family | 22,686 | 0.390 → 0.037 | 0.734 → 0.718 | 0.248 → 0.287 | 0.247 → 0.238 | 0.320 → 0.397 | 0.435 → 0.546 |
| WN18RR1_1 | 56,812 | 0.012 → 0.001 | 0.100 → 0.096 | 0.065 → 0.061 | 0.041 → 0.037 | 0.049 → 0.029 | 0.053 → 0.083 |

Batch size 512 trained these 24 runs in 31 minutes instead of 304. At learning rate 0.001 and 100 epochs it makes 16 times fewer updates, which leaves some models undertrained: ComplEx goes from second to last on `family`, while TuckER and TransH gain on some datasets. Comparing models at batch size 512 would mostly measure which ones tolerate few updates, so the benchmark keeps 32 and gains speed by training runs in parallel.

TODO: It's not that larget batch sizes lead to worse results, but that it requires looking for the best lr., and we don't want to spend time looking for hyperparameters.

## Running in parallel

At batch size 32 one run keeps one CPU core busy and uses about 17% of an RTX 3090. `--workers N` trains N runs at once, one model on one dataset each, starting with the largest datasets. With 4 workers the GPU is fully busy and each epoch takes 1.4 to 1.8 times as long as the same model alone (TransE 27.3 s against 14.8 s per epoch, TransH 44.1 s against 31.8 s, on WN18RR-sized datasets), so 4 workers train about 2.6 times as fast as one. More workers would share the same full GPU.

Each run keeps its seed and settings, so parallel training gives the same results: 9 runs on three small datasets, trained by 3 workers, interrupted and resumed, gave the same losses and MRR as a single process.

Locks keep the runs apart. Each run has `exp_checkpoints/<dataset>/<model>.lock`, and the workers training a dataset share its `.lock`, so two commands with overlapping datasets never train the same run twice, and a single-process run that holds a dataset keeps it to itself.

```bash
python scripts/train_kge.py --skip-models CompGCN --workers 4                                # every dataset
python scripts/train_kge.py --skip-models CompGCN --workers 4 --datasets WN18RR WN18RRN_M    # some datasets
python scripts/train_kge.py --skip-models CompGCN --workers 4 --skip-datasets YAGO3-10        # every dataset but YAGO3-10
```

Run the command inside `tmux` so it outlives the terminal: `tmux new -s train`, then `Ctrl+B` `D` to leave it running and `tmux attach -t train` to come back.

## Stopping and resuming

`Ctrl+C` in the terminal of the command stops every worker, and so does `pkill -INT -f scripts/train_kge.py`. Each run saves a checkpoint after every epoch, so running the same command again resumes every run from its last epoch and skips the finished ones. A reboot or a crash loses at most the epoch in progress. A run that fails, usually by running out of GPU memory, is logged and retried by the next command.

## Following the training

`python scripts/kge_eta.py --workers 4` (with the same `--set` and `--skip-datasets` as the training command) prints the runs in progress with their time left, the runs finished, stopped partway and pending, the training time left per dataset and the expected finish time. `watch -n 300 python scripts/kge_eta.py --workers 4` refreshes it every 5 minutes.

A pending run is estimated from the finished runs of the same model on at least 20,000 training triples: their median seconds per epoch per 1,000 training triples, times the run's training triples. Since parallel runs are slower than the finished runs predict, these estimates are scaled by the median slowdown of the runs in progress. The time left is the training time divided by the number of workers, but never less than the longest single run. The YAGO3-10 datasets have 4 to 15 times the training triples of any other dataset, so their estimates are extrapolated and the least reliable.

The other outputs:

| File | Contents |
|---|---|
| `exp_results/progress.csv` | one row per model and dataset: status (`pending`, `training`, `evaluating`, `finished`, `failed`), epoch, last loss, training and evaluation time, MRR, mean rank, Hits@1/3/5/10; rewritten after every epoch |
| `exp_results/<dataset>/status/<model>.json` | the same fields and the loss of every epoch |
| `exp_results/<dataset>/<model>/` | the trained model, `results.json` and the loss plot, written when the run finishes |
| `logs/train_kge.log` | every message of every run, with the worker that wrote it |

With workers the terminal shows one line every 10 epochs per run and one line when a run finishes. With one process it shows PyKEEN's progress bars.

## Training the copies

The synthetic copies live next to the base datasets, one folder per copy: `exp_datasets/<dataset>_ours/<dataset>_ours.tsv` (this generator, 24 copies), `<dataset>_kbgen/` (kbgen, 24) and `<dataset>_pygraft/` (PyGraft, 19). A copy is one triples file, and its entities keep the names of the source dataset's entities.

Each copy is trained and tested on itself, so the benchmark shows whether the models rank the same on a copy as on its source. `scripts/split_copies.py` writes the copy's `train`, `valid` and `test` files:

- the copy's triples, each kept once, are shuffled with seed 1235;
- `test` and `valid` get the same share of the triples as in the source dataset's splits, and `train` the rest;
- a valid or test triple with a head, tail or relation that `train` lacks is moved to `train`, since no model has an embedding for it. On the `_ours` copies this moved 7 (`family_ours`) to 4,280 (`WN18RRN_1_ours`) triples.

The split is reproducible: two runs write identical files, so git ignores them and each machine writes its own. `train_kge.py` records the checksums of the splits in each dataset's `settings.json`, and refuses to resume a dataset whose splits changed.

`--set` picks the datasets `train_kge.py` trains when `--datasets` is not given: `base` (the default), `ours`, `kbgen` or `pygraft`. Results of every set go to the same `exp_results/`, one folder per dataset. `kge_eta.py --set ours` estimates one set; its speeds come from every finished run, and on a machine with no finished run yet, from the runs in progress.

On a new machine, with the environment of `requirements.txt` installed and the branch with the copies checked out:

```bash
python scripts/split_copies.py --suffix _ours                           # about 30 s for the 24 copies
tmux new -s train
python scripts/train_kge.py --skip-models CompGCN --set ours --workers 4
```

`--workers 4` was measured on an RTX 3090; on another GPU, start with 4 and compare the seconds per epoch that `kge_eta.py` reports with 2 or 3 workers. The `_ours` copies have 0.70 (`frenchN_1_ours`) to 3.06 (`family_ours`) times the triples of their source datasets, and 1.01 times in total (5,140,873 against 5,089,314), so the set takes about as long as the base set.

## Run time

Measured on an RTX 3090 for the base set, without CompGCN:

| | One process | 4 workers |
|---|---|---|
| The 19 datasets other than YAGO3-10 | about 29 h | about 11 h |
| The five YAGO3-10 datasets | about 8.3 days | about 3.2 days |
| All 24 datasets | about 9.7 days | about 3.7 days |

The 4-worker figures divide the one-process times by 2.6. The `_kbgen` and `_ours` sets take about as long if their datasets have about as many training triples as the base ones.
