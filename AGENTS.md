# AGENTS.md

This file provides guidance to AI coding assistants (Claude Code, Codex, Cursor, Gemini CLI, etc.) when working with code in this repository.

## Project Summary

This project is a tool for **Synthetic Knowledge Graph (KG) generation**. It creates a synthetic Knowledge Graph from topological metrics of a source graph (number of nodes, relations, frequencies of the elements in the domain and range of each relation, etc.) and a set of Horn Rules, without needing continued access to the original graph once metrics are extracted.

Graphs are never loaded into memory-intensive libraries like RDFlib for bulk work. They are always stored in a graph database (Virtuoso or GraphDB) and manipulated via SPARQL queries through `SPARQLWrapper`.

## Environment Setup

- Requires Python >= 3.10.
- Dependencies are pinned in `requirements.txt` (install with `pip install -r requirements.txt`), a `pip freeze --exclude-editable` of the environment the experiments run in (Python 3.10.22; PyTorch 2.14.1 from PyPI, built for CUDA 13.0); `pyproject.toml` only carries project metadata and tool config (no dependency list or build backend).
- Package code lives under `src/skgg/` (installed name: `skgg`) — install it in editable mode (`pip install -e .`) or run scripts with `src` on `PYTHONPATH` so `from skgg...` imports resolve.

## Graph Database

`docker-compose.yml` defines two alternative graph-database stacks, selected via Compose profiles — bring one up at a time:

- `docker compose --profile virtuoso up` — Virtuoso (port 8890) + a YASGUI SPARQL UI (port 8080).
- `docker compose --profile graphdb up` — GraphDB 10.7 (port 7200).
- `--profile all` starts everything.

`DatabaseAuthConfig.auth_type` must match the store in use: `DIGEST` for Virtuoso, `BASIC` for GraphDB (or `NONE`). Each experiment config's `data.database_url` / `sparql_endpoint` selects which running store and repository/endpoint to talk to (see Configuration below).

A dataset keeps all its named graphs in one repository, under a root taken from the config's `graph.namespace` without its trailing `/` or `#` (e.g. `http://FrenchRoyalty.org`). Configs don't list graph URIs: `GraphConfig` (`core/config.py`) derives them, and names a pipeline run's graphs after its rule filter (`core/rules.rule_filter_label`: `pca=0.9`, `pca=1`, or `std=1` when no PCA threshold is set), so runs at different thresholds keep separate graphs:

| Graph | URI |
|---|---|
| base graph, the uploaded source | `<root>/base` |
| EDB of a run | `<root>/skgg/<filter>/edb` |
| synthetic graph, EDB + completion | `<root>/skgg/<filter>` |
| filled graph (`--fill`) | `<root>/skgg/<filter>/filled` |

Configs with the same namespace share these graphs, so uploading another variant's triple file replaces the base graph. "Graph layout" in `docs/architecture.md` explains why each graph is stored in full instead of as a delta of another one.

## Running an experiment

Experiments are driven by JSON config files in `configurations/` (e.g. `french_royalty.json`), loaded via `RunConfig.from_json(...)`.

The main entry point is `run_synthetic_graph_experiment` in `src/skgg/cli/main.py`, runnable either as a library call or from the CLI:

```python
from pathlib import Path
from skgg.cli.main import run_synthetic_graph_experiment

run_synthetic_graph_experiment(Path("configurations/french_royalty.json"))
```

```bash
python -m skgg.cli.main -f french_royalty.json
python -m skgg.cli.main -f french_royalty.json --skip-edb --log-level DEBUG
python -m skgg.cli.main -f french_royalty.json --pca-conf 0.95
python -m skgg.cli.main -f french_royalty.json --pca-conf 0.9 --fill   # also fills open relations into <root>/skgg/pca=0.9/filled
python -m skgg.cli.main -f french_royalty.json --synthetic-uri http://FrenchRoyalty.org/skgg/test   # EDB at .../skgg/test/edb, earlier runs' graphs untouched
python -m skgg.cli.main -f french_royalty.json --base-uri http://FrenchRoyalty.org/complete   # metrics, fill and summary read this graph instead of <root>/base
python -m skgg.cli.main -f french_royalty_complete_test --base-uri http://FrenchRoyalty.org/complete-test/pca=0.9 --namespace http://FrenchRoyalty.org/   # rules' bare terms under http://FrenchRoyalty.org/, graphs still under .../complete-test/
```

`-f`/`--config-file` (required) accepts a bare filename resolved under `configurations/` (or a path, used as-is, if it contains a `/`), with or without the `.json` extension (`-f french_royalty` works too, in every `cli/` script); `--skip-edb` skips EDB generation and reuses whatever triples already sit at the EDB graph of the run's rule filter, and fails if that graph is empty; `--log-level` overrides the config's `logging.level` for that run only, without editing the JSON file; `--pca-conf` overrides the config's optional `rules.pca_threshold` for that run only, which also picks the graphs the run writes (`<root>/skgg/pca=0.95` for `--pca-conf 0.95`); `--fill` runs the optional fill step (phase 4), which copies the synthetic graph to the filled graph and fills the relations still open there, leaving the synthetic graph as EDB + completion; `--synthetic-uri URI` writes the synthetic graph to `URI` instead of `<root>/skgg/<filter>`, the EDB to `URI/edb` and the filled graph to `URI/filled`, so a test run leaves the graphs of earlier runs alone. `--skip-edb` then reuses `URI/edb`, and the run fails before writing anything if one of these graphs would be the base graph. `--base-uri URI` reads the source graph from `URI` instead of `<root>/base`: the metrics, the fill step and the summary read it, and the run's own graphs keep their URIs. `--namespace NS` puts the rules' bare terms under `NS` instead of `graph.namespace`, for a base graph whose IRIs are under another namespace; the graph URIs are still derived from `graph.namespace`.

