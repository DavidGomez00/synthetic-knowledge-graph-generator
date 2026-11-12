# KG completion and evaluation

KGC-Benchmark trains knowledge graph embedding models with [PyKEEN](https://pykeen.readthedocs.io/) on one
or more graphs, reports standard link-prediction metrics, and compares the graphs prediction by prediction and
by how they rank the models.

```
KGC-Benchmark/
├── KGC.py               Train and evaluate models, optionally after hyperparameter optimization
├── input.json           Configuration for KGC.py
├── report_results.py    Collect Hits@k and MRR into one CSV and analyse every test prediction
├── cliffs_delta.py      Cliff's delta, the effect size of the relation comparison
└── kendall_tau.py       Kendall's tau between the model rankings of two graphs
```

## Input

`KGC.py` reads a **tab-separated** `.tsv` file of triples: one triple per line, head, relation and tail separated by tabs.

It also reads **N-Triples** (`.nt`) files. When `kg_path` ends in `.nt`, KGC.py first converts the file to a `.tsv` with the same name in the same folder (`.data/kg/graph.nt` becomes `.data/kg/graph.tsv`), then runs as usual on the `.tsv`:

- IRIs are written without their angle brackets: `<http://example.org/spouse>` becomes `http://example.org/spouse`.
- Blank nodes (`_:b1`) and literals (`"42"^^<http://www.w3.org/2001/XMLSchema#integer>`, `"Anne"@en`) are written as they appear in the `.nt` file. A literal is an entity like any other, so remove the literal triples from the graph first if you don't want them in the training and test splits.
- Empty lines and comments are skipped. A line that isn't an N-Triples statement stops the run with the line number.
- If the `.tsv` already exists and is newer than the `.nt` file, KGC.py uses it without converting again. Otherwise, it **overwrites** the `.tsv`, so don't keep another graph under that name.

A graph in another RDF format, such as Turtle, must be converted to N-Triples or TSV first.

## KGC.py
```bash
python KGC.py
```

It reads `input.json` (see [configuration](configuration.md)) and:

1. Converts the graph to a `.tsv` if it's an `.nt` file (see [Input](#input)), and loads the `.tsv` into a PyKEEN `TriplesFactory`.
2. Splits it into training (80%) and testing (20%) sets.
3. If `save_splits` is true, writes `train` and `test` tab separated files to `results_path`.
4. For every model in `models`: trains it on the training split with PyKEEN's `pipeline` using the sLCWA training loop, the configured embedding size, batch size and epochs, and optionally filtered negative sampling.
5. Saves the pipeline results to `<results_path>/<model>/` and a `loss_plot.png` next to them.

Example configuration, training every model on the PyGraft version of the French royalty KG:

```json
{
  "kg_path": ".data/french_royalty/pygraft/french_royalty.pygraft.tsv",
  "results_path": "Output/french_royalty/pygraft",
  "models": ["TransE", "TransH", "TransD", "RotatE", "ComplEx", "TuckER", "CompGCN"],
  "batch_size": 32,
  "create_inverse_triples": true
}
```

Every key left out takes its default (see [configuration](configuration.md)).

### Hyperparameter optimization

With `"hpo": true`, KGC.py searches each model's hyperparameters before step 4, using PyKEEN's
`hpo_pipeline`:

1. Holds `validation_ratio` of the training split out as a validation set, once for all models. The `test`
   split is the same as in a run without HPO with the same `random_seed`, so both runs can be compared.
2. Runs `n_trials` trials per model on the rest of the training split, each scored by Hits@1 on the
   validation set. The trials search the embedding size, the batch size, the number of negatives per
   positive, PyKEEN's default ranges for the optimizer and loss, and model-specific ranges defined in
   `get_model_specific_params`. `embedding_dim` and `batch_size` from `input.json` are ignored.
3. Saves the study to `<results_path>/<model>/hpo/`: `study.json`, `trials.tsv` and
   `best_pipeline/pipeline_config.json`.
4. Trains the model with the best trial's hyperparameters on the **whole** training split and saves it like a
   run without HPO (step 5), so `report_results.py` reads it too.

## Comparing graphs

To compare graphs, for example a KG and synthetic graphs generated from it, run KGC.py once per graph with the same models and the same `random_seed`. Give each graph its own `results_path` under a common folder (see [Folder layout](#folder-layout)), then run `report_results.py`.

Each graph then works as a benchmark for those models. `ranking_agreement.csv` shows which synthetic graph ranks the models most like the original (see [Comparing model rankings across graphs](#comparing-model-rankings-across-graphs)). A model trained on only one of two graphs is left out of their comparison, so train every model on every graph.

The models used so far are TransE, TransH, TransD, RotatE, ComplEx, TuckER and CompGCN. Other PyKEEN models can be listed in `models` too, but haven't been tried.

> A graph normalized by [VANILLA](https://github.com/SDM-TIB/VANILLA) gives each triple a predicate specific to its object, so it has many more distinct relations than the original. Keep this in mind when comparing models and reading their memory use.

## report_results.py

`report_results.py` gathers the runs of every graph into one place, in two steps:

1. **The metrics table.** It writes Hits@1 to Hits@1000, MRR and count, from the `both` / `realistic` slice (see [Evaluation metrics](#evaluation-metrics)), to `metrics_report.csv` in the `--root` folder (`Output/` by default). There is one row per dataset and model. MRR, count and Hits@1, 3, 5 and 10 come from the run's `results.json`. PyKEEN doesn't store the other values of k, so they are counted from the ranks of the per-prediction analysis, and stay empty for a run it leaves out.
2. **The per-prediction analysis.** It recomputes the rank of every test prediction behind that table, and compares the graphs relation by relation and query by query. It also compares how each graph ranks the models. The results go to one folder per KG, `Output/<kg>/prediction_analysis/`. See [Per-prediction analysis](#per-prediction-analysis).

```bash
python report_results.py                                         # source vs every skgg* folder
python report_results.py --root Output/french_royalty --crop      # one KG: source vs its synthetic graphs
python report_results.py --reference source --compare "skgg.std=*"
```

### Folder layout

The script finds every `results.json` under `--root` and expects the layout KGC.py writes:

```
<root>/<dataset...>/<model>/results.json
```

`<dataset...>` is the `results_path` of the run, relative to `--root`. It can be more than one folder, and the `dataset` column reports it with `/`, e.g. `french_royalty/skgg.std=1.filled`. When `--root` is one KG's folder, such as `Output/french_royalty`, the KG folder is still included, so the CSV files and plots are the same as the ones a run over all of `Output` writes for that KG. The analysis splits it in two:

| Part | Example | Meaning |
|---|---|---|
| `kg` | `french_royalty` | The parent folders: the KG that the graphs come from. |
| `graph` | `skgg.std=1.filled` | The last folder: one graph of that KG. |

Graphs are only compared with graphs of the same `kg`. Give each graph of a KG its own `results_path` under a common parent folder, for example:

```
Output/french_royalty/
├── source/              the reference graph (--reference)
├── skgg.std=1.filled/   compared query by query (matches --compare "skgg*")
└── pygraft/             compared relation by relation only
```

A folder whose `results.json` can't be read, or lacks the `both` / `realistic` slice, is skipped with a warning. The rest of the report still runs.

The plots show each KG folder and graph folder under a readable name from the `FOLDER_NAMES` dictionary at the top of `report_results.py`, for example `french_royalty` as "French Royalty" and `skgg.pca=0.9.filled` as "SKGG (PCA 0.9)". A folder that isn't in it is shown by its folder name. The CSV files and the plot file names always use the folder names, and so do `--reference` and `--compare`.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--root` | `Output` | Results folder to scan. |
| `--output`, `-o` | `<root>/metrics_report.csv` | Path of the metrics table. |
| `--analysis-dir` | `<root>/<kg>/prediction_analysis` | Where to write each KG's per-prediction analysis (CSV files and plots) instead: `<analysis-dir>/<kg>/`. |
| `--reference` | `source` | Graph folder that every other graph of its KG is compared with. |
| `--compare` | `skgg*` | Graph folders compared with the reference query by query: graphs that keep the reference's entity names. Shell-style patterns, several allowed. Quote them so the shell does not expand them. |
| `--crop` | off | Also write every plot without its caption band to a `cropped/` subfolder of its KG's analysis folder, ready for a publication. |

The defaults are relative to the repository root, wherever the script is run from. Paths passed on the command line are relative to the current directory.

Each run overwrites the files in a KG's analysis folder, but it does not delete files from earlier runs. A plot of a graph or model that is no longer under `--root` stays, and so does a comparison CSV that the new run did not write (see the `note:` messages in [troubleshooting](troubleshooting.md#report_resultspy)). Empty the folder to start clean.

## Evaluation metrics

### How a test triple is ranked

Every metric is a summary of **ranks**. For each test triple `(h, r, t)`, PyKEEN makes two predictions:

- **Tail prediction** `(h, r, ?)`: score `(h, r, e)` for every entity `e` in the graph, sort the candidates from best to worst score, and record the position of the true tail `t`. Rank 1 means the model put the correct answer first.
- **Head prediction** `(?, r, t)`: the same, scoring `(e, r, t)` for every entity `e` and recording the position of `h`.

The evaluation is **filtered**, which is PyKEEN's default and the standard in the literature. Before ranking, every candidate that forms a triple already known to be true (in the training set or the test set) is removed, except the one being tested. Without this, a query such as `(Louis_XIV, child, ?)`, which has several correct answers, would penalize the model for ranking another real child above the one being tested.

**The number of predictions is not the number of candidates.** A relation's count of test predictions, the `n` in the plots, is twice its number of test triples. Each prediction is still ranked among every entity of the graph, not only the entities of that relation. For example, PyGraft's `child` relation has 95 test triples, so n = 190, but each prediction is ranked among about 1,086 entities. Hits@190 is therefore the share of predictions whose correct answer is in the top 190 of about 1,086, and it does not reach 1 at k = n. With CompGCN it is 0.28, against about 0.17 for a random guess, and the median correct answer ranks 435th. A Hits@k curve only reaches 1 at the graph's worst possible rank, about its number of entities.

### Where the numbers are stored

Each `<results_path>/<model>/results.json` stores the metrics as `metrics[<side>][<ties>][<metric>]`:

| Level | Values | Meaning |
|---|---|---|
| `<side>` | `head`, `tail`, `both` | Ranks from head prediction, tail prediction, or both pooled together. |
| `<ties>` | `optimistic`, `pessimistic`, `realistic` | How ties are ranked when other candidates get exactly the same score as the true answer: optimistic puts the true answer first among them, pessimistic puts it last, and realistic uses the average of the two. A large gap between optimistic and pessimistic means the model gives many entities the same score. |

`report_results.py` reads the `both` / `realistic` slice, which is the one to report.

### Metrics collected by `report_results.py`

| Metric | Key in `results.json` | Range | Better | Meaning |
|---|---|---|---|---|
| Hits@k (k = 1 to 1000) | `hits_at_1` to `hits_at_1000` | 0 to 1 | Higher | Fraction of test predictions where the true entity is among the top *k* candidates. Hits@1 is the fraction where the model's single best guess is correct. Hits@10 = 0.40 means the right answer is in the top 10 for 40% of predictions. Hits@k never decreases as k grows. `results.json` only holds k = 1, 3, 5 and 10. |
| Mean Reciprocal Rank (MRR) | `inverse_harmonic_mean_rank` | 0 to 1 | Higher | Mean of `1 / rank` over all predictions. Rank 1 adds 1, rank 2 adds 0.5, rank 10 adds 0.1, and rank 1000 adds almost nothing. It rewards answers at or near the top, and it barely changes whether a missed answer sits at rank 200 or rank 2000. |
| count | `count` | Up to 2 × test triples | n/a | Number of ranks the metrics average over. In the `both` slice this is two per test triple (one head prediction and one tail prediction). |

### Other metrics in `results.json`

PyKEEN stores further rank statistics that `report_results.py` does not collect. They can be read directly from `results.json`.

| Metric | Key | Better | Meaning |
|---|---|---|---|
| Mean Rank (MR) | `arithmetic_mean_rank` | Lower | Average rank, from 1 up to the number of entities. Unlike MRR, a few very bad ranks can pull it up a lot. |
| Geometric / harmonic mean rank | `geometric_mean_rank`, `harmonic_mean_rank` | Lower | Other averages of the ranks. The harmonic mean rank is `1 / MRR`. |
| Median rank | `median_rank` | Lower | Rank of the middle prediction. Half the predictions rank better than this. |
| Inverse ranks | `inverse_arithmetic_mean_rank`, `inverse_geometric_mean_rank`, `inverse_median_rank` | Higher | `1 /` the corresponding rank statistic. |
| Spread | `standard_deviation`, `variance`, `median_absolute_deviation` | n/a | How much the ranks vary across predictions. |
| Adjusted Mean Rank (AMR) | `adjusted_arithmetic_mean_rank` | Lower | MR divided by the MR expected from random scoring. 1 = random, near 0 = perfect, range 0 to 2. |
| Adjusted Mean Rank Index (AMRI) | `adjusted_arithmetic_mean_rank_index` | Higher | MR rescaled so that 1 = perfect, 0 = random and −1 = worst possible. `adjusted_geometric_mean_rank_index` does the same for the geometric mean rank. |
| Adjusted MRR | `adjusted_inverse_harmonic_mean_rank` | Higher | MRR rescaled so that 1 = perfect and 0 = random. Negative values are worse than random. |
| Adjusted Hits@10 | `adjusted_hits_at_k` | Higher | Hits@10 rescaled the same way: 1 = perfect, 0 = random. |
| z-scores | `z_arithmetic_mean_rank`, `z_geometric_mean_rank`, `z_inverse_harmonic_mean_rank`, `z_hits_at_k` | Higher | How many standard deviations the result lies above random scoring. They grow with test-set size, so they show how confident one can be that the model beats random, not how good it is. |

### Comparing metrics across graphs

The expected score of a random model depends on how many candidate entities each query has. For example, a random guess is in the top 10 far more often among 500 entities than among 5,000. Hits@k, MRR and MR are therefore not on the same scale for two graphs with different numbers of entities, such as a KG and a synthetic graph generated from it. The adjusted metrics (AMRI, adjusted MRR, adjusted Hits@10) subtract out that random baseline. Report them alongside Hits@k and MRR when comparing graphs of different sizes.

## Per-prediction analysis

After writing the table, [`report_results.py`](#report_resultspy) looks at every test prediction behind it. The results of each KG go to its own folder, `Output/<kg>/prediction_analysis/` (e.g. `Output/french_royalty/prediction_analysis/`), since its graphs are only compared with each other. The CSV files hold only that KG's runs, and the file names below are relative to that folder. The plots of the whole KG are in the folder itself, and the plots of each model in a subfolder named after it, e.g. `CompGCN/`. With `--crop`, `cropped/` repeats the same layout.

### Recomputing the ranks

`results.json` stores only aggregates. For every run, the script reloads three things:

- `trained_model.pkl`
- the id mappings and training triples in `training_triples/`
- the `test` file KGC.py wrote to `results_path`, so `save_splits` must be `true`

It then ranks both predictions of every test triple the way PyKEEN's evaluation does: filtered, with `realistic` ties. The ranks of a run therefore average to its row in `metrics_report.csv`.

KGC.py writes `train` and `test` before it trains the models. While it is retraining a `results_path`, the `test` file on disk does not belong to the models still in that folder. A run whose recomputed MRR differs from its `results.json` by more than 0.001 is therefore left out of the analysis with a warning.

### Pairing predictions between graphs

The comparison answers one question: **when a prediction is accurate in one graph, is it also accurate in the other?** It compares a reference graph (`--reference`, default `source`) with every sibling folder that matches `--compare` (default `skgg*`, e.g. `skgg.std=1.filled`). Only models trained on both graphs are compared.

These graphs share no test triples, even when one is generated from the other. SKGG keeps each entity's relations but rewires their targets: `Marie_Antoinette` keeps her 3 `child` triples, but with different children. Each graph is also split on its own. Predictions are therefore paired by **query**:

- a tail query `(h, r, ?)`, such as `(Marie_Antoinette, child, ?)`
- a head query `(?, r, t)`, such as `(?, child, Marie_Antoinette)`

A query pairs when it has at least one answer in the `test` file of both graphs. Each graph ranks its own held-out answers. A query is **accurate at k** in a graph when at least half of its held-out answers rank in the top k. Most queries have a single answer, so this is simply a hit or a miss.

PyGraft names its entities `E1`, `E2`, ..., so none of its queries pair with the source graph. Its runs still appear in `predictions.csv` and `relation_report.csv`.

### Output files

Every plot keeps its title and explanation in a band at the top, exactly 330 pixels tall (2.2 inches at 150 dpi) and closed by a faint rule. Nothing else is drawn in it, so cropping the top 330 pixels leaves the bare figure for a publication, where that text belongs in the caption. Run with `--crop` to have the script write these cropped copies to a `cropped/` subfolder of each analysis folder. They are recropped from the full plots on every run, so they never go out of date.

| File | One row per | Contents |
|---|---|---|
| `metrics_report.csv` | dataset and model | The rows of the metrics table (`--output`) for this KG only, with the same columns |
| `predictions.csv` | test prediction of every run | `side` (the entity predicted, `head` or `tail`), the test triple, `rank`, `candidates` (the entities it was ranked among, after filtering), `reciprocal_rank`, and `hits_at_1` to `hits_at_15` (1 or 0; `rank` gives any other k) |
| `relation_report.csv` | dataset, model and relation | The columns of `metrics_report.csv`, computed per relation |
| `training_losses.csv` | dataset, model and epoch | The training `loss` of each epoch, as PyKEEN stores it in `results.json`: averaged over the training triples. Every run of the table is included, even one the analysis leaves out |
| `relation_comparison.csv` | KG, compared graph, model and relation | Each relation in the reference graph against every other graph, see [Comparing relations across graphs](#comparing-relations-across-graphs) |
| `ranking_agreement.csv` | KG, compared graph and metric | How alike the reference graph and every other graph rank the models, as a Kendall's tau, see [Comparing model rankings across graphs](#comparing-model-rankings-across-graphs) |
| `query_comparison.csv` | query paired between two graphs | The `query`, then for each graph (`reference_` and `compared_` columns): its number of held-out `answers`, their `mrr`, and the share of them in the top k (`hits_at_k`, for k = 1, 3, 5 and 10) |
| `query_comparison_summary.csv` | comparison, model, group of queries and k | See the table below |
| `relations.png` | KG | `relation_report.csv` as a chart: one row of panels per model, MRR and Hits@10 per relation, and one column per graph. Each graph keeps its color in every plot: the reference first, then the compared graphs, then the rest (e.g. source, skgg.std=1.filled, pygraft) |
| `<model>/<model>_hits_at_k.png` | KG and model | A first panel over all relations, then one panel per relation, and one line per graph: the share of test predictions whose true answer ranks in the top k, for every k (its height at k = 1 and k = 10 is Hits@1 and Hits@10). Under each panel's title, one line per graph gives its number of test predictions (n), its Cliff's delta against the reference (δ) and the Holm-corrected Mann-Whitney p-value (p, shown as p<0.001 below 0.001), in bold when it does not differ significantly from the reference. The figure is widened past 13 inches when these lines need more room |
| `models_hits_at_1-15.png` | KG | One panel per graph and one line per model: Hits@1 to Hits@15, to compare how fast each model's accuracy grows with k. Each model keeps its color in every plot of this kind |
| `<model>/<model>_relations_hits_at_1-15.png` | KG and model | Hits@1 to Hits@15 of one model on every graph: a first panel over all relations, then one panel per relation, and one line per graph, in the graph colors of the other plots. Each panel gives its number of test predictions per graph, since a rare relation rests on few of them. A graph without a line in a panel doesn't have that relation |
| `training_loss.png` | KG | One panel per model and one line per graph: the training loss of each epoch, on a log scale. Each model has its own loss function, so its panel has its own scale: compare the lines of a panel, not the panels. A loss that is still falling at the last epoch suggests more epochs would help |
| `<model>/<compared>_<model>.png` | comparison and model | Left: how the ranks of all test predictions of each graph spread over rank buckets. Right: for the paired queries, how each rank bucket of the reference graph spreads over the buckets of the compared graph |

`query_comparison_summary.csv` has one row per group of queries and per k (1, 3, 5 and 10). There are three kinds of group:

- all paired queries (`relation` = `all`, `side` = `both`)
- the head queries and the tail queries (`side` = `head` or `tail`)
- the queries of each relation

| Column | Meaning |
|---|---|
| `paired_queries` | Number of queries in the group. |
| `reference_mrr`, `compared_mrr` | Mean per-query MRR in each graph. |
| `spearman_rho`, `spearman_p` | Rank correlation between the two graphs' per-query MRR, and its p-value. Near 0, how hard a query is in one graph says nothing about how hard it is in the other. Empty below 3 queries, or when one graph gives every query the same value. |
| `both_accurate`, `only_reference`, `only_compared`, `neither` | Queries accurate at k in both graphs, in only one of them, or in neither. |
| `compared_accurate_when_reference_accurate` | Share of the queries accurate in the reference graph that are also accurate in the compared graph. |
| `compared_accurate_when_reference_inaccurate` | The same share among the queries the reference graph gets wrong. The gap between these two shares shows how much accuracy in one graph predicts accuracy in the other. |
| `cohen_kappa` | Agreement on accurate versus inaccurate, corrected for chance. 1 means the graphs always agree, 0 means no better than chance, and a negative value means worse than chance. |

### Comparing relations across graphs

`relation_comparison.csv` asks, for each relation and model: **are the ranks in another graph distributed differently from the reference graph's?** It differs from the paired-query comparison in two ways:

- It uses every test prediction of the relation in each graph, not only the queries held out in both.
- It needs no shared entities, so it compares the reference with every other graph of the KG, PyGraft included. A relation is compared when both graphs have it; `type` exists only in PyGraft's graph, so it is not compared.

Each graph pair and model also gets a row with `relation` = `all`, which compares all the test predictions of the two graphs. These include the relations only one graph has, such as PyGraft's `type`, so they match the "all relations" panel of `<model>_hits_at_k.png`.

The graphs have different numbers of entities (PyGraft 1,089, source 2,038), so a raw rank of 500 is a random guess in one and above random in the other (see [Comparing metrics across graphs](#comparing-metrics-across-graphs)). The distribution tests therefore use the **normalized rank** `(rank − 1) / (candidates − 1)`: 0 means ranked first, 1 means ranked last, and a random guess averages 0.5. PyKEEN's AMRI (`adjusted_arithmetic_mean_rank_index`) is 1 − 2 × their mean, when every query has the same number of candidates.

| Column | Meaning |
|---|---|
| `reference_predictions`, `compared_predictions` | Number of test predictions of the relation in each graph. |
| `reference_mrr`, `compared_mrr`, `mrr_difference` | MRR of the relation in each graph, and compared minus reference. |
| `mrr_difference_low`, `mrr_difference_high` | 95% bootstrap interval of `mrr_difference` (2,000 resamples of each graph's predictions, fixed seed). An interval without 0 means the MRR difference is unlikely to be chance. |
| `reference_hits_at_10`, `compared_hits_at_10` | Hits@10 of the relation in each graph. |
| `cliffs_delta` | The chance that a prediction of the compared graph ranks better than one of the reference graph, minus the reverse, on normalized ranks. From −1 (the compared graph always ranks worse) to +1 (always better); 0 means neither tends to rank better. See [Cliff's delta](#cliffs-delta). |
| `effect` | The size of `cliffs_delta`: negligible below 0.147, small below 0.33, medium below 0.474, large above (Romano et al., 2006). |
| `mannwhitney_p` | Mann-Whitney U test: does one graph tend to rank better than the other? |
| `ks_statistic`, `ks_p` | Kolmogorov-Smirnov test: do the two rank distributions differ in any way, including in shape? The statistic is the largest gap between their Hits@k curves (on normalized ranks). |
| `mannwhitney_p_holm`, `ks_p_holm` | The p-values after a Holm correction over the relations of the same graph pair and model. Use these to decide significance. See [p-values](#p-values). |

The bootstrap interval and the tests can disagree. The interval is about the mean of the reciprocal ranks, which only the top of the ranking moves. The tests weigh every prediction, wherever it ranks. A relation can therefore gain MRR in a few top-ranked predictions while its distribution as a whole does not change significantly.

See [Cliff's delta and p-values](#cliffs-delta-and-p-values) for how `cliffs_delta` and the p-values are computed and how to read them.

### Comparing model rankings across graphs

`ranking_agreement.csv` treats each graph as a **benchmark** and asks: **does another graph rank the models in the same order as the reference graph?** A synthetic graph that does would lead to the same conclusions about which model is best, even when every model scores higher or lower on it. For example, the ranking of models by their MRR on `source` is compared with their ranking by MRR on `skgg.std=1.filled`.

- It compares the reference with every other graph of the KG, PyGraft and KBGen included, since a ranking needs no shared entities.
- It ranks the models by each of Hits@1, Hits@3, Hits@5, Hits@10, Hits@15 and MRR, from the rows of `metrics_report.csv`. The `AGREEMENT_HITS_AT` setting in `report_results.py` holds these k.
- A model counts only when it was trained on both graphs and has a value of the metric in both. Hits@15 is not in `results.json`, so a run that the analysis left out has none, and it drops out of the Hits@15 row only.

| Column | Meaning |
|---|---|
| `metric` | The metric the models are ranked by: `hits_at_1` to `hits_at_15`, or `mrr`. |
| `models` | Number of models ranked in both graphs. |
| `kendall_tau` | Kendall's tau (tau-b) of the two rankings, from −1 (reversed order) through 0 (unrelated) to +1 (the same order). See [Kendall's tau](#kendalls-tau). Empty below 2 models, or when one graph gives every model the same value. |
| `kendall_p` | Its p-value: how likely a tau this far from 0 would be if the two rankings were unrelated. It is not corrected for multiple tests. |

To find the graph that best stands in for the reference, compare `kendall_tau` across the `compared` graphs, one metric at a time.

## Cliff's delta and p-values

The relation comparison describes each difference between two graphs with two numbers:

- **Cliff's delta (δ)** answers *how large is the difference?*
- **The p-value** answers *could the difference be chance?*

Neither answers both questions, so read them together.

### Cliff's delta

**Meaning.** Take one test prediction of the reference graph and one of the compared graph, at random. Cliff's delta is the probability that the compared graph's prediction ranks better, minus the probability that the reference graph's prediction ranks better:

```
δ = P(compared ranks better) − P(reference ranks better)
```

It goes from −1 to +1:

| δ | Meaning |
|---|---|
| +1 | Every prediction of the compared graph ranks better than every prediction of the reference graph |
| 0 | Neither graph tends to rank better |
| −1 | Every prediction of the compared graph ranks worse |

For example, δ = −0.64 means that in a random pair, the compared graph ranks worse 64 percentage points more often than it ranks better.

**Definition.** With r₁ … rₙ the normalized ranks of the reference graph's n predictions and c₁ … cₘ those of the compared graph's m predictions (lower is better):

```
δ = ( #{(i, j) : cⱼ < rᵢ}  −  #{(i, j) : cⱼ > rᵢ} ) / (n × m)
```

**Computation.** The code is in [`cliffs_delta.py`](../cliffs_delta.py), whose docstring repeats this section. `compare_relations` in `report_results.py` calls its `cliffs_delta` for every relation, model and pair of graphs:

1. **Normalize the ranks.** Each prediction's rank becomes `(rank − 1) / (candidates − 1)`, so that graphs with different numbers of entities are on one scale (see [Comparing relations across graphs](#comparing-relations-across-graphs)).
2. **Compare every pair.** In principle, δ compares every prediction of the reference graph with every prediction of the compared graph. It counts the pairs where the compared one ranks better, subtracts the pairs where it ranks worse, and divides by the number of pairs. A tie counts for neither side.
3. **Take it from the Mann-Whitney U statistic.** `scipy.stats.mannwhitneyu(reference, compared)` returns the U of its first sample, which counts those pairs with a tie as half:

   ```
   U = #{rᵢ > cⱼ} + ½ × #{rᵢ = cⱼ}
   ```

   Every pair is either better, worse or tied, so `#{r > c} + #{r < c} + #{r = c} = n × m`, and therefore:

   ```
   δ = 2U / (n × m) − 1
   ```

   `cliffs_delta(reference, compared)` computes δ this way, from the same U as the Mann-Whitney test (see [p-values](#p-values)). Sorting the ranks takes O((n + m) log(n + m)) time, whereas building every pair takes n × m comparisons. This needs SciPy 1.7 or later. Older versions return the smaller of the two samples' U values, so δ would never be positive.

The same number goes by other names. δ is the **rank-biserial correlation**. With A = U / (n × m), the **common-language effect size** (also called the Vargha-Delaney A, or the AUC), δ = 2A − 1.

**Libraries.** Neither SciPy nor NumPy has a Cliff's delta function. [pingouin](https://pingouin-stats.org)'s `pingouin.mwu(reference, compared)["RBC"]` gives the same value with the same sign, but the project does not depend on it. `pairwise_cliffs_delta` in `cliffs_delta.py` follows the definition literally, comparing every pair, to check `cliffs_delta`. `python cliffs_delta.py` runs that check on the example below and on random samples.

A small example, with normalized ranks where lower is better:

| | Reference 0.1 | Reference 0.3 |
|---|---|---|
| **Compared 0.2** | reference better | compared better |
| **Compared 0.5** | reference better | reference better |

The compared graph is better in 1 of the 4 pairs and worse in 3, so δ = (1 − 3) / 4 = −0.5.

**Why this effect size.** Ranks are far from normally distributed: most fall near the top or spread over a long tail. Cliff's delta needs no assumption about their distribution, a few extreme ranks don't distort it, and it doesn't grow with the number of predictions. That last point makes relations of different sizes comparable.

**How large is large.** The `effect` column labels |δ| with the bounds of Romano et al. (2006), which `EFFECT_SIZES` in `cliffs_delta.py` holds:

| \|δ\| | `effect` |
|---|---|
| below 0.147 | negligible |
| 0.147 to 0.33 | small |
| 0.33 to 0.474 | medium |
| 0.474 and above | large |

**Where it is used:**
- the `cliffs_delta` and `effect` columns of `relation_comparison.csv`
- the δ under each panel title of `<model>_hits_at_k.png`, one line per compared graph

**Limit.** δ only measures whether one graph tends to rank better. Two distributions can have δ near 0 and still differ in shape, for example when one graph ranks its predictions both higher and lower more often. The Kolmogorov-Smirnov test below detects that.

**References.** N. Cliff, "Dominance statistics: Ordinal analyses to answer ordinal questions", *Psychological Bulletin* 114(3), 1993. J. Romano, J. D. Kromrey, J. Coraggio and J. Skowronek, "Appropriate statistics for ordinal level data: Should we really be using t-test and Cohen's d for evaluating group differences on the NSSE and other surveys?", Annual meeting of the Florida Association of Institutional Research, 2006.

### p-values

**Meaning.** A test starts from a **null hypothesis**: that there is no difference between the two graphs. The p-value is the probability of observing a difference at least as large as the measured one if the null hypothesis were true. A small p-value means the data would be unlikely without a real difference, so it counts as evidence of one.

A p-value does **not** say:
- the probability that the graphs are the same
- how large the difference is (that is δ's job)
- that the graphs are similar when p is large. A large p-value only means that no difference was detected, which can also happen because there are too few predictions.

**The tests.** Each relation, model and pair of graphs gets two tests on the normalized ranks of all its test predictions, both two-sided and both computed with SciPy:

| Test | Column | Null hypothesis | What it detects |
|---|---|---|---|
| Mann-Whitney U (`scipy.stats.mannwhitneyu`) | `mannwhitney_p` | A prediction of one graph is as likely to rank better as worse than one of the other (δ = 0) | One graph tends to rank better |
| Kolmogorov-Smirnov (`scipy.stats.ks_2samp`) | `ks_p` | Both graphs' ranks come from the same distribution | Any difference, including in shape. `ks_statistic` is the largest vertical gap between the two graphs' Hits@k curves on normalized ranks |

The relations here have between 62 and 760 test predictions per graph, so SciPy uses its large-sample approximations rather than exact computations. `query_comparison_summary.csv` also has `spearman_p`, the p-value of the Spearman correlation (null hypothesis: no correlation between the two graphs' per-query MRRs). It is not corrected as described below.

**Correction for multiple tests.** Every graph pair and model is tested on each of its relations, 7 for French Royalty. At a threshold of 0.05, each test has a 5% chance of a false positive, so with 7 tests the chance that at least one is a false positive is much higher. The **Holm correction** keeps that chance at 5% for the whole family of tests:

1. Sort the family's m p-values from smallest to largest.
2. Multiply the i-th smallest by m − i + 1: the smallest by m, the next by m − 1, and so on.
3. Never let a corrected value fall below the one before it, and cap the values at 1.

For example, 0.01, 0.02 and 0.04 become 0.03, 0.04 and 0.04. A family is all the relations of one KG, compared graph and model. The `all` row is a family of its own, a single test, so its corrected p-value equals its raw one. The corrected values are in `mannwhitney_p_holm` and `ks_p_holm`. Use these, not the raw p-values, to decide significance. Holm is less strict than the better-known Bonferroni correction (which multiplies every p-value by m) and gives the same guarantee.

**Where it is used.** A difference is **significant** when its Holm-corrected p-value is below 0.05, the `SIGNIFICANCE` setting in `report_results.py`. In `<model>_hits_at_k.png`, each graph's line shows `mannwhitney_p_holm` as p, and the line is **bold** when it is 0.05 or more: no significant difference from the reference was detected for that relation. The KS p-values are only reported in `relation_comparison.csv`.

### Reading δ and p together

| p (Holm) | \|δ\| | Reading |
|---|---|---|
| below 0.05 | large | A clear, real difference |
| below 0.05 | negligible | A real but tiny difference. This is common with many predictions, where even small differences become significant |
| 0.05 or more | negligible | No difference detected, and any difference is small. This is the strongest evidence of similarity these columns give |
| 0.05 or more | not negligible | Inconclusive: there may be a difference, but too few predictions to tell. For example, `father` for SKGG (PCA 0.9) with CompGCN has only 62 predictions |

Showing that two graphs behave alike needs an equivalence test, which the project does not run yet. See the [backlog](backlog.md).

## Kendall's tau

**Meaning.** Rank the same models by one metric in two graphs, and take every pair of models. The pair is **concordant** when both graphs put the same model ahead, and **discordant** when they put different models ahead. Kendall's tau is the share of concordant pairs minus the share of discordant pairs:

| τ | Meaning |
|---|---|
| +1 | Both graphs rank the models in the same order |
| 0 | The two orders are unrelated |
| −1 | One graph ranks the models in the reverse order of the other |

Only the order counts, not how far apart the scores are. A graph on which every model scores half as much as on the reference still has τ = +1.

**Definition.** With n models there are n₀ = n (n − 1) / 2 pairs. With C concordant and D discordant pairs:

```
τ = (C − D) / n₀
```

Two models with the same value of the metric, for example two models with Hits@15 = 1, are **tied**, and their pair is neither concordant nor discordant. The script uses **tau-b**, which divides by the pairs that are not tied rather than by every pair. Two identical rankings with ties then still get +1:

```
τ_b = (C − D) / √((n₀ − n₁) × (n₀ − n₂))
```

where n₁ and n₂ are the pairs tied in the reference graph and in the compared graph. Without ties, τ_b = τ.

**Computation.** The code is in [`kendall_tau.py`](../kendall_tau.py), whose docstring repeats this section. Its `ranking_agreement` takes the rows of `metrics_report.csv`. For every graph pair and metric, it calls `kendall_tau`, which calls `scipy.stats.kendalltau`. SciPy computes tau-b by default, and its p-value as well: exactly for a few models without ties, and from a normal approximation otherwise. pandas' `DataFrame.corr(method="kendall")` gives the same tau, but no p-value. `pairwise_kendall_tau` follows the definition literally, comparing every pair of models, to check `kendall_tau`. `python kendall_tau.py` runs that check on the example below and on random samples.

A small example, with the MRR of four models:

| Model | Reference | Compared |
|---|---|---|
| A | 0.40 (1st) | 0.35 (1st) |
| B | 0.30 (2nd) | 0.15 (3rd) |
| C | 0.20 (3rd) | 0.25 (2nd) |
| D | 0.10 (4th) | 0.05 (4th) |

Of the 6 pairs, only B and C are in a different order, so τ = (5 − 1) / 6 = 0.67.

**Reading τ with few models.** Seven models make only 21 pairs, and one swap of two neighbouring models changes τ by 2/21 = 0.095. The p-value is therefore large unless the agreement is nearly perfect. For 7 models without ties, τ = 0.62 (4 discordant pairs) has p = 0.069, and p falls below 0.05 only from τ = 0.71 (3 discordant pairs or fewer). Compare the τ of the graphs with each other to see which one ranks the models most like the reference. Read the p-value only as a reminder that τ rests on few models.

**References.** M. G. Kendall, "A new measure of rank correlation", *Biometrika* 30(1–2), 1938. M. G. Kendall, "The treatment of ties in ranking problems", *Biometrika* 33(3), 1945.

## Requirements

`torch`, `pykeen`, `pandas`, `numpy`, `scipy` and `matplotlib`. See [Quick start](../README.md#quick-start).
