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

Experiments are driven by JSON config files in `configurations/` (e.g. `french_royalty.source.json`, `lung_cancer.json`), loaded via `RunConfig.from_json(...)`.

The main entry point is `run_synthetic_graph_experiment` in `src/skgg/cli/main.py`, runnable either as a library call or from the CLI:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.source.json"))
```

```bash
python -m skgg.cli.main -f french_royalty.source.json
python -m skgg.cli.main -f french_royalty.source.json --skip-edb --log-level DEBUG
python -m skgg.cli.main -f french_royalty.source.json --pca-threshold 0.95
```

`-f`/`--config-file` (required) accepts a bare filename resolved under `configurations/` (or a path, used as-is, if it contains a `/`); `--skip-edb` skips EDB generation and reuses whatever triples already sit at `graph.edb_uri`; `--log-level` overrides the config's `logging.level` for that run only, without editing the JSON file; `--pca-threshold` overrides the config's `rules.pca_threshold` for that run only.

Typical experiment flow (see `cli/main.py`):
1. Load `RunConfig` from JSON and set up logging.
2. Compute `GraphMetrics` (predicate profiles) from the source graph (`graph.base_uri`) over SPARQL.
3. Build the term mapping (`graph.term_namespaces` overrides merged over `utils.DEFAULT_PREFIXES`, under the `graph.namespace` default) and parse the Horn rule set from a CSV (`rules.rules_file`), keeping only rules classified POSITIVE (PCA confidence >= `pca_threshold`) — NEGATIVE and UNKNOWN (missing PCA confidence) rules are dropped and excluded from every later step.
4. Generate the EDB (extensional database) — `engine/edb.py` — inserting triples that satisfy rule bodies/profiles into `graph.edb_uri`.
5. As currently wired, `cli/main.py` logs five numbered phases (`[n/5]`): metrics/rules, EDB, completion, cycle-breaking, summary. The completion phase forward-chains *every* rule over the EDB with `engine/completion.py`'s `complete_graph` (which returns the triples it added and takes a `label` for its log lines), repeating each pass until nothing more is added — no rule ordering and no profile budget applied during this loop (`apply_rule` is called without a `profile`, so a rule can in principle overshoot its head predicate's target frequency). The cycle-breaking phase then alternates `engine/cycles.py`'s `break_cycles` (seeds one stale cycle per call, returns the seeded triple count) with `complete_graph` until nothing is seeded — see "Stale cycle" in `docs/concepts.md`. Rule/predicate closure (`support`/`frequency` targets reached) is tracked via `engine/generator.py`'s `get_closed_rules`/`get_closed_preds`, called after each `complete_graph` pass — see "Rule application order" in `docs/concepts.md` for why this differs from `engine/idb.py`'s original (now-removed) `generate_idb`.

`cli/upload.py` is a separate, standalone script (CLI under an `if __name__ == "__main__":` guard; the upload step itself is the importable `upload_graph(client, triple_file, graph_uri, term_mapping)`) that uploads a base graph from a `.nt` or `.tsv` file (`graph.triple_file` in config; `.tsv` rows are bare `subject\tpredicate\tobject` terms, resolved to full URIs via the term mapping):

```bash
python -m skgg.cli.upload -f french_royalty.source.json
python -m skgg.cli.upload -f french_royalty.source.json --log-level DEBUG
python -m skgg.cli.upload -f french_royalty.normalized.json --triple-file path/to/skgg.tsv --graph-uri http://FrenchRoyalty.org/normalized/skgg
```

It only uploads: the pipeline reads its metrics from `graph.base_uri` as uploaded, with no rule-based completion beforehand. It takes the same `-f`/`--config-file` and `--log-level` as `cli/main.py`. `--triple-file` overrides `graph.triple_file` (a bare filename resolves under `data.input_dir`; a path containing `/` is used as given) and `--graph-uri` overrides the target graph (default `graph.base_uri`) — e.g. to re-insert an already generated synthetic graph into `graph.synthetic_uri` without regenerating it.

`cli/prepare_data.py` is another standalone, local-only script (no SPARQL; importable as `prepare_data(input_file, output, term_mapping)`). It writes a cleaned copy of a `.nt`/`.tsv` file in **both** formats (`<output>.tsv` and `<output>.nt`), with two things removed: duplicate triples (the first occurrence is kept), and "literals", meaning every non-type triple whose object is never typed (never the subject of a `type`/`rdf:type` triple). Every triple whose predicate is in `--literal-predicates` (default `name`, whose objects are always literals, even when a person's name equals their entity ID and so looks typed) is dropped too. Type triples are always kept. It also logs a warning for every subject term that is never typed.

Converting `.tsv` to `.nt` needs a term mapping: pass `-f` (only the config's `graph` section is read, for `namespace`/`term_namespaces`) or `--namespace`. A `/` inside a bare term is written as `%2F` in the `.nt`. For `.nt` input no mapping is needed: every IRI is cut to its last segment in the `.tsv` (`%2F` decoded back to `/`), and the script fails if two IRIs collide.

```bash
python -m skgg.cli.prepare_data .data/source/french_royalty.tsv -f french_royalty.source.json  # -> french_royalty.no-literals.{tsv,nt}
python -m skgg.cli.prepare_data path/to/graph.nt -o path/to/out --log-level DEBUG              # -> out.{tsv,nt}; DEBUG lists every untyped subject
```

`cli/main.py`'s `__main__` block parses `-f`/`--config-file`, `--skip-edb`, `--log-level`, and `--pca-threshold` from the CLI (see the `bash` example above) and calls `run_synthetic_graph_experiment` end-to-end; confirmed working (verified via `python -m skgg.cli.main -f french_royalty.source.json`; see `BACKLOG.md`). Check `BACKLOG.md` for the current TODO list before assuming any other code path is exercised/working.

## Architecture

Terse reference below; see `docs/architecture.md` for diagrams and prose, and `docs/concepts.md` for a glossary of the domain terms used here (EDB/IDB, Horn rule, closure, predicate profile, ...).

```
src/skgg/
  config.py           # RunConfig and all sub-configs (dataclasses), loaded from configurations/*.json
  utils.py             # logging setup, SPARQL client factory, misc file helpers
  cli/
    main.py            # run_synthetic_graph_experiment: the end-to-end experiment pipeline
    upload.py           # standalone script: upload a .nt/.tsv file to a graph URI
    prepare_data.py     # standalone script: copy a .nt/.tsv file without duplicates or untyped objects; report untyped subjects
  core/
    rules.py           # Atom / RuleSignature (Horn rule) dataclasses, rule-set CSV parsing
    queries.py          # All SPARQL query construction + execution against the graph DB (insert/select/ask/clear/count)
  engine/
    metrics.py          # GraphMetrics / PredicateProfile: topological descriptors (domain/range frequency per predicate)
    edb.py               # Builds the Extensional DB: selects/generates triples satisfying rule bodies + profile constraints
    generator.py           # Lower-level triple generation/binding logic shared by edb.py/completion.py/cycles.py: grounding sampler for rule bodies (`sample_groundings`, used by edb.py and cycles.py), rule application (apply_rule, queries the real graph directly), and get_closed_rules/get_closed_preds (SPARQL checks for whether a rule/predicate reached its support/frequency target, used by completion.py and cli/main.py)
    completion.py          # complete_graph: forward-chains rules over a base graph assuming rule bodies are fully grounded
    cycles.py              # break_cycles: seeds one stale rule cycle (p -> p, A -> B -> A) per call in the synthetic graph; the caller completes it again
