# Getting started

A walkthrough of setting up the project and running one experiment end-to-end, using the French Royalty dataset (`configurations/french_royalty.source.json`).

## 1. Install

```bash
pip install -r requirements.txt
pip install -e .          # installs `skgg` from src/ in editable mode
```

Requires Python >= 3.10.

## 2. Start a graph database

`french_royalty.source.json` points at GraphDB (`database_url: http://localhost:7200/`), so:

```bash
docker compose --profile graphdb up
```

GraphDB doesn't auto-create repositories from a client connection the way Virtuoso auto-creates graphs — before running anything, open the GraphDB Workbench at `http://localhost:7200` and create a repository whose ID matches the config's `sparql_endpoint` (for `french_royalty.source.json`: `repositories/FrenchRoyalty` → repository ID `FrenchRoyalty`).

If you'd rather use Virtuoso instead, `docker compose --profile virtuoso up` brings up Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080); point a config's `data.database_url`/`sparql_endpoint` at it and set `db_config.auth_type` to `"DIGEST"` (GraphDB uses `"BASIC"`).

## 3. Prepare the data

The pipeline does not handle literals yet, so the source graph is first cleaned with `cli/prepare_data.py`, which works on local files only (no graph database needed):

```bash
python -m skgg.cli.prepare_data .data/source/french_royalty.tsv -f french_royalty.source.json
```

This writes `.data/source/french_royalty.no-literals.tsv` and `.data/source/french_royalty.no-literals.nt`: the same triples without duplicates, without triples whose object is never typed, and without `name` triples (the `--literal-predicates` default). The config passed with `-f` supplies the term mapping needed to write the `.nt` copy. `-o` sets a different output path.

## 4. Upload the source graph

`cli/upload.py` is a standalone script (the upload step itself is importable as `upload_graph`):

```bash
python -m skgg.cli.upload -f french_royalty.source.json
```

This uploads `.data/source/french_royalty.no-literals.tsv` (`graph.triple_file` in the config, resolved under `data.input_dir`) into `base_uri`, the graph that metrics get extracted from in step 5. The script only uploads; it runs no rule-based completion. `-f`/`--config-file` resolves a bare filename under `configurations/` (same as step 5 below), `--triple-file` and `--graph-uri` override the input file and the target graph, and `--log-level` overrides the config's `logging.level` for the run.

`graph.triple_file` accepts a `.tsv` file of bare `subject<TAB>predicate<TAB>object` terms — `french_royalty.source.json` uses this format; terms are resolved to full URIs via the term mapping before insertion, the same way rule bodies are. An `.nt` file works too.

## 5. Run the experiment

```bash
python -m skgg.cli.main -f french_royalty.source.json
```

or, as a library call:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.source.json"))
```

This computes `GraphMetrics` from `base_uri`, generates the EDB, then grows the IDB into `synthetic_uri` — the finished synthetic graph. Progress is logged to the console (level set by each config's `logging.level`) and a copy is written under `logs/` (gitignored).

The CLI also accepts:
- `--skip-edb` — skip EDB generation and reuse whatever triples already sit at `graph.edb_uri` (e.g. from a previous run).
- `--log-level DEBUG` (or `INFO`/`WARNING`/...) — override the config's `logging.level` for this run only, without editing the JSON file.
- `--pca-threshold 0.9` — override the config's `rules.pca_threshold` for this run only; rules with PCA confidence below it are excluded from every pipeline step.

```bash
python -m skgg.cli.main -f french_royalty.source.json --skip-edb --log-level DEBUG
```

## Where things live

| What | Where |
|---|---|
| Source data (`.nt`/`.tsv`/`.ttl`/rules `.csv`) | `.data/<variant>/` (e.g. `.data/source/`), referenced by `data.input_dir` in the matching config |
| Experiment configs | `configurations/*.json` |
| Named graphs (base/EDB/synthetic) | in the running Virtuoso/GraphDB instance, keyed by the URIs in each config's `graph` section — nothing is written to disk by `cli/main.py` |
| Run logs | `logs/` (gitignored) |

## Troubleshooting

- Full architecture and known rough edges: [`architecture.md`](architecture.md), [`concepts.md`](concepts.md), [`../BACKLOG.md`](../BACKLOG.md).
