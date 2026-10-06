# AGENTS.md

This file provides guidance to AI coding assistants (Claude Code, Codex, Cursor, Gemini CLI, etc.) when working with code in this repository.

## Project Summary

This project is a tool for **Synthetic Knowledge Graph (KG) generation**. It creates a synthetic Knowledge Graph from topological metrics of a source graph (number of nodes, relations, frequencies of the elements in the domain and range of each relation, etc.) and a set of Horn Rules, without needing continued access to the original graph once metrics are extracted.

Graphs are never loaded into memory-intensive libraries like RDFlib for bulk work. They are always stored in a graph database (Virtuoso or GraphDB) and manipulated via SPARQL queries through `SPARQLWrapper`.

## Environment Setup

- Requires Python >= 3.10.
- Dependencies are pinned in `requirements.txt` (install with `pip install -r requirements.txt`); `pyproject.toml` only carries project metadata and tool config (no dependency list or build backend).
- Package code lives under `src/skgg/` (installed name: `skgg`) — install it in editable mode (`pip install -e .`) or run scripts with `src` on `PYTHONPATH` so `from skgg...` imports resolve.

## Graph Database

`docker-compose.yml` defines two alternative graph-database stacks, selected via Compose profiles — bring one up at a time:

- `docker compose --profile virtuoso up` — Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080).
- `docker compose --profile graphdb up` — GraphDB 10.7 (port 7200).
- `--profile all` starts everything.

`DatabaseAuthConfig.auth_type` must match the store in use: `DIGEST` for Virtuoso, `BASIC` for GraphDB (or `NONE`). Each experiment config's `data.database_url` / `sparql_endpoint` selects which running store and repository/endpoint to talk to (see Configuration below).

## Running an experiment

Experiments are driven by JSON config files in `configurations/` (e.g. `fr.no-literals.json`, `family.source.json`), loaded via `RunConfig.from_json(...)`.

