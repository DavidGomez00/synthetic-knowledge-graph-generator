# KGE training protocol

This page describes how every knowledge graph embedding model of the benchmark is trained and evaluated: the data splits, every setting passed to PyKEEN, the evaluation, where the protocol differs from VANILLA, and a full test run on French Royalty. The protocol was fixed on 16 November 2026 and tested on French Royalty. The same day all 62 French datasets were trained with it (434 runs), and the baseline, every model on the 24 original graphs, was started. The code is `scripts/train_kge.py`, `scripts/split_vanilla.py` and `kgc_benchmark/report_results.py`. Every value here was read from the code, from the trained French Royalty models, or from PyKEEN 1.11.1 itself.

## Summary

- Seven models are trained, TransE, TransH, TransD, RotatE, ComplEx, TuckER and CompGCN, once each per dataset, with PyKEEN 1.11.1.
- Every dataset uses VANILLA's own 80/20 train/test split, made by calling VANILLA's code. There is no validation set, no early stopping and no hyperparameter search.
- Six models use VANILLA's settings: 100 epochs, batch size 32, embedding dimension 50 and learning rate 0.001 (Adam, PyKEEN's default), except that on the YAGO3-10 datasets and their copies they train at batch size 4,096. CompGCN trains with a larger batch and learning rate so that it can run on the largest graphs, and its results are compared across datasets, not with the other models.
- Models are evaluated by filtered, rank-based link prediction on the test split, predicting both heads and tails: Hits@1, 3, 5, 10 and 15, and MRR.
- A full run of the seven models on French Royalty trained and evaluated correctly, in 19 s (CompGCN) to 8 min (TransH) per model; the 62 French datasets then took 51.7 minutes and 32.4 run-hours.
- Each copy is compared with its original by Spearman's ρ between the model rankings and by the mean squared error of each metric.

## 1. Data and splits

The benchmark has 133 datasets in `exp_datasets/`: 24 original graphs (French Royalty, Family, SGKG, WN18RR, YAGO3-10 and their 1:1, 1:N, N:1 and N:M sub-graphs) and 109 synthetic copies made by this project's generator, kbgen and PyGraft, including copies at several rule thresholds. The 24 archived copies in `exp_datasets/ours-no-reflexivity-09-11/` are split the same way. The copies come from the branch `pca-partial-rules` (up to commit `ecce66e`), which records how the rule-level copies were made in `runs/rule-levels/`; only their triples files and used rules are taken from it, with `exp_datasets/rule_levels.tsv`, the thresholds of each level.

### Split procedure

1. The source file is the original graph's `<name>.unified.tsv` (for the French Royalty graphs, without their `type` triples) or the copy's `<name>.tsv`.
2. `split_vanilla.py` imports `load_dataset` from VANILLA's `Validated_KG_Completion/KGC.py`, unchanged, and loads the file without inverse triples.
3. It splits with the same call as `KGC.py`, `TriplesFactory.split(random_state=1235)`, PyKEEN's default 80/20 ratio. PyKEEN moves triples into the training part where needed so that every entity and relation appears in training.
4. The two parts are written as `KGC.py` writes them, to `train` and `test`, tab-separated, without header or index. There is no `valid` file, since `KGC.py` has no validation set.

### Checks on the 157 split datasets

- Every source file was checked first: three tab-separated fields per line, LF line endings, no header rows and no duplicate triples.
- Train and test share no triple, and together they hold exactly the triples of the source file.
- Every entity and relation of the test split appears in training.
- Training holds 79.9% to 80.0% of each dataset's triples.
- The split is deterministic: splitting again gives identical files. It depends on the line order of the source file and on the PyKEEN version.

### Earlier splits

