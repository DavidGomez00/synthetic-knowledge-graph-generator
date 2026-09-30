# SKGG: Synthetic Knowledge Graph Generation

A tool for generating a **synthetic Knowledge Graph (KG)** from the topological metrics of a source graph (node/relation counts, domain/range frequencies per relation) and a set of Horn Rules.

Graphs are never loaded into memory-intensive libraries like RDFlib for bulk work. They live in a graph database (Virtuoso or GraphDB) and are manipulated via SPARQL through `SPARQLWrapper`.

## Requirements

- Python >= 3.10
- Docker (for the graph database)

## Setup

```bash
pip install -r requirements.txt
pip install -e .          # installs the `skgg` package from src/ in editable mode
```

## Graph database

`docker-compose.yml` defines two alternative stacks, selected via Compose profiles — bring up one at a time:

```bash
docker compose --profile graphdb up    # GraphDB 10.7 (7200), RECOMMENDED
docker compose --profile virtuoso up   # Virtuoso (8890) + YASGUI SPARQL UI (8080)
docker compose --profile all up        # both
```

## Running an experiment

Experiments are driven by JSON config files in `configurations/` (e.g. `french_royalty.source.json`).

### Prepare the data

This method does not support literals yet, so `skgg.cli.prepare_data` handles the source graph to create a new version with no literals, creating a copy in `<output>.tsv` and `<output>.nt` formats. This drops duplicate triples, every non-type triple whose object is never typed (literals), and every triple with a fixed predicate listed in `--literal-predicates` (default `name` for FrenchRoyalty), and logs a warning for every subject that is never typed.

```bash
python -m skgg.cli.prepare_data data/french_royalty/source/french_royalty.tsv -f french_royalty.source.json  # -> data/french_royalty/source/french_royalty.no-literals.{tsv,nt}
python -m skgg.cli.prepare_data path/to/graph.nt -o path/to/out --log-level DEBUG              # -> path/to/out.{tsv,nt}; DEBUG lists every untyped subject
```

Without `-o`, the output goes next to the input as `<stem>.no-literals.{tsv,nt}`. A `.tsv` input needs a term mapping to write its `.nt` copy: pass a config with `-f` or a default namespace with `--namespace`. An `.nt` input needs neither.

### Convert between .nt and .tsv

`skgg.cli.convert` converts a triples file from `.nt` to `.tsv` or from `.tsv` to `.nt`, depending on the input's suffix. Without `-o`, the output goes next to the input with the other suffix. It removes only duplicate triples and lines that are not a triple, and logs a warning for each skipped line.

```bash
python -m skgg.cli.convert path/to/graph.tsv --namespace http://example.org/  # -> path/to/graph.nt
python -m skgg.cli.convert path/to/graph.tsv -f french_royalty.source.json    # namespaces from the config's graph section
python -m skgg.cli.convert path/to/graph.nt -o path/to/out.tsv                # every IRI cut to its last segment
```

A `.tsv` input needs a term mapping, from `--namespace` or from a config passed with `-f` (as for `prepare_data`). Each row must hold three tab-separated values, which become N-Triples terms as follows:

- `type` in the predicate column becomes `rdf:type`.
- A value starting with `http` is kept as a full IRI, and one starting with `_:` as a blank node.
- Any other value is appended to its namespace. Spaces, `%`, `/`, `#` and the other characters N-Triples forbids in IRIs are percent-encoded.

For example, the row `143<TAB>niece<TAB>340` with `--namespace http://Family.org/` becomes `<http://Family.org/143> <http://Family.org/niece> <http://Family.org/340> .`. Objects always become IRIs, so a `.tsv` file cannot hold literals.

For an `.nt` input the term mapping is optional. With one, an IRI is shortened to a bare value only when that value maps back to the same IRI, and other IRIs are kept in full. Without one, every IRI is cut to its last segment, and the script fails if two IRIs share a last segment. Percent-encoded characters are decoded, and a literal becomes its text without quotes, language tag or datatype.

