# Backlog

Known issues and pending refactors, organized by module. Resolved items are archived in `BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## `core/`
- [ ] **`get_existing_triples`** *(low priority)*: Seems counter intuitive that we are querying for the existing triples in the graph only to use them to see which candidates are novel (`engine/generator.py` still does `g[0] not in existing` after the query). Maybe `get_existing_triples` should return the novel set directly instead. I need to look through the usage of this function to be sure.

## 'engine/'
- [ ] **`cycles.py`/`generator.py`** *(low priority)*: 
  - [ ] Generated seeds live only in the synthetic graph, so a `--skip-edb` rerun repeats the cycle-breaking.
  - [ ] `apply_rule` in completion is not profile-capped, so a seeded cycle can overshoot its target frequency.
- [ ] **`metrics.py`** *(low priority)*: `GraphMetrics.from_uri` dumps its result to `logs/metrics/<uri>.json` for debugging. There's still no path to make the pipeline consume that JSON instead of re-querying the graph over SPARQL.
- [ ] **`edb.py`** *(low priority)*: `insert_random_triples`'s Step 3 fallback picks the subject uniformly at random, then resamples random objects until the solvability check passes. Choosing the subject with the *greatest* remaining domain count first instead of at random, while keeping object selection random, should need fewer resampling retries and be faster overall.

## Graph layout (`config.py`, `cli/`, `engine/`)
- [ ] **Graph storage layout** *(low priority)*: Give the named graphs a good, standard naming across the pipeline stages, keeping one GraphDB repository per dataset (already the case).
  - **Proposed naming**: `{namespace}graph/{variant}/{stage}[-delta]`, where `{variant}` (e.g. `default`, `enriched`) replaces today's `enriched_` prefix. No suffix means a self-contained graph; `-delta` means only the triples that stage added. Stages: `base`, `complete-delta` + `complete` (the metrics source), `edb`, `idb-delta` + `synthetic` (the deliverable, always self-contained), and an optional `_meta` graph (config, threshold, timestamp, triple counts).
  - **Config**: replace the four `*_uri` fields with a single `graph_prefix` and derive the stage URIs in `GraphConfig`, so names can't drift. Also fix the `graph.name` typo (`FrechRoyalty`) in `configurations/french_royalty*.json`.

## Schema support
- [ ] Add schema support. The schema should be a file that defines the classes and relations of the graph.