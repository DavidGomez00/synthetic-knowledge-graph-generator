# Generating kbgen's rule-level copies on another machine

This page lists the steps to make kbgen's copies of the rule-level experiment on any machine with a clone of this repository: get kbgen and patch it, run its driver on some datasets, check the copies, split them and bring them back. [`rule-levels.md`](rule-levels.md) explains the experiment: each dataset is copied with only part of its mined rules, at the levels `r0` (none), `r25`, `r50` and `r75`, with a PCA threshold per dataset and level read from `exp_datasets/rule_levels.tsv`. The level `r100` is the existing `<dataset>_kbgen` copy and is never made again.

kbgen is not seeded, so two runs of the same dataset and level give different copies. Each dataset is made on one machine only: check the table below, and take only the datasets the user assigns to the machine.

## Who makes what

84 copies in all: `r0`, `r25`, `r50` and `r75` of every dataset, except the SGKG datasets, which need only `r0` (their other levels keep every rule, so the existing `_kbgen` copy stands in for them). The time is an estimate of the CPU time of one kbgen process on the development laptop (Intel i5-11300H), summed over the levels, and the longest level bounds the wall time of a dataset whatever the number of processes. It scales each dataset's all-rules synthesis time of 10 November 2026 by the share of the PCA confidence of its rules that each level keeps, on top of about 1 ms per fact without rules; on french1_N it was within 1 s of the measured times. The cost without rules is a guess for the larger datasets, so the SGKG times in particular can be off: their all-rules copies took about 20 minutes with only 3 to 6 rules. The five french datasets, made on 16 November 2026 with 4 processes at once, took 58 minutes of synthesis against the 72 estimated (french 34 against 39, french1_1 17 against 23), and 17 minutes of wall time; `r0` took under 3 seconds each.

| Dataset | Levels | Estimated time | Longest level | Machine |
|---|---|---:|---:|---|
| french | r0-r75 | 39 min | 18 min | development laptop, 16 November 2026 |
| french1_1 | r0-r75 | 23 min | 10 min | development laptop, 16 November 2026 |
| french1_N | r0-r75 | under 1 min | under 1 min | development laptop, 16 November 2026 |
| frenchN_1 | r0-r75 | 4 min | 2 min | development laptop, 16 November 2026 |
| frenchN_M | r0-r75 | 7 min | 3 min | development laptop, 16 November 2026 |
| family | r0-r75 | 11.6 h | 5.2 h (r75) | Hefesto (i9-7900X), 16 November 2026 |
| Family1_1 | r0-r75 | 14 min | 6 min | Hefesto (i9-7900X), 16 November 2026 |
| Family1_N | r0-r75 | 4 min | 2 min | Hefesto (i9-7900X), 16 November 2026 |
| FamilyM_N | r0-r75 | 12 min | 5 min | Hefesto (i9-7900X), 16 November 2026 |
| FamilyN_1 | r0-r75 | 2 min | 1 min | Hefesto (i9-7900X), 16 November 2026 |
| SGKG1 to SGKG4 | r0 | 1 to 25 min each | | Hefesto (i9-7900X), 16 November 2026 |
| WN18RR | r0-r75 | 1.9 h | 52 min | Hefesto (i9-7900X), 16 November 2026 |
| WN18RR1_1 | r0-r75 | 1.3 h | 34 min | Hefesto (i9-7900X), 16 November 2026 |
| WN18RR1_N | r0-r75 | 2 min | 1 min | Hefesto (i9-7900X), 16 November 2026 |
| WN18RRN_1 | r0-r75 | 46 min | 18 min | Hefesto (i9-7900X), 16 November 2026 |
| WN18RRN_M | r0-r75 | 1.9 h | 52 min | Hefesto (i9-7900X), 16 November 2026 |
| YAGO3-10 | r0-r75 | 10.1 h | 4.2 h | Hefesto (i9-7900X), 16 November 2026 |
| YAGO3-10_1_1 | r0-r75 | 3.2 h | 1.4 h | Hefesto (i9-7900X), 16 November 2026 |
| YAGO3-10_1_N | r0-r75 | 2.2 h | 52 min | Hefesto (i9-7900X), 16 November 2026 |
| YAGO3-10_N_1 | r0-r75 | 5.7 h | 2.3 h | Hefesto (i9-7900X), 16 November 2026 |
| YAGO3-10_N_M | r0-r75 | 9.6 h | 3.9 h | Hefesto (i9-7900X), 16 November 2026 |

Update the Machine column when the work is assigned, and commit it with the copies.

## Requirements

