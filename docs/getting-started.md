# Getting started

A walkthrough of setting up the project and running one experiment end-to-end,
using the French Royalty dataset (`configurations/french_royalty_source.json`).
`lung_cancer.json` also loads cleanly.

## 1. Install

```bash
pip install -r requirements.txt
pip install -e .          # installs `skgg` from src/ in editable mode
```

Requires Python >= 3.10.

## 2. Start a graph database

`french_royalty_source.json` points at GraphDB (`database_url: http://localhost:7200/`), so:

```bash
docker compose --profile graphdb up
```

GraphDB doesn't auto-create repositories from a client connection the way
Virtuoso auto-creates graphs — before running anything, open the GraphDB
Workbench at `http://localhost:7200` and create a repository whose ID matches
the config's `sparql_endpoint` (for `french_royalty_source.json`:
`repositories/FrenchRoyalty` → repository ID `FrenchRoyalty`).

If you'd rather use Virtuoso instead, `docker compose --profile virtuoso up`
brings up Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080); point a
config's `data.database_url`/`sparql_endpoint` at it and set
`db_config.auth_type` to `"DIGEST"` (GraphDB uses `"BASIC"`).

## 3. Build the source graph (upload + completion)

`cli/upload.py` is a standalone script, not a function:

```bash
python -m skgg.cli.upload -f french_royalty_source.json --complete
```

This uploads `.data/french_royalty/source/french_royalty.tsv`
(`graph.triple_file` in the config, resolved under `data.input_dir`) into
`base_uri`, then — because `--complete` was passed — forward-chains the rule
set over it (`engine/completion.py`) to produce `complete_uri`, the graph
that metrics get extracted from. See [`architecture.md`](architecture.md) for
why this "completion" step exists. Omit `--complete` to only upload the base
graph. `-f`/`--config-file` resolves a bare filename under `configurations/`
(same as step 4 below), and `--log-level`/`--pca-threshold` override the
config's `logging.level`/`rules.pca_threshold` for the run.

`graph.triple_file` accepts a `.tsv` file of bare
`subject<TAB>predicate<TAB>object` terms — `french_royalty_source.json` uses this
format; terms are resolved to full URIs via the term mapping before
insertion, the same way rule bodies are. An `.nt` file works too.

## 4. Run the experiment

```bash
python -m skgg.cli.main -f french_royalty_source.json
```

or, as a library call:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty_source.json"))
```

This computes `GraphMetrics` from `complete_uri`, generates the EDB, then
grows the IDB into `synthetic_uri` — the finished synthetic graph. Progress is
logged to the console (level set by each config's `logging.level`) and a copy
is written under `logs/` (gitignored).

The CLI also accepts:
- `--skip-edb` — skip EDB generation and reuse whatever triples already sit
  at `graph.edb_uri` (e.g. from a previous run).
- `--log-level DEBUG` (or `INFO`/`WARNING`/...) — override the config's
  `logging.level` for this run only, without editing the JSON file.
- `--pca-threshold 0.9` — override the config's `rules.pca_threshold` for
  this run only; rules with PCA confidence below it are excluded from every
  pipeline step.

```bash
python -m skgg.cli.main -f french_royalty_source.json --skip-edb --log-level DEBUG
```

## Where things live

| What | Where |
|---|---|
| Source data per dataset (`.nt`/`.tsv`/`.ttl`/rules `.csv`) | `.data/<dataset>/`, referenced by `data.input_dir` in the matching config |
| Experiment configs | `configurations/*.json` |
| Named graphs (base/complete/EDB/synthetic) | in the running Virtuoso/GraphDB instance, keyed by the URIs in each config's `graph` section — nothing is written to disk by `cli/main.py` |
| Run logs | `logs/` (gitignored) |

## Troubleshooting

- Full architecture and known rough edges: [`architecture.md`](architecture.md),
  [`concepts.md`](concepts.md), [`../BACKLOG.md`](../BACKLOG.md).
