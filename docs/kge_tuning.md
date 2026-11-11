# Tuning the KGE models

Hyperparameter tuning was dropped from the benchmark on 2026-11-11, and the search was stopped. The benchmark trains every model with the default settings of `scripts/train_kge.py` ([`kge_training.md`](kge_training.md)), which reads nothing written by `tune_kge.py`. The search finished 7 of its 19 search datasets (`FamilyN_1`, `Family1_N`, `Family1_1`, `FamilyM_N`, `french1_N`, `frenchN_M`, `frenchN_1`) and five of the six models on `family`, without TuckER. Their results are kept in `configurations/kge_hyperparameters.json` and [`kge_hyperparameters.md`](kge_hyperparameters.md). No final run was trained with them. The rest of this page describes the protocol as it was planned.

`scripts/tune_kge.py` searches the hyperparameters of the seven models of the KGC-Benchmark project (TransE, TransH, TransD, RotatE, ComplEx, TuckER, CompGCN) and trains every benchmark dataset with the best ones. This page describes the protocol and how to estimate its run time on a machine. The values it uses are constants at the top of the script, and every search result in `configurations/kge_hyperparameters.json` stores them under `protocol`, so the record is exact even if this page falls behind. [`kge_hyperparameters.md`](kge_hyperparameters.md) is the table generated from that record.

## Why a search

`scripts/train_kge.py` trains every model with the fixed settings of KGC-Benchmark's `input.json`: embedding dimension 50, batch size 32, learning rate 0.001 and 100 epochs. Those results stay in `exp_results` as the default-settings baseline. Some models do poorly with them. ComplEx stays near chance on the small Family splits and on `french1_N` (MRR 0.003 to 0.006, adjusted mean rank index 0.01 to 0.07), and most models were still improving at epoch 100. The tuned runs go to `exp_results_hpo`, so both can be compared.

The search is the same for every model, so the comparison between models stays fair: no model gets hyperparameters of its own tuned, and the data and the evaluation are those of the baseline. A flaw of a model is reported as it is. TransE and TransD, for example, rank the query entity itself first in most test queries, so their Hits@1 is near 0.

## Search datasets

Every dataset is searched on its own, because the datasets of a family differ in size (1,357 to 10,043 training triples among the French ones) and in the cardinality of their relations, and both change which hyperparameters work. The YAGO3-10 family is the exception: a search on each of its datasets would take months, so it is searched once, on its smallest dataset, and its five datasets are trained with the result.

| Search dataset | Trained with its hyperparameters |
|---|---|
| each of the 19 French, Family, WN18RR and SGKG datasets | itself |
| `YAGO3-10_1_1` (272,793 training triples, 10 trials) | `YAGO3-10`, `YAGO3-10_1_1`, `YAGO3-10_1_N`, `YAGO3-10_N_1`, `YAGO3-10_N_M` |

CompGCN is neither searched nor trained on the YAGO family. It encodes the whole graph at every training step, so a single trial on `YAGO3-10_1_1` would take about a day.

## Search space

Each model and search dataset gets one Optuna study, of 30 trials (10 on `YAGO3-10_1_1`), run through PyKEEN's `hpo_pipeline` with the sLCWA training loop, inverse triples, filtered negative sampling and the Adam optimizer. Every model trains with batch size 512 (`BATCH_SIZE`) and searches the same four hyperparameters (`SHARED_RANGES`):

| Hyperparameter | Range |
|---|---|
| embedding dimension | 50, 100 or 200 |
| learning rate | 0.0001 to 0.1, log scale |
| negatives per positive | 1 to 32, log scale |
| corruption of negatives | uniform or Bernoulli |

Batch size 512 is the largest that was measured to run at full speed on a 24 GB GPU: at dimension 200, TuckER peaks at 6.1 GB with it and 32 negatives, and 11.7 GB with 64, and runs out of memory with batch size 1024 and 64 negatives, and PyKEEN then splits each batch into sub-batches, which is slow (see "GPU memory"). A large batch takes fewer steps per epoch and needs a larger learning rate, which the search covers: on `french1_N`, TransE at batch size 256 reached a test MRR of 0.037 with learning rate 0.001, and 0.092 with 0.01. On the smallest datasets an epoch is only a few steps (`french1_N` has 1,356 training triples), so they get fewer steps than the larger ones within the 300 epochs.

