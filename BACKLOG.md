# Backlog

Known issues and pending refactors, organized by module. See `AGENTS.md` for
the current architecture map. Resolved items are archived in
`BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## `core/`
- [ ] **`rules.py`** *(low priority)*: The extensional dependency graph is created over all the rules, not just the relevant ones for EDB generation. Restricting the dependencies only to EDB relevant rules could increase the efficiency of the tool.
- [ ] **`get_existing_triples`** *(low priority, renamed from `get_existing_queries`)*: Seems counter intuitive that we are querying for the existing triples in the graph only to use them to see which candidates are novel (`engine/generator.py` still does `g[0] not in existing` after the query). Maybe `get_existing_triples` should return the novel set directly instead. I need to look through the usage of this function to be sure.

## 'engine/'
- [ ] **`cycles.py`/`generator.py`** *(low priority)*: `break_cycles` end-to-end run is now confirmed (see `BACKLOG_ARCHIVE.md`). Two related caveats are still open: seeds live only in the synthetic graph, so a `--skip-edb` rerun repeats the cycle-breaking; and `apply_rule` in completion is not profile-capped, so a seeded cycle can overshoot its target frequency.
- [ ] **`metrics.py`**: `GraphMetrics.from_uri` now also dumps its result to `logs/metrics/<uri>.json` for debugging (see `_dump_metrics_for_debugging`), but that's write-only -- there's still no path to make the pipeline consume that JSON instead of re-querying the graph over SPARQL. That's the part still open.
- [ ] **`edb.py`** *(low priority)*: `insert_random_triples`'s Step 3 fallback picks the subject uniformly at random (`random.choice(available_subjects)`) then resamples random objects until the solvability check passes. Choosing the subject with the *greatest* remaining domain count first (the hardest to place, since it needs the most distinct objects) instead of at random, while keeping object selection random, should need fewer resampling retries and be faster overall -- same "most constrained variable first" idea as the MRV heuristic noted elsewhere for the grounding sampler's variable order.

## Graph layout (`config.py`, `cli/`, `engine/`)
- [ ] **Graph storage layout**: Keep one GraphDB repository per dataset (already the case), and reorganize the named graphs inside each repository:
  - Store the *inferred* triples of each stage in their own named graph (a delta over the previous stage) instead of duplicating the whole graph, so what each stage adds stays inspectable and can be cleared independently.
  - Additionally build *self-contained* graphs (previous stage + inferred triples) when needed. The final `synthetic` graph should be self-contained, since it's the deliverable and shouldn't depend on the EDB/IDB delta graphs still existing.
  - Decide and document per stage (`base`, `complete`, `edb`, `idb`, `synthetic`) whether it is a delta or a snapshot, and update the `graph` section of the config (`base_uri`, `complete_uri`, `edb_uri`, `synthetic_uri`) and the metric/query code that assumes a stage graph is self-contained (e.g. `GraphMetrics.from_uri` over `complete_uri`) accordingly.
  - **Proposed naming**: `{namespace}graph/{variant}/{stage}[-delta]`. `{variant}` (e.g. `default`, `enriched`) replaces the current `enriched_` name prefix and is a path segment so a variant's graphs are easy to list; no suffix = self-contained, `-delta` = only what that stage added. Resulting graphs per variant:
    - `…/base` (self-contained, written by `upload.py`)
    - `…/complete-delta` (delta) and `…/complete` (= `base` + `complete-delta`, source for metric extraction), written by `completion.py`
    - `…/edb` (self-contained; generated from metrics + rules, not derived from `complete`), written by `edb.py`
    - `…/idb-delta` (delta) and `…/synthetic` (= `edb` + `idb-delta`, the deliverable), written by `completion.py`/`cycles.py`
    - `…/_meta` (config, threshold, timestamp, triple counts per stage), written by `cli/main.py`
  - Only `complete` and `synthetic` need both delta and snapshot forms. Materializing `synthetic` is recommended; `complete` could be skipped in favour of a `FROM` union if metric queries aren't simpler against a single graph.
  - Replace the four `*_uri` config fields with a single `graph_prefix` (e.g. `http://FrenchRoyalty.org/graph/enriched`) and derive the stage URIs in `GraphConfig`, so names can't drift or contain typos.
  - Fix the `graph.name` typo (`FrechRoyalty`) in `configurations/french_royalty*.json`.
  - *(optional)* Since the repository already isolates the dataset, graph URIs no longer need a dataset prefix. Add a run id only if comparing runs becomes a need, and consider a small `meta` graph recording config, threshold, timestamp and triple counts per stage.
