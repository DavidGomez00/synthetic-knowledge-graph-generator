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

## Running in parallel

At batch size 32 one run keeps one CPU core busy and uses about 17% of an RTX 3090. `--workers N` trains N runs at once, one model on one dataset each, starting with the largest datasets. With 4 workers the GPU is fully busy and each epoch takes 1.4 to 1.8 times as long as the same model alone (TransE 27.3 s against 14.8 s per epoch, TransH 44.1 s against 31.8 s, on WN18RR-sized datasets), so 4 workers train about 2.6 times as fast as one. More workers would share the same full GPU.

Each run keeps its seed and settings, so parallel training gives the same results: 9 runs on three small datasets, trained by 3 workers, interrupted and resumed, gave the same losses and MRR as a single process.

Locks keep the runs apart. Each run has `exp_checkpoints/<dataset>/<model>.lock`, and the workers training a dataset share its `.lock`, so two commands with overlapping datasets never train the same run twice, and a single-process run that holds a dataset keeps it to itself.

```bash
python scripts/train_kge.py --skip-models CompGCN --workers 4                                # every dataset
python scripts/train_kge.py --skip-models CompGCN --workers 4 --datasets WN18RR WN18RRN_M    # some datasets
```

Run the command inside `tmux` so it outlives the terminal: `tmux new -s train`, then `Ctrl+B` `D` to leave it running and `tmux attach -t train` to come back.

## Stopping and resuming

`Ctrl+C` in the terminal of the command stops every worker, and so does `pkill -INT -f scripts/train_kge.py`. Each run saves a checkpoint after every epoch, so running the same command again resumes every run from its last epoch and skips the finished ones. A reboot or a crash loses at most the epoch in progress. A run that fails, usually by running out of GPU memory, is logged and retried by the next command.

## Following the training

`python scripts/kge_eta.py --workers 4` prints the runs in progress with their time left, the runs finished, stopped partway and pending, the training time left per dataset and the expected finish time. `watch -n 300 python scripts/kge_eta.py --workers 4` refreshes it every 5 minutes.

A pending run is estimated from the finished runs of the same model on at least 20,000 training triples: their median seconds per epoch per 1,000 training triples, times the run's training triples. Since parallel runs are slower than the finished runs predict, these estimates are scaled by the median slowdown of the runs in progress. The time left is the training time divided by the number of workers, but never less than the longest single run. The YAGO3-10 datasets have 4 to 15 times the training triples of any other dataset, so their estimates are extrapolated and the least reliable.

The other outputs:

| File | Contents |
|---|---|
| `exp_results/progress.csv` | one row per model and dataset: status (`pending`, `training`, `evaluating`, `finished`, `failed`), epoch, last loss, training and evaluation time, MRR, mean rank, Hits@1/3/5/10; rewritten after every epoch |
| `exp_results/<dataset>/status/<model>.json` | the same fields and the loss of every epoch |
| `exp_results/<dataset>/<model>/` | the trained model, `results.json` and the loss plot, written when the run finishes |
| `logs/train_kge.log` | every message of every run, with the worker that wrote it |

With workers the terminal shows one line every 10 epochs per run and one line when a run finishes. With one process it shows PyKEEN's progress bars.

## Run time

Measured on an RTX 3090 for the base set, without CompGCN:

| | One process | 4 workers |
|---|---|---|
| The 19 datasets other than YAGO3-10 | about 29 h | about 11 h |
| The five YAGO3-10 datasets | about 8.3 days | about 3.2 days |
| All 24 datasets | about 9.7 days | about 3.7 days |

The 4-worker figures divide the one-process times by 2.6. The `_kbgen` and `_ours` sets take about as long if their datasets have about as many training triples as the base ones.
