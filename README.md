# SKGG: Synthetic Knowledge Graph Generation

A tool for generating a **synthetic Knowledge Graph (KG)** from the topological metrics of a source graph (node/relation counts, domain/range frequencies per relation) and a set of Horn Rules.

## Requirements

Graphs live in a graph database (Virtuoso or GraphDB) and are manipulated via SPARQL through `SPARQLWrapper`. We recommend using Docker containers to manage the graph databases.

- Python >= 3.10
- Docker (for the graph database)

## Setup

```bash
pip install -r requirements.txt
pip install -e .          # installs the `skgg` package from src/ in editable mode
```

## Graph database

`docker-compose.yml` defines two alternative stacks, selected via Compose profiles:

```bash
docker compose --profile graphdb up    # GraphDB 10.7 (7200)
docker compose --profile virtuoso up   # Virtuoso (8890) + YASGUI SPARQL UI (8080)
docker compose --profile all up        # both
```

GraphDB keeps its home in `/mnt/Pandora/graphdb-home` by default. To use another folder on your machine, put `GRAPHDB_HOME=/path/to/graphdb-home` in a `.env` file next to `docker-compose.yml`. Git ignores that file.

## Running an experiment

Experiments are driven by JSON config files in `configurations/` (e.g. `french_royalty.json`).

### Prepare the data

This method does not support literals yet, so the project keeps only the cleaned graph of each dataset, and that file is always the dataset's triple file. The original graph, with literals, stays outside the repository. `configurations/french_royalty.json` reads the benchmark copy of French Royalty in `exp_datasets/french/` (`french.unified.tsv` and its rules, `french.csv`). `skgg.cli.clean` writes the cleaned copy as a `.tsv` file, without literals or duplicate triples. For more details, see the preparation step in [`docs/architecture.md`](docs/architecture.md#data-flow).

```bash
python -m skgg.cli.clean path/to/original.tsv -o path/to/dataset.tsv                   # the original stays outside the project
python -m skgg.cli.clean path/to/graph.nt -o path/to/out.tsv --log-level DEBUG          # DEBUG lists every untyped subject
```

Without `-o`, the output goes next to the input as `<stem>.no-literals.tsv`. The IRIs of an `.nt` input are cut to their last segment, and the script fails if two IRIs collide. Use `skgg.cli.convert` if you also need an `.nt` copy.

### Convert between .nt and .tsv

`skgg.cli.convert` converts a triples file from `.nt` to `.tsv` or from `.tsv` to `.nt`, depending on the input's suffix. Without `-o`, the output goes next to the input with the other suffix. It removes only duplicate triples and lines that are not a triple, and logs a warning for each skipped line.

```bash
python -m skgg.cli.convert path/to/graph.tsv --namespace http://example.org/  # -> path/to/graph.nt
python -m skgg.cli.convert path/to/graph.tsv -f french_royalty.json           # namespaces from the config's graph section
python -m skgg.cli.convert path/to/graph.nt -o path/to/out.tsv                # every IRI cut to its last segment
```

A `.tsv` input needs a term mapping, from `--namespace` or from a config passed with `-f`. Each row must hold three tab-separated values, which become N-Triples terms as follows:

- `type` in the predicate column becomes `rdf:type`.
- A value starting with `http://` or `https://` is kept as a full IRI, and one starting with `_:` as a blank node.
- Any other value is appended to its namespace. Spaces, `%`, `/`, `#` and the other characters N-Triples forbids in IRIs are percent-encoded.

For example, the row `Louis_XIV<TAB>spouse<TAB>Maria_Theresa_of_Spain` with `--namespace http://FrenchRoyalty.org/` becomes `<http://FrenchRoyalty.org/Louis_XIV> <http://FrenchRoyalty.org/spouse> <http://FrenchRoyalty.org/Maria_Theresa_of_Spain> .`. Objects always become IRIs, so a `.tsv` file cannot hold literals.

For an `.nt` input the term mapping is optional. With one, an IRI is shortened to a bare value only when that value maps back to the same IRI, and other IRIs are kept in full. Without one, every IRI is cut to its last segment, and the script fails if two IRIs share a last segment. Percent-encoded characters are decoded, and a literal becomes its text without quotes, language tag or datatype.