A negative is a training triple with its head or its tail replaced by a random entity, and training scores each true triple above its negatives. Uniform corruption replaces the head or the tail with the same probability (PyKEEN's `BasicNegativeSampler`). Bernoulli corruption (`BernoulliNegativeSampler`, from the TransH paper, Wang et al. 2014) replaces the head of a relation's triples with probability tails-per-head / (tails-per-head + heads-per-tail): a 1-N relation such as `hasChild` mostly gets its head replaced, because a random tail would often be another true child. It suits the benchmark, whose 1_1, 1_N, N_1 and N_M datasets differ in the cardinality of their relations. PyKEEN's HPO takes one sampler class, so `CorruptionSampler` takes the choice as a parameter and delegates to one of the two; the record names the chosen PyKEEN sampler (`basic` or `bernoulli`), which the final run uses. Negatives are filtered in both cases: a negative that is a training triple is dropped. At most 32 negatives are drawn per positive: more cost time and memory, and the calibration's worst trials used 52 to 55.

PyKEEN would also search a default range for some hyperparameters of particular models: the margin of the margin ranking loss (TransE, TransH, TransD, RotatE, CompGCN), the scoring norm of TransE and TransH, the relation dimension of TransD and TuckER, TuckER's three dropouts, and the weight of TransH's regularizer. `pinned_kwargs` fixes each of them to the model's default (margin 1, TransE norm 1, TransH norm 2, TuckER dropouts 0.3, 0.4 and 0.5, TransH regularizer weight 0.05), so they are not searched.

Trials are proposed by Optuna's TPE sampler. A new sampler is made for every trial, so its seed is 1235, plus a number derived from the search dataset and model name (`crc32` of `<dataset>/<model>` modulo 10⁶), plus the number of trials already run. Each study therefore starts from different random configurations, and a rerun proposes the same ones. PyKEEN gives each trial a random training seed that `hpo_pipeline` can't set; the record keeps the best trial's seed as `trial_random_seed`.

## Trials, stopped early

A trial trains for at most 300 epochs and is checked every 5 epochs by its MRR on a validation sample. Two mechanisms end a trial earlier:

- PyKEEN's early stopper ends it when the MRR of 5 checks in a row (patience 5) has not improved by at least 1% (relative delta 0.01) on the best one, and restores the weights of the best check.
- Optuna's Hyperband pruner (minimum 5 epochs, maximum 300, reduction factor 3) receives each check and stops a trial whose MRR ranks below most of the trials that reached the same epoch.

A trial that is not pruned is scored by its MRR on the validation sample at the end of training, and only those trials can be chosen as the best one. The score is MRR, not Hits@1, because TransE and TransD keep a Hits@1 near 0 whatever their hyperparameters, which would leave their search with nothing to compare.

In the calibration runs, trials on `french` stopped between epochs 30 and 115, and pruned trials ended within 10 seconds.

## Validation sample

Checks and scores use a fixed random sample of at most 5,000 validation triples (seed 1235). The datasets with more are the four SGKG ones (5,459 to 7,274), `WN18RR`, `WN18RRN_M`, `WN18RR1_1` and `WN18RRN_1` (6,538 to 9,301), and the four YAGO3-10 subsets (34,100 to 104,280), whose checks would otherwise take much longer than the training between them. The checks of the early stopper remove only the training triples from the ranking. The score of a trial and the test evaluation of a final run remove the training triples and the whole validation split.

## Final runs

Each dataset is trained with the best hyperparameters of its search, read from the record. The number of epochs is not copied from the search: the dataset's own validation sample chooses it, with the same early stopper as in the trials, for at most 300 epochs. When training ends, PyKEEN restores the model of the best check. Its weights are kept in `exp_checkpoints_hpo/<dataset>/<model>.best.pt`, which is deleted once the model is finished; the search deletes the best-weights files its trials leave in PyKEEN's checkpoint folder after each trial. The final runs use seed 1235 and are evaluated on the whole `test` split, filtering the training triples and the whole validation split, as the baseline is.

## Differences from KGC.py's `hpo` mode

- Every model searches the same four hyperparameters, with a fixed batch size of 512. `KGC.py`'s `get_model_specific_params` adds model-specific ranges (TransE's scoring norm, ComplEx's regularizer, TuckER's relation dimension, dropouts and batch normalization), and PyKEEN's defaults add the margin and others.
- Trials are scored by MRR. `KGC.py` scores them by Hits@1.
- Trials are scored on each dataset's own `valid` split. `KGC.py` holds 10% of the training triples out instead, because its graphs have no validation split.
- A trial trains for at most 300 epochs and is stopped early by the early stopper and the Hyperband pruner. In `KGC.py` every trial trains for 100 epochs.
- Final runs choose their number of epochs by early stopping on their own validation sample, up to 300. `KGC.py` trains its final model for 100 epochs, on its whole training split including the 10% it held out.

## Running, resuming and reproducing

```bash
python scripts/tune_kge.py --skip-models CompGCN                     # search every dataset, then train every dataset
python scripts/tune_kge.py --phase tune --searches french family     # only the searches on french and family
python scripts/tune_kge.py --phase train                             # only the final runs, from the record
```

Both phases can be killed at any moment, and the same command resumes them. A trial in progress is lost. Optuna marks it as failed once its heartbeat is older than two minutes, and runs its parameters once more. A final run resumes from its last epoch, with the state of its early stopper, and gives the same result as an uninterrupted run. A trial that fails, for example by running out of GPU memory, is logged and the study goes on. A run gives up on a study after 10 of its trials failed, and the next run tries it again with another 10.

The final runs need only the record: `--phase train` reproduces them from `configurations/kge_hyperparameters.json` without the study databases. Each final run writes the configuration it used, with the epoch its early stopping kept, to `exp_results_hpo/<dataset>/hparams/<model>.json`, and refuses to resume a model whose configuration in the record changed.

