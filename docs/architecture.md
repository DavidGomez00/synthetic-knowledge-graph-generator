# Architecture

This page expands the module map in [`AGENTS.md`](../AGENTS.md) with diagrams and the reasons behind the design. [`concepts.md`](concepts.md) defines the domain terms used below (EDB/IDB, Horn rule, closure, predicate profile, ...), and [`algorithm.md`](algorithm.md) describes the method independently of the code.

## Data flow

The pipeline turns a source graph into a synthetic one in two stages. It first profiles the uploaded base graph, then builds a new graph from those profiles and the rule set alone. Once the profiles are extracted, the original graph is not read again.

```mermaid
flowchart LR
    RAW["source graph<br/>(.nt/.tsv file)"] -->|cli/clean.py| NT["cleaned graph<br/>(.tsv file)"]
    NT -->|cli/upload.py| BASE[("base_uri")]
    NS["graph.namespace<br/>graph.term_namespaces"] --> TERM["term mapping"]
    RULES["rules<br/>(.csv file)"] -->|"parse_rule_set<br/>remove_cyclic_rules"| HORN["Horn rules"]

    BASE -->|engine/metrics.py| METRICS["GraphMetrics<br/>(per-predicate profiles)"]

    METRICS & HORN & TERM --> EDBGEN["engine/edb.py"]
    EDBGEN --> EDB[("edb_uri")]

    EDB -->|"engine/completion.py<br/>forward-chain rules"| SYN[("synthetic_uri")]
    SYN -->|"engine/fill.py<br/>fill open relations"| SYN

    style BASE fill:#2563eb,color:#fff
    style EDB fill:#2563eb,color:#fff
    style SYN fill:#16a34a,color:#fff
```

Blue nodes are named graphs in the database, keyed by the URIs in each config's `graph` section. The green node is the synthetic graph the pipeline delivers.