The main entry point is `run_synthetic_graph_experiment` in `src/skgg/cli/main.py`, runnable either as a library call or from the CLI:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/fr.no-literals.json"))
```

```bash
python -m skgg.cli.main -f fr.no-literals.json
python -m skgg.cli.main -f fr.no-literals.json --skip-edb --log-level DEBUG
python -m skgg.cli.main -f fr.no-literals.json --pca-threshold 0.95
```

`-f`/`--config-file` (required) accepts a bare filename resolved under `configurations/` (or a path, used as-is, if it contains a `/`), with or without the `.json` extension (`-f fr.no-literals` works too, in every `cli/` script); `--skip-edb` skips EDB generation and reuses whatever triples already sit at `graph.edb_uri`; `--log-level` overrides the config's `logging.level` for that run only, without editing the JSON file; `--pca-threshold` overrides the config's `rules.pca_threshold` for that run only.

Typical experiment flow (see `cli/main.py`):
1. Load `RunConfig` from JSON and set up logging.
2. Compute `GraphMetrics` (predicate profiles) from the source graph (`graph.base_uri`) over SPARQL.
3. Build the term mapping (`graph.term_namespaces` overrides merged over `core/utils.DEFAULT_PREFIXES`, under the `graph.namespace` default) and parse the Horn rule set from a CSV (`rules.rules_file`), keeping only rules classified POSITIVE (PCA confidence >= `pca_threshold`) — NEGATIVE and UNKNOWN (missing PCA confidence) rules are dropped and excluded from every later step. `core/rules.remove_inverse_rules` then deletes the redundant rule of each inverse pair (`?x p ?y => ?y q ?x` and `?x q ?y => ?y p ?x`) whose predicates are exact inverses and derived by no other rule, so that the deleted rule's head predicate becomes extensional and the EDB generates it instead of leaving a stale cycle to seed; see "Inverse rule pair" in `docs/concepts.md`. The summary lists the removed rules with their support, original -> synthetic, after the other rules.
4. Generate the EDB (extensional database) — `engine/edb.py` — inserting triples that satisfy rule bodies/profiles into `graph.edb_uri`.
5. As currently wired, `cli/main.py` logs five numbered phases (`[n/5]`): metrics/rules, EDB, completion, cycle-breaking, summary. The completion phase forward-chains *every* rule over the EDB with `engine/completion.py`'s `complete_graph` (which returns the triples it added and takes a `label` for its log lines), repeating each pass until nothing more is added — no rule ordering and no profile budget applied during this loop (`apply_rule` is called without a `profile`, so a rule can in principle overshoot its head predicate's target frequency). The cycle-breaking phase then alternates `engine/cycles.py`'s `break_cycles` (seeds one stale cycle per call, returns the seeded triple count) with `complete_graph` until nothing is seeded — see "Stale cycle" in `docs/concepts.md`. Rule/predicate closure (`support`/`frequency` targets reached) is tracked via `engine/generator.py`'s `get_closed_rules`/`get_closed_preds`, called after each `complete_graph` pass — see "Rule application order" in `docs/concepts.md` for why this differs from `engine/idb.py`'s original (now-removed) `generate_idb`. After the EDB step, the initial completion and every cycle-breaking round, `cli/main.py`'s `_ClosureProgress.log` logs how many predicates and rules are closed, which ones closed since the previous check, and each open predicate's current frequency against its target (copied from the profiles before EDB generation spends `PredicateProfile.frequency` as a budget); the open rule IDs are logged at DEBUG.

`cli/upload.py` is a separate, standalone script (CLI under an `if __name__ == "__main__":` guard; the upload step itself is the importable `upload_graph(client, triple_file, graph_uri, term_mapping)`) that uploads a base graph from a `.nt` or `.tsv` file (`graph.triple_file` in config; `.tsv` rows are bare `subject\tpredicate\tobject` terms, resolved to full URIs via the term mapping):

```bash
python -m skgg.cli.upload -f fr.no-literals.json
python -m skgg.cli.upload -f fr.no-literals.json --log-level DEBUG
python -m skgg.cli.upload -f fr.no-literals.json --triple-file data/fr/fr.no-literals.skgg.tsv --graph-uri http://FrenchRoyalty.org/no-literals/skgg
```

It only uploads: the pipeline reads its metrics from `graph.base_uri` as uploaded, with no rule-based completion beforehand. It takes the same `-f`/`--config-file` and `--log-level` as `cli/main.py`. `--triple-file` overrides `graph.triple_file` (a bare filename resolves under `data.input_dir`; a path containing `/` is used as given) and `--graph-uri` overrides the target graph (default `graph.base_uri`) — e.g. to re-insert an already generated synthetic graph into `graph.synthetic_uri` without regenerating it.

`cli/download.py` is the reverse of `cli/upload.py` (importable as `download_graph(client, graph_uri, output, term_mapping, page_size)`). It writes a named graph to `<output>.nt` and `<output>.tsv`, using the database connection and term mapping of the config passed with `-f` (required). `--graph-uri` picks the graph (default `graph.base_uri`), `-o`/`--output` the output path without suffix (default `data.input_dir` / the graph URI's last segment), and `--log-level` overrides `logging.level`. The `.nt` comes from `core/queries.export_graph_nt`. On GraphDB that is one `GET` on the repository's `/statements` endpoint with `infer=false`. On Virtuoso it is a series of sorted CONSTRUCT queries of `db_config.chunk_size` triples each, because Virtuoso's Graph Store endpoint stops at `ResultSetMaxRows` (10,001 of Family's 31,414 triples in the default container). Terms are written separated by single spaces whatever the store used. The script fails, deleting the partial `.nt`, if its triple count differs from `get_triple_count`. The `.tsv` is converted from the `.nt` with `cli/convert.convert` and the config's term mapping.

```bash
python -m skgg.cli.download -f family.source.json --graph-uri http://Family.org/skgg   # -> data/family/skgg.{nt,tsv}
python -m skgg.cli.download -f family.source.json -o path/to/family                   # graph.base_uri -> path/to/family.{nt,tsv}
```

`cli/clean.py` is another standalone, local-only script (no SPARQL; importable as `clean(input_file, output, literal_predicates, drop_types)`). It writes a cleaned copy of a `.nt`/`.tsv` file as a `.tsv` file (`-o`, default `<stem>.no-literals.tsv` next to the input). Every `/` in a term's own name becomes `_` (e.g. `Matilda_of_Saxony_1172_1209/10` → `Matilda_of_Saxony_1172_1209_10`, which would otherwise shorten to `10`): for a bare `.tsv` term that is the whole term, and for an IRI only its last segment, where the `/` is written as `%2F`. The `/` between an IRI's path segments are left alone, and the script fails if two terms clean to the same name. Two things are removed: duplicate triples (the first occurrence is kept), and "literals", meaning every non-type triple whose object is never typed (never the subject of a `type`/`rdf:type` triple). Every triple whose predicate is in `--literal-predicates` (default `name`, whose objects are always literals, even when a person's name equals their entity ID and so looks typed) is dropped too. Type triples are kept unless `--drop-types` is given: that drops every type triple as a final step, after checking that every subject term is typed to the same single class, and otherwise fails without writing the output. It also logs a warning for every subject term that is never typed.

It needs no term mapping: for `.nt` input every IRI is cut to its last segment, and the script fails if two IRIs collide. Use `cli/convert.py` for an `.nt` copy.

```bash
python -m skgg.cli.clean data/fr/fr.tsv                                        # -> fr.no-literals.tsv
python -m skgg.cli.clean path/to/graph.nt -o path/to/out.tsv --log-level DEBUG  # DEBUG lists every untyped subject
python -m skgg.cli.clean data/fr/fr.tsv --drop-types                           # no type triples; fails unless every entity is a Person
```

`cli/convert.py` is a third local-only script (importable as `convert(input_file, output_file, term_mapping)`). It converts a triples file from `.nt` to `.tsv` or from `.tsv` to `.nt`, picking the direction from the input's suffix, and writes it to `-o` or else to the input path with the other suffix. It only drops duplicate triples and lines that are not a triple (each logged as a warning), so literals are kept: in the `.tsv` a literal becomes its text, without quotes, language tag or datatype, with tabs and line breaks turned into spaces. The term mapping comes from `-f` (only the config's `graph` section is read, for `namespace`/`term_namespaces`) or `--namespace`, and `.tsv` input requires it. For `.nt` input it is optional: with a mapping, an IRI is shortened only when that bare term maps back to the same IRI, and other IRIs are written in full; without one, every IRI is cut to its last segment and the script fails if two IRIs collide. When a bare `.tsv` term becomes an IRI, the characters N-Triples forbids in IRIs plus `%`, `/` and `#` are percent-encoded, and converting to `.tsv` decodes them. `.tsv` terms starting with `http` are kept as full IRIs and those starting with `_:` as blank nodes, and `type` maps to `rdf:type` through `core/utils.DEFAULT_PREFIXES`. `core/utils.load_term_mapping` builds the mapping from `-f`/`--namespace`.

