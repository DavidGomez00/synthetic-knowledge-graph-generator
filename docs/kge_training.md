# Training the KGE benchmark

`scripts/train_kge.py` trains the seven models of the KGC-Benchmark project (TransE, TransH, TransD, RotatE, ComplEx, TuckER, CompGCN) on the datasets under `exp_datasets/`: the 24 original graphs, called the base set or baseline, and their synthetic copies, made by this project's generator (`ours`), kbgen and PyGraft.

The project has no hyperparameter search. Every model trains with fixed settings, VANILLA's, so the sets can be compared run by run; CompGCN has its own batch size and learning rate and is compared across datasets only. The two searches tried before (`tune_kge.py` and the speed search `tune_speed.py`) were removed on 2026-11-16 as out of scope, with their records.

[`training-protocol.md`](training-protocol.md) lists every training and evaluation setting, the differences from VANILLA's `KGC.py` and a test run on French Royalty.

## Settings

| Setting | Value |
|---|---|
| Training loop | PyKEEN's sLCWA, inverse triples, filtered negative sampling, margin ranking loss |
| Epochs | 100, no early stopping |
| Batch size | 32; CompGCN 131,072, capped at the training instances |
| Learning rate | Adam, 0.001 (PyKEEN's default); CompGCN 0.03 |
| Embedding dimension | 50 |
| Seed | 1235 |
| Splits | VANILLA's 80/20 split of each dataset: models train on `train` and are evaluated on `test`; there is no `valid` |

The settings are the `Settings` class of `train_kge.py`. It writes them, with checksums of the split files, to `exp_results/<dataset>/settings.json`, and refuses a dataset whose recorded settings or splits differ from the command's. Results with other settings need another `--output`.

## Why batch size 32

A larger batch would be faster, but it changes which models come out ahead. Six models were trained at batch size 512, with the other settings unchanged, on four datasets of different sizes, and compared with their batch-32 results (test MRR, one run each, on an RTX 3090 and on the earlier 80/10/10 splits):

| Dataset | Training triples | ComplEx | RotatE | TransD | TransE | TransH | TuckER |
|---|---|---|---|---|---|---|---|
| FamilyN_1 | 2,718 | 0.006 → 0.006 | 0.094 → 0.017 | 0.118 → 0.066 | 0.063 → 0.018 | 0.181 → 0.154 | 0.055 → 0.006 |
| french | 10,043 | 0.061 → 0.014 | 0.371 → 0.312 | 0.171 → 0.192 | 0.259 → 0.182 | 0.261 → 0.311 | 0.313 → 0.214 |
| family | 22,686 | 0.390 → 0.037 | 0.734 → 0.718 | 0.248 → 0.287 | 0.247 → 0.238 | 0.320 → 0.397 | 0.435 → 0.546 |
| WN18RR1_1 | 56,812 | 0.012 → 0.001 | 0.100 → 0.096 | 0.065 → 0.061 | 0.041 → 0.037 | 0.049 → 0.029 | 0.053 → 0.083 |

Batch size 512 trained these 24 runs in 31 minutes instead of 304. At learning rate 0.001 and 100 epochs it makes 16 times fewer updates, which leaves some models undertrained: ComplEx goes from second to last on `family`, while TuckER and TransH gain on some datasets. Comparing models at batch size 512 would mostly measure which ones tolerate few updates, so the benchmark keeps 32 and gains speed by training runs in parallel.

TODO: It's not that larget batch sizes lead to worse results, but that it requires looking for the best lr., and we don't want to spend time looking for hyperparameters.

CompGCN is the exception. It encodes the whole graph at every step, so at batch size 32 it would take about 60 days per YAGO3-10 dataset on the training machine; it trains with batch size 131,072 and learning rate 0.03 instead, and its results are compared across datasets, not with the other models ([`training-protocol.md`](training-protocol.md), section 3).

## Order and parallel runs

Datasets are trained smallest `train` file first, so the fast graphs finish early and the slowest, the YAGO3-10 datasets, come last. `--largest-first` reverses the order: with several workers it starts the longest runs at once instead of leaving them alone at the end, which can shorten the whole command.

At batch size 32 a run is CPU-bound: it keeps one CPU core busy and uses only part of a GPU. `--workers N` trains N runs at once, one model on one dataset each, in its own process, and `--threads` (default 4) caps the CPU threads of each. Worker i gets device i of `--devices`, in turn; by default every MIG instance and GPU that `nvidia-smi -L` lists, because a CUDA process sees only one MIG instance.

On the training machine (12 MIG 1g.24gb slices on 6 RTX PRO 6000 Blackwell GPUs, 81.6 CPUs for the container), 12 workers, one per slice, is the setting to use. 24 workers, two per slice, slow each other down, and two CompGCN runs on a YAGO3-10 dataset could run out of a slice's 24 GB together. On an RTX 3090, 4 workers filled the GPU and trained about 2.6 times as fast as one process.

Each run keeps its seed and settings, so parallel training gives the same results as one process: 9 runs on three small datasets, trained by 3 workers, interrupted and resumed, gave the same losses and MRR as a single process. Locks keep the runs apart: each run has `exp_checkpoints/<dataset>/<model>.lock`, and the workers training a dataset share its `.lock`, so two commands with overlapping datasets never train the same run twice.

```bash
python scripts/train_kge.py --set base --workers 12                          # the baseline
python scripts/train_kge.py --workers 12 --datasets WN18RR WN18RRN_M          # some datasets
python scripts/train_kge.py --set base --workers 12 --skip-models CompGCN     # every model but CompGCN
```

Run the command inside `tmux` so it outlives the terminal: `tmux new -s train`, then `Ctrl+B` `D` to leave it running and `tmux attach -t train` to come back.

## Training the baseline

The baseline is every model on every original graph: `--set base` selects the 24 base datasets and none of the copies, so 168 runs.

```bash
tmux new -s baseline
python scripts/train_kge.py --set base --workers 12 --threads 4
```

The French and Family graphs come first, then SGKG and WN18RR, then the five YAGO3-10 datasets, YAGO3-10 itself last. Scaled linearly from the French Royalty test run, the six models other than CompGCN take about 256 run-hours, 86% of them on the YAGO3-10 datasets (3.5 million of the 4.1 million training triples). The 19 other datasets take about 37 run-hours, a few hours with 12 workers. The longest single runs are TransH on YAGO3-10, YAGO3-10_N_1 and YAGO3-10_N_M, about 13 to 14 hours each, and CompGCN takes about 25 minutes per YAGO3-10 dataset. With 12 workers the whole baseline should take a little over a day.

These figures are a lower bound: the YAGO3-10 datasets have about 123,000 entities against French Royalty's 2,644, which slows evaluation and some models more than linearly. Check the pace once the first YAGO3-10 epochs are logged (`python scripts/kge_eta.py --workers 12`). When the baseline is done:

```bash
python kgc_benchmark/report_results.py
python scripts/results_table.py
```

## Splits

Each model is trained once per dataset, on the dataset's own `train`, and tested on its `test`. Since 2026-11-16 every dataset has VANILLA's 80/20 split (`scripts/split_vanilla.py`), with no `valid`; `train_kge.py` needs only `train` and `test` and reads a missing `valid` as empty. [`training-protocol.md`](training-protocol.md), section 1, describes the split and its checks. Each run keeps its trained model, and its complete log is `exp_results/<dataset>/logs/<model>.log`.

## Stopping and resuming

`Ctrl+C` in the terminal of the command stops every worker, and so does `pkill -INT -f scripts/train_kge.py`. Each run saves a checkpoint after every epoch, so running the same command again resumes every run from its last epoch and skips the finished ones. A reboot or a crash loses at most the epoch in progress. A run that fails, usually by running out of GPU memory, is logged and retried by the next command.

## Following the training

`python scripts/kge_eta.py --workers 12` prints the runs in progress with their time left, the runs finished, stopped partway and pending, the training time left per dataset and the expected finish time. `watch -n 300 python scripts/kge_eta.py --workers 12` refreshes it every 5 minutes.

A pending run is estimated from the finished runs of the same model on at least 20,000 training triples: their median seconds per epoch per 1,000 training triples, times the run's training triples. Since parallel runs are slower than the finished runs predict, these estimates are scaled by the median slowdown of the runs in progress. The time left is the training time divided by the number of workers, but never less than the longest single run. The YAGO3-10 datasets have many times the training triples of any other dataset, so their estimates are extrapolated and the least reliable.

The other outputs:

| File | Contents |
|---|---|
| `exp_results/progress.csv` | one row per model and dataset: status (`pending`, `training`, `evaluating`, `finished`, `failed`), epoch, last loss, training and evaluation time, MRR, mean rank, Hits@1/3/5/10; rewritten after every epoch |
| `exp_results/<dataset>/status/<model>.json` | the same fields and the loss of every epoch |
| `exp_results/<dataset>/<model>/` | the trained model, `results.json` and the loss plot, written when the run finishes |
| `exp_results/<dataset>/logs/<model>.log` | the run's own log: command, versions, device, settings, PyKEEN arguments, split checksums, every epoch, metrics, errors |
| `logs/train_kge.log` | every message of every run, with the worker that wrote it |

With workers the terminal shows one line every 10 epochs per run and one line when a run finishes. With one process it shows PyKEEN's progress bars.

## Training the copies

The synthetic copies live next to the base datasets, one folder per copy, each with its `.tsv` and VANILLA's 80/20 split of it. `--set` picks them by the end of the folder name: `ours` (112 datasets), `kbgen` (112) or `pygraft` (19). A copy is named `<dataset>_<set>` for the full copies of the main benchmark, and `<dataset>_<set>_<tag>` for the copies made at one rule threshold (`std1`, and the rule levels `r25` to `r100`, described in `docs/rule-levels.md`, made as `runs/rule-levels/` records); both kinds belong to the set of their generator, and a copy uses the settings of its base dataset. `--set ours-levels` and `--set kbgen-levels` keep only the rule-level copies of one generator. `--set all` trains every dataset. Results of every set go to the same `exp_results/`, one folder per dataset.

Each copy is trained and tested on itself, so the benchmark shows whether the models rank the same on a copy as on its source. Before 2026-11-16 the copies had the splits of `scripts/split_copies.py`, now unused, which shuffled the copy's triples with seed 1235, gave `test` and `valid` the shares of the source dataset's splits, and moved to `train` every held-out triple with a term `train` lacked.

```bash
tmux new -s train
python scripts/train_kge.py --set ours --workers 12
```

## Collecting the results of several machines

A finished run leaves its trained model in `exp_results/<dataset>/<model>/` (`trained_model.pkl`, with `results.json`, the loss plot and `training_triples/`, which PyKEEN needs to load the model); its checkpoint is deleted. With `--upload`, `train_kge.py` pushes every finished run, with its log, to a results branch of the GitHub repository after each run, so the results of every machine end up in one place.

An upload skips a run the branch already has only when the dataset's `settings.json`, its training settings and the checksums of its splits, is the same on the branch as locally. When it differs, as for the runs that `kge-results` holds from the earlier splits, the upload removes that dataset from the branch (its runs, splits and settings) and uploads the local runs in its place, logging the runs it replaced; the commit message names the replaced datasets, and git history keeps the old runs. `--upload-branch` sends the runs to another branch; each branch gets its own worktree, `.<branch>/`:

```bash
python scripts/train_kge.py --set base --workers 12 --upload
```

The machine needs push access to the repository, the same SSH key that cloned it. `scripts/upload_results.py` does the work: it checks out the branch in its worktree, copies the runs the branch lacks with their status, log, settings and the dataset's splits, commits them with the machine's name and pushes. If another machine pushed first, it rebases and pushes again. Uploads of one machine take turns on the worktree's lock, and one takes about 12 seconds; a worker that finishes a run while another worker is uploading doesn't wait for the lock, since every upload sends every finished run the branch lacks, so a later upload sends its run, and the command's last upload waits for the lock and sends what is left. Before 2026-11-16 the workers queued for the lock, and with many short runs about a third of them waited instead of training. A failed upload is logged and training goes on; the next upload, after the next run, sends what was missed, and `python scripts/upload_results.py` uploads by hand.

To gather everything on one machine, `python scripts/upload_results.py --download` copies every run of the branch that the local `exp_results/` lacks, except the runs of a dataset whose `settings.json` differs from the local one, and rebuilds `progress.csv`, after which `kge_eta.py` and `report_results.py` read them as local runs. A results branch shares no files with the code branches, but a clone fetches it too unless it uses `git clone --single-branch -b main`.

## Run time

On the training machine, the seven models trained on `french` (8,265 training triples), 7 at once on separate MIG slices, in 147 s (TransE), 177 s (RotatE), 322 s (ComplEx), 325 s (TuckER), 413 s (TransD), 487 s (TransH) and 19 s (CompGCN). Scaled linearly to all 159 datasets (11.7 million training triples), the six models other than CompGCN take about 740 run-hours, about 2 to 2.6 days with 12 to 24 workers; the baseline alone takes about 256 run-hours. These are lower bounds, for the reason given in "Training the baseline".

Measured earlier on an RTX 3090 for the base set on the earlier splits, without CompGCN: about 29 hours for the 19 datasets other than YAGO3-10 and 9.7 days in all with one process, and about 11 hours and 3.7 days with 4 workers.