### Upload the source graph

`skgg.cli.upload` inserts a `.nt`/`.tsv` triple file into the graph database, in the named graph `graph.base_uri`. Terms in a `.tsv` file are resolved to full URIs through the config's `graph.namespace` and `graph.term_namespaces`.

```bash
python -m skgg.cli.upload -f french_royalty.source.json --triple-file data/french_royalty/source/french_royalty.no-literals.tsv
python -m skgg.cli.upload -f french_royalty.source.json   # uploads graph.triple_file
```

To upload the file written by `prepare_data`, pass it with `--triple-file` or set `graph.triple_file` to it. A bare filename resolves under `data.input_dir`, and a path containing `/` is used as given. `--graph-uri` uploads to a different named graph, e.g. to re-insert an already generated synthetic graph into `graph.synthetic_uri`. `--log-level` overrides the config's `logging.level` for the run.

### Complete a graph with its rules

`skgg.cli.complete` applies a rule set to a real graph until a pass adds nothing, and writes the result to `--complete-uri`, replacing that graph's contents. The source is a graph URI or a `.nt`/`.tsv` file, which is loaded directly into `--complete-uri`. A config passed with `-f` supplies the source (`graph.base_uri`), rules file, PCA threshold, namespace and database connection, and flags override any of them. Without `-f`, pass `--source`, `--rules-file`, `--pca-threshold`, `--namespace` and the connection flags (`--database-url`, `--sparql-endpoint`, `--auth-type`, `--user`, `--password`).

```bash
python -m skgg.cli.complete -f family.source.json --complete-uri http://Family.org/complete                                  # graph.base_uri -> complete
python -m skgg.cli.complete -f family.source.json --source data/family/family.nt  --complete-uri http://Family.org/complete  # a file instead of base_uri
python -m skgg.cli.complete --source http://Family.org/source --complete-uri http://Family.org/complete --rules-file data/family/family.csv --pca-threshold 1 --namespace http://Family.org/ --database-url http://localhost:7200/ --sparql-endpoint repositories/Family --auth-type BASIC --user admin --password rootpassword
```

### Run the pipeline

```bash
python -m skgg.cli.main -f french_royalty.source.json
python -m skgg.cli.main -f french_royalty.source.json --skip-edb --log-level DEBUG
```

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.source.json"))
```

This loads the config, computes graph metrics over SPARQL, parses the ontology and Horn rule set, generates the EDB, then grows the rule-derived facts until a fixed point, producing the synthetic graph.

`-f`/`--config-file` resolves a bare filename under `configurations/`; `--skip-edb` reuses the existing EDB graph instead of regenerating it; `--log-level` overrides the config's `logging.level` for that run. For a full walkthrough, see [`docs/getting-started.md`](docs/getting-started.md).

## Project layout

```
src/skgg/          # package source (see docs/architecture.md for the full module map)
configurations/    # per-experiment JSON configs
schemas/           # schema template (.ttl) for declaring a graph's classes and relations
data/<dataset>/    # source graph data (.nt/.tsv/.ttl/.csv) referenced by configs, e.g. data/french_royalty/source/
docs/              # architecture, concepts glossary, getting-started guide
notebooks/         # exploratory/prototype work
logs/              # per-run logs (gitignored)
```

## More

- [`docs/getting-started.md`](docs/getting-started.md): full setup + first experiment walkthrough.
- [`docs/architecture.md`](docs/architecture.md): components and data flow, with diagrams.
- [`docs/concepts.md`](docs/concepts.md): glossary of domain terms (EDB/IDB, Horn rules, closure, ...).
- [`AGENTS.md`](AGENTS.md): terse architecture reference for contributors and AI coding agents alike (`CLAUDE.md` imports this same file for Claude Code).
- [`BACKLOG.md`](BACKLOG.md): known issues and pending refactors; check before assuming a code path is exercised/working.