Until 16 November 2026 the original graphs had 80/10/10 train/valid/test splits (reproduced exactly by VANILLA's `KGC_hpo.py`), YAGO3-10 had its standard 99/0.5/0.5 split, and the copies had the splits of `scripts/split_copies.py`. All were replaced by the 80/20 split above, and git history keeps them. Results trained on the old splits were deleted, since they are not comparable.

YAGO3-10's old splits stored integer IDs, translated by `id_to_name.json` (now `id_to_name.old-splits.json`, which nothing reads). Its new splits use entity names, as its unified file does. It has 123,182 entities, all present in training.

## 2. Training settings

Every run is one call to PyKEEN's `pipeline` with the arguments below. Anything not listed is PyKEEN 1.11.1's default, and the defaults that matter are listed with the source "default". The settings are the `Settings` class of `scripts/train_kge.py` and are recorded with every dataset's results.

| Setting | Value | Source |
|---|---|---|
| Training loop | sLCWA (stochastic local closed-world assumption) | set |
| Epochs | 100, no early stopping | set |
| Batch size | 32; 4,096 on the YAGO3-10 datasets and their copies (`YAGO_BATCH_SIZE`, see "Why batch size 32" in `kge_training.md`) (CompGCN: see section 3) | set |
| Embedding dimension | 50 (RotatE and ComplEx: 50 complex numbers) | set |
| Inverse triples | yes: each training triple (h, r, t) also adds (t, r⁻¹, h) | set |
| Random seed | 1235 | set |
| Optimizer | Adam, learning rate 0.001, betas (0.9, 0.999), no weight decay, no learning-rate schedule | default |
| Loss | margin ranking loss, margin 1.0, mean reduction, for every model | default |
| Negative sampling | basic sampler, 1 negative per positive, corrupting the head or the tail | default |
| Filtered negatives | yes: negatives that are known training triples are discarded (Bloom filter) | set |
| Last incomplete batch | kept, except for the models with batch normalization (TuckER, CompGCN), where PyKEEN drops it | default |
| Validation | none | |

PyKEEN's `pipeline` uses its default loss, margin ranking, whenever no loss is given, and this replaces each model's own default loss: TuckER's own default, for example, is a binary cross-entropy. VANILLA's `KGC.py` calls the pipeline the same way, so the two agree. This was confirmed by loading every trained model.

### Per-model configuration

The model hyperparameters are PyKEEN's defaults, apart from the embedding dimension. The parameter counts are those of the French Royalty runs (2,644 entities, 22 relations with the inverses).

| Model | Model details (PyKEEN defaults) | Regularization | Parameters | Batch size | Learning rate |
|---|---|---|---|---|---|
| TransE | L1 distance | none | 133,300 | 32; YAGO3-10: 4,096 | 0.001 |
| TransH | L2 distance, relation hyperplanes | norm-limit and orthogonality regularizers, weight 0.05 | 134,400 | 32; YAGO3-10: 4,096 | 0.001 |
| TransD | entity and relation projection vectors, relation dimension 50 | none | 266,600 | 32; YAGO3-10: 4,096 | 0.001 |
| RotatE | complex embeddings, rotation in the complex plane | none | 266,600 | 32; YAGO3-10: 4,096 | 0.001 |
| ComplEx | complex embeddings | Lp regularizer, weight 0.01 | 266,600 | 32; YAGO3-10: 4,096 | 0.001 |
| TuckER | core tensor 50×50×50, dropouts 0.3, 0.4 and 0.5, batch normalization | dropout | 258,500 | 32; YAGO3-10: 4,096 | 0.001 |
| CompGCN | one CompGCN layer (multiplicative composition, symmetric edge weighting, batch normalization, dropout 0), DistMult decoder | none | 143,500 | 131,072, capped (section 3) | 0.03 |

## 3. CompGCN

CompGCN re-encodes the whole graph at every training step, so its cost per step barely depends on the batch size and its training time grows with the number of steps. At VANILLA's batch size 32 it would take about 60 days per YAGO3-10 dataset on this machine. Its results are compared across datasets only, never with the other models, so it trains with its own settings:

- Batch size 131,072 (`Settings.compgcn_batch_size`), capped at the number of training instances, the training triples plus their inverses (`compgcn_batch_size` in `train_kge.py`). On every dataset smaller than that, which is every dataset except the YAGO3-10 ones, CompGCN trains on the whole training set as one batch, one update per epoch. Without the cap PyKEEN finds no complete batch, since it drops the incomplete one of a model with batch normalization, and stops with `NoTrainingBatchError`.
- Learning rate 0.03 (`Settings.compgcn_learning_rate`), since a larger batch gives fewer updates per epoch.
- 100 epochs, as for the other models.

These values are the fastest pair measured on this machine by an earlier speed test, which tried batch sizes up to 131,072 and learning rates from 0.001 to 0.03; at the largest batch sizes, 0.03 gave the best validation MRR on every dataset it tried. Its log, `logs/tune_speed.log`, holds the measurements, made with two runs sharing each MIG slice:

| Measurement | Batch 8,192 | 32,768 | 131,072 |
|---|---|---|---|
| YAGO3-10, 100 epochs | about 6.1 h | about 92 min | about 25 min |
| YAGO3-10, validation MRR at learning rate 0.03 (old splits) | 0.085 (10 epochs) | 0.106 (40 epochs) | 0.108 |
| WN18RR, validation MRR at learning rate 0.03 (old splits) | 0.085 | 0.050 | |

On small datasets one update per epoch means 100 updates in all, which may lower CompGCN's scores there: on WN18RR the larger batch already scored lower (validation MRR 0.050 at 32,768 against 0.085 at 8,192). On French Royalty CompGCN still learned, with MRR 0.201, against 0.245 in an earlier run at batch size 2,048 on the same split.

## 4. Evaluation

- Each test triple (h, r, t) gives two queries: predict the tail of (h, r, ?) and the head of (?, r, t). Every entity is scored as a candidate.
- Filtered setting: before ranking, every other candidate that forms a known true triple, of the training or the test split, is removed.
- Realistic rank: when several candidates tie in score, the correct answer gets the mean of their ranks.
- Metrics: Hits@k for k = 1, 3, 5, 10 and 15, the share of queries whose correct answer ranks in the top k, and MRR, the mean reciprocal rank, over head and tail queries together.
- PyKEEN computes Hits@1, 3, 5 and 10 and MRR at the end of training. `report_results.py` then ranks every test triple again with the saved model, which gives Hits@15 and the per-relation results, and leaves out a run whose recomputed MRR differs from PyKEEN's by more than 0.001.
- A copy is compared with its original by Spearman's ρ between the two rankings of the models (`scipy.stats.spearmanr`), and by the mean squared error of each metric over the models trained on both (`sklearn.metrics.mean_squared_error`), both computed metric by metric (`kgc_benchmark/spearman_rho.py`, `scripts/compare_results.py`, `scripts/results_table.py`). A ρ with p < 0.005 is marked with an asterisk.
- `compare_results.py` writes, for each graph at each threshold, the metrics table, the agreement table and a bump chart of the model ranking (`exp_analysis/results_tables/<graph>_<threshold>/`), and for each graph one MSE table with a row per threshold (`exp_analysis/results_tables/<graph>/mse.*`); `results_table.py` writes the main tables of the benchmark.

## 5. Differences from VANILLA's KGC.py

| Aspect | VANILLA `KGC.py` | This benchmark |
|---|---|---|
| Train/test split | 80/20, `split(random_state=1235)` | the same, made with VANILLA's own code |
| Epochs, batch size, embedding dimension | 100, 32, 50 | the same, except CompGCN |
| Training loop, loss, optimizer, seed | sLCWA, PyKEEN defaults, 1235 | the same |
| Filtered negative sampling | yes | the same |
| Inverse triples | no | yes (CompGCN needs them) |
| Last incomplete batch | always kept (`drop_last=False`) | PyKEEN's default: dropped for TuckER and CompGCN |
| CompGCN | not in its default list of models | trained, with batch size 131,072 (capped) and learning rate 0.03 |
| Interruption | a stopped run starts over | resumes from the checkpoint written after every epoch |

## 6. Test run on French Royalty

The seven models were trained on `french` (8,265 training and 2,067 test triples, 2,644 entities) with exactly the settings above, 7 runs at once, each on its own MIG slice with 3 CPU threads.

| Model | Training time | MRR | Hits@1 | Hits@10 | Loss, epoch 1 → 100 |
|---|---|---|---|---|---|
| TransE | 147 s | 0.225 | 0.025 | 0.624 | 0.881 → 0.004 |
| RotatE | 177 s | 0.504 | 0.450 | 0.586 | 0.874 → 0.002 |
| ComplEx | 322 s | 0.041 | 0.015 | 0.085 | 8.244 → 0.074 |
| TuckER | 325 s | 0.420 | 0.294 | 0.658 | 1.560 → 0.100 |
| TransD | 413 s | 0.221 | 0.014 | 0.684 | 0.748 → 0.012 |
| TransH | 487 s | 0.130 | 0.087 | 0.194 | 0.881 → 0.147 |
| CompGCN | 19 s | 0.201 | 0.107 | 0.396 | 3.844 → 0.009 |

Every model completed its 100 epochs with a falling loss and was evaluated without error. ComplEx scores low although its loss falls; that comes from VANILLA's settings (batch size 32, learning rate 0.001), and with other settings it had reached MRR 0.28 on the same split. CompGCN's first attempt failed because its batch was larger than the training set, which the cap of section 3 fixed.

## 7. Reproducibility and records

- Hardware: 2 AMD EPYC 9554 (81.6 CPUs available to the container), 376 GB of RAM, and 6 NVIDIA RTX PRO 6000 Blackwell GPUs split into 12 MIG 1g.24gb slices. Each run sees one slice.
- Software: Python 3.10.22, PyTorch 2.14.1 (CUDA 13.0, cuDNN 9.24), PyKEEN 1.11.1 and NumPy 2.2.6, pinned in `requirements.txt`.
- Per dataset: `exp_results/<dataset>/settings.json` holds every setting and the SHA-256 of each split, and `train_kge.py` refuses to continue a dataset whose settings or splits changed.
- Per run: `exp_results/<dataset>/logs/<model>.log` holds the command, host, device, library versions, settings, every argument passed to PyKEEN, each split's line count and SHA-256, every epoch's loss and time, the test metrics, and any error with its traceback. The trained model, its training triples, PyKEEN's `results.json` and a loss plot are saved in `exp_results/<dataset>/<model>/`.
- Interruptions: a checkpoint is written after every epoch, so a stopped run resumes where it stopped. The project's earlier tests found that resumed runs give the same losses and MRR as uninterrupted ones.
- Runs per model: one per dataset, with one seed, so the results carry no estimate of the variance between seeds.
- Order: datasets are trained smallest `train` file first, so the YAGO3-10 datasets come last (`--largest-first` reverses it). On the training machine 12 workers run at once, one per MIG slice; [`kge_training.md`](kge_training.md) gives the baseline plan and its expected time.

Commands, from the repository root:

```bash
python scripts/split_vanilla.py exp_datasets/*/*.unified.tsv exp_datasets/*_ours*/*.tsv exp_datasets/*_kbgen*/*.tsv exp_datasets/*_pygraft*/*.tsv
python scripts/train_kge.py --set base --workers 12 --threads 4   # the baseline; --set all for every dataset
python kgc_benchmark/report_results.py
python scripts/compare_results.py
python scripts/results_table.py
```

## 8. Decisions and open points

Decided on 16 November 2026:

- All datasets use VANILLA's 80/20 split, with no validation set, and the empty validation files were removed.
- The hyperparameter search was removed from the project as out of scope, and all models use fixed settings.
- CompGCN uses its own batch size and learning rate, and is compared across datasets only.
- All results trained on the earlier splits or with searched hyperparameters were deleted.
- Ranking agreement is Spearman's ρ (it replaced Kendall's τ), with an asterisk for p < 0.005, and the distance between a copy's and the original's metrics is the mean squared error, metric by metric (it replaced the mean absolute and relative differences).
- The copies made with no rules (level r0) are not part of the benchmark and were removed.
- Datasets train smallest first, so the YAGO3-10 datasets come last.
- An upload to the results branch replaces a dataset's runs there when its settings or splits changed, and a worker no longer waits for another worker's upload.

Open points:

- On small datasets CompGCN gets only 100 updates in all, which may understate its performance there.
- With the fixed default settings some models, ComplEx and TransH in particular, score low on French Royalty.
- The six models other than CompGCN should take about 685 run-hours on the 133 datasets (10.9 million training triples), scaled linearly from the French Royalty timings. This is a lower bound: the 62 French datasets took 31.7 run-hours for those six models, 2.1 times the 15.0 that scaling predicted, with about 92 runs sharing the machine (all seven models: 32.4 run-hours, 51.7 minutes of wall-clock time; [`kge_training.md`](kge_training.md), "Run time").
