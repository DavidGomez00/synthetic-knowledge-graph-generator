# Getting started

A walkthrough of setting up the project and running one experiment end-to-end,
using the Mario dataset (`configurations/mario.json`). `french_royalty.json`
also loads cleanly.

## 1. Install

```bash
pip install -r requirements.txt
pip install -e .          # installs `skgg` from src/ in editable mode
```

Requires Python >= 3.10.

## 2. Start a graph database

`mario.json` points at GraphDB (`database_url: http://localhost:7200/`), so:

```bash
docker compose --profile graphdb up
```

GraphDB doesn't auto-create repositories from a client connection the way
Virtuoso auto-creates graphs — before running anything, open the GraphDB
Workbench at `http://localhost:7200` and create a repository whose ID matches
the config's `sparql_endpoint` (for `mario.json`: `repositories/MarioGraph` →
repository ID `MarioGraph`).

If you'd rather use Virtuoso instead, `docker compose --profile virtuoso up`
brings up Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080); point a
config's `data.database_url`/`sparql_endpoint` at it and set
`db_config.auth_type` to `"DIGEST"` (GraphDB uses `"BASIC"`).

## 3. Build the source graph (upload + completion)

`cli/upload.py` is a standalone script, not a function — edit the
`graph_config` path at its top (defaults to `configurations/mario.json`) if
you want a different dataset, then run it directly:

```bash
python -m skgg.cli.upload
```

This uploads `.data/Mario/mario.nt` (`graph.triple_file` in the config) into
`base_uri`, then forward-chains the rule set over it (`engine/completion.py`)
to produce `complete_uri` — the graph that metrics get extracted from. See
[`architecture.md`](architecture.md) for why this "completion" step exists.

`graph.triple_file` also accepts a `.tsv` file of bare `subject<TAB>predicate<TAB>object`
terms — `french_royalty.json` uses this format
(`.data/FrenchRoyalty/french_royalty.tsv`); terms are resolved to full URIs via
the ontology term mapping before insertion, the same way rule bodies are.

## 4. Run the experiment

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/mario.json"))
```

This computes `GraphMetrics` from `complete_uri`, generates the EDB, then
grows the IDB into `synthetic_uri` — the finished synthetic graph. Progress is
logged to the console (level set by each config's `logging.level`) and a copy
is written under `logs/` (gitignored).

## Where things live

| What | Where |
|---|---|
| Source data per dataset (`.nt`/`.tsv`/`.ttl`/rules `.csv`) | `.data/<Dataset>/`, referenced by `data.input_dir` in the matching config |
| Experiment configs | `configurations/*.json` |
| Named graphs (base/complete/EDB/synthetic) | in the running Virtuoso/GraphDB instance, keyed by the URIs in each config's `graph` section — nothing is written to disk by `cli/main.py` |
| Run logs | `logs/` (gitignored) |

## Troubleshooting

- Full architecture and known rough edges: [`architecture.md`](architecture.md),
  [`concepts.md`](concepts.md), [`../BACKLOG.md`](../BACKLOG.md).