```bash
python -m skgg.cli.convert path/to/graph.tsv --namespace http://example.org/  # -> path/to/graph.nt
python -m skgg.cli.convert path/to/graph.nt -o path/to/out.tsv                # every IRI cut to its last segment
```

`cli/complete.py` completes a real graph with a rule set (importable as `complete(client, rules, term_mapping, source, complete_uri, chunk_size)`). It copies the source into `--complete-uri` (default `graph.base_uri`), replacing that graph's contents, and runs `engine/completion.complete_graph` on it until a pass adds nothing. If the source is `--complete-uri` itself, nothing is copied and the rules only add the derived triples to it. The source is a graph URI, or a `.nt`/`.tsv` file that `core/queries.initialize_graph` loads directly into `--complete-uri`. Every setting can come from `-f` (source `graph.base_uri`, rules `data.input_dir / rules.rules_file`, `rules.pca_threshold`, the namespace, and the database connection from `data`/`db_config`), and flags override it: `--source`, `--rules-file`, `--pca-threshold`, `--namespace`, `--database-url`, `--sparql-endpoint`, `--auth-type`, `--user`, `--password`, `--log-level`. Without `-f`, `--source`, `--rules-file`, `--pca-threshold` and `--namespace` are required, and the connection falls back to the `DataConfig`/`DatabaseAuthConfig` defaults (Virtuoso on port 8890). `--complete-uri` is required only without `-f`. No profiles are used and `remove_inverse_rules` is not applied. On Family at PCA threshold 1, completing the original 28,358-triple graph adds 49 triples in 3 passes; `data/family/family.nt` is that completed graph (28,407 triples) plus one `rdf:type` `http://Family.org/Person` triple for each of its 3,007 entities, 31,414 triples in all.

