#!/usr/bin/env bash
# Entrypoint of the Docker image (see docs/docker.md).
#
# 1. Copies the image's clone (/opt/skgg/repo) into /workspace if it is empty, as on
#    the first start with a new volume.
# 2. Pushes go through GITHUB_TOKEN, read from the environment when git needs it.
# 3. Updates /workspace to the latest commit of REPO_REF (SKGG_UPDATE=0 skips it).
# 4. Copies the runs already on the kge-results branch into exp_results/, so they
#    are not trained again (SKGG_DOWNLOAD=0 skips it).
# 5. Runs the command. Arguments starting with "-" go to
#    `python scripts/train_kge.py --upload`, and the container then stays up idle
#    (SKGG_IDLE=0 exits instead), so `--restart unless-stopped` restarts it after a
#    reboot without training again once it is done. Anything else runs as given.
set -euo pipefail

log() { echo "[skgg-entrypoint] $*" >&2; }

cd /workspace
if [[ ! -e .git ]]; then
    if [[ -n "$(ls -A)" ]]; then
        log "/workspace is not empty and holds no copy of the repository; refusing to write to it."
        exit 1
    fi
    if [[ ! -w . ]]; then
        log "/workspace is not writable by uid $(id -u); give the volume or folder to uid 1000."
        exit 1
    fi
    log "Copying the repository into /workspace."
    cp -a /opt/skgg/repo/. /workspace/
fi

git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"
# Keep only the configured remote branch, so the shallow fetch below stays small.
git config remote.origin.fetch "+refs/heads/$REPO_REF:refs/remotes/origin/$REPO_REF"

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    # The helper prints the token when git asks for it; nothing is written to disk.
    git config --global credential.helper \
        '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
else
    log "GITHUB_TOKEN is not set: finished runs cannot be pushed to the kge-results branch."
fi

if [[ "${SKGG_UPDATE:-1}" == 1 ]]; then
    if git fetch -q --depth 1 origin "$REPO_REF"; then
        # Tracked files only: results, checkpoints and logs are ignored by git.
        git reset -q --hard FETCH_HEAD
        log "Code at $(git log -1 --format='%h %s')."
    else
        log "Could not fetch $REPO_REF; keeping the code at $(git log -1 --format=%h)."
    fi
fi

if [[ "${SKGG_DOWNLOAD:-1}" == 1 ]]; then
    python scripts/upload_results.py --download \
        || log "No runs downloaded from the kge-results branch (see above)."
fi

if [[ $# -gt 0 && "$1" != -* ]]; then
    log "Running: $*"
    exec "$@"
fi

set -- python scripts/train_kge.py --upload "$@"
log "Running: $*"
# In the background, so a docker stop (SIGTERM) reaches train_kge.py, which stops
# cleanly, and this script exits with it instead of idling.
"$@" &
pid=$!
stopping=0
trap 'stopping=1; kill -TERM "$pid" 2>/dev/null || true' TERM INT
code=0
wait "$pid" || code=$?
while kill -0 "$pid" 2>/dev/null; do
    code=0
    wait "$pid" || code=$?
done
trap - TERM INT
if [[ "$stopping" == 1 || "${SKGG_IDLE:-1}" != 1 ]]; then
    exit "$code"
fi
if [[ "$code" == 0 ]]; then
    log "Training finished. Idle until stopped (docker stop); results are in exp_results/."
else
    log "Training ended with status $code (see above). Idle until stopped; restart the container to retry the failed runs."
fi
trap 'exit 0' TERM INT
sleep infinity &
wait $!
