# SKGG — Synthetic Knowledge Graph Generation

A tool for generating a **synthetic Knowledge Graph (KG)** from the
topological metrics of a source graph (node/relation counts, domain/range
frequencies per relation, etc.) and a set of Horn Rules — without needing
continued access to the original graph once its metrics are extracted.

Graphs are never loaded into memory-intensive libraries like RDFlib for bulk
work. They live in a graph database (Virtuoso or GraphDB) and are manipulated
via SPARQL through `SPARQLWrapper`.

## Requirements

- Python >= 3.10
- Docker (for the graph database)

## Setup

```bash
pip install -r requirements.txt
pip install -e .          # installs the `skgg` package from src/ in editable mode
```

## Graph database

`docker-compose.yml` defines two alternative stacks, selected via Compose
profiles — bring up one at a time:

```bash
docker compose --profile virtuoso up   # Virtuoso (8890) + YASGUI SPARQL UI (8080)
docker compose --profile graphdb up    # GraphDB 10.7 (7200)
docker compose --profile all up        # both
```

## Running an experiment

Experiments are driven by JSON config files in `configurations/` (e.g.
`mario.json`, `french_royalty.json`):

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/mario.json"))
```

This loads the config, computes graph metrics over SPARQL, parses the
ontology and Horn rule set, generates the EDB (facts satisfying rule bodies),
then grows the IDB (rule-derived facts) until closure — producing the
synthetic graph. For a full walkthrough (including uploading a base graph and
building the source graph first), see
[`docs/getting-started.md`](docs/getting-started.md).

## Project layout

```
src/skgg/          # package source (see docs/architecture.md for the full module map)
configurations/    # per-experiment JSON configs
.data/<Dataset>/   # source graph data (.nt/.tsv/.ttl/.csv) referenced by configs
docs/              # architecture, concepts glossary, getting-started guide
notebooks/         # exploratory/prototype work
logs/              # per-run logs (gitignored)
```

## More

- [`docs/getting-started.md`](docs/getting-started.md) — full setup + first
  experiment walkthrough.
- [`docs/architecture.md`](docs/architecture.md) — components and data flow,
  with diagrams.
- [`docs/concepts.md`](docs/concepts.md) — glossary of domain terms (EDB/IDB,
  Horn rules, closure, ...).
- [`AGENTS.md`](AGENTS.md) — terse architecture reference for contributors and
  AI coding agents alike (`CLAUDE.md` imports this same file for Claude Code).
- [`BACKLOG.md`](BACKLOG.md) — known issues and pending refactors; check
  before assuming a code path is exercised/working.