### Upload the source graph

`skgg.cli.graph upload` inserts a `.nt`/`.tsv` triple file into the graph database, in the base graph (the config's `graph.namespace` without its trailing `/`, plus `/base`, e.g. `http://FrenchRoyalty.org/base`), sending `db_config.chunk_size` triples per request. Terms in a `.tsv` file are resolved to full URIs through the config's `graph.namespace` and `graph.term_namespaces`, the same way `skgg.cli.convert` resolves them.

```bash
python -m skgg.cli.graph upload -f french_royalty.json   # uploads graph.triple_file, exp_datasets/french/french.unified.tsv
python -m skgg.cli.graph upload -f french_royalty.json --triple-file french.unified.skgg.std=1.tsv --graph-uri http://FrenchRoyalty.org/skgg/std=1   # a file under data.input_dir, e.g. one written by download
```

`graph.triple_file` is the dataset's cleaned graph, and `--triple-file` uploads another file instead. A bare filename resolves under `data.input_dir`, and a path containing `/` is used as given. `--graph-uri` uploads to a different named graph, e.g. to re-insert a downloaded synthetic graph into `http://FrenchRoyalty.org/skgg/pca=0.9`. `--namespace` puts the `.tsv` file's bare terms under another namespace than `graph.namespace`, and the default graph URI is still derived from `graph.namespace`. `--log-level` overrides the config's `logging.level` for the run.

### Download a graph

`skgg.cli.graph download` writes a named graph from the database to `<output>.nt` and `<output>.tsv`, using the connection in the config passed with `-f`. It downloads the base graph unless `--graph-uri` names another graph. `-o` sets the output path without suffix. It defaults to `data.input_dir` plus the stem of `graph.triple_file` and the graph URI's path under the namespace, with dots for slashes: with `french_royalty.json`, `http://FrenchRoyalty.org/skgg/pca=0.9/filled` goes to `exp_datasets/french/french.unified.skgg.pca=0.9.filled`.

```bash
python -m skgg.cli.graph download -f french_royalty.json --graph-uri http://FrenchRoyalty.org/skgg/std=1   # -> exp_datasets/french/french.unified.skgg.std=1.{nt,tsv}
python -m skgg.cli.graph download -f french_royalty.json --graph-uri http://FrenchRoyalty.org/skgg/std=1 -o exp_datasets/french/skgg/french.skgg.std=1   # into a skgg/ folder, created if missing
python -m skgg.cli.graph download -f french_royalty.json -o path/to/french_royalty                         # base graph -> path/to/french_royalty.{nt,tsv}
```

On GraphDB the whole graph comes from one request to the repository's `/statements` endpoint, with inferred triples excluded. Virtuoso's Graph Store endpoint returns at most `ResultSetMaxRows` triples, so on Virtuoso the script sends sorted CONSTRUCT queries of `db_config.chunk_size` triples each. Either way, the script fails if it wrote a different number of triples than the graph holds. The `.tsv` is converted from the `.nt` as `skgg.cli.convert` does with the config's term mapping, so it can be uploaded again with the same config. An IRI outside the term mapping's namespaces stays in full in the `.tsv`, so for a graph whose IRIs are not under `graph.namespace`, pass their namespace with `--namespace`, and pass it again when uploading the `.tsv`.

### Complete a graph with its rules

`skgg.cli.complete` applies a rule set to a real graph until a pass adds nothing, and writes the result to `--complete-uri` (default the base graph), replacing that graph's contents. If the source is `--complete-uri` itself, the graph is completed in place and only the derived triples are added. The source is a graph URI or a `.nt`/`.tsv` file, which is loaded directly into `--complete-uri`. Only the rules with std confidence 1 are applied, unless `--pca-conf X` is given, which applies the rules with PCA confidence >= X instead. The base graph is only completed with std confidence 1 rules, so `--pca-conf` is refused when `--complete-uri` is the config's base graph. A config passed with `-f` supplies the source (the base graph), rules file, namespace and database connection, and flags override any of them. Without `-f`, pass `--source`, `--complete-uri`, `--rules-file`, `--namespace` and the connection flags (`--database-url`, `--sparql-endpoint`, `--auth-type`, `--user`, `--password`).

```bash
python -m skgg.cli.complete -f french_royalty.json                                                                                          # completes the base graph in place
python -m skgg.cli.complete -f french_royalty.json --complete-uri http://FrenchRoyalty.org/complete                                          # base graph -> complete
python -m skgg.cli.complete -f french_royalty.json --source exp_datasets/french/french.unified.tsv --complete-uri http://FrenchRoyalty.org/complete  # a file instead of the base graph
python -m skgg.cli.complete --source http://FrenchRoyalty.org/base --complete-uri http://FrenchRoyalty.org/complete --rules-file exp_datasets/french/french.csv --pca-conf 0.9 --namespace http://FrenchRoyalty.org/ --database-url http://localhost:7200/ --sparql-endpoint repositories/FrenchRoyalty --auth-type BASIC --user admin --password rootpassword
```

### Validate a graph against SHACL shapes

`skgg.cli.validate` checks a graph against the `sh:sparql` constraints of a SHACL shapes file and writes every triple that violates one to a `.violations.tsv` report, with the triple's line in the `.tsv` file, its terms, the shape and the shape's `sh:message`. It checks the config's base graph unless `--source` names another graph URI or a `.tsv` file. A `.tsv` file is loaded into a temporary named graph (`--graph-uri`, default `http://FrenchRoyalty.org/validation` for `http://FrenchRoyalty.org/`), which is cleared after the check unless `--keep-graph` is given. The base graph and the pipeline's graphs are refused as `--graph-uri`. The shapes file defaults to `data.input_dir / <stem of graph.triple_file>.shapes.ttl`, and its `ex:` prefix must equal the config's `graph.namespace`. `french_royalty.json` reads `exp_datasets/french/`, which has no shapes file, and the repository has none, so the examples below pass a shapes file of your own with `--shapes`. The source graph and file are not modified, and the pipeline does not use the shapes yet.

```bash
python -m skgg.cli.validate -f french_royalty.json --shapes path/to/french.shapes.ttl                                                       # base graph -> exp_datasets/french/french.unified.base.violations.tsv
python -m skgg.cli.validate -f french_royalty.json --shapes path/to/french.shapes.ttl --source exp_datasets/french/french.unified.skgg.std=1.tsv  # -> exp_datasets/french/french.unified.skgg.std=1.violations.tsv
```

### Mine rules

`mine_rules/run_amie.py` mines Horn rules from a triples file with AMIE3 and writes them to a CSV that `rules.rules_file` can point to. The AMIE3 jar is not in git: download `amie3.5.1.jar` from https://github.com/dig-team/amie/releases into `mine_rules/`, or pass its path with `--jar`. Without `-o`, the CSV goes next to the input as `<stem>.csv`.

```bash
python mine_rules/run_amie.py path/to/dataset.tsv   # -> path/to/dataset.csv
```

The CSV lists the rules by std confidence, then PCA confidence, both descending (ties keep AMIE's output order), and its `rule_id` column numbers them 1..N in that order. The pipeline names rules by this ID in its logs, run summary and relation-graph PNG, so a rule keeps its ID whatever confidence threshold a run filters the rules on. `--mins`, `--minis`, `--minhc`, `--minc`, `--minpca` and `--maxad` set AMIE's thresholds, and `--help` lists the other options.

`mine_rules/mine_datasets.py` runs `run_amie.py` on the `<folder>.unified.tsv` of every folder under `exp_datasets/` and writes `<folder>/<folder>.csv`. AMIE runs with its default thresholds, and any option the script does not define is passed to `run_amie.py`. A dataset that already has its CSV is skipped unless `--force` is given. AMIE's output for each dataset goes to `logs/amie/<folder>.log`. The rules of the 24 datasets are already in `exp_datasets/`.

```bash
python mine_rules/mine_datasets.py                                      # every dataset under exp_datasets/
python mine_rules/mine_datasets.py --datasets french family --force
python mine_rules/mine_datasets.py --minhc 0.05 --xmx 24G --timeout 7200
```

### Run the pipeline

```bash
python -m skgg.cli.main -f french_royalty.json
python -m skgg.cli.main -f french_royalty.json --skip-edb --log-level DEBUG
```

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.json"))
```

This loads the config, computes graph metrics over SPARQL, parses the ontology and Horn rule set, generates the EDB, then grows the rule-derived facts until a fixed point, producing the synthetic graph.

The run's graphs are named after its rule filter, so runs at different thresholds keep separate graphs. With no PCA threshold (the rules with std confidence 1) a run on `french_royalty.json` writes the EDB to `http://FrenchRoyalty.org/skgg/std=1/edb` and the synthetic graph to `http://FrenchRoyalty.org/skgg/std=1`. With `--pca-conf 0.9` it writes `.../skgg/pca=0.9/edb` and `.../skgg/pca=0.9`, and `--fill` adds `.../skgg/pca=0.9/filled`. The base graph, `http://FrenchRoyalty.org/base`, is shared by every run.

`-f`/`--config-file` resolves a bare filename under `configurations/`, with or without the `.json` extension (`-f french_royalty` works too, in every `cli/` script); `--skip-edb` reuses the EDB graph of the run's rule filter instead of regenerating it; `--log-level` overrides the config's `logging.level` for that run; `--synthetic-uri URI` writes the synthetic graph to `URI` and the EDB to `URI/edb` instead, leaving earlier runs' graphs alone; `--base-uri URI` reads the source graph from `URI` instead of the base graph; `--namespace NS` puts the rules' bare terms under `NS` instead of `graph.namespace`, without changing the graph URIs; `--skip-reflexivity` generates as before the self-loop fix, with self-loops left to chance instead of the source's count. For a full walkthrough, see [`docs/getting-started.md`](docs/getting-started.md).

### Generate every benchmark dataset

`cli/generate_datasets.py` runs the pipeline on every folder under `exp_datasets/` that has a `<folder>.unified.tsv` and a `<folder>.csv`, and saves the synthetic graph to `<folder>_ours/<folder>_ours.tsv`. It needs a running GraphDB, where it creates one temporary repository per dataset and deletes it afterwards. It uses the default rule filter (std confidence 1) and no fill step. A dataset that already has its `.tsv` is skipped unless `--force` is given, so an interrupted batch can be resumed. Each dataset's output goes to `logs/ours/<folder>.log`, at INFO level by default; `--log-level WARNING` leaves out the pipeline's phases and run summary.

```bash
python -m skgg.cli.generate_datasets                                    # every dataset under exp_datasets/ that has no copy yet
python -m skgg.cli.generate_datasets --datasets french WN18RR --force
python scripts/check_outputs.py                                         # compare each copy with its source
```

`scripts/check_outputs.py` compares each copy with its source: triple counts, new entities, and, per relation, the frequency, the degrees and the number of self-loops. [`docs/generate-benchmarks.md`](docs/generate-benchmarks.md) lists the steps to run the batch on another machine, from the clone to bringing the copies back. The copies of every dataset are tracked in git, with those of kbgen (`<folder>_kbgen/`) and PyGraft (`<folder>_pygraft/`), and [`docs/generation-times.md`](docs/generation-times.md) compares how long each method took.

## Project layout

```
src/skgg/          # package source (see docs/architecture.md for the full module map)
configurations/    # per-experiment JSON configs
docs/              # architecture, concepts glossary, getting-started guide
logs/              # per-run logs (gitignored)
```

## More

- [`docs/getting-started.md`](docs/getting-started.md): full setup + first experiment walkthrough.
- [`docs/generate-benchmarks.md`](docs/generate-benchmarks.md): generating the benchmark copies on another machine.
- [`docs/generation-times.md`](docs/generation-times.md): time to make each benchmark copy with this generator, kbgen and PyGraft.
- [`docs/architecture.md`](docs/architecture.md): components and data flow, with diagrams.
- [`docs/concepts.md`](docs/concepts.md): glossary of domain terms (EDB/IDB, Horn rules, closure, ...).
- [`AGENTS.md`](AGENTS.md): terse architecture reference for contributors and AI coding agents alike (`CLAUDE.md` imports this same file for Claude Code).
- [`BACKLOG.md`](BACKLOG.md): known issues and pending refactors; check before assuming a code path is exercised/working.