## Outputs

| Path | Content |
|---|---|
| `exp_hpo/<search dataset>/<model>/study.db` | the Optuna study (SQLite) |
| `exp_hpo/<search dataset>/<model>/trials.tsv` | every trial with its parameters, state, value and `duration`. A pruned trial's value is its last MRR check |
| `exp_hpo/progress.csv` | trials complete, pruned, failed and running per study, with the best MRR |
| `configurations/kge_hyperparameters.json` | the record: per search dataset and model, the best trial's pipeline configuration, score, trial counts, search space, protocol and library versions |
| `docs/kge_hyperparameters.md` | the table generated from the record |
| `exp_results_hpo/` | the final runs, in the layout of `exp_results`, so `report_results.py --root exp_results_hpo` reads them |
| `logs/tune_kge.log` | the full log of every run, PyKEEN's and Optuna's messages included; the terminal shows only the script's own lines, one per trial, and warnings |

## GPU memory

A training step's memory depends on the batch size, the negatives per positive and the embedding dimension, and only a little on the dataset: the embedding tables and Adam's state take at most 0.18 GB, on YAGO3-10's 123,143 entities. Measured with PyTorch's peak allocation at batch size 512, dimension 200, and with full batches (PyKEEN's sub-batching turned off):

| Model | 32 negatives (the search's largest) | 64 negatives |
|---|---|---|
| TuckER | 5.8 to 6.1 GB | 11.4 to 11.7 GB |
| TransE, TransH, TransD, RotatE, ComplEx | 0.2 to 1.0 GB | 0.2 to 1.05 GB |

TuckER sets the requirement, because its core tensor grows with batch size × negatives × dimension². With batch size 512 and at most 32 negatives, the search needs an 8 GB GPU, tightly, and runs comfortably on 10 to 12 GB: the peak, plus about 0.5 GB of CUDA context and room for fragmentation. The other five models alone fit in 4 GB. Batch size 1024 doesn't fit on a 24 GB GPU at 64 negatives (TuckER fails to allocate a 9.8 GB tensor on an otherwise empty RTX 3090). On a GPU too small for a batch, PyKEEN splits it into sub-batches, so the trial still runs, more slowly.

Evaluation is not part of the requirement: `tune_kge.py` lets PyKEEN choose the evaluation batch size, which it lowers until the batch fits. A fixed evaluation batch of 32 would take up to 15 GB (TransD on YAGO3-10), so on a smaller GPU the checks only get slower.

## Estimating the run time

The run time depends on the GPU, so it is measured on each machine with a short calibration run before the search starts. A calibration is needed once per machine; the same rates serve every set of datasets trained on it, since the script and the models don't change.

1. Run a few trials of every model on one small and one medium dataset, writing to a throwaway folder so nothing reaches `exp_hpo` or the record. On an RTX 3090 this takes about 1.5 hours, most of it on SGKG1.

   ```bash
   CAL=/tmp/kge_calibration
   A="--phase tune --hpo-output $CAL/hpo --hyperparameters $CAL/record.json --table $CAL/table.md --skip-models CompGCN"
   python scripts/tune_kge.py $A --searches french --trials 3
   python scripts/tune_kge.py $A --searches SGKG1 --trials 2
   ```

2. Turn the trial durations into an estimate for a set of datasets:

   ```bash
   cd scripts && python estimate_tuning_time.py $CAL/hpo --root ../exp_datasets                   # the base datasets
   cd scripts && python estimate_tuning_time.py $CAL/hpo --root ../exp_datasets --suffix _ours     # the datasets named <base name>_ours
   ```

   `estimate_tuning_time.py` reads the `duration` of every complete or pruned trial in the calibration's `trials.tsv` files and computes, per model, the seconds a trial takes per 1,000 training triples. It multiplies that rate by each search dataset's training triples and number of trials for the search, and counts one trial's worth per model and trained dataset for the final runs. With `--suffix`, it reads the training triples of `<name><suffix>`, and uses the base dataset's when that folder doesn't exist yet. It prints one row per search dataset and the total for the set.

3. Read the result with its limits. Trial time grows with the number of training triples, but also with the number of entities, which the rate ignores, so the YAGO rows, extrapolated from graphs 5 to 20 times smaller, are the least reliable. A calibration study has only 2 or 3 trials, which the pruner seldom stops, so the estimate tends to be high: later trials of a full study are pruned more often. Add CompGCN only after calibrating it on the same datasets; it is much slower than the other six.

Reference, on an RTX 3090 with the six models other than CompGCN: the calibration took 1 hour 28 minutes and measured 2.8 to 9.5 seconds per trial per 1,000 training triples (RotatE fastest, ComplEx slowest). The script estimated 6.9 days of search and 1.5 days of final runs for the base set, about 8.4 days in all, of which 23 hours of search and 32 hours of final runs are on the YAGO family. The calibration trials used batch sizes 128 to 1024; the search now uses 512 only.