Typical experiment flow (see `cli/main.py`):
1. Load `RunConfig` from JSON and set up logging.
2. Compute `GraphMetrics` (predicate profiles) from the base graph (`<root>/base`, or `--base-uri`) over SPARQL.
3. Build the term mapping (`graph.term_namespaces` overrides merged over `core/utils.DEFAULT_PREFIXES`, under the `graph.namespace` default) and parse the Horn rule set from a CSV (`rules.rules_file`), keeping only rules classified POSITIVE: std confidence >= `core/rules.DEFAULT_STD_THRESHOLD` (1) by default, or PCA confidence >= `rules.pca_threshold` when the config or `--pca-conf` sets one. NEGATIVE and UNKNOWN (missing the confidence filtered on) rules are dropped and excluded from every later step. The run then fails with a `ValueError` if a predicate of a kept rule has no profile in the base graph, e.g. when `graph.namespace` (or `--namespace`) differs from the namespace of the uploaded graph's IRIs. The relation graph (`core/cycles.get_relation_graph`) is then drawn to `logs/relation_graph_<graph.name>.png`, and `core/cycles.remove_minimal_cyclic_rules` removes the fewest rules that leave it with no cycle: it picks, per strongly connected component, the order of the predicates that keeps the most rules (then the most support) going from body to head, and removes the rest, recursive rules always included. A component with more than 20 predicates (`MAX_EXACT_COMPONENT_SIZE`) loses every rule with an edge inside it instead. See section 5.2 of `docs/algorithm.md` and "Cyclic rule removal" in `docs/architecture.md`. The head predicate of a removed rule becomes extensional unless a remaining rule still derives it, so no stale cycle is left for completion; see "Cyclic rule" in `docs/concepts.md`. The summary lists the removed rules with their support, original -> synthetic, after the other rules, and shows every rule's std and PCA confidence, original -> synthetic.
4. Generate the EDB (extensional database) — `engine/edb.py` — inserting triples that satisfy rule bodies/profiles into the run's EDB graph (`<root>/skgg/<filter>/edb`). Before that, `core/rules.write_used_rules` copies the rows of the rules left after the confidence filter and `remove_minimal_cyclic_rules` to `<rules_file stem>_used.csv` next to the rules file (e.g. `data/french_royalty/french_royalty_used.csv`), with the same columns, values and order, so it can be read back as a rules file. It is not written with `--skip-edb`, and `.gitignore` ignores `data/**/*_used.csv`.
5. As currently wired, `cli/main.py` logs five numbered phases (`[n/5]`): metrics/rules, EDB, completion, filling open relations (only with `--fill`; otherwise phase 4 logs that it is skipped), summary. The summary compares the base graph with the final graph: the filled graph with `--fill`, the synthetic graph without. Each rule row also gives the rule's std and PCA confidence in the base graph and in the final graph, with the delta, to show whether the synthetic graph preserves them. Both are computed as AMIE does: support divided by the body size (`core/queries.get_body_size`) and by the PCA body size (`get_pca_body_size`, which counts the body groundings whose `functional_variable`, read from the rules CSV, has some value for the head predicate). On the base graph these counts equal the CSV's `positive_examples`, `body_size` and `pca_body_size` for all 121 French Royalty rules, so the original confidences are the ones AMIE mined. A confidence (and its delta) is `n/a` when its denominator is 0, and PCA confidence also when the CSV has no `functional_variable` column. The completion phase forward-chains *every* rule over the EDB with `engine/completion.py`'s `complete_graph` (which returns the triples it added and takes a `label` for its log lines), repeating each pass until nothing more is added — no rule ordering and no profile budget applied during this loop (`apply_rule` has no profile cap, so a rule can in principle overshoot its head predicate's target frequency). Since the cycle removal leaves no cycle, completion can derive every intensional predicate from the EDB (see "Stale cycle" in `docs/concepts.md`). Rule/predicate closure (`support`/`frequency` targets reached) is tracked via `engine/generator.py`'s `get_closed_rules`/`get_closed_preds`, called after each `complete_graph` pass — see "Rule application order" in `docs/concepts.md` for why this differs from `engine/idb.py`'s original (now-removed) `generate_idb`. Phase 1 logs the rule filter and the URIs of the run's graphs, and ends by logging the extensional and intensional relations with their target frequencies. In phase 2, `engine/edb.py` logs the relations that warm-up (the direct matches run before the main loop) added triples to, as added/target and open or closed, then the extensional relations still to generate, as current/target. At the start of phase 3 and after the completion, `cli/main.py`'s `_ClosureProgress.log` logs how many predicates and rules are closed, which ones closed since the previous check, and each open extensional and intensional predicate's current frequency against its target (copied from the profiles before EDB generation spends `PredicateProfile.frequency` as a budget); the open rule IDs are logged at DEBUG. Phase 3 also lists the rules it applies, and `complete_graph` logs the triples each rule added in each pass and in total. With `--fill`, phase 4 copies the synthetic graph to the filled graph (`core/queries.copy_graph`, which replaces its contents), then `cli/main.py`'s `_fill_open_relations` works on the filled graph: it lists each open relation with the rules it is the head of and in the body of (`core/rules.get_impacted_rules`), and calls `engine/fill.py`'s `fill_open_predicates`. That logs each open rule's case (A: every body predicate closed, can't grow; B1: completes bodies for unexplained heads; B2: adds groundings with their head), runs a rule-driven pass that raises open rules' support toward their target, then a random pass for the budget left. A triple is kept only if the support of every closed rule stays the same, no open rule passes its target, and no body grounding is left without its head; an open rule that reaches its target is closed. Afterwards phase 4 logs each open rule's support before -> after, and a warning if a closed rule's support changed or an open rule passed its target, which should never happen. See section 4.1 of `docs/algorithm.md` and "Filling open relations" in `docs/concepts.md`; the step is unfinished ("Finish the fill step" in `BACKLOG.md`).

`cli/graph.py` moves a named graph between a local file and the graph database, with two subcommands. Both take the same `-f`/`--config-file` (required) and `--log-level` as `cli/main.py`, through `cli/common.py`'s `add_config_args`/`load_config`. Both also take `--namespace NS`, which puts the bare `.tsv` terms under `NS` instead of `graph.namespace`, for a graph whose IRIs are under another namespace; the default graph URI and output path are still derived from `graph.namespace`. `upload` (importable as `upload_graph(client, triple_file, graph_uri, term_mapping, chunk_size)`) overwrites a graph with a `.nt` or `.tsv` file (`graph.triple_file` in config), sending `db_config.chunk_size` triples per request. `.tsv` rows are bare `subject\tpredicate\tobject` terms, turned into IRIs by `core/triples.tsv_term_to_nt` through the term mapping, the same way `cli/convert.py` does it:

```bash
python -m skgg.cli.graph upload -f french_royalty.json
python -m skgg.cli.graph upload -f french_royalty.json --log-level DEBUG
python -m skgg.cli.graph upload -f french_royalty.json --triple-file data/french_royalty/skgg/french_royalty.skgg.pca=0.9.tsv --graph-uri http://FrenchRoyalty.org/skgg/pca=0.9
```

`upload` only uploads: the pipeline reads its metrics from the base graph as uploaded, with no rule-based completion beforehand. `--triple-file` overrides `graph.triple_file` (a bare filename resolves under `data.input_dir`; a path containing `/` is used as given) and `--graph-uri` overrides the target graph (default the base graph, `<root>/base`), e.g. to re-insert a downloaded synthetic graph into `<root>/skgg/<filter>` without regenerating it.

`download` (importable as `download_graph(client, graph_uri, output, term_mapping, page_size)`) is the reverse. It writes a named graph to `<output>.nt` and `<output>.tsv`, using the database connection and term mapping of the config passed with `-f`. `--graph-uri` picks the graph (default the base graph) and `-o`/`--output` the output path without suffix. The default output is `data.input_dir` / `core/utils.graph_file_stem`: the stem of `graph.triple_file`, then the graph URI's path under the root with dots for slashes (`french_royalty.skgg.pca=0.9.filled` for `http://FrenchRoyalty.org/skgg/pca=0.9/filled`), or the URI's last segment for a URI outside the root. The `.nt` comes from `core/queries.export_graph_nt`. On GraphDB that is one `GET` on the repository's `/statements` endpoint with `infer=false`. On Virtuoso it is a series of sorted CONSTRUCT queries of `db_config.chunk_size` triples each, because Virtuoso's Graph Store endpoint stops at `ResultSetMaxRows` (10,001 of Family's 31,414 triples in the default container). Terms are written separated by single spaces whatever the store used. The script fails, deleting the partial `.nt`, if its triple count differs from `get_triple_count`. The `.tsv` is converted from the `.nt` with `core/triples.convert` and the config's term mapping, so an IRI outside its namespaces stays in full: a graph whose IRIs are not under `graph.namespace` needs `--namespace`.

```bash
python -m skgg.cli.graph download -f french_royalty.json --graph-uri http://FrenchRoyalty.org/skgg/std=1   # -> data/french_royalty/french_royalty.skgg.std=1.{nt,tsv}
python -m skgg.cli.graph download -f french_royalty.json --graph-uri http://FrenchRoyalty.org/skgg/std=1 -o data/french_royalty/skgg/french_royalty.skgg.std=1   # into the skgg/ folder
python -m skgg.cli.graph download -f french_royalty.json -o path/to/french_royalty                         # base graph -> path/to/french_royalty.{nt,tsv}
python -m skgg.cli.graph download -f french_royalty_complete_test --graph-uri http://FrenchRoyalty.org/complete-test/pca=0.9 --namespace http://FrenchRoyalty.org/   # IRIs under http://FrenchRoyalty.org/ become bare terms in the .tsv
```

`cli/clean.py` is another standalone, local-only script (no SPARQL; importable as `clean(input_file, output, literal_predicates, drop_types)`). It writes a cleaned copy of a `.nt`/`.tsv` file as a `.tsv` file (`-o`, default `<stem>.no-literals.tsv` next to the input). Every `/` in a term's own name becomes `_` (e.g. `Matilda_of_Saxony_1172_1209/10` → `Matilda_of_Saxony_1172_1209_10`, which would otherwise shorten to `10`): for a bare `.tsv` term that is the whole term, and for an IRI only its last segment, where the `/` is written as `%2F`. The `/` between an IRI's path segments are left alone, and the script fails if two terms clean to the same name. Two things are removed: duplicate triples (the first occurrence is kept), and "literals", meaning every non-type triple whose object is never typed (never the subject of a `type`/`rdf:type` triple). Every triple whose predicate is in `--literal-predicates` (default `name`, whose objects are always literals, even when a person's name equals their entity ID and so looks typed) is dropped too. Type triples are kept unless `--drop-types` is given: that drops every type triple as a final step, after checking that every subject term is typed to the same single class, and otherwise fails without writing the output. It also logs a warning for every subject term that is never typed.

It needs no term mapping: for `.nt` input every IRI is cut to its last segment, and the script fails if two IRIs collide. Use `cli/convert.py` for an `.nt` copy.

The project keeps only the cleaned graph of a dataset, as its triple file: `data/french_royalty/french_royalty.tsv` is the cleaned French Royalty graph, and the original graph, with literals, is kept outside the repository. To rebuild a triple file, clean the original into it with `-o`:

```bash
python -m skgg.cli.clean path/to/original.tsv -o data/french_royalty/french_royalty.tsv  # the original stays outside the project
python -m skgg.cli.clean path/to/graph.nt -o path/to/out.tsv --log-level DEBUG          # DEBUG lists every untyped subject
python -m skgg.cli.clean path/to/original.tsv --drop-types                              # -> path/to/original.no-literals.tsv, no type triples; fails unless every entity is a Person
```

`cli/convert.py` is a third local-only script. The conversion itself is `core/triples.convert(input_file, output_file, term_mapping)`, which `cli/convert.py` re-exports. It converts a triples file from `.nt` to `.tsv` or from `.tsv` to `.nt`, picking the direction from the input's suffix, and writes it to `-o` or else to the input path with the other suffix. It only drops duplicate triples and lines that are not a triple (each logged as a warning), so literals are kept: in the `.tsv` a literal becomes its text, without quotes, language tag or datatype, with tabs and line breaks turned into spaces. The term mapping comes from `-f` (only the config's `graph` section is read, for `namespace`/`term_namespaces`) or `--namespace`, and `.tsv` input requires it. For `.nt` input it is optional: with a mapping, an IRI is shortened only when that bare term maps back to the same IRI, and other IRIs are written in full; without one, every IRI is cut to its last segment and the script fails if two IRIs collide. When a bare `.tsv` term becomes an IRI, the characters N-Triples forbids in IRIs plus `%`, `/` and `#` are percent-encoded, and converting to `.tsv` decodes them. `.tsv` terms starting with `http://` or `https://` are kept as full IRIs and those starting with `_:` as blank nodes, and `type` maps to `rdf:type` through `core/utils.DEFAULT_PREFIXES`. `core/utils.load_term_mapping` builds the mapping from `-f`/`--namespace`.

```bash
python -m skgg.cli.convert path/to/graph.tsv --namespace http://example.org/  # -> path/to/graph.nt
python -m skgg.cli.convert path/to/graph.nt -o path/to/out.tsv                # every IRI cut to its last segment
```

`scripts/format_change.py` is a standalone version of `cli/convert.py` that imports nothing from `skgg` and only needs the Python standard library, so it can be copied and run outside the project. It reads no config file: `--namespace` is required in both directions, every bare term goes under it except `type` (`rdf:type`), and an `.nt` IRI is shortened only when that bare term maps back to it. With the same namespace, it writes the same output as `cli/convert.py`. If the conversion in `core/triples.py` changes, change this copy too.

```bash
python scripts/format_change.py path/to/graph.tsv --namespace http://example.org/  # -> path/to/graph.nt
```

`scripts/merge_splits.py` is another standard-library script. For each folder directly under its root argument (default `exp_datasets`) that has `train`, `valid` and `test` files, it writes `<folder>/<folder>.unified.tsv` with their triples in that order, keeping the first occurrence of a repeated triple and normalizing line endings to LF. A first line reading `0 1 2` is a saved column header (the first line of every split of `Family1_N`, `FamilyM_N`, `FamilyN_1`, `french1_N`, `frenchN_1` and `frenchN_M`) and is skipped. A term wrapped in a placeholder namespace, such as `<http:WN18RR/_hypernym>` in `WN18RRN_M`, is written bare (`_hypernym`), and so is an IRI under `http://YAGO3.org/`, the namespace of `YAGO3-10_N_M`. Only that prefix is removed, so `http://YAGO3.org/de/SC_Leipzig` becomes `de/SC_Leipzig`. A `subject predicate object` row is a header left in the data (line 119225 of `YAGO3-10_1_1/train`) and is skipped on any line. If the folder has an `id_to_name.json` file, its `entity_ids` and `relation_ids` replace the integer IDs of the splits with names. Only `YAGO3-10` has one: SimE's splits hold integers, and the file was rebuilt by aligning them row by row with the named YAGO3-10 of [ConvE](https://github.com/TimDettmers/ConvE), the source named in the folder's `license.txt`. The `type` triples of a folder whose name starts with `french` are left out of its unified file, and stay in its splits. It leaves every other file alone. It also checks that train and test share no triple: a dataset that fails the check still gets its file, the shared triples are logged as errors, and the script exits with status 1 after the last dataset.

```bash
python scripts/merge_splits.py                # every dataset under exp_datasets/
python scripts/merge_splits.py path/to/root
```

`mine_rules/mine_datasets.py` mines the rules of every benchmark dataset. For each folder directly under `--root` (default `exp_datasets`) with a `<folder>.unified.tsv`, it calls `mine_rules/run_amie.py` and writes `<folder>/<folder>.csv`, in the format `parse_rule_set` reads, with AMIE's full output in `--log-dir` (default `logs/amie`). AMIE runs with its default thresholds (pruning on head coverage >= 0.01, std and PCA confidence >= 0.1, and only relations with at least 100 facts as rule heads), which reproduce the 121 rules of `data/french_royalty/french_royalty.csv`. Options the script does not define go to `run_amie.py`. `-mins` is ignored while AMIE prunes on head coverage, so a minimum support needs `--amie-arg=-pm --amie-arg=support` as well. A dataset that already has its CSV is skipped unless `--force` is given, and `--datasets` limits the run to the named folders. A failed dataset does not stop the others, and the script exits with status 1 after the last one. Each AMIE process uses every core by default, so the datasets run one at a time; pass `--xmx` for a larger heap than the JVM default. `WN18RRN_M` has the same triples as `WN18RR` (only their splits differ), so `exp_datasets/WN18RRN_M/WN18RRN_M.csv` is a copy of `exp_datasets/WN18RR/WN18RR.csv` and has no AMIE log.

```bash
python mine_rules/mine_datasets.py                                      # every dataset under exp_datasets/
python mine_rules/mine_datasets.py --datasets french family --force
python mine_rules/mine_datasets.py --minhc 0.05 --xmx 24G --timeout 7200
```

`scripts/train_kge.py` trains the seven models of the KGC-Benchmark project (TransE, TransH, TransD, RotatE, ComplEx, TuckER, CompGCN) on every folder under `exp_datasets/`, smallest `train` file first, with the settings of that project's `input.json`: PyKEEN's sLCWA loop, 100 epochs, batch size 32, embedding dimension 50, inverse triples, filtered negative sampling and seed 1235. Its packages (PyKEEN, Optuna, PyTorch, seaborn) are pinned in `requirements.txt` with the rest. Models train on `train` and are evaluated on `test`, and `valid` only adds known answers to the ranking filter. `YAGO3-10` is the one folder with an `id_to_name.json`, so its integer IDs are replaced by names when the splits are loaded, and the split files stay as they are. Six pairs of IDs in that file share a name, so `YAGO3-10` trains on 123,143 entities, not 123,149. The names in the other four YAGO3-10 folders are not changed (see `BACKLOG.md`).

The output has the layout of KGC-Benchmark's `KGC.py`, so its `report_results.py --root exp_results` reads the finished models at any moment. Because every dataset is one level under the root, that script treats `exp_results` as one knowledge graph with 24 graphs, and its comparison plots show only the first 8. `exp_results/progress.csv` has one row per model and dataset of the runs started so far, with status (`pending`, `training`, `evaluating`, `finished` or `failed`), epoch, last loss, times, MRR, mean rank and Hits@1/3/5/10, and is rewritten after every epoch. `exp_results/<dataset>/status/<model>.json` holds the same fields and the loss of every epoch. A killed run leaves its model as `training` or `evaluating`, and `updated_at` stops changing. `logs/train_kge.log` records each run's command, library versions and settings.

The script can be killed at any moment. The same command resumes from `exp_checkpoints/<dataset>/<model>.pt`, written after every epoch, and skips finished models, and a resumed run gives the same losses and MRR as an uninterrupted one. A failed model is retried by the next run. `--skip-models` leaves models out of a run, and running without it later trains only those. `--epochs` and `--batch-size` must stay the same for a given `--output`, or the dataset is refused.

`--workers N` trains N runs (one model on one dataset each) at once, each in its own process, largest dataset first. At batch size 32 one run keeps one CPU core busy and uses about 17% of an RTX 3090. With 4 workers on WN18RRN_M the GPU was fully busy and each epoch took 1.4 to 1.8 times as long as the same model alone on WN18RR (TransE 27.3 s against 14.8 s, TransH 44.1 s against 31.8 s), so 4 workers train about 2.6 times as fast as one. The results are the same as with one process: 9 runs on three small datasets, trained by 3 workers, interrupted and resumed, gave the same losses and MRR as a single process. Each run has a lock (`<checkpoints>/<dataset>/<model>.lock`) and the workers share the dataset's `.lock`, so several commands can run at once without training a run twice, and a run already failed in the same command is not retried by another worker. With workers the terminal shows only the script's lines and warnings, with a line every 10 epochs per run instead of progress bars, and `logs/train_kge.log` names the worker of each line. Ctrl-C, or SIGINT or SIGTERM to the main process, stops every worker, and the same command resumes.

Measured on an RTX 3090, the six models other than CompGCN take about 9.7 days for all 24 datasets, 29 hours of it outside YAGO. CompGCN encodes the whole graph for every batch and takes about 106 days at batch size 32, 104 of them on the five YAGO datasets.

```bash
python scripts/train_kge.py --skip-models CompGCN                  # the other six models on every dataset
python scripts/train_kge.py                                        # later: only the models still missing
python scripts/train_kge.py --datasets french family --models TransE
python scripts/train_kge.py --skip-models CompGCN --workers 4      # 4 runs at once
```

`scripts/kge_eta.py` reads `exp_results/progress.csv` (`--results` for another `--output`) and prints the run in training with its time left, the runs finished and pending, the time left per dataset and the expected finish time. A pending run's time comes from the finished runs of the same model on at least 20,000 training triples (`MIN_TRIPLES`): their median seconds per epoch per 1,000 training triples, times the run's training triples. `--exclude YAGO` leaves out the datasets whose name starts with `YAGO`. Runs sharing the GPU are slower than alone, so pending runs are estimated with the median ratio between the seconds per epoch of the runs in progress and what the finished runs predict for them (about 1.6 with 4 workers). `--workers N` then estimates a run of `train_kge.py --workers N`: the training time left divided by N, but never less than the longest single run left. A run whose status stays `training` with no update for more than three epochs' time is listed as stopped partway, as a run left by a stopped process is. It needs only the standard library.

```bash
python scripts/kge_eta.py
watch -n 300 python scripts/kge_eta.py              # refreshes every 5 minutes
python scripts/kge_eta.py --exclude YAGO
python scripts/kge_eta.py --workers 4
```

`scripts/tune_kge.py` searches the hyperparameters of the same seven models, then trains every dataset with them; [`docs/kge_tuning.md`](docs/kge_tuning.md) describes the protocol. It runs one Optuna study per model and search dataset (`SEARCHES`) through PyKEEN's `hpo_pipeline`: 30 trials on each of the 19 French, Family, WN18RR and SGKG datasets, and 10 on `YAGO3-10_1_1`, whose result trains the five YAGO3-10 datasets. CompGCN is left out of the YAGO family. Every model trains with batch size 512 (`BATCH_SIZE`, the largest that runs at full speed on a 24 GB GPU) and searches the same four hyperparameters (`SHARED_RANGES`: embedding dimension 50/100/200, learning rate 0.0001 to 0.1, 1 to 32 negatives per positive, and uniform or Bernoulli corruption of the negatives through `CorruptionSampler`), and `pinned_kwargs` fixes the ones PyKEEN would also search for some models (margin, scoring norm, relation dimension, TuckER's dropouts, TransH's regularizer weight) to the model's default, so no model gets knobs of its own tuned. A trial trains for at most 300 epochs, is checked every 5 epochs by MRR on a sample of at most 5,000 validation triples, ends through early stopping (patience 5) or Optuna's Hyperband pruner, and is scored by that MRR. The final runs train every dataset with the best hyperparameters of its search, choosing up to 300 epochs by early stopping on their own validation sample. `--phase tune` writes the studies to `exp_hpo/` (`study.db`, `trials.tsv`, `progress.csv`) and each finished study to `configurations/kge_hyperparameters.json`, the record, and `docs/kge_hyperparameters.md`, the table generated from it. Both are committed. `--phase train` reads only the record and writes `exp_results_hpo/`, in the layout of `exp_results/`, plus `<dataset>/hparams/<model>.json` with the configuration each model used and the epoch it kept. Both phases resume when run again: a killed trial is run again with the same parameters, and a final run resumes from its last epoch. `train_kge.train_model` takes the tuned hyperparameters through its optional `pipeline_kwargs`; without them it trains as before.

```bash
python scripts/tune_kge.py --skip-models CompGCN                   # search every dataset, then train every dataset
python scripts/tune_kge.py --phase tune --searches french          # only the search on french
python scripts/tune_kge.py --phase train                           # only the final runs, from the record
```

`scripts/estimate_tuning_time.py` estimates how long `tune_kge.py` takes on the current machine. It reads the trial durations of a short calibration run of `tune_kge.py` (a few trials per model on `french` and `SGKG1`, written to a throwaway `--hpo-output`), measures each model's seconds per trial per 1,000 training triples, and scales them by the training triples of every dataset of a set, `--suffix` picking a set such as the `_ours` datasets. "Estimating the run time" in `docs/kge_tuning.md` gives the commands and the reference figures of an RTX 3090 (about 8.4 days for the base set without CompGCN).

```bash
cd scripts && python estimate_tuning_time.py /tmp/kge_calibration/hpo --root ../exp_datasets --suffix _ours
```

`cli/complete.py` completes a real graph with a rule set (importable as `complete(client, rules, term_mapping, source, complete_uri, chunk_size)`). It copies the source into `--complete-uri` (default the base graph, `<root>/base`), replacing that graph's contents, and runs `engine/completion.complete_graph` on it until a pass adds nothing. If the source is `--complete-uri` itself, nothing is copied and the rules only add the derived triples to it. The source is a graph URI, or a `.nt`/`.tsv` file that `core/queries.initialize_graph` loads directly into `--complete-uri`. By default it applies only the rules with std confidence 1 (`parse_rule_set`'s `std_threshold`); `--pca-conf X` applies the rules with PCA confidence >= X instead. The config's `rules.pca_threshold` is not used. The base graph is only completed with std confidence 1 rules: `--pca-conf` is refused when `--complete-uri` is the config's base graph. Every other setting can come from `-f` (source the base graph, rules `data.input_dir / rules.rules_file`, the namespace, and the database connection from `data`/`db_config`), and flags override it: `--source`, `--rules-file`, `--namespace`, `--database-url`, `--sparql-endpoint`, `--auth-type`, `--user`, `--password`, `--log-level`. Without `-f`, `--source`, `--rules-file` and `--namespace` are required, and the connection falls back to the `DataConfig`/`DatabaseAuthConfig` defaults (Virtuoso on port 8890). `--complete-uri` is required only without `-f`. No profiles are used and no cyclic rule is removed.

```bash
python -m skgg.cli.complete -f french_royalty.json                                                                                          # completes the base graph in place
python -m skgg.cli.complete -f french_royalty.json --complete-uri http://FrenchRoyalty.org/complete                                          # base graph -> complete
python -m skgg.cli.complete -f french_royalty.json --source data/french_royalty/french_royalty.tsv --complete-uri http://FrenchRoyalty.org/complete  # a file instead of the base graph
python -m skgg.cli.complete --source http://FrenchRoyalty.org/base --complete-uri http://FrenchRoyalty.org/complete --rules-file data/french_royalty/french_royalty.csv --pca-conf 0.9 --namespace http://FrenchRoyalty.org/ --database-url http://localhost:7200/ --sparql-endpoint repositories/FrenchRoyalty --auth-type BASIC --user admin --password rootpassword
```

`cli/validate.py` checks a graph against the SHACL-SPARQL shapes of a `<dataset>.shapes.ttl` file and reports every triple that violates one (importable as `validate(client, source, shapes_file, term_mapping, graph_uri, chunk_size, output, keep_graph)`, which returns the number of flagged triples). `--source` picks the graph: a graph URI or a `.tsv` file, default the config's base graph. A graph URI is queried in place. A `.tsv` file is loaded into a temporary named graph, `--graph-uri` (default `<root>/validation`, e.g. `http://FrenchRoyalty.org/validation`), replacing that graph's contents, and the graph is cleared afterwards unless `--keep-graph` is given; both flags are ignored, with a warning, for a graph URI source. It refuses a `--graph-uri` that is the base graph or starts with `<root>/skgg/`, where the pipeline writes. `core/shapes.load_shapes` reads the shapes file with rdflib (only the shapes, never the data), and `core/queries.build_shape_query` wraps each `sh:select` in a subquery over `FROM <graph-uri>`, keeping only rows whose `$this` is one of the shape's targets (`sh:targetSubjectsOf`, `sh:targetObjectsOf`, `sh:targetClass`, `sh:targetNode`). Only `sh:sparql` constraints are checked; other shapes, deactivated ones, shapes without a target and queries that use `$shapesGraph`, `$currentShape` or `$PATH` are skipped with a warning. A result row names its triples as `?this ?path ?value`, then `?this2 ?path2 ?value2`, and so on. The report (`-o`, default a `.tsv` source's path with suffix `.violations.tsv`, or for a graph URI `data.input_dir / <stem>.violations.tsv`, with the stem of `core/utils.graph_file_stem` as for `download`) has one row per flagged triple and shape, with columns `line`, `subject`, `predicate`, `object`, `shape` and `message` (`sh:message`), sorted by the triple's line in the `.tsv` file. The line is empty for a triple that is not in the file, and always empty for a graph URI source. `-f` is required (database connection, term mapping and default source), `--shapes` defaults to `data.input_dir / <stem of graph.triple_file>.shapes.ttl` (`data/french_royalty/french_royalty.shapes.ttl`), and `--log-level` overrides `logging.level`. Neither the source graph nor the source file is modified, and the pipeline does not use the shapes yet.

