#!/usr/bin/env bash
# Makes the PCA-setting copies (cli/main.py --completeness pca) of the rule levels
# whose input differs from the std setting, in exp_datasets_pca/. Every other level
# gets the same rules with the same completeness in both settings, so its std copy in
# exp_datasets/ stands for it. The levels come from exp_datasets_pca/rule_levels.tsv
# (python -m skgg.cli.rule_levels --completeness pca).
#
# Every copy runs at once, each in its own generate_datasets process with its own
# GraphDB repository. A copy that already exists is skipped, so running the script
# again makes only the missing ones. Run it detached, so that it outlives the shell:
#
#   setsid nohup runs/rule-levels-pca/generate.sh > runs/rule-levels-pca/generate.out 2>&1 < /dev/null &
#
# Needs GraphDB: docker compose --profile graphdb up -d
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
cd "$REPO"

PYTHON=${PYTHON:-/home/david/.pyenv/versions/KG/bin/python}
OUT=$HERE/ours
# Largest first, so the longest runs start first.
JOBS=${JOBS:-"YAGO3-10_N_1:r25 YAGO3-10_N_1:r50 YAGO3-10_N_1:r75 YAGO3-10_N_1:r100
family:pca1 family:r25 family:r50 family:r75 family:r100
french:r25 french:r50 french:r75 french:r100
french1_1:r25 french1_1:r50 french1_1:r75 french1_1:r100"}

mkdir -p "$OUT"
log() { printf '%s\t%s\n' "$(date '+%F %T')" "$*" | tee -a "$HERE/steps.log"; }

until curl -sf -o /dev/null -u admin:rootpassword http://localhost:7200/rest/repositories; do
    log "waiting for GraphDB"
    sleep 10
done

# Written before the jobs start, so two jobs finishing at once can't both write it.
if [ ! -s "$OUT/times.tsv" ]; then
    printf 'dataset\tlevel\tcompleteness\trules_used\tsource_triples\ttriples\tseconds\tstarted\tfinished\tcommit\tlog\n' > "$OUT/times.tsv"
fi

log "start $(git rev-parse --short HEAD) $(echo $JOBS | wc -w) jobs"
declare -A PIDS
for job in $JOBS; do
    dataset=${job%%:*}
    level=${job##*:}
    "$PYTHON" -m skgg.cli.generate_datasets --completeness pca --datasets "$dataset" --levels "$level" \
        --log-dir "$OUT" --times "$OUT/times.tsv" > "$OUT/${dataset}_${level}.out" 2>&1 &
    PIDS[$job]=$!
done

failed=0
for job in $JOBS; do
    wait "${PIDS[$job]}"
    status=$?
    log "$job exit $status"
    [ "$status" -eq 0 ] || failed=$((failed + 1))
done
log "end: $failed failed"
exit $(( failed > 0 ))
