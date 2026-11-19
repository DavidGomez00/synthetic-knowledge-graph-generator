#!/usr/bin/env bash
# Makes the rule-level copies of the datasets with type triples (exp_datasets_type/),
# with this generator and then with kbgen, from the rules mined
# without types (exp_datasets/<d>/<d>.csv, copied next to each dataset).
#
# This generator copies the source's type triples (cli/main.py --copy-relations type)
# and generates the other relations as usual. kbgen is given the rules this generator
# used at each level (--rules-from-ours) and its default entity count, and every
# triple of its output is kept, its entity types and schema included.
#
# A copy that already exists is skipped, so running the script again makes only the
# missing ones. Run it detached, so that it outlives the shell:
#
#   setsid nohup runs/rule-levels-type/generate.sh > runs/rule-levels-type/generate.out 2>&1 < /dev/null &
#
# Needs GraphDB: docker compose --profile graphdb up -d
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
cd "$REPO"

PYTHON=${PYTHON:-/home/david/.pyenv/versions/KG/bin/python}
ROOT=exp_datasets_type
DATASETS="french french1_1 frenchN_1"
LEVELS="pca1 r25 r50 r75 r100"

log() { printf '%s\t%s\n' "$(date '+%F %T')" "$*" | tee -a "$HERE/steps.log"; }

until curl -sf -o /dev/null -u admin:rootpassword http://localhost:7200/rest/repositories; do
    log "waiting for GraphDB"
    sleep 10
done

log "start $(git rev-parse --short HEAD)$(git diff --quiet HEAD -- src runs/rule-levels/kbgen || echo +dirty)"

for d in $DATASETS; do
    cmp -s "exp_datasets/$d/$d.csv" "$ROOT/$d/$d.csv" || cp "exp_datasets/$d/$d.csv" "$ROOT/$d/$d.csv"
done
"$PYTHON" -m skgg.cli.rule_levels --root "$ROOT" -o "$ROOT/rule_levels.tsv"
log "rule_levels exit $?"

mkdir -p "$HERE/ours"
"$PYTHON" -m skgg.cli.generate_datasets --root "$ROOT" --output-root "$ROOT" \
    --datasets $DATASETS --levels $LEVELS --copy-relations type \
    --log-dir "$HERE/ours" --times "$HERE/ours/times.tsv"
ours=$?
log "ours exit $ours"

"$PYTHON" runs/rule-levels/kbgen/synthesize_exp_datasets.py --root "$ROOT" \
    --rule-levels "$ROOT/rule_levels.tsv" --datasets $DATASETS --levels $LEVELS \
    --rules-from-ours --jobs 6 \
    --work-dir "$HERE/kbgen-work" --results-dir "$HERE/kbgen-results"
kbgen=$?
log "kbgen exit $kbgen"

"$PYTHON" scripts/check_kbgen_levels.py --summary "$HERE/kbgen-results/rule-levels/summary.csv" \
    --rules-from-ours --levels-file "$ROOT/rule_levels.tsv" --level-root "$ROOT" > "$HERE/check_kbgen.txt" 2>&1
log "check_kbgen exit $?"
for level in $LEVELS; do
    python scripts/check_outputs.py --root "$ROOT" --datasets $DATASETS --suffix "_ours_$level"
done > "$HERE/check_ours.txt" 2>&1
log "end: ours $ours, kbgen $kbgen"
exit $(( ours != 0 || kbgen != 0 ))