```

Data flow: **term mapping + rules CSV + source graph metrics → EDB (facts satisfying rule bodies) → IDB (rule-derived facts, grown until closure) → synthetic graph**, all mediated through SPARQL against the graph store, keyed by graph URIs defined per-experiment in the `graph` section of each config JSON (`base_uri`, `edb_uri`, `synthetic_uri`).

Rules are parsed from CSV into `RuleSignature`/`Atom` objects (`core/rules.py`); each rule has body atoms and a head atom over predicates/variables, plus confidence metrics (PCA/Std confidence). `parse_rule_set` expects lowercase snake_case columns (`body`, `head`, `std_confidence`, `pca_confidence`, `head_coverage`, `positive_examples`), matching AMIE-style mined-rule exports like `.data/source/french_royalty.no-literals.csv` — a CSV with the older CamelCase columns (`Body`/`Head`/`PCA_Confidence`/...) will raise a `KeyError`. `rules.pca_threshold` in config (overridable per-run via `--pca-threshold`) classifies each rule's `HornRule.classification` as POSITIVE/NEGATIVE/UNKNOWN by comparing PCA confidence against the threshold; `parse_rule_set` then drops every non-POSITIVE rule, so only rules meeting the threshold are ever seen by EDB generation, IDB/completion, and cycle-breaking.

LoRA fine-tuning of LLMs and Chain-of-Thought dataset generation from KGs are not implemented under `src/` yet — check `notebooks/` (`notebooks/Disha/`, `notebooks/Mine/`) for exploratory/prototype work in that direction. `config.py` previously carried placeholder `FineTuningConfig`/`CoTGenerationConfig` dataclasses for this; they were removed as dead code (nothing read them) and should be reintroduced once that pipeline is actually built.

## Notes

- No test suite, linting/CI pipeline, or Makefile currently exists in this repo — `ruff` and `mypy` are configured in `pyproject.toml` (strict mypy, ruff rule sets E/F/I/UP/B/N) but are not wired into any automated command; run them manually (`ruff check .`, `mypy .`) if validating changes. See `BACKLOG.md` for the open question of whether/how to add a `tests/` + CI setup.
- Per-experiment outputs (logs) are written under `logs/`. This folder is gitignored.
- Input graph data (`.nt`/`.tsv`, `.ttl`, rule CSVs) lives under `.data/`, a git-tracked symlink to a local, unversioned folder that currently holds the French Royalty data, one subfolder per variant: `source/`, `normalized/`, `pygraft/` and `skgg/` (synthetic graphs). Each config's `data.input_dir` picks the subfolder (`configurations/french_royalty.{source,normalized,pygraft}.json`). `configurations/lung_cancer.json` still expects `.data/lung_cancer/`, which this layout does not provide.

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
