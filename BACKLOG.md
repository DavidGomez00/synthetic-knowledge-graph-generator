# Backlog

Known issues and pending refactors, organized by module. See `AGENTS.md` for
the current architecture map. Resolved items are archived in
`BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## `core/`
- [ ] **`rules.py`** *(low priority)*: The extensional dependency graph is created over all the rules, not just the relevant ones for EDB generation. Restricting the dependencies only to EDB relevant rules could increase the efficiency of the tool.

## 'engine/'
- [ ] **`metrics.py`**: There has to be a way to pass the metrics without reading an actual graph. Pass the metrics through a JSON or smth.
  - [ ] **`main.py`**: We should read the original metrics from the JSON or data input, not through SPARQL queries.

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