```bash
python -m skgg.cli.validate -f french_royalty.json                                                           # base graph -> data/french_royalty/french_royalty.base.violations.tsv
python -m skgg.cli.validate -f french_royalty.json --source data/french_royalty/skgg/french_royalty.skgg.std=1.tsv  # -> data/french_royalty/skgg/french_royalty.skgg.std=1.violations.tsv
python -m skgg.cli.validate -f french_royalty.json --source path/to/graph.tsv --shapes path/to/x.shapes.ttl -o path/to/report.tsv --keep-graph
```

`cli/main.py`'s `__main__` block parses `-f`/`--config-file`, `--skip-edb`, `--fill`, `--log-level`, `--pca-conf`, `--synthetic-uri`, `--base-uri` and `--namespace` from the CLI (see the `bash` example above) and calls `run_synthetic_graph_experiment` end-to-end; confirmed working (verified via `python -m skgg.cli.main -f french_royalty.json`, at std=1 and at PCA 0.9; see `BACKLOG.md`). Check `BACKLOG.md` for the current TODO list before assuming any other code path is exercised/working.

## Architecture

Terse reference below; see `docs/architecture.md` for diagrams and prose, and `docs/concepts.md` for a glossary of the domain terms used here (EDB/IDB, Horn rule, closure, predicate profile, ...).

