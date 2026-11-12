# Troubleshooting

Each entry gives the symptom, the cause, and the fix.

## `Configuration file input.json not found`

`KGC.py` reads `input.json` relative to the current directory. Run it from the repository root.

## `... line N is not an N-Triples statement` or `... has a tab inside a term`

**Cause:** `kg_path` ends in `.nt`, and line N of that file isn't a single subject, predicate and object
followed by `.`, usually because it's Turtle (prefixes, `;` or `,` lists) or a statement is split over two
lines. A literal containing a tab can't be written to the `.tsv` either. No `.tsv` is written. See
[Input](kgc.md#input).
**Fix:** convert the graph to strict N-Triples, for example with `rapper -i turtle -o ntriples` or rdflib, and
escape tabs in literals as `\t`.

## KGC.py trains on an old version of an `.nt` graph

**Cause:** the `.tsv` next to the `.nt` file is newer than it, so KGC.py reused it instead of converting
again. This happens when the `.nt` file is replaced by a copy that keeps an older modification time.
**Fix:** delete the `.tsv` and run KGC.py again.

## report_results.py

`report_results.py` prints `error:` when it stops, `warning:` when it leaves a run out, and `note:` when a
comparison has nothing to compare. See [KG completion](kgc.md#report_resultspy).

### `error: results directory not found` or `error: no results.json files found under ...`

**Cause:** `--root` points to the wrong folder, or no run has finished yet. A relative `--root` is resolved
from the current directory; the default is `Output/` in the repository root, wherever the script runs from.
**Fix:** pass the folder that contains `<dataset...>/<model>/results.json`.

### `warning: skipping .../results.json (...)`

**Cause:** the file can't be parsed or has no `metrics.both.realistic` slice, usually because training was
interrupted while PyKEEN was saving.
**Fix:** retrain that model. The other runs are still reported.

### `warning: no prediction analysis for ... (missing ...)`

**Cause:** the run is in `metrics_report.csv`, but one of the files needed to recompute its ranks is missing:
`<results_path>/test` (KGC.py ran with `"save_splits": false`), or `trained_model.pkl` or `training_triples/`
in the model folder.
**Fix:** retrain with `"save_splits": true`.

### `warning: no prediction analysis for ... (label ... is not in the model's vocabulary ...)` or `(MRR ... from its saved model and splits vs ... in results.json ...)`

**Cause:** the `test` file in `results_path` (or its `valid` file, if it has one) was written by a later
KGC.py run than the model, so the model was evaluated on other test triples. KGC.py rewrites `train` and `test` when it starts, before training any
model, so this happens when a run reuses a `results_path` with another `kg_path`, a changed graph file or
another `random_seed`, including while that run is still training.
**Fix:** give each graph its own `results_path`, and retrain the models of that folder with the `kg_path`
and seed that wrote its `test` file. A run left out here keeps its row in `metrics_report.csv`.

### `warning: no run could be ranked, so no prediction analysis was written`

**Cause:** every run hit one of the two warnings above. Only `metrics_report.csv` is written.

### `note: <kg> has no 'source' graph, so no relations were compared`

**Cause:** no folder is named like `--reference` (default `source`), or the reference graph and the other
graphs of its KG share no model or no relation. `relation_comparison.csv` is not written, so one left in
that KG's analysis folder by an earlier run is out of date.
**Fix:** pass the name of the reference graph's folder with `--reference`.

### `note: <kg> has no 'source' graph or no other graph, so no model rankings were compared`

**Cause:** no folder is named like `--reference` (default `source`), or the reference is the only graph of its
KG in the metrics table. `ranking_agreement.csv` is not written, so one left in that KG's analysis folder by an
earlier run is out of date.
**Fix:** pass the name of the reference graph's folder with `--reference`.

### `note: no query of <kg> is held out in both 'source' and a graph matching ... with the same model`

**Cause:** no sibling folder matches `--compare` (default `skgg*`), no model was trained on both graphs, or
the graphs name their entities differently (PyGraft's `E1`, `E2`, ... never pair). `query_comparison.csv`,
`query_comparison_summary.csv` and the `<compared>_<model>.png` plots are not written.
**Fix:** check the patterns against the folder names, and quote them so the shell does not expand them.

### `warning: <kg> has N graphs, the relation plots show the first 8`

**Cause:** the plots have 8 colors. The reference comes first, then the `--compare` graphs, then the rest in
alphabetical order, and the graphs after the eighth are left out of `relations.png`,
`<model>_hits_at_k.png`, `<model>_relations_hits_at_1-15.png` and `models_hits_at_1-15.png`.
The CSV files still include them.
**Fix:** move the graphs you don't need in the plots out of the KG's folder.

### `warning: <kg> has N models, the Hits@k line plots show the first 8`

**Cause:** `models_hits_at_1-15.png` draws one line per model, in 8 colors. The models are taken in
alphabetical order, and the ones after the eighth are left out of it. The CSV files still include them.
**Fix:** move the runs of the models you don't need in the plots out of the KG's folder.
