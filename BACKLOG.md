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
- [ ] **`edb.py`** *(low priority, now empirically confirmed)*: The "searchspace" technique (`generator.create_searchspace` + `check_triples_from_rule`'s join query) materializes the full cartesian product of each open predicate's remaining domain × range as a scratch named graph, then queries it. Maybe there's a better approach based on projections instead, avoiding materializing the cartesian product up front. No longer just theoretical: reusing this technique for `completion.py`'s closed-head-completion fallback (branch `same_approach_searchspace`) hit a 693,594-row candidate set for a rule needing only 17 witnesses on an unselective `rdf:type` correlation, crashing `_select_valid_bindings`'s per-binding recursion outright before a `LIMIT` was added — see `BACKLOG_ARCHIVE.md`'s `completion.py` entry for the full writeup.
- [ ] **`edb.py`** *(low priority)*: `insert_random_triples`'s Step 3 fallback picks the subject uniformly at random (`random.choice(available_subjects)`) then resamples random objects until the solvability check passes. Choosing the subject with the *greatest* remaining domain count first (the hardest to place, since it needs the most distinct objects) instead of at random, while keeping object selection random, should need fewer resampling retries and be faster overall -- same "most constrained variable first" idea as the MRV heuristic noted elsewhere for `_filter_bindings`'s binding order.


## 'cli/'
- [ ] **`upload.py`**: Add an option to complete / not complete the graph once uploaded.
- [ ] **`upload.py`**: Change to work with .ttl files.

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