# Getting started

A walkthrough of setting up the project and running one experiment end-to-end, using the French Royalty dataset (`configurations/french_royalty.json`).

## 1. Install

```bash
pip install -r requirements.txt
pip install -e .          # installs `skgg` from src/ in editable mode
```

Requires Python >= 3.10.

## 2. Start a graph database

`french_royalty.json` points at GraphDB (`database_url: http://localhost:7200/`), so:

```bash
docker compose --profile graphdb up
```

GraphDB doesn't auto-create repositories from a client connection the way Virtuoso auto-creates graphs — before running anything, open the GraphDB Workbench at `http://localhost:7200` and create a repository whose ID matches the config's `sparql_endpoint` (for `french_royalty.json`: `repositories/FrenchRoyalty` → repository ID `FrenchRoyalty`).

If you'd rather use Virtuoso instead, `docker compose --profile virtuoso up` brings up Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080); point a config's `data.database_url`/`sparql_endpoint` at it and set `db_config.auth_type` to `"DIGEST"` (GraphDB uses `"BASIC"`).

## 3. Prepare the data

The pipeline does not handle literals yet, so the project keeps only the cleaned graph of each dataset, and that file is always the dataset's triple file. French Royalty's, `data/french_royalty/french_royalty.tsv`, is already cleaned, so you can skip to step 4. The original graph, with literals, is kept outside the repository. To rebuild the triple file from it, or to add a dataset, clean the original with `cli/clean.py`, which works on local files only (no graph database needed):

```bash
python -m skgg.cli.clean path/to/original.tsv -o data/french_royalty/french_royalty.tsv
```

This writes the same triples without duplicates, without triples whose object is never typed, and without `name` triples (the `--literal-predicates` default). Without `-o`, the output goes next to the input as `<stem>.no-literals.tsv`.

If a dataset only needs the other format, without any cleaning, `cli/convert.py` converts a `.nt` file to `.tsv` or back; see [Convert between .nt and .tsv](../README.md#convert-between-nt-and-tsv) in the README.

## 4. Upload the source graph

The `upload` subcommand of `cli/graph.py` loads a triples file into the database (the upload step itself is importable as `skgg.cli.graph.upload_graph`):

```bash
python -m skgg.cli.graph upload -f french_royalty.json
```

This uploads `data/french_royalty/french_royalty.tsv` (`graph.triple_file` in the config, resolved under `data.input_dir`) into the base graph, `http://FrenchRoyalty.org/base` (the config's `graph.namespace` plus `base`), the graph that metrics get extracted from in step 5. The subcommand only uploads; it runs no rule-based completion. `-f`/`--config-file` resolves a bare filename under `configurations/`, with or without the `.json` extension (same as step 5 below), `--triple-file` and `--graph-uri` override the input file and the target graph, and `--log-level` overrides the config's `logging.level` for the run.

`graph.triple_file` accepts a `.tsv` file of bare `subject<TAB>predicate<TAB>object` terms — `french_royalty.json` uses this format; terms are resolved to full URIs via the term mapping before insertion (`core/triples.tsv_term_to_nt`, as in `cli/convert.py`). An `.nt` file works too.

## 5. Run the experiment

```bash
python -m skgg.cli.main -f french_royalty.json
```

or, as a library call:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.json"))
```

This computes `GraphMetrics` from the base graph, generates the EDB into `http://FrenchRoyalty.org/skgg/std=1/edb`, then grows the IDB into `http://FrenchRoyalty.org/skgg/std=1`, the finished synthetic graph. The last segment names the run's rule filter: `std=1` keeps the rules with std confidence 1, and `--pca-conf 0.9` below writes `.../skgg/pca=0.9/edb` and `.../skgg/pca=0.9` instead, so runs at different thresholds keep separate graphs ([Graph layout](architecture.md#graph-layout) lists them all). Progress is logged to the console (level set by each config's `logging.level`) and a copy is written under `logs/` (gitignored).

The CLI also accepts:
- `--skip-edb` — skip EDB generation and reuse whatever triples already sit in the EDB graph of the run's rule filter (e.g. from a previous run). The run fails if that graph is empty.
- `--log-level DEBUG` (or `INFO`/`WARNING`/...) — override the config's `logging.level` for this run only, without editing the JSON file.
- `--pca-conf 0.9` — keep only the rules with PCA confidence >= 0.9 for this run, overriding the config's `rules.pca_threshold`. Without either, the rules with std confidence 1 are kept. Excluded rules are left out of every pipeline step. The threshold also names the run's graphs (`.../skgg/pca=0.9`).

```bash
python -m skgg.cli.main -f french_royalty.json --skip-edb --log-level DEBUG
```

## Where things live

| What | Where |
|---|---|
| Dataset files: the cleaned graph (`<dataset>.tsv`), its rules (`<dataset>.csv`) and SHACL shapes (`<dataset>.shapes.ttl`) | `data/<dataset>/` (e.g. `data/french_royalty/`), referenced by `data.input_dir` in the matching config |
| Downloaded synthetic graphs (`<dataset>.skgg.<filter>.tsv`, `.filled.tsv`) | `data/<dataset>/skgg/` (e.g. `data/french_royalty/skgg/`), written with `cli/graph.py download -o` |
| Experiment configs | `configurations/*.json` |
| Named graphs (base/EDB/synthetic/filled) | in the running Virtuoso/GraphDB instance, under URIs derived from the config's `graph.namespace` and the run's rule filter (see [Graph layout](architecture.md#graph-layout)). `cli/main.py` writes no graph to disk |
| Run logs | `logs/` (gitignored) |

## Troubleshooting

- Full architecture and known rough edges: [`architecture.md`](architecture.md), [`concepts.md`](concepts.md), [`../BACKLOG.md`](../BACKLOG.md).
