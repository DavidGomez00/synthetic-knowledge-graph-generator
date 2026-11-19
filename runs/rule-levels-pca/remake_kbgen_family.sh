#!/usr/bin/env bash
# Remakes kbgen's family copies (pca1, r25 to r100) from the rules this generator uses
# in the PCA setting, read from exp_datasets/family_ours_<level>/family_ours_<level>_used.csv.
# The earlier family_kbgen_r25 to r100 were made from the std copies' rules, 3 more
# (43, 45, 50); family_kbgen_pca1 is new. See README.md in this folder.
#
# Runs from a clone of the repository with no GraphDB. Start it in tmux, so a lost
# connection doesn't stop it:
#
#   tmux new-session -d -s kbgen-family 'bash runs/rule-levels-pca/remake_kbgen_family.sh 2>&1 | tee runs/rule-levels-pca/kbgen.out; exec bash'
#
# PYTHON is the interpreter with kbgen's packages (runs/rule-levels/kbgen/requirements.txt).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
cd "$REPO"

PYTHON=${PYTHON:-python}
LEVELS="pca1 r25 r50 r75 r100"
JOBS=${JOBS:-5}

log() { printf '%s\t%s\n' "$(date '+%F %T')" "$*" | tee -a "$HERE/steps.log"; }

{
    echo "recorded: $(date '+%F %T %Z')"
    echo "host: $(hostname)"
    echo "cpu: $(lscpu | sed -n 's/^Model name:[[:space:]]*//p'), $(nproc) threads"
    echo "ram: $(free -g | awk '/^Mem:/{print $2}') GB"
    echo "commit: $(git rev-parse --short HEAD)$(git diff --quiet HEAD -- runs/rule-levels/kbgen exp_datasets/family_ours_* || echo +dirty)"
} > "$HERE/machine.txt"

# Our used rules must come from the PCA setting: 26 complete rules at pca1.
for level in $LEVELS; do
    used="exp_datasets/family_ours_$level/family_ours_${level}_used.csv"
    [ -f "$used" ] || { log "missing $used: pull the branch with the PCA copies"; exit 1; }
done
pca1=$(($(wc -l < exp_datasets/family_ours_pca1/family_ours_pca1_used.csv) - 1))
[ "$pca1" -eq 26 ] || { log "family_ours_pca1 uses $pca1 rules, not 26"; exit 1; }

log "start kbgen family $LEVELS on $(hostname), $JOBS processes"
"$PYTHON" runs/rule-levels/kbgen/synthesize_exp_datasets.py --root exp_datasets \
    --rules-from-ours --force --datasets family --levels $LEVELS --jobs "$JOBS" \
    --order-from runs/rule-levels/kbgen-results/rule-levels/summary.csv \
    --work-dir "$HERE/kbgen-work" --results-dir "$HERE/kbgen-results"
status=$?
log "kbgen exit $status"

# kbgen must have been given exactly the rules our copy used at each level.
"$PYTHON" - "$LEVELS" <<'EOF' | tee -a "$HERE/steps.log"
import csv, json, sys
bad = 0
for level in sys.argv[1].split():
    copy = f"exp_datasets/family_kbgen_{level}/family_kbgen_{level}.stats.json"
    used = f"exp_datasets/family_ours_{level}/family_ours_{level}_used.csv"
    given = set(json.load(open(copy))["given_rule_ids"])
    ours = {row["rule_id"] for row in csv.DictReader(open(used))}
    ok = given == ours and not given & {"43", "45", "50"} - ours
    bad += not ok
    print(f"{level}: kbgen given {len(given)} rules, ours used {len(ours)}: {'same' if ok else 'DIFFERENT'}")
sys.exit(bad)
EOF
log "rule check exit $?"

"$PYTHON" scripts/check_kbgen_levels.py --rules-from-ours --datasets family --levels $LEVELS \
    --summary "$HERE/kbgen-results/rule-levels/summary.csv" | tee -a "$HERE/steps.log"
log "check_kbgen exit ${PIPESTATUS[0]}"

if [ -d ../VANILLA/Validated_KG_Completion ]; then
    "$PYTHON" scripts/split_vanilla.py $(for l in $LEVELS; do echo "exp_datasets/family_kbgen_$l/family_kbgen_$l.tsv"; done)
    log "split exit $?"
else
    log "no ../VANILLA/Validated_KG_Completion: split the 5 copies on the training machine"
fi
log "end"
exit "$status"
