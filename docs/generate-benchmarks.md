# Generating the benchmark copies on another machine

This page lists the steps to run `cli/generate_datasets.py` on a machine other than the one the project was developed on, check its output and bring the copies back. The batch writes one synthetic copy per dataset under `exp_datasets/`, `<dataset>_ours/<dataset>_ours.tsv`, at the default rule filter (std confidence 1) and without the fill step.

## Requirements

- Linux or macOS with Docker and Docker Compose, Python 3.10 or newer, and git with access to the GitHub repository.
- About 1.7 GB of disk for the clone (1.24 GB of files, of which 217 MB are this generator's copies and 110 MB kbgen's and PyGraft's, and about 450 MB of history), and a few hundred MB for GraphDB's data.
- Memory: on the development machine (8 cores, 15 GB), GraphDB peaked at about 3 GB and the pipeline at under 1 GB, both on the YAGO3-10 datasets, so 8 GB free should be enough.
- Time: the batch runs one dataset at a time and the pipeline uses one core. On the development machine, the 24 datasets took 3 h 42 min one at a time before the self-loop fix, and the fix makes each dataset that takes more than 30 seconds 1.10 to 1.56 times as long. The run of 11 November 2026 at commit `c6e5b35` took 3 h 11 min because two of the YAGO3-10 datasets ran in separate batches (see section 4). `docs/generation-times.md` gives the time of each dataset.

## 1. Get the code and the datasets

The datasets (`exp_datasets/<dataset>/`, with each `<dataset>.unified.tsv` and its rules, `<dataset>.csv`) are tracked in git, so the clone has everything the batch reads. So are the copies of this generator, made on 11 November 2026 (`exp_datasets/<dataset>_ours/`), of kbgen, made on 10 November (`<dataset>_kbgen/`), and of PyGraft, made on 11 November (`<dataset>_pygraft/`).

```bash
git clone git@github.com:DavidGomez00/synthetic-knowledge-graph-generator.git
cd synthetic-knowledge-graph-generator
git switch edb-self-loops     # the branch with the self-loop fix, until it is merged into main
git rev-parse --short HEAD    # note the commit the copies come from
```

## 2. Install the Python package

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

The batch starts each step as `python -m skgg.cli...` with the same interpreter, so it must run inside this environment.

## 3. Start GraphDB

GraphDB keeps its data in the host folder that `GRAPHDB_HOME` names, by default `/mnt/Pandora/graphdb-home`, which only exists on the development machine. Point it at another folder in a `.env` file next to `docker-compose.yml` (git ignores that file). The container runs as root, so Docker creates the folder if it's missing, owned by root.

```bash
echo "GRAPHDB_HOME=$HOME/graphdb-home" > .env
docker compose --profile graphdb up -d
curl -s http://localhost:7200/rest/repositories    # prints [] once GraphDB is up
```

The batch creates and deletes its own repositories (`skgg_ours_<dataset>`), so GraphDB needs no setup in its Workbench. A fresh GraphDB has security turned off, so the default credentials of the batch (`admin`/`rootpassword`) are not checked. With security on, pass `--user` and `--password`.

## 4. Run the batch

Run it from the repository root, where the pipeline writes `logs/`. The batch skips the datasets that already have a copy, and the clone has one for every dataset, so remove them first; `git restore exp_datasets` puts the committed ones back. `nohup` keeps the batch running after the terminal closes.

```bash
rm -r exp_datasets/*_ours
mkdir -p logs/ours
nohup python -m skgg.cli.generate_datasets > logs/ours/batch.log 2>&1 &
```

This generates all 24 datasets, smallest first; the five YAGO3-10 datasets come last and take most of the time. `tail -f logs/ours/batch.log` shows one line per dataset, and `logs/ours/<dataset>.log` the phases of one dataset. If the batch stops, the same command resumes it: datasets that already have their copy are skipped. `--force --datasets <names>` generates the named datasets again.

To use more cores, split the datasets between several batches with `--datasets`, each with its own `batch.log`, so that no dataset is in two of them. Each dataset gets its own GraphDB repository, but two batches that reach the same dataset replace each other's repository and the first one fails. Running two or three YAGO3-10 datasets at once makes each of them 1.3 to 1.5 times slower than alone, because they share GraphDB and the CPU.

## 5. Check the copies

```bash
python scripts/check_outputs.py
```

The script compares each copy with its source: triple counts, new entities, and, per relation, the frequency, the degrees and the number of self-loops. It exits with status 1 if a copy is missing or malformed. Expected differences:

- `french`: `parent` is 47 triples short (1,192 of 1,239), because only the fill step adds the `parent` triples that its rules don't derive.
- `family`: several relations are far above their frequency (86,649 triples in all, against 28,358), because completion has no cap, and completion derives self-loops that the source doesn't have (104 in the copy of 11 November 2026, in `aunt`, `nephew`, `niece`, `son` and `uncle`). The pipeline isn't seeded, so these numbers change from run to run.

Every other copy should match its source on every relation.

## 6. Bring the copies back

The new copies replace the committed ones in the working tree, so `git status` lists them as changed. Either commit them on a branch and push it, or pack them with the logs and the commit they come from:

```bash
git rev-parse HEAD > logs/ours/commit.txt
tar czf ours.tar.gz exp_datasets/*_ours logs/ours
```

Unpack the archive at the root of the project on the other machine. Then stop GraphDB with `docker compose --profile graphdb down`.
