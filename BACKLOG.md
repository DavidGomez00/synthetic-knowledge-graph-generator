# Backlog

Known issues and pending refactors, organized by module. See `AGENTS.md` for
the current architecture map. Resolved items are archived in
`BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## Next session
- [ ] *(urgent)*: Refresh how intensional dependencies work and why they are useful/necessary (see `core/rules.py`'s `get_intensional_dependencies` and `docs/concepts.md`'s "Intensional rule dependencies" section).

## `core/`
- [ ] **`rules.py`** *(low priority)*: The extensional dependency graph is created over all the rules, not just the relevant ones for EDB generation. Restricting the dependencies only to EDB relevant rules could increase the efficiency of the tool.

## 'engine/'
- [ ] **`metrics.py`**: There has to be a way to pass the metrics without reading an actual graph. Pass the metrics through a JSON or smth. We should read the original metrics from the JSON or data input, not through SPARQL queries.
- [ ] **`edb.py`**: I don't think the "generation of triples from rule bodies" is good. Check it.
- [ ] **`edb.py`** *(low priority)*: The "searchspace" technique (`generator.create_searchspace` + `check_triples_from_rule`'s join query) materializes the full cartesian product of each open predicate's remaining domain × range as a scratch named graph, then queries it. Maybe there's a better approach based on projections instead, avoiding materializing the cartesian product up front.
- [ ] **`edb.py`**: Idea for reaching stale state with a rule still short of its support: if an open rule's *only* remaining open predicate is in its body (everything else already closed), we could directly instantiate however many triples for that predicate are needed to close the rule, as long as we don't exceed the rule's support upper bound. Don't know the mechanics yet, but any such generation would also need to account for intensional dependencies (closing this rule out of order could feed a same-head rule that was depending on it, or itself depend on a rule that hasn't closed yet).


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