# Backlog

Known issues and pending features. Resolved items are archived in `BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## `core/`
- [ ] **`get_existing_triples`** *(low priority)*: Seems counter intuitive that we are querying for the existing triples in the graph only to use them to see which candidates are novel (`engine/generator.py` still does `g[0] not in existing` after the query). Maybe `get_existing_triples` should return the novel set directly instead. I need to look through the usage of this function to be sure.

## 'engine/'
- [ ] **Irreflexive relations** (`core/queries.py`, `metrics.py`, `edb.py`, `generator.py`, `fill.py`): The schema (see "Schema support") states which relations are irreflexive, and that must change both how queries are built and how triples are generated. A reflexive triple (subject = object) of an irreflexive relation must never be generated, and never be taken into account as an example: not in a rule's support or groundings, and not in a predicate's metrics. The fr.no-literals source graph has no reflexive triples, but a synthetic graph generated from it had 6 (2 `successor`, 2 `child`, 1 `spouse`, 1 `parent`).
  - [ ] Queries: replace the filter in `build_rule_query`, which makes every rule variable take a different value, with one `FILTER (?s != ?o)` per body or head atom whose relation is irreflexive. Variables that share no such atom may take the same value. Build this filter in one shared function and use it in every query that matches rule groundings: `build_rule_query` (completion), `get_support`, `count_producible_heads`, the groundings `generator.sample_groundings` reads, and `build_head_impact_query`/`build_body_impact_query` (`fill.py`). Then a reflexive triple of an irreflexive relation is never counted as an example of a rule, even when a real graph contains one (e.g. one loaded with `cli/complete.py`).
  - [ ] Triple generation: never create a reflexive triple of an irreflexive relation. `edb.py`: random triples (`insert_random_triples`) and rule-body groundings (`generator.sample_groundings`); direct matches already exclude it. `generator.apply_rule`: the head triples of completion. `fill.py`: its draws, which today follow the source graph's reflexive count per predicate.
  - [ ] Metrics: `GraphMetrics.from_uri` should leave out (and log) the reflexive triples of an irreflexive relation in the source graph, so they don't enter the target frequency, domain and range.
  - Why: with the per-atom filter in completion and in `get_support`, completion derives the head of every grounding that support can count. The exception in "Filling open relations" (`docs/concepts.md`) then disappears: random triples are safe for any relation in no kept rule's body, and the head check of `fill.py` never fires.
  - Until the schema is read, a relation with no reflexive triple in the source graph (profile `reflexivity` 0) can be treated as irreflexive.
- [ ] **Finish the fill step** (`fill.py`, phase 4 of `cli/main.py`): fill keeps closed rules' support and raises open rules' support up to their target (section 4.1 of `docs/algorithm.md`), but it stops early when a triple feeds several open rules. On `fr.no-literals.csv` at PCA 0.9 it leaves `father` at 271/561 and rules 10, 19 and 24 at 271/431, 54/215 and 54/200: the random pass rejects 1,915 `father` triples because of rule 10 (`father => parent`) and the rule-driven pass 970 because of rule 19 (`father & mother => spouse`).
  - [ ] Add the heads of other open rules in the same grounding: when a grounding leaves a body of another open rule without its head and that head predicate is still open, add the head too (as completion would, limited to open rules), checked with `_FillState.impact` and paid from the head predicate's budget. Today only the head of the rule being filled may be missing (`planned` in `_FillState.impact`). Expected to unblock rules 10, 19 and 24 together (`father(e,b)` with `parent(e,b)` and `spouse(a,b)`).
  - [ ] Re-run the rule-driven pass after the random pass. The random pass closes head predicates with unexplained head triples (267 `parent`, 757 `spouse` in the run above), which turns rules 10, 19 and 24 into case B1; a second pass could complete bodies for those heads. Decide whether the random pass should leave the head predicates of open rules for last, or skip them, so the B1 pass gets to choose the pairs.
  - [ ] Case A rules (every body predicate closed) are only reported. Decide whether they need anything else, e.g. a summary line, or a warning at rule-selection time.
  - [ ] Cost: every candidate triple runs one or more SPARQL queries per rule its predicate occurs in, and every rejected grounding is inserted and deleted again. Check the run time on larger rule sets and batch the checks if needed.
  - [ ] `cli/complete.py` doesn't apply the confidence filter of `--pca-conf` together with the cycle removal of a run, so it can't re-check that a synthetic graph is closed under the rules a run used. Add an option to load the same rule set as `cli/main.py` (or read `<rules>_used.csv`).
  - [ ] `get_domain`/`get_range` log a warning ("Retrieved None ...") for a predicate with no triple in the graph, which fill hits when a synthetic predicate is still empty. Return an empty dict quietly there.
- [ ] **`generator.py`** *(low priority)*: `apply_rule` inserts every new head triple, so completion can overshoot the head predicate's target frequency. A cap must check each triple against the head predicate's remaining profile (`is_assignment_solvable`) and also spend it (frequency, domain and range counts), which the unused `profile` parameter removed from `apply_rule` didn't do.
- [ ] **`metrics.py`** *(low priority)*: `GraphMetrics.from_uri` dumps its result to `logs/metrics/<uri>.json` for debugging. There's still no path to make the pipeline consume that JSON instead of re-querying the graph over SPARQL.

## Graph layout (`core/config.py`, `cli/`, `engine/`)
- [ ] **Graph storage layout** *(low priority)*: Give the named graphs a good, standard naming across the pipeline stages, keeping one GraphDB repository per dataset.

## Schema support
- [ ] Add schema support. The schema is a `<graph_name>.schema.ttl` file that defines the classes and their properties in the graph.
  - [x] Add a schema template (`schemas/template.ttl`) and the French Royalty schema (`data/fr/fr.ttl`).
  - [ ] Add a `schema_file` config field and parse the schema.
  - [ ] Decide how to use the schema in the pipeline.
  - [ ] Read which relations are irreflexive (e.g. `owl:IrreflexiveProperty`) from the schema, for the "Irreflexive relations" item under `engine/`.
- [ ] Use the SHACL shapes (`<dataset>.shapes.ttl`) in the pipeline. `cli/validate.py` only reports the triples of a graph or `.tsv` file that violate them; EDB generation and completion still insert such triples.


# Future work
- [ ] Add support for literals.