- Python 3.10 or newer, git, and access to github.com to clone kbgen (`https://github.com/aolimelo/kbgen`, public).
- The packages kbgen imports: rdflib, numpy, scipy, tqdm and matplotlib. `requirements.txt` pins all of them. The french copies were made with Python 3.10.12, rdflib 7.6.0, numpy 2.2.6, scipy 1.15.3, matplotlib 3.10.8 and tqdm 4.67.3.
- One core per process: kbgen is single-threaded Python, and the driver runs one level of one dataset per process (`--jobs`).
- Memory: not measured on the YAGO3-10 datasets. Step 4 measures it before their batch.
- Disk: kbgen's working files take about 300 MB per YAGO3-10 dataset plus about 170 MB per level, much less for the others; a few GB cover everything.

## 1. Update the clone of this repository

The levels file, the kbgen patch and this page are on the branch `rule-levels` until it is merged into `main`. `REPO` is the path of the clone, and `KBGEN` where kbgen goes; the commands below use both.

```bash
export REPO=~/synthetic-knowledge-graph-generator   # the clone on this machine
export KBGEN=~/kbgen
cd $REPO
git fetch origin
git switch rule-levels
git pull
ls exp_datasets/rule_levels.tsv baselines/kbgen.patch scripts/format_change.py scripts/check_kbgen_levels.py
```

## 2. Python environment

Any environment with the packages of `requirements.txt` works, for example the one that trains the models. Otherwise:

```bash
python3 -m venv $REPO/.venv
source $REPO/.venv/bin/activate
pip install -r $REPO/requirements.txt
```

The driver starts kbgen's scripts with the interpreter that runs it, so run every command below inside this environment.

## 3. Get kbgen and apply the patch

`baselines/kbgen.patch` holds every change made to kbgen's code for these copies, against commit `e5d06bc`, the head of the authors' `master` on 16 November 2026. It is the Python 3 fixes (`list()` around dict views and the results of `filter()` and `zip()`, `allow_pickle=True` when loading the `.npz`, rules files opened in text mode, URIRefs wrapped in `str()` in SPARQL patterns) and the driver `synthesize_exp_datasets.py`, with its `--levels` and `--rules-from-ours` options. kbgen's generation logic is unchanged, known bugs included ([`benchmark-copies.md`](benchmark-copies.md), kbgen section).

```bash
git clone https://github.com/aolimelo/kbgen.git $KBGEN
cd $KBGEN
git switch -c exp-datasets e5d06bc
git apply $REPO/baselines/kbgen.patch
git status --short                   # 6 modified files and ?? synthesize_exp_datasets.py
sha256sum synthesize_exp_datasets.py # 06568bc9bcd120296225da24bde9f2d51eb311036e95d3db0a1b29d8cfa44103
```

The copies made on 16 November 2026 before 16:23 came from the driver with checksum `f8c727b2...`. The current one adds the level `std1` (rules with std confidence 1, through the column `std_threshold` of `rule_levels.tsv`) and `--rules-from-ours`, which gives kbgen the rules this generator used at each level, and writes the columns `std_threshold` and `rules_from` to the summary; it picks the same rules as before for the other levels. kbgen's own files are unchanged. Don't change kbgen's code otherwise: the copies of every machine must come from the same code.

## 4. Measure the memory of a YAGO3-10 process

Only needed before running a YAGO3-10 dataset. Run `r0` of the smallest one alone; it takes a few minutes, and its copy is a real one that the batch later skips. The wrapper prints the peak memory of the largest process.

```bash
cd $KBGEN
python -c 'import resource, subprocess, sys; c = subprocess.run(sys.argv[1:]).returncode; print(f"peak RSS {resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2**20:.1f} GB"); sys.exit(c)' \
  python synthesize_exp_datasets.py --root $REPO/exp_datasets --levels r0 --datasets YAGO3-10_1_N --jobs 1
```

Run at most as many YAGO3-10 processes at once as the free memory holds at that peak, with some margin, since the larger YAGO3-10 datasets have up to 2.8 times as many facts.

## 5. Run the batch

`--root $REPO/exp_datasets` is required: the driver's default root is the path on the development laptop. For each dataset, the driver first makes kbgen's N-Triples file, tensor, M1 and M2 in `$KBGEN/work/<dataset>/`, once, in a few minutes at most. A fresh run learns the same tensor and the same M2 statistics per relation as the development laptop did for the existing `_kbgen` copies; only the numbering of entities and relations differs (checked on french1_N). Then, for each level, it learns M3 from the level's rules in `$KBGEN/work/rule-levels/<dataset>-<level>/` and writes the copy to `$REPO/exp_datasets/<dataset>_kbgen_<level>/<dataset>_kbgen_<level>.tsv`, next to the dataset and its full copy `<dataset>_kbgen`. It skips `r100` and the levels with `kbgen_same_as`, and stops if `rule_levels.tsv` and the dataset's rules file disagree on the number of rules a level keeps.

