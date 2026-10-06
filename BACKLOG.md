# Backlog

Known issues and pending features. Resolved items are archived in `BACKLOG_ARCHIVE.md` once checked off here, to keep this file scannable.

## `core/`
- [ ] **`get_existing_triples`** *(low priority)*: Seems counter intuitive that we are querying for the existing triples in the graph only to use them to see which candidates are novel (`engine/generator.py` still does `g[0] not in existing` after the query). Maybe `get_existing_triples` should return the novel set directly instead. I need to look through the usage of this function to be sure.

## 'engine/'
- [ ] **`edb.py`**: When generating random triples, consider predicate irreflexivity to exclude the subject from possible objects. 
- [ ] **`cycles.py`/`generator.py`** *(low priority)*: 
  - [ ] Move the cycle detection and seeding to the first steps of the pipeline. Instead of seeding these relations independently, treat "need-to-seed" relations as extensional.
  - [ ] `apply_rule` in completion is not profile-capped, so a seeded cycle can overshoot its target frequency. Add a parameter to cap it or not.
- [ ] **`metrics.py`** *(low priority)*: `GraphMetrics.from_uri` dumps its result to `logs/metrics/<uri>.json` for debugging. There's still no path to make the pipeline consume that JSON instead of re-querying the graph over SPARQL.

## Graph layout (`core/config.py`, `cli/`, `engine/`)
- [ ] **Graph storage layout** *(low priority)*: Give the named graphs a good, standard naming across the pipeline stages, keeping one GraphDB repository per dataset.

## Schema support
- [ ] Add schema support. The schema is a `<graph_name>.schema.ttl` file that defines the classes and their properties in the graph.
  - [x] Add a schema template (`schemas/template.ttl`) and the French Royalty schema (`data/fr/fr.ttl`).
  - [ ] Add a `schema_file` config field and parse the schema.
  - [ ] Decide how to use the schema in the pipeline.
- [ ] Use the SHACL shapes (`<dataset>.shapes.ttl`) in the pipeline. `cli/validate.py` only reports the triples of a graph or `.tsv` file that violate them; EDB generation and completion still insert such triples.


# Future work
- [ ] Add support for literals.