```bash
python -m skgg.cli.complete -f family.source.json                                                                       # completes graph.base_uri in place
python -m skgg.cli.complete -f family.source.json --complete-uri http://Family.org/complete                                  # graph.base_uri -> complete
python -m skgg.cli.complete -f family.source.json --source data/family/family.nt  --complete-uri http://Family.org/complete  # a file instead of base_uri
python -m skgg.cli.complete --source http://Family.org/source --complete-uri http://Family.org/complete --rules-file data/family/family.csv --pca-threshold 1 --namespace http://Family.org/ --database-url http://localhost:7200/ --sparql-endpoint repositories/Family --auth-type BASIC --user admin --password rootpassword
```

`cli/validate.py` checks a graph against the SHACL-SPARQL shapes of a `<dataset>.shapes.ttl` file and reports every triple that violates one (importable as `validate(client, source, shapes_file, term_mapping, graph_uri, chunk_size, output, keep_graph)`, which returns the number of flagged triples). `--source` picks the graph: a graph URI or a `.tsv` file, default the config's `graph.base_uri`. A graph URI is queried in place. A `.tsv` file is loaded into a temporary named graph, `--graph-uri` (default `graph.namespace` + `validation`, e.g. `http://FrenchRoyalty.org/validation`), replacing that graph's contents, and the graph is cleared afterwards unless `--keep-graph` is given; both flags are ignored, with a warning, for a graph URI source. It refuses a `--graph-uri` equal to the config's `base_uri`, `edb_uri` or `synthetic_uri`. `core/shapes.load_shapes` reads the shapes file with rdflib (only the shapes, never the data), and `core/queries.build_shape_query` wraps each `sh:select` in a subquery over `FROM <graph-uri>`, keeping only rows whose `$this` is one of the shape's targets (`sh:targetSubjectsOf`, `sh:targetObjectsOf`, `sh:targetClass`, `sh:targetNode`). Only `sh:sparql` constraints are checked; other shapes, deactivated ones, shapes without a target and queries that use `$shapesGraph`, `$currentShape` or `$PATH` are skipped with a warning. A result row names its triples as `?this ?path ?value`, then `?this2 ?path2 ?value2`, and so on. The report (`-o`, default a `.tsv` source's path with suffix `.violations.tsv`, or `data.input_dir / <last segment of the graph URI>.violations.tsv`) has one row per flagged triple and shape, with columns `line`, `subject`, `predicate`, `object`, `shape` and `message` (`sh:message`), sorted by the triple's line in the `.tsv` file. The line is empty for a triple that is not in the file, and always empty for a graph URI source. `-f` is required (database connection, term mapping and default source), `--shapes` defaults to `data.input_dir / <folder name>.shapes.ttl`, and `--log-level` overrides `logging.level`. Neither the source graph nor the source file is modified, and the pipeline does not use the shapes yet.

```bash
python -m skgg.cli.validate -f fr.no-literals.json                                          # graph.base_uri -> data/fr/source.violations.tsv
python -m skgg.cli.validate -f fr.no-literals.json --source data/fr/fr.no-literals.skgg.tsv  # -> data/fr/fr.no-literals.skgg.violations.tsv
python -m skgg.cli.validate -f fr.no-literals.json --source path/to/graph.tsv --shapes path/to/x.shapes.ttl -o path/to/report.tsv --keep-graph
```

`cli/main.py`'s `__main__` block parses `-f`/`--config-file`, `--skip-edb`, `--log-level`, and `--pca-threshold` from the CLI (see the `bash` example above) and calls `run_synthetic_graph_experiment` end-to-end; confirmed working (verified via `python -m skgg.cli.main -f fr.no-literals.json`; see `BACKLOG.md`). Check `BACKLOG.md` for the current TODO list before assuming any other code path is exercised/working.

## Architecture

Terse reference below; see `docs/architecture.md` for diagrams and prose, and `docs/concepts.md` for a glossary of the domain terms used here (EDB/IDB, Horn rule, closure, predicate profile, ...).

```
src/skgg/
  cli/
    main.py            # run_synthetic_graph_experiment: the end-to-end experiment pipeline
    upload.py           # standalone script: upload a .nt/.tsv file to a graph URI
    download.py         # standalone script: download a graph URI to .nt and .tsv files
    clean.py            # standalone script: copy a .nt/.tsv file to .tsv without duplicates or untyped objects; report untyped subjects
    convert.py          # standalone script: convert a triples file from .nt to .tsv or back
    complete.py         # standalone script: complete a graph URI or .nt/.tsv file with a rule set into a separate graph
    validate.py         # standalone script: report the triples of a graph URI or .tsv file that violate a SHACL-SPARQL shapes file
  core/
    config.py           # RunConfig and all sub-configs (dataclasses), loaded from configurations/*.json
    utils.py            # logging setup, config lookup, SPARQL client factory, term mapping and term formatting
    visualization.py    # plot_relation_graph: renders the predicate relation graph to a PNG, cycles in red
    rules.py           # Atom / RuleSignature (Horn rule) dataclasses, rule-set CSV parsing, relation graph and stale cycles, inverse rule pairs
    shapes.py           # SparqlConstraint / load_shapes: the sh:sparql constraints of a <dataset>.shapes.ttl file
    queries.py          # All SPARQL query construction + execution against the graph DB (insert/select/ask/clear/count)
  engine/
    metrics.py          # GraphMetrics / PredicateProfile: topological descriptors (domain/range frequency per predicate)
    edb.py               # Builds the Extensional DB: selects/generates triples satisfying rule bodies + profile constraints
    generator.py           # Lower-level triple generation/binding logic shared by edb.py/completion.py/cycles.py: grounding sampler for rule bodies (`sample_groundings`, used by edb.py and cycles.py), rule application (apply_rule, queries the real graph directly), and get_closed_rules/get_closed_preds (SPARQL checks for whether a rule/predicate reached its support/frequency target, used by completion.py and cli/main.py)
    completion.py          # complete_graph: forward-chains rules over a base graph assuming rule bodies are fully grounded
    cycles.py              # break_cycles: seeds one stale rule cycle (p -> p, A -> B -> A) per call in the synthetic graph; the caller completes it again
```

Data flow: **term mapping + rules CSV + source graph metrics → EDB (facts satisfying rule bodies) → IDB (rule-derived facts, grown until closure) → synthetic graph**, all mediated through SPARQL against the graph store, keyed by graph URIs defined per-experiment in the `graph` section of each config JSON (`base_uri`, `edb_uri`, `synthetic_uri`).

Rules are parsed from CSV into `RuleSignature`/`Atom` objects (`core/rules.py`); each rule has body atoms and a head atom over predicates/variables, plus confidence metrics (PCA/Std confidence). `parse_rule_set` expects lowercase snake_case columns (`rule_id`, `body`, `head`, `std_confidence`, `pca_confidence`, `head_coverage`, `positive_examples`), matching the CSVs written by `mine_rules/run_amie.py` like `data/fr/fr.no-literals.csv` — a CSV with the older CamelCase columns (`Body`/`Head`/`PCA_Confidence`/...) will raise a `KeyError`. Rule IDs are read from the `rule_id` column (`run_amie.py` numbers rules 1..N in AMIE's output order), so a rule keeps its ID whatever the PCA threshold; `parse_rule_set` raises `ValueError` if the column is missing or an ID is empty or repeated. Summaries and relation-graph labels sort IDs with `core/rules.rule_sort_key` (numeric IDs in numeric order). `rules.pca_threshold` in config (overridable per-run via `--pca-threshold`) classifies each rule's `HornRule.classification` as POSITIVE/NEGATIVE/UNKNOWN by comparing PCA confidence against the threshold; `parse_rule_set` then drops every non-POSITIVE rule, so only rules meeting the threshold are ever seen by EDB generation, IDB/completion, and cycle-breaking.

LoRA fine-tuning of LLMs and Chain-of-Thought dataset generation from KGs are not implemented under `src/` yet — check `notebooks/` (`notebooks/Disha/`, `notebooks/Mine/`) for exploratory/prototype work in that direction. `core/config.py` previously carried placeholder `FineTuningConfig`/`CoTGenerationConfig` dataclasses for this; they were removed as dead code (nothing read them) and should be reintroduced once that pipeline is actually built.

## Notes

- No test suite, linting/CI pipeline, or Makefile currently exists in this repo — `ruff` and `mypy` are configured in `pyproject.toml` (strict mypy, ruff rule sets E/F/I/UP/B/N) but are not wired into any automated command; run them manually (`ruff check .`, `mypy .`) if validating changes. See `BACKLOG.md` for the open question of whether/how to add a `tests/` + CI setup.
- Per-experiment outputs (logs) are written under `logs/`. This folder is gitignored.
- Input graph data (`.nt`/`.tsv`, `.ttl`, rule CSVs) lives under `data/`, a plain local folder with one subfolder per dataset (e.g. `data/family/`, `data/fr/`). Each config's `data.input_dir` picks the folder that its `graph.triple_file` and `rules.rules_file` are read from: `configurations/family.source.json` reads `data/family/`, and the four `configurations/fr*.json` (`fr.json`, `fr.no-literals.json`, `fr.fixed.json`, `fr.pygraft.json`) read `data/fr/`. `fr.fixed.json` reads `fr.no-literals.fixed.tsv`, which is `fr.no-literals.tsv` without 37 `child`, `parent`, `predecessor`, `spouse` and `successor` triples, and `fixed-rules.csv`, 22 hand-picked rules whose PCA confidence is set to 1. `fr.no-literals.fixed.csv` holds the rules AMIE mined from `fr.no-literals.fixed.tsv`. Loading a config raises `FileNotFoundError` if that folder does not exist.
- A dataset's schema is a `.ttl` file next to its data (e.g. `data/fr/fr.ttl`) that declares its classes (`owl:Class`) and entity-to-entity relations (`owl:ObjectProperty` with `rdfs:domain`/`rdfs:range`). Start new schemas from `schemas/template.ttl`. The pipeline does not read schemas yet (see `BACKLOG.md`).
- A dataset's SHACL constraints are a `<dataset>.shapes.ttl` file in its `data.input_dir` (e.g. `data/fr/fr.shapes.ttl`), with one `sh:sparql` constraint per shape. Its `ex:` prefix must equal the config's `graph.namespace`. Only `cli/validate.py` reads it.

## Writing documentation

These rules apply to every Markdown file in the repo (`AGENTS.md`, `BACKLOG*.md`, `README.md`, `docs/`) and to any other prose you write for it, such as commit messages and docstrings where they fit.

### Line breaks

Markdown has no line-length limit, so don't hard-wrap text. Write each paragraph and each list item on a single line, however long. Only break a line where the rendered output needs it: between paragraphs and blocks, between list items, table rows, and inside code blocks. Hard-wrapped text makes every edit reflow the lines around it and turns small changes into noisy diffs.

### Plain, readable prose

Avoid the patterns listed in Wikipedia's [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing). The point is readability, not hiding that an AI helped write the text. The ones that show up most in technical docs:

- Inflated significance or promotional tone: "pivotal", "crucial", "robust", "seamless", "marks a shift", "plays a key role".
- Filler words and transitions: "Additionally", "Furthermore", "Moreover", "It's worth noting that", "delve", "leverage", "utilize".
- "Serves as", "stands as" or "represents" where "is" works.
- Negative parallelisms ("not just X, but Y", "not X, but Y", "Y rather than X") and lists of three added only for rhythm.
- Trailing "-ing" clauses that add vague analysis, e.g. "…, highlighting the importance of X" or "…, ensuring consistency".
- Vague attributions ("it is widely considered", "experts argue") instead of a concrete source, file or measurement.
- Closing paragraphs that restate what was just said, or "Challenges and future work" sections with no specifics.
- Heavy formatting: bold scattered through sentences, em dashes as the default punctuation, bullet lists with bold inline headers where a sentence would do, emoji, headings that only contain other headings, skipped heading levels.
- Chat-style lines aimed at a reader in a conversation ("Let me know if…", "I hope this helps") and leftover placeholder text.

Instead, say what the code does using concrete names, numbers and file paths. Prefer short sentences and plain verbs, and cut any sentence that adds no information.