```
src/skgg/
  cli/
    main.py            # run_synthetic_graph_experiment: the end-to-end experiment pipeline
    common.py           # not a script: the shared -f/--config-file and --log-level options and config loading
    graph.py            # standalone script: upload a .nt/.tsv file to a graph URI, or download a graph URI to .nt and .tsv files
    clean.py            # standalone script: copy a .nt/.tsv file to .tsv without duplicates or untyped objects; report untyped subjects
    convert.py          # standalone script: convert a triples file from .nt to .tsv or back
    complete.py         # standalone script: complete a graph URI or .nt/.tsv file with a rule set into a separate graph
    validate.py         # standalone script: report the triples of a graph URI or .tsv file that violate a SHACL-SPARQL shapes file
  core/
    config.py           # RunConfig and all sub-configs (dataclasses), loaded from configurations/*.json; GraphConfig derives the graph URIs
    utils.py            # logging setup, config lookup, SPARQL client factory, term mapping and term formatting, local file names of graphs
    visualization.py    # plot_relation_graph: renders the predicate relation graph to a PNG, cycles in red
    rules.py           # Atom / RuleSignature (Horn rule) dataclasses, rule-set CSV parsing and rule-set helpers (rule ID order, rule filter label, impacted rules, extensional dependencies)
    cycles.py           # get_relation_graph / remove_minimal_cyclic_rules: the predicate relation graph and the removal of the fewest rules that leave it with no cycle
    triples.py          # local .nt/.tsv files: readers (read_nt/read_tsv), term conversion between the two formats, and convert
    shapes.py           # SparqlConstraint / load_shapes: the sh:sparql constraints of a <dataset>.shapes.ttl file
    queries.py          # All SPARQL query construction + execution against the graph DB (insert/select/ask/clear/count)
  engine/
    metrics.py          # GraphMetrics / PredicateProfile: topological descriptors (domain/range frequency per predicate)
    edb.py               # Builds the Extensional DB: selects/generates triples satisfying rule bodies + profile constraints
    generator.py           # Lower-level triple generation/binding logic shared by edb.py/completion.py/fill.py: grounding sampler for rule bodies (`sample_groundings`, used by edb.py; `sample_grounding_groups`, used by fill.py), rule application (apply_rule, queries the real graph directly), and get_closed_rules/get_closed_preds (SPARQL checks for whether a rule/predicate reached its support/frequency target, used by completion.py and cli/main.py)
    completion.py          # complete_graph: forward-chains rules over a base graph assuming rule bodies are fully grounded
    fill.py                # fill_open_predicates: adds the missing triples of the relations still open after completion, keeping closed rules' support and raising open rules' support toward their target
```

