# AGENTS.md

This file provides guidance to AI coding assistants (Claude Code, Codex,
Cursor, Gemini CLI, etc.) when working with code in this repository.

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

Experiments are driven by JSON config files in `configurations/` (e.g. `mario.json`, `french_royalty.json`), loaded via `RunConfig.from_json(...)`.

The main entry point is `run_synthetic_graph_experiment` in `src/skgg/cli/main.py`:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/mario.json"))
```

Typical experiment flow (see `cli/main.py`):
1. Load `RunConfig` from JSON and set up logging.
2. Compute `GraphMetrics` (predicate profiles) from the source graph (`graph.complete_uri`) over SPARQL.
3. Parse the ontology into a term mapping and parse the Horn rule set from a CSV (`rules.rules_file`), filtered by `pca_threshold`.
4. Generate the EDB (extensional database) — `engine/edb.py` — inserting triples that satisfy rule bodies/profiles into `graph.edb_uri`.
5. Generate the IDB (intensional database) — `engine/idb.py` — applying rules over the EDB to produce the synthetic graph at `graph.synthetic_uri`, iterating until rules/predicates reach target support/frequency (closure). Within a same-head-predicate group, rules are applied in intensional-dependency order (`core/rules.py`'s `get_intensional_dependencies`): more restrictive rules close before more general ones, and recursive rules wait for all non-recursive rules producing the same head — see `docs/concepts.md`.

`cli/upload.py` is a separate, standalone script (run top-level, not via a function) that uploads a base graph from a `.nt` or `.tsv` file (`graph.triple_file` in config; `.tsv` rows are bare `subject\tpredicate\tobject` terms, resolved to full URIs via the ontology term mapping) and then runs rule-based completion (`engine/completion.py`) to build the "complete" graph used as the source for metric extraction. Edit the `graph_config` path at the top of the file before running.

`cli/main.py`'s `__main__` block runs `run_synthetic_graph_experiment` end-to-end and is confirmed working (verified via `python -m skgg.cli.main` against `mario.json`; see `BACKLOG.md`). Check `BACKLOG.md` for the current TODO list before assuming any other code path is exercised/working.

## Architecture

Terse reference below; see `docs/architecture.md` for diagrams and prose, and
`docs/concepts.md` for a glossary of the domain terms used here (EDB/IDB, Horn
rule, closure, predicate profile, ...).

```
src/skgg/
  config.py           # RunConfig and all sub-configs (dataclasses), loaded from configurations/*.json
  utils.py             # logging setup, SPARQL client factory, misc file helpers
  cli/
    main.py            # run_synthetic_graph_experiment: the end-to-end experiment pipeline
    upload.py           # standalone script: upload base graph + rule-based completion
  core/
    rules.py           # Atom / RuleSignature (Horn rule) dataclasses, rule-set CSV parsing, term mapping from ontology
    queries.py          # All SPARQL query construction + execution against the graph DB (insert/select/ask/clear/count)
  engine/
    metrics.py          # GraphMetrics / PredicateProfile: topological descriptors (domain/range frequency per predicate)
    edb.py               # Builds the Extensional DB: selects/generates triples satisfying rule bodies + profile constraints
    idb.py                # Builds the Intensional DB: iteratively applies rules over the EDB, tracking closure of rules/predicates
    generator.py           # Lower-level triple generation/binding logic shared by edb.py/idb.py/completion.py: search space construction (edb.py only) plus rule application (apply_rule, queries the real graph directly)
    completion.py          # complete_graph: forward-chains rules over a base graph assuming rule bodies are fully grounded
```

Data flow: **ontology + rules CSV + source graph metrics → EDB (facts satisfying rule bodies) → IDB (rule-derived facts, grown until closure) → synthetic graph**, all mediated through SPARQL against the graph store, keyed by graph URIs defined per-experiment in the `graph` section of each config JSON (`base_uri`, `complete_uri`, `edb_uri`, `synthetic_uri`).

Rules are parsed from CSV into `RuleSignature`/`Atom` objects (`core/rules.py`); each rule has body atoms and a head atom over predicates/variables, plus confidence metrics (PCA/Std confidence). `parse_rule_set` expects lowercase snake_case columns (`body`, `head`, `std_confidence`, `pca_confidence`, `head_coverage`, `positive_examples`), matching AMIE-style mined-rule exports like `.data/FrenchRoyalty/french_royalty_stdc=1.csv` — a CSV with the older CamelCase columns (`Body`/`Head`/`PCA_Confidence`/...) will raise a `KeyError`; see `BACKLOG.md` for the resulting `.data/Mario/mario.csv` incompatibility. `rules.pca_threshold` in config classifies each rule's `HornRule.classification` as POSITIVE/NEGATIVE/UNKNOWN by comparing PCA confidence against the threshold, but nothing currently filters rules out of EDB/IDB generation based on that classification — see `BACKLOG.md`.

LoRA fine-tuning of LLMs and Chain-of-Thought dataset generation from KGs are not implemented under `src/` yet — check `notebooks/` (`notebooks/Disha/`, `notebooks/Mine/`) for exploratory/prototype work in that direction. `config.py` previously carried placeholder `FineTuningConfig`/`CoTGenerationConfig` dataclasses for this; they were removed as dead code (nothing read them) and should be reintroduced once that pipeline is actually built.

## Notes

- No test suite, linting/CI pipeline, or Makefile currently exists in this repo — `ruff` and `mypy` are configured in `pyproject.toml` (strict mypy, ruff rule sets E/F/I/UP/B/N) but are not wired into any automated command; run them manually (`ruff check .`, `mypy .`) if validating changes. See `BACKLOG.md` for the open question of whether/how to add a `tests/` + CI setup.
- Per-experiment outputs (logs) are written under `logs/`. This folder is gitignored.
- Input graph data (`.nt`/`.tsv`, `.ttl`, rule CSVs) per dataset lives under `.data/<Dataset>/` (e.g. `.data/Mario/`, `.data/FrenchRoyalty/`) and is referenced by `data.input_dir` in each experiment config.
