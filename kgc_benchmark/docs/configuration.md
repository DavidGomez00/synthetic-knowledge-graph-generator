# Configuration: input.json

Read by `KGC.py` from the current directory (the file name is fixed in the code as `input.json`). Relative
paths in it are relative to the directory KGC.py runs from, normally the repository root.

| Key | Default | Meaning |
|---|---|---|
| `kg_path` | required | Path to the KG as a **tab-separated** file (subject, predicate, object), or as an N-Triples `.nt` file, which is converted to a `.tsv` next to it. See [Input](kgc.md#input). |
| `results_path` | required | Output directory for splits, models and plots. |
| `models` | `["TransE","TransH","TransD","ComplEx","RotatE","TuckER"]` | PyKEEN model names to train. |
| `num_epochs` | 100 | Training epochs (of every trial too, with `hpo`). |
| `embedding_dim` | 50 | Embedding size. Ignored with `hpo`, which searches it. |
| `batch_size` | 1024 | Training batch size. Ignored with `hpo`, which searches it. |
| `random_seed` | 1235 | Seed for the splits, the training and the HPO sampler. |
| `create_inverse_triples` | false | Add inverse relations to the triples factory. CompGCN needs `true`. |
| `filtered_negative_sampling` | true | Filter true triples out of negative samples. |
| `save_splits` | true | Write `train` and `test` files to `results_path`. `report_results.py` needs `test` for its per-prediction analysis. |
| `log_level` | `INFO` | Python logging level. |
| `hpo` | false | Search each model's hyperparameters before training it. See [Hyperparameter optimization](kgc.md#hyperparameter-optimization). |
| `n_trials` | 30 | With `hpo`: number of trials per model. |
| `validation_ratio` | 0.1 | With `hpo`: share of the training split held out to score the trials. |