Data flow: **term mapping + rules CSV + source graph metrics → EDB (facts satisfying rule bodies) → IDB (rule-derived facts, grown until closure) → synthetic graph**, all mediated through SPARQL against the graph store, keyed by the graph URIs that `GraphConfig` derives from the config's `graph.namespace` and the run's rule filter (see the table under "Graph Database").

Rules are parsed from CSV into `RuleSignature`/`Atom` objects (`core/rules.py`); each rule has body atoms and a head atom over predicates/variables, plus confidence metrics (PCA/Std confidence). `parse_rule_set` expects lowercase snake_case columns (`rule_id`, `body`, `head`, `std_confidence`, `pca_confidence`, `head_coverage`, `positive_examples`, and optionally `functional_variable`, which the summary's PCA confidence needs), matching the CSVs written by `mine_rules/run_amie.py` like `data/french_royalty/french_royalty.csv` — a CSV with the older CamelCase columns (`Body`/`Head`/`PCA_Confidence`/...) will raise a `KeyError`. Rule IDs are read from the `rule_id` column (`run_amie.py` sorts the rules by std confidence, then PCA confidence, both descending, and numbers them 1..N in that order), so a rule keeps its ID whatever the PCA threshold; `parse_rule_set` raises `ValueError` if the column is missing or an ID is empty or repeated. Summaries and relation-graph labels sort IDs with `core/rules.rule_sort_key` (numeric IDs in numeric order). `parse_rule_set` classifies each rule's `HornRule.classification` as POSITIVE/NEGATIVE/UNKNOWN by comparing its confidence against the threshold: PCA confidence against `rules.pca_threshold` when the config (or `--pca-conf`) sets it, std confidence against `DEFAULT_STD_THRESHOLD` (1) otherwise. It then drops every non-POSITIVE rule, so only rules meeting the threshold are ever seen by EDB generation and completion.

LoRA fine-tuning of LLMs and Chain-of-Thought dataset generation from KGs are not implemented under `src/` yet. `core/config.py` previously carried placeholder `FineTuningConfig`/`CoTGenerationConfig` dataclasses for this; they were removed as dead code (nothing read them) and should be reintroduced once that pipeline is actually built.

## Notes

- No test suite, linting/CI pipeline, or Makefile currently exists in this repo — `ruff` and `mypy` are configured in `pyproject.toml` (strict mypy, ruff rule sets E/F/I/UP/B/N) but are not wired into any automated command; run them manually (`ruff check .`, `mypy .`) if validating changes. See `BACKLOG.md` for the open question of whether/how to add a `tests/` + CI setup.
- Per-experiment outputs (logs) are written under `logs/`. This folder is gitignored.
- Input graph data (`.tsv`/`.nt` triples, rule CSVs, SHACL shapes) lives under `data/`, a plain local folder with one subfolder per dataset, whose files are named after the dataset. The project keeps only the cleaned graph of a dataset (no literals, see `cli/clean.py`), and that file is always its base triple file: `data/french_royalty/` holds `french_royalty.tsv` (the base graph), `french_royalty.csv` (the rules AMIE mined from it) and `french_royalty.shapes.ttl`. The original graph, with literals, is kept outside the repository. Each config's `data.input_dir` picks the folder that its `graph.triple_file` and `rules.rules_file` are read from: `configurations/french_royalty.json` reads `data/french_royalty/`. The synthetic graphs downloaded from the store are kept as `.tsv` files in the dataset's `skgg/` subfolder, named after their graph (`core/utils.graph_file_stem`): `data/french_royalty/skgg/` holds `french_royalty.skgg.std=1.tsv`, `french_royalty.skgg.pca=0.9.tsv` and their `.filled.tsv` copies. Loading a config raises `FileNotFoundError` if its `data.input_dir` does not exist.
- The benchmark datasets live under `exp_datasets/`, one folder per dataset. Each has the `train`, `valid` and `test` splits, the `<folder>.unified.tsv` that `scripts/merge_splits.py` writes from them, and the `<folder>.csv` of rules that `mine_rules/mine_datasets.py` mines from the unified file, 2,057 rules over the 24 datasets. The rules are mined from the whole unified file, test triples included, because they only feed the generator. `YAGO3-10_1_1`, `_1_N`, `_N_1` and `_N_M` have cleaned entity names that differ from those of `YAGO3-10` (see `BACKLOG.md`).
- A dataset's schema is a `<dataset>.ttl` file next to its data that declares its classes (`owl:Class`) and entity-to-entity relations (`owl:ObjectProperty` with `rdfs:domain`/`rdfs:range`). French Royalty has no schema file in `data/french_royalty/`. Start new schemas from `schemas/template.ttl`. The pipeline does not read schemas yet (see `BACKLOG.md`).
- A dataset's SHACL constraints are a `<dataset>.shapes.ttl` file in its `data.input_dir`, named after the stem of `graph.triple_file` (`data/french_royalty/french_royalty.shapes.ttl`), with one `sh:sparql` constraint per shape. Its `ex:` prefix must equal the config's `graph.namespace`. Only `cli/validate.py` reads it, and finds it there by default.

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
