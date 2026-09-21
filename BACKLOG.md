# Backlog

Known issues and pending refactors, organized by module. See `AGENTS.md` for
the current architecture map. Resolved items are archived in
`BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## Next session
- [ ] *(urgent)*: Refresh how intensional dependencies work and why they are useful/necessary (see `core/rules.py`'s `get_intensional_dependencies` and `docs/concepts.md`'s "Intensional rule dependencies" section).

## `core/`
- [ ] **`rules.py`** *(low priority)*: The extensional dependency graph is created over all the rules, not just the relevant ones for EDB generation. Restricting the dependencies only to EDB relevant rules could increase the efficiency of the tool.
- [ ] **`get_existing_queries`** *(low priority)*: Seems counter intuitive that we are querying for the existing queries in the graph only to use them to see which queries are novel. Maybe we should use this call to return the set of triples from the candidates that are novel? I need to look through the usage of this function to be sure.

## 'engine/'
- [ ] **`metrics.py`**: `GraphMetrics.from_uri` now also dumps its result to `logs/metrics/<uri>.json` for debugging (see `_dump_metrics_for_debugging`), but that's write-only -- there's still no path to make the pipeline consume that JSON instead of re-querying the graph over SPARQL. That's the part still open.
- [ ] **`edb.py`**: I don't think the "generation of triples from rule bodies" is good. Check it.
- [ ] **`edb.py`** *(low priority)*: `insert_random_triples`'s Step 3 fallback picks the subject uniformly at random (`random.choice(available_subjects)`) then resamples random objects until the solvability check passes. Choosing the subject with the *greatest* remaining domain count first (the hardest to place, since it needs the most distinct objects) instead of at random, while keeping object selection random, should need fewer resampling retries and be faster overall -- same "most constrained variable first" idea as the MRV heuristic noted elsewhere for the grounding sampler's variable order.


## 'cli/'
- [ ] **`upload.py`**: Add an option to complete / not complete the graph once uploaded.
- [ ] **`upload.py`**: Change to work with .ttl files.

## Graph layout (`config.py`, `cli/`, `engine/`)
- [ ] **Graph storage layout**: Keep one GraphDB repository per dataset (already the case), and reorganize the named graphs inside each repository:
  - Store the *inferred* triples of each stage in their own named graph (a delta over the previous stage) instead of duplicating the whole graph, so what each stage adds stays inspectable and can be cleared independently.
  - Additionally build *self-contained* graphs (previous stage + inferred triples) when needed. The final `synthetic` graph should be self-contained, since it's the deliverable and shouldn't depend on the EDB/IDB delta graphs still existing.
  - Decide and document per stage (`base`, `complete`, `edb`, `idb`, `synthetic`) whether it is a delta or a snapshot, and update the `graph` section of the config (`base_uri`, `complete_uri`, `edb_uri`, `synthetic_uri`) and the metric/query code that assumes a stage graph is self-contained (e.g. `GraphMetrics.from_uri` over `complete_uri`) accordingly.
  - **Proposed naming**: `{namespace}graph/{variant}/{stage}[-delta]`. `{variant}` (e.g. `default`, `enriched`) replaces the current `enriched_` name prefix and is a path segment so a variant's graphs are easy to list; no suffix = self-contained, `-delta` = only what that stage added. Resulting graphs per variant:
    - `…/base` (self-contained, written by `upload.py`)
    - `…/complete-delta` (delta) and `…/complete` (= `base` + `complete-delta`, source for metric extraction), written by `completion.py`
    - `…/edb` (self-contained; generated from metrics + rules, not derived from `complete`), written by `edb.py`
    - `…/idb-delta` (delta) and `…/synthetic` (= `edb` + `idb-delta`, the deliverable), written by `idb.py`
    - `…/_meta` (config, threshold, timestamp, triple counts per stage), written by `cli/main.py`
  - Only `complete` and `synthetic` need both delta and snapshot forms. Materializing `synthetic` is recommended; `complete` could be skipped in favour of a `FROM` union if metric queries aren't simpler against a single graph.
  - Replace the four `*_uri` config fields with a single `graph_prefix` (e.g. `http://FrenchRoyalty.org/graph/enriched`) and derive the stage URIs in `GraphConfig`, so names can't drift or contain typos.
  - Fix the `graph.name` typo (`FrechRoyalty`) in `configurations/enriched_french_royalty.json`.
  - *(optional)* Since the repository already isolates the dataset, graph URIs no longer need a dataset prefix. Add a run id only if comparing runs becomes a need, and consider a small `meta` graph recording config, threshold, timestamp and triple counts per stage.

## `data/`
- [ ] **`french_royalty.tsv`** *(important)*: `spouse` is declared `rdfs:subPropertyOf` `marriedTo` in `french_royalty.ttl`, but the base graph doesn't materialize that: only 9 couples (18 triples) + 2 patch triples use `marriedTo` at all, out of 1152 `spouse` triples. For a complete KG, every `A spouse B` should imply `A marriedTo B`. Note that the ontology file does not specify marriedTo or spouse as symmetric, so `A spouse B` does not imply `B spouse A`. Not implemented yet — see `docs/french-royalty-corrections.md` for the full review this came out of.

## 'docs/'
- [ ] **concepts.md** *(low priority)*: Check remaining definitions are as
      intended (head coverage, std/PCA confidence). Support's definition was
      audited and fixed to match AMIE3 (see `get_support` in `core/queries.py`
      and its `get_head_variables()`-based projection).
- [ ] **getting_started.md** *(low priority)*: Define clearly all the neccessary inputs for the execution of the repo.

## Doubts
- [ ] Should the support be an upper bound as well? Depending on how the EDB is generated, the freqency of predicates can be reduced, thus reducing the support of some rules.