The driver starts the smallest datasets first and runs `--jobs` levels at once. Give each long dataset (family and the YAGO3-10 datasets) its own command, started first, with its levels from the longest to the shortest, so that its longest level starts at once:

```bash
cd $KBGEN
nohup python synthesize_exp_datasets.py --root $REPO/exp_datasets --levels r75 r50 r25 r0 --datasets family --jobs 4 \
  > work/levels_family.log 2>&1 &
nohup python synthesize_exp_datasets.py --root $REPO/exp_datasets --levels r75 r50 r25 r0 --jobs 4 \
  --datasets Family1_1 Family1_N FamilyM_N FamilyN_1 SGKG1 SGKG2 SGKG3 SGKG4 WN18RR WN18RR1_1 WN18RR1_N WN18RRN_1 WN18RRN_M \
  > work/levels_rest.log 2>&1 &
```

Adapt `--datasets` and `--jobs` to the datasets assigned and the cores and memory of the machine; the sum of `--jobs` over the commands running at once should not exceed the physical cores. Two commands must not share a dataset. On a laptop, prefix each command with `systemd-inhibit --what=sleep:idle --why="kbgen rule levels"` so that it doesn't suspend.

Progress: each finished level logs a line ending in `finished`, and each failed one `FAILED` with the path of its log.

```bash
grep -h -e " finished$" -e FAILED $KBGEN/work/levels_*.log
ls -d $REPO/exp_datasets/*_kbgen_r*
```

If a command stops, run it again: steps whose output exists are skipped. A process killed while writing a copy can leave it incomplete, and the rerun then skips it; the check of step 6 finds it, and `--force --datasets <dataset> --levels <level>` makes that level again.

## 6. Check the copies

Commands that run side by side each rewrite the summary when they end, so rebuild it once all are done, then check every level of the datasets this machine made:

```bash
cd $KBGEN
python synthesize_exp_datasets.py --root $REPO/exp_datasets --summary-only --levels r0
cd $REPO
python scripts/check_kbgen_levels.py --summary $KBGEN/results/rule-levels/summary.csv
```

For each copy, the script checks that kbgen was given the `rules_kept` rules of `rule_levels.tsv` and parsed its `rules_kbgen`, that the copy has as many facts as kbgen kept from the dataset (within 0.1%) in as many lines, and that no relation was lost. It exits with status 1 if a copy fails.

## 7. Split the copies

```bash
cd $REPO
for level in r0 r25 r50 r75; do
  python scripts/split_copies.py --suffix _kbgen_${level}
done
python scripts/check_kbgen_levels.py --summary $KBGEN/results/rule-levels/summary.csv --splits
```

`--suffix _kbgen_r25` picks the `<dataset>_kbgen_r25` folders and finds each one's source by removing the suffix. `split_copies.py` skips the copies that already have their splits, so it can run after each machine's batch, and a copy's splits are the same on every machine (seed 1235).

## 8. Record the run and bring the copies back

Record the run in `baselines/kbgen-runs/`, next to the patch, under a short name for the machine, so that the generation times can be reported per machine:

```bash
cd $REPO
M=<machine name>
mkdir -p baselines/kbgen-runs/$M
cp $KBGEN/results/rule-levels/summary.csv $KBGEN/work/levels_*.log baselines/kbgen-runs/$M/
{ echo "machine: $M"; date -Is; lscpu | grep "Model name"; echo "cores: $(nproc)"; free -g | head -2
  python --version; sha256sum baselines/kbgen.patch; git rev-parse HEAD; } > baselines/kbgen-runs/$M/machine.txt
```

Add the commands run and when each started and ended to `machine.txt`. The summary's `seconds_*` columns give each step's time per level.

Then commit the copies, their splits and the record on the branch and push them. Machines write different folders, so a push refused because another machine pushed first only needs a rebase:

```bash
git add exp_datasets/*_kbgen_r* baselines/kbgen-runs/$M docs/generate-kbgen-levels.md
git status --short exp_datasets baselines | head    # only this machine's datasets
git commit -m "Add kbgen's rule-level copies made on $M"
git pull --rebase origin rule-levels && git push origin rule-levels
```

`git add` of the pattern also stages copies of other machines that this clone already has, but those are unchanged and add nothing. Leave `$KBGEN/work/` out of git. The copies of a level of the 19 datasets other than YAGO3-10 take about 26 MB, plus as much for their splits, and those of the five YAGO3-10 datasets about 72 MB, plus their splits.

The KGE models are trained on these copies in a separate step, with the commands of [`rule-levels.md`](rule-levels.md).
