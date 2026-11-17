# Training the KGE benchmark

`scripts/train_kge.py` trains the seven models of the KGC-Benchmark project (TransE, TransH, TransD, RotatE, ComplEx, TuckER, CompGCN) on the datasets under `exp_datasets/`: the 24 original graphs, called the base set or baseline, and their synthetic copies, made by this project's generator (`ours`), kbgen and PyGraft.

The project has no hyperparameter search. Every model trains with fixed settings, VANILLA's, so the sets can be compared run by run; CompGCN has its own batch size and learning rate and is compared across datasets only. The two searches tried before (`tune_kge.py` and the speed search `tune_speed.py`) were removed on 2026-11-16 as out of scope, with their records.

[`training-protocol.md`](training-protocol.md) lists every training and evaluation setting, the differences from VANILLA's `KGC.py` and a test run on French Royalty.

## Settings

| Setting | Value |
|---|---|
| Training loop | PyKEEN's sLCWA, inverse triples, filtered negative sampling, margin ranking loss |
| Epochs | 100, no early stopping |
| Batch size | 32; 4,096 on the YAGO3-10 datasets and their copies; CompGCN 131,072, capped at the training instances |
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

The YAGO3-10 datasets are the other exception. At batch size 32 their runs took 500 to 1,100 seconds per epoch on the training machine, 14 to 31 hours per run, so on the five YAGO3-10 datasets and their copies every model but CompGCN trains at batch size 4,096 (`YAGO_BATCH_SIZE` in `train_kge.py`, which `--batch-size` doesn't change), with the other settings unchanged, learning rate 0.001 included. A copy trains with its source's batch size, so the two stay comparable. The batch size was chosen by timing one epoch of each model on YAGO3-10 (871,232 training triples, the largest) at batch sizes 256 to 16,384, and one full evaluation on `test`, on 17 November 2026, each model alone on a MIG 1g.24gb slice while the baseline ran on the machine. 4,096 is the smallest power of two that keeps every model under an hour per run:

| Model | Seconds per epoch at 1,024 / 2,048 / 4,096 | Evaluation (s) | 100 epochs and evaluation at 4,096 (min) |
|---|---|---|---|
| TransE | 28.7 / 16.3 / 10.7 | 144 | 20 |
| TransH | 39.3 / 22.7 / 12.3 | 275 | 25 |
| TransD | 52.5 / 29.4 / 16.7 | 868 | 42 |
| RotatE | 38.1 / 22.4 / 13.5 | 234 | 26 |
| ComplEx | 43.6 / 24.6 / 14.7 | 53 | 25 |
| TuckER | 40.1 / 22.8 / 15.0 | 140 | 27 |

No run used more than 0.8 GB of GPU memory, even at 16,384. Several runs on one slice are slower than these times. At 4,096 a run makes 128 times fewer updates than at 32, so the YAGO3-10 results are not comparable with batch-32 results, and the section above applies to them: some models may come out undertrained at learning rate 0.001.

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

The French and Family graphs come first, then SGKG and WN18RR, then the five YAGO3-10 datasets, YAGO3-10 itself last. Scaled linearly from the French Royalty test run, the six models other than CompGCN take about 256 run-hours, 86% of them on the YAGO3-10 datasets (3.5 million of the 4.1 million training triples). The 19 other datasets take about 37 run-hours, a few hours with 12 workers. At batch size 32 the longest single runs would have been TransH on YAGO3-10, YAGO3-10_N_1 and YAGO3-10_N_M, about 13 to 14 hours each by this estimate, and 14 to 31 hours as measured; since 17 November 2026 the YAGO3-10 datasets train at batch size 4,096 ("Why batch size 32"), and each run takes 20 to 42 minutes alone on a slice. CompGCN takes about 25 minutes per YAGO3-10 dataset.

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

A finished run leaves its trained model in `exp_results/<dataset>/<model>/` (`trained_model.pkl`, with `results.json`, the loss plot and `training_triples/`, which PyKEEN needs to load the model); its checkpoint is deleted. With `--upload`, `train_kge.py` commits every finished run, with its log, to the checked-out branch and pushes it to GitHub after each run, so the results of every machine end up in one place: `exp_results/` is tracked by git, except `progress.csv`.

An upload sends every finished run whose files git doesn't have as they are: a new run, or one trained again. The runs that are not finished stay uncommitted, so `train_kge.py` can resume them from `exp_checkpoints/`:

```bash
python scripts/train_kge.py --set base --workers 12 --upload
```

The machine needs push access to the repository, the same SSH key that cloned it. `scripts/upload_results.py` does the work: it commits the finished runs git lacks, with their status, log, settings and the dataset's splits, names the machine in the commit and pushes. If another machine pushed first, it rebases and pushes again. It commits only the runs' files, whatever else is staged, and refuses to push while the branch has unpushed commits that change code. Uploads of one machine take turns on a lock in the git folder; a worker that finishes a run while another worker is uploading doesn't wait for the lock, since every upload sends every finished run the branch lacks, so a later upload sends its run, and the command's last upload waits for the lock and sends what is left. Before 2026-11-16 the workers queued for the lock, and with many short runs about a third of them waited instead of training. A failed upload is logged and training goes on; the next upload, after the next run, sends what was missed, and `python scripts/upload_results.py` uploads by hand.

To gather everything on one machine, `git pull` brings in the runs the other machines pushed, after which `kge_eta.py` and `report_results.py` read them as local runs. Until 17 November 2026 the runs went to the orphan branch `kge-results` through a worktree, and `upload_results.py --download` copied them into `exp_results/`; that day the branch was merged into `vanilla-training` and deleted.

## Run time

### Where the times are kept

Each run records its own times: `train_seconds` and `evaluate_seconds` in `exp_results/<dataset>/status/<model>.json`, the seconds of every epoch in `exp_results/<dataset>/logs/<model>.log`, and the same fields in `exp_results/progress.csv`. `--upload` commits the status file and the log of every finished run with it; `progress.csv` is not tracked. When each command started and when each run finished is in `logs/train_kge.log`, which is local only. The figures below were read from these files, and this section keeps them.

### French Royalty datasets (measured, 16 November 2026)

The 62 French datasets (the five French graphs, their full copies, std1 copies and rule-level copies r25 to r100; 238,537 training triples) were trained with every model, 434 runs, on VANILLA's 80/20 splits:

| | |
|---|---|
| Command | `train_kge.py --datasets <the French datasets> --workers 32 --threads 2 --upload`, run beside the baseline's 60 workers (about 92 runs at once on the 12 MIG slices) |
| Wall-clock time | 51.7 minutes (20:45:59 to 21:37:43); the five original graphs trained earlier in the baseline command, finishing between 20:25 and 20:39 |
| Compute | 32.4 run-hours of training and under a minute of evaluation |
| Failures | 1: CompGCN on `frenchN_M_ours_r50` ran out of GPU memory on its crowded slice; run again alone, it took 27 seconds |
| Not counted | the 70 runs of the r0 copies, trained by the same command (5.2 run-hours) and deleted afterwards |

| Model | Training (run-hours) |
|---|---|
| TransD | 6.9 |
| TransH | 6.4 |
| ComplEx | 5.7 |
| TuckER | 5.2 |
| RotatE | 3.9 |
| TransE | 3.6 |
| CompGCN | 0.7 |

The longest run was TransD on `french_kbgen_r25`, 17.1 minutes.

### Estimates

On the training machine, the seven models trained on `french` (8,265 training triples), 7 at once on separate MIG slices, in 147 s (TransE), 177 s (RotatE), 322 s (ComplEx), 325 s (TuckER), 413 s (TransD), 487 s (TransH) and 19 s (CompGCN). Scaled linearly, the six models other than CompGCN take about 685 run-hours on the 133 datasets (10.9 million training triples), and about 256 run-hours on the baseline. These are lower bounds: on the French datasets that scaling predicted 15.0 run-hours for those six models, and they took 31.7, 2.1 times as much, with about 92 runs sharing the machine; larger graphs can also slow some models more than linearly ("Training the baseline").

Measured earlier on an RTX 3090 for the base set on the earlier splits, without CompGCN: about 29 hours for the 19 datasets other than YAGO3-10 and 9.7 days in all with one process, and about 11 hours and 3.7 days with 4 workers.
