#!/usr/bin/env bash
# Makes the rule-level copies of the benchmark datasets with this project's generator
# (ours) and with kbgen, both given the same rules at every level.
#
#   runs/rule-levels/reproduce.sh                 # every step, every dataset
#   runs/rule-levels/reproduce.sh ours            # one step
#   DATASETS="french1_N frenchN_M" runs/rule-levels/reproduce.sh ours kbgen
#
# Steps, in order (see README.md):
#   record  write machine.txt: machine, OS, Python, GraphDB, commits
#   check   check_copies.py: which copies exist and were made as intended
#           (imports valid levels of an earlier kbgen run from $KBGEN_LEGACY_WORK)
#   ours    cli/generate_datasets.py --levels for the copies still missing or invalid;
#           saves the rules each copy used as <d>_ours_<L>/<d>_ours_<L>_used.csv
#   kbgen   kbgen's driver --rules-from-ours for the copies still missing or invalid;
#           gives kbgen exactly the rule IDs of those _used.csv files
#   verify  check_copies.py, check_outputs.py and check_kbgen_levels.py
#   report  make_report.py -> REPORT.md
#
# A level where our generator uses no rule, or the same rules as another level
# (ours_same_as in exp_datasets/rule_levels.tsv), gets no copy from either method.
# The ours step needs GraphDB: docker compose --profile graphdb up -d
# Several invocations with different DATASETS can run at once; each dataset has its
# own GraphDB repository.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
cd "$REPO"

DATASETS=${DATASETS:-"french french1_1 french1_N frenchN_1 frenchN_M family Family1_1 Family1_N FamilyM_N FamilyN_1 WN18RR WN18RR1_1 WN18RR1_N WN18RRN_1 WN18RRN_M YAGO3-10 YAGO3-10_1_1 YAGO3-10_1_N YAGO3-10_N_1 YAGO3-10_N_M SGKG1 SGKG2 SGKG3 SGKG4"}
LEVELS=${LEVELS:-"std1 r25 r50 r75 r100"}
SKGG_PYTHON=${SKGG_PYTHON:-python}                                   # environment of requirements.txt, with skgg installed
KBGEN_PYTHON=${KBGEN_PYTHON:-$HOME/.pyenv/versions/KG/bin/python}    # environment of kbgen/requirements.txt
KBGEN_JOBS=${KBGEN_JOBS:-6}                                          # kbgen levels synthesized at once
KBGEN_LEGACY_WORK=${KBGEN_LEGACY_WORK:-}                             # earlier kbgen work folder to import valid levels from

WORK=$HERE/kbgen-work
RESULTS=$HERE/kbgen-results
STEPS=${*:-record check ours kbgen verify report}

log_step() { printf '%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$1" "$2" "$DATASETS" >> "$HERE/steps.log"; }

# Refuse to make copies from code that is not committed.
require_clean() {
    if ! git diff --quiet HEAD -- src runs/rule-levels/kbgen scripts/format_change.py; then
        echo "Uncommitted changes under src/, runs/rule-levels/kbgen/ or scripts/format_change.py; commit them first." >&2
        exit 1
    fi
}

# Lines "<levels>\t<datasets>" of the copies still to make with method $1.
plan() {
    # shellcheck disable=SC2086
    "$SKGG_PYTHON" "$HERE/check_copies.py" --plan "$1" --datasets $DATASETS --levels $LEVELS
}

step_record() {
    {
        echo "recorded: $(date '+%F %T %Z')"
        echo "host: $(hostname)"
        echo "cpu: $(lscpu | sed -n 's/^Model name: *//p'), $(nproc) threads"
        echo "ram: $(free -g | awk '/Mem/{print $2}') GB"
        echo "os: $(. /etc/os-release && echo "$PRETTY_NAME"), kernel $(uname -r)"
        echo "skgg python: $("$SKGG_PYTHON" --version 2>&1) ($(command -v "$SKGG_PYTHON"))"
        echo "kbgen python: $("$KBGEN_PYTHON" --version 2>&1) ($KBGEN_PYTHON)"
        echo "graphdb: $(curl -s -u admin:rootpassword http://localhost:7200/rest/info/version || echo unreachable)"
        echo "repository: $(git rev-parse HEAD) on $(git rev-parse --abbrev-ref HEAD)"
        echo "kbgen: upstream e5d06bc + baselines/kbgen.patch, driver sha256 $(sha256sum runs/rule-levels/kbgen/synthesize_exp_datasets.py | cut -d' ' -f1)"
    } > "$HERE/machine.txt"
    cat "$HERE/machine.txt"
}

step_check() {
    # shellcheck disable=SC2086
    "$SKGG_PYTHON" "$HERE/check_copies.py" --datasets $DATASETS --levels $LEVELS \
        ${KBGEN_LEGACY_WORK:+--import-legacy "$KBGEN_LEGACY_WORK"} || true
}

step_ours() {
    require_clean
    plan ours | while IFS=$'\t' read -r levels datasets; do
        echo "ours: levels $levels of $datasets"
        # shellcheck disable=SC2086
        "$SKGG_PYTHON" -m skgg.cli.generate_datasets --levels $levels --datasets $datasets --force \
            --log-dir runs/rule-levels/ours --times runs/rule-levels/ours/times.tsv
    done
}

step_kbgen() {
    require_clean
    mkdir -p "$RESULTS/logs"
    plan kbgen | while IFS=$'\t' read -r levels datasets; do
        echo "kbgen: levels $levels of $datasets"
        # --force redoes the level steps, never the shared tensor, M1 and M2 of a dataset.
        # shellcheck disable=SC2086
        "$KBGEN_PYTHON" "$HERE/kbgen/synthesize_exp_datasets.py" --root "$REPO/exp_datasets" \
            --levels $levels --datasets $datasets --rules-from-ours --force --jobs "$KBGEN_JOBS" \
            --work-dir "$WORK" --results-dir "$RESULTS" \
            2>&1 | tee -a "$RESULTS/logs/kbgen_$(date '+%Y%m%d_%H%M%S')_${datasets%% *}.log"
    done
    # The summary of every level in the work folder, imported ones included.
    # shellcheck disable=SC2086
    "$KBGEN_PYTHON" "$HERE/kbgen/synthesize_exp_datasets.py" --summary-only --levels $LEVELS \
        --work-dir "$WORK" --results-dir "$RESULTS"
}

step_verify() {
    # shellcheck disable=SC2086
    "$SKGG_PYTHON" "$HERE/check_copies.py" --datasets $DATASETS --levels $LEVELS
    for level in $LEVELS; do
        made=$(awk -F'\t' -v l="$level" 'NR > 1 && $2 == l && $7 == "valid" {print $1}' "$HERE/status.tsv")
        # shellcheck disable=SC2086
        [ -n "$made" ] && python scripts/check_outputs.py --suffix "_ours_$level" --datasets $made
    done
    # shellcheck disable=SC2086
    python scripts/check_kbgen_levels.py --summary "$RESULTS/rule-levels/summary.csv" --rules-from-ours \
        --datasets $DATASETS --levels $LEVELS
}

step_report() {
    "$SKGG_PYTHON" "$HERE/make_report.py"
}

for step in $STEPS; do
    log_step "$step" start
    "step_$step"
    log_step "$step" end
done
