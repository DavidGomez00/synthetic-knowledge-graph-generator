# KGC-Benchmark

Find out how well a knowledge graph supports link prediction. KGC-Benchmark trains knowledge graph embedding
models on your graph with [PyKEEN](https://pykeen.readthedocs.io/) and reports Hits@k and MRR. When you have
several versions of a graph, it shows which version predicts better, relation by relation and query by query,
and which version ranks the models most like the original.

## Quick start

```bash
# 1. Install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Point input.json at your graph (see below), then train and evaluate
python KGC.py

# 3. Collect the results of every run into one table, with plots
python report_results.py
```

Run every command from the repository root. A CUDA GPU makes training faster, but you don't need one.

## Your graph

A graph is a tab-separated file with one triple per line: head, relation and tail.

```
E2082	successor	E2102
E1959	predecessor	E150
E1339	spouse	E1690
```

You can also use an N-Triples file (`.nt`). KGC.py converts it to a `.tsv` file with the same name in the same
folder, and trains on that file. IRIs lose their angle brackets: `<http://example.org/spouse>` becomes
`http://example.org/spouse`. Graphs in another RDF format, such as Turtle, have to be converted to one of these
two formats first.

## Configuring a run

`KGC.py` reads its settings from `input.json`. Only two keys are required:

```json
{
  "kg_path": ".data/french_royalty/source/french_royalty.tsv",
  "results_path": "Output/french_royalty/source"
}
```

Add a key only when you want to change its default:

| Key | Default | What it does |
|---|---|---|
| `kg_path` | *required* | The graph to train on, as a `.tsv` or `.nt` file |
| `results_path` | *required* | The folder to save the splits, trained models and plots in |
| `models` | `["TransE", "TransH", "TransD", "ComplEx", "RotatE", "TuckER"]` | The [PyKEEN models](https://pykeen.readthedocs.io/en/stable/reference/models.html) to train |
| `num_epochs` | `100` | Training epochs |
| `embedding_dim` | `50` | Embedding size |
| `batch_size` | `1024` | Training batch size |
| `create_inverse_triples` | `false` | Add an inverse of every relation. **CompGCN requires `true`.** |
| `hpo` | `false` | Tune each model's hyperparameters before training it (see below) |

[docs/configuration.md](docs/configuration.md) lists every key, including the random seed and the logging
level.

### Tuning hyperparameters

With `"hpo": true`, KGC.py searches each model's embedding size, batch size and other hyperparameters, then
trains the model with the best ones. In this mode, `embedding_dim` and `batch_size` are ignored. Two more keys
control the search:

| Key | Default | What it does |
|---|---|---|
| `n_trials` | `30` | Hyperparameter combinations tried per model |
| `validation_ratio` | `0.1` | Share of the training triples held out to score each combination |

The test split is the same with and without HPO, so the results of both modes can be compared.

## Comparing several graphs

Run `KGC.py` once per graph. Give each run its own `results_path`, with all the versions of a graph in one
shared folder:

```
Output/
└── french_royalty/
    ├── source/              ← the original graph (the reference)
    ├── skgg.std=1.filled/   ← a version to compare with it
    └── pygraft/
```

Then run `python report_results.py`. It writes:

- **`Output/metrics_report.csv`**: Hits@1 to Hits@1000 and MRR, with one row per graph and model.
- **`Output/<kg>/prediction_analysis/`**, one per KG (e.g. `Output/french_royalty/prediction_analysis/`): the
  KG's rows of `metrics_report.csv`, the rank of every test prediction, the metrics per relation, and plots:
  - **Hits@1 to Hits@15**: one line per model for each graph (`models_hits_at_1-15.png`), and one line
    per graph for each relation, one figure per model (`<model>/<model>_relations_hits_at_1-15.png`)
  - **the training loss of every model**, one line per graph (`training_loss.png`)
  - the reference graph compared with the other versions, relation by relation and query by query
  - **how alike each version ranks the models**: the Kendall's tau between its model ranking and the
    reference's, by Hits@1, 3, 5, 10, 15 and MRR (`ranking_agreement.csv`)
  - the plots of each model go to a subfolder named after it, e.g. `CompGCN/`

By default, the reference is the folder named `source` and it is compared query by query with the folders
matching `skgg*`. You can change both settings:

```bash
python report_results.py --reference source --compare "skgg*"
python report_results.py --help    # all options
```

Every plot has its title and explanation in a band at the top. For a paper, where that text goes in the
caption, add `--crop`: each analysis folder then also gets a `cropped/` subfolder with the plots without
that band.

The plots name each KG and graph by its folder unless you give it a readable name in the `FOLDER_NAMES`
dictionary at the top of `report_results.py`, e.g. `"french_royalty": "French Royalty"`.

## Documentation

| Page | Contents |
|---|---|
| [KG completion and evaluation](docs/kgc.md) | How the scripts work, the metrics, the output files, the statistical tests and Kendall's tau |
| [Configuration](docs/configuration.md) | Every key of `input.json` |
| [Troubleshooting](docs/troubleshooting.md) | The messages the scripts print, and what to do about them |
| [Backlog](docs/backlog.md) | Planned improvements that aren't implemented yet |

## Origin and license

This project started as the KG completion part of [VANILLA](https://github.com/SDM-TIB/VANILLA), developed by the Scientific Data Management Group at TIB by Disha Purohit and Yashrajsinh Chudasama, supervised by Maria-Esther Vidal. Its history was carried over from that repository.