1. Preparation (`cli/clean.py`, optional and local). The pipeline does not support literals yet, so this script writes a cleaned copy of a `.nt`/`.tsv` file as a `.tsv` file, cutting `.nt` IRIs to their last segment. It replaces every `/` in a term's own name with `_` (written as `%2F` inside an IRI's last segment) and fails if two terms clean to the same name. It drops duplicate triples, every non-type triple whose object is never typed (never the subject of a `type`/`rdf:type` triple), and every triple whose predicate is in `--literal-predicates` (default `name`). Type triples are kept unless `--drop-types` is given, which drops them all last, after checking that every subject is typed to the same single class. It logs a warning for every subject that is never typed; DEBUG lists each one. `cli/convert.py` only converts a file between `.nt` and `.tsv`, keeping literals, and builds its term mapping from `-f` or `--namespace` (`core/utils.load_term_mapping`). Neither script touches the database.
2. Upload (`cli/upload.py`). Loads a `.nt` or `.tsv` file (`graph.triple_file`) into `base_uri`, or into `--graph-uri` if given. `.tsv` rows are bare `subject\tpredicate\tobject` terms, resolved to full URIs through the term mapping before insertion.
3. Metrics and rules (phase 1 of `cli/main.py`). `engine/metrics.py` profiles `base_uri` over SPARQL: per-predicate frequency, domain and range counts, reflexivity. These profiles are all the pipeline needs from the source graph. The rule set is then parsed (`core/rules.parse_rule_set`, which keeps only rules with std confidence 1, or with PCA confidence >= `rules.pca_threshold` when the config or `--pca-conf` sets one). `core/visualization.plot_relation_graph` writes the remaining rules' predicate dependency graph to `logs/relation_graph_<graph.name>.png`, with every predicate on a cycle drawn in red. `core/rules.remove_cyclic_rules` then deletes every [cyclic rule](concepts.md#cyclic-rule), so the rule set the EDB and completion use has no cycle.
4. EDB generation (phase 2). `engine/edb.py`'s `generate_extensional_predicates` fills `edb_uri` with triples for the extensional predicates (those no rule head produces) until each one reaches its target frequency. It applies three mechanisms in a fixed priority: `check_direct_matches` adds the triples the profiles force, `check_triples_from_rule` builds groundings of each rule's extensional body with `generator.sample_groundings` so the joins the rules need exist, and `insert_random_triples` spends the remaining budget with draws weighted by each entity's remaining count. See [section 3 of `algorithm.md`](algorithm.md#3-extensional-database-edb) for the method. `--skip-edb` skips this phase and reuses the triples already at `edb_uri`.
5. Completion (phase 3). `engine/completion.py`'s `complete_graph` copies `edb_uri` into `synthetic_uri`, then applies every rule on each pass and repeats until a pass adds nothing. It returns the number of triples added and takes a `label` for its log lines. Rules are not ordered, and `apply_rule` is called without a `profile`, so a rule can overshoot its head predicate's target frequency. The profiles passed to `complete_graph` are only used afterwards: `engine/generator.py`'s `get_closed_rules`/`get_closed_preds` check which rules reached their `support` and which predicates reached their `frequency`, and set their `closed` fields for reporting. Phase 5 logs the summary (see `cli/main.py` below). An earlier design, `engine/idb.py`'s `generate_idb`, ordered rules with the same head and checked upfront that every intensional predicate could be derived; it has been removed (see [Rule application order](concepts.md#rule-application-order)).
6. Filling open relations (phase 4). Completion leaves every kept rule closed, but not always every predicate. `engine/fill.py`'s `fill_open_predicates` adds the missing triples of each open predicate to `synthetic_uri`, drawing subjects and objects weighted by how many more triples they need to match their counts in `base_uri`, and keeps a triple only if it changes the support of no kept rule (see [Filling open relations](concepts.md#filling-open-relations)).

## Components

```mermaid
flowchart TD
    subgraph cli ["cli/"]
        MAIN["main.py<br/>run_synthetic_graph_experiment"]
        UPLOAD["upload.py"]
        DOWNLOAD["download.py"]
        COMPLETE["complete.py"]
        CLEAN["clean.py"]
        CONVERT["convert.py"]
        VALIDATE["validate.py"]
    end

    subgraph engine ["engine/"]
        METRICS["metrics.py"]
        EDB["edb.py"]
        GEN["generator.py"]
        COMPLETION["completion.py"]
        FILL["fill.py"]
        CYCLES["cycles.py"]
    end

    subgraph core ["core/"]
        RULES["rules.py"]
        SHAPES["shapes.py"]
        QUERIES["queries.py"]
        VIS["visualization.py"]
        CONFIG["config.py"]
        UTILS["utils.py"]
    end

    DB[("Virtuoso /<br/>GraphDB")]

    MAIN --> CONFIG & METRICS & EDB & GEN & COMPLETION & FILL & CYCLES & RULES & QUERIES & VIS & UTILS
    UPLOAD --> CONFIG & QUERIES & UTILS
    DOWNLOAD --> CONFIG & CONVERT & QUERIES & UTILS
    COMPLETE --> CONFIG & COMPLETION & RULES & QUERIES & UTILS
    VALIDATE --> CONFIG & SHAPES & QUERIES & UTILS
    CLEAN & CONVERT --> UTILS

    EDB --> GEN & METRICS & RULES & QUERIES & UTILS
    COMPLETION --> GEN & METRICS & RULES & QUERIES
    CYCLES --> GEN & METRICS & RULES & QUERIES & UTILS
    GEN --> QUERIES & RULES & METRICS & UTILS
    METRICS --> QUERIES
    QUERIES --> RULES & SHAPES & UTILS
    RULES --> UTILS
    UTILS --> CONFIG

    QUERIES -->|SPARQL| DB
```

`clean.py` and `convert.py` only read and write local files. Every other script reaches the database through `core/queries.py`.

### `core/`

- `config.py`: `RunConfig` and its sub-configs (dataclasses), loaded from `configurations/*.json`. Loading a config raises `FileNotFoundError` if `data.input_dir` does not exist. LoRA fine-tuning and Chain-of-Thought dataset generation are not implemented under `src/` yet (see `notebooks/` for prototypes); their placeholder `FineTuningConfig`/`CoTGenerationConfig` dataclasses were removed as dead code.
- `utils.py`: logging setup (`setup_logging`), config lookup under `configurations/` (`resolve_config_path`), SPARQL client construction from a `RunConfig` (`create_sparql_client`) or from explicit connection settings (`build_sparql_client`, used by `cli/complete.py`), and term handling. `build_term_mapping` merges `graph.term_namespaces` over `DEFAULT_PREFIXES` under the `graph.namespace` default, `load_term_mapping` builds the same mapping from `-f`/`--namespace` for the local-file scripts, `format_term`/`format_triple` turn bare terms into N-Triples, and `short_term` cuts a URI to its last segment.
- `rules.py`: the `Atom`/`RuleSignature`/`HornRule` dataclasses and CSV parsing into a rule set (`parse_rule_set`, keyed by the CSV's `rule_id` column; `rule_sort_key` orders those IDs numerically). `get_extensional_dependencies` gives the "more restrictive rule first" order that EDB generation uses ([`algorithm.md` §3.2.2](algorithm.md#322-mechanism-2-rule-driven-grounding)). `get_relation_graph`/`find_stale_cycles` build the predicate dependency graph and find the stale cycles in it for `engine/cycles.py`, and `remove_cyclic_rules` deletes every rule with an edge on a cycle of it (see [Cyclic rule](concepts.md#cyclic-rule)).
- `queries.py`: builds and runs every SPARQL query. No other module calls `SPARQLWrapper` directly to read or write. SELECTs are sent as URL-encoded POST requests, because `get_existing_triples` builds queries with large `VALUES` clauses that can exceed the store's maximum URL or header size for GET. `initialize_graph` clears a graph and fills it from another graph URI or a `.nt`/`.tsv` file. `insert_triples_bulk` and `export_graph_nt` move whole graphs in and out as N-Triples: on GraphDB through the repository's `/statements` REST endpoint, and on Virtuoso through its Graph Store endpoint for uploads and through sorted, paged CONSTRUCT queries for downloads, because Virtuoso's Graph Store endpoint returns at most `ResultSetMaxRows` triples. `TripleBuffer` wraps `insert_triples_sparql` to collect triples that were decided without a database read (`engine/edb.py`'s direct matches and random assignment) and insert them in fewer, larger batches.
- `shapes.py`: `load_shapes` reads the `sh:sparql` constraints of a `<dataset>.shapes.ttl` file into `SparqlConstraint`s (shape, `sh:message`, `sh:select`, targets, prefixes), parsing only that file with rdflib. `triples_in_row` reads the triples a result row names (`?this ?path ?value`, `?this2 ?path2 ?value2`, ...). `core/queries.build_shape_query` turns a constraint into a query over one named graph.
- `visualization.py`: `plot_relation_graph` renders a predicate dependency graph to a PNG with `matplotlib` (`Agg` backend, so it runs without a display), coloring predicates on a cycle red and labeling each edge with its rule IDs.

### `engine/`

- `metrics.py`: `GraphMetrics`/`PredicateProfile`, the topological descriptors of the source graph. `GraphMetrics.from_uri` computes them over SPARQL and dumps them to `logs/metrics/` for debugging.
- `generator.py`: triple-generation code shared by the other engine modules. `sample_groundings` builds groundings of a rule's extensional body directly from the predicate profiles, without materializing a cartesian product; `engine/edb.py` uses it, and so does `engine/cycles.py` to seed a stale cycle's ungrounded body atoms. `apply_rule` applies one rule, querying the graph for bindings; given a `profile`, it also checks with `is_assignment_solvable` that each new triple keeps the remaining profile realizable. `engine/completion.py` always calls it without a `profile`. `get_closed_rules`/`get_closed_preds` check over SPARQL whether a rule or predicate reached its `support` or `frequency` target.
- `edb.py`: EDB generation, step 4 of "Data flow" above.
- `completion.py`: `complete_graph`, step 5 of "Data flow" above. `cli/complete.py` also uses it on real graphs.
- `fill.py`: `fill_open_predicates`, step 6 of "Data flow" above. Each candidate triple goes through `core/queries.build_head_impact_query` and `build_body_impact_query` for every kept rule its predicate occurs in, and is inserted at once when it passes, so later checks see it.
- `cycles.py`: `break_cycles` finds stale cycles (`core/rules.find_stale_cycles`), seeds one rule of one cycle in the synthetic graph with `sample_groundings`, and returns the number of triples it seeded. `cli/main.py` no longer calls it, since `core/rules.remove_cyclic_rules` leaves no cycle to seed. See [Stale cycle](concepts.md#stale-cycle).

### `cli/`

- `main.py`: the experiment entry point (`run_synthetic_graph_experiment`, or `python -m skgg.cli.main -f <config>`). Before generating the EDB, it writes the rules left after the confidence filter and `remove_cyclic_rules` to `<rules_file stem>_used.csv` next to the rules file. Phase 1 ends by logging the extensional and intensional relations with their target frequencies, and `engine/edb.py` logs the relations warm-up added triples to and the extensional relations left to generate. At the start of phase 3 and after the completion, `_ClosureProgress.log` logs the number of closed predicates and rules, the ones that closed since the previous check, and each open extensional and intensional predicate's current frequency against its target. Phase 3 lists the rules it applies, and `complete_graph` logs the triples each rule added per pass and in total. Phase 4, `log_summary`, logs one report comparing the synthetic graph with the original: total triples, one row per predicate (frequency original -> synthetic, `[OPEN]`/`[CLOSED]`, and `[EXTENSIONAL]`/`[INTENSIONAL]`, where intensional means some rule has the predicate as its head), one row per rule (support original -> synthetic, `[OPEN]`/`[CLOSED]`), and then the rules `remove_cyclic_rules` removed, with their support only. URIs are shortened to their last segment and each rule is printed as `body => head`.
- `upload.py`: uploads a `.nt`/`.tsv` file into `base_uri`, or into `--graph-uri` if given (`python -m skgg.cli.upload -f <config>`). It runs no rule-based completion.
- `download.py`: writes a named graph to `<output>.nt` and `<output>.tsv` (`python -m skgg.cli.download -f <config> [--graph-uri <uri>] [-o <output>]`), with `core/queries.export_graph_nt` for the `.nt` and `cli/convert.convert` for the `.tsv`. It fails if the `.nt` has a different number of triples than the graph.
- `complete.py`: completes a real graph with a rule set (`python -m skgg.cli.complete [-f <config>] [--complete-uri <uri>]`). It copies a graph URI or a `.nt`/`.tsv` file into `--complete-uri` (default `graph.base_uri`) and runs `complete_graph` on it until a pass adds nothing. If the source is `--complete-uri`, it is completed in place without a copy. Settings come from a config, from flags (including the database connection), or both. It uses no profiles and does not apply `remove_cyclic_rules`.
- `validate.py`: reports the triples of a graph that violate a SHACL-SPARQL shapes file (`python -m skgg.cli.validate -f <config> [--source <uri|file.tsv>] [--shapes <file>] [--graph-uri <uri>] [-o <report>] [--keep-graph]`). The source defaults to the config's `graph.base_uri`, which is queried in place. A `.tsv` source is loaded into a temporary named graph that is cleared after the check. It runs every constraint with `build_shape_query` and writes a `.violations.tsv` report with the line number (`.tsv` sources only), terms, shape and message of each flagged triple.
- `clean.py`: writes a cleaned `.tsv` copy of a `.nt`/`.tsv` file, step 1 of "Data flow" above.
- `convert.py`: converts a triples file from `.nt` to `.tsv` or back, dropping only duplicate triples and lines that are not a triple. Literals are kept; in the `.tsv` a literal becomes its bare text.

## Known rough edges

See [`BACKLOG.md`](../BACKLOG.md) for the current list.
