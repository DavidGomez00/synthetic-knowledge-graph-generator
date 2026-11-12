# syntax=docker/dockerfile:1
#
# Self-contained image for training the KGE benchmark (and running the other
# experiments): Python 3.10.22 built by pyenv, the packages pinned in
# requirements.txt (PyTorch from PyPI, built for CUDA 13.0), Java and AMIE3, and
# a clone of the repository, copied at the first start into the volume
# /workspace. Started without arguments, it updates the clone, downloads the runs
# other machines finished, trains with scripts/train_kge.py --upload, pushes each
# finished run to the kge-results branch with the token in GITHUB_TOKEN, and then
# stays up idle. See docs/docker.md.
#
# The build needs no files from the host:
#   docker build -t skgg https://github.com/DavidGomez00/synthetic-knowledge-graph-generator.git#main

ARG REPO_URL=https://github.com/DavidGomez00/synthetic-knowledge-graph-generator.git
ARG REPO_REF=main

# The repository at REPO_REF. BuildKit resolves the ref to a commit, so a new
# commit invalidates the cache from here on and an unchanged one reuses it.
FROM scratch AS repo
ARG REPO_URL
ARG REPO_REF
ADD --keep-git-dir=true ${REPO_URL}#${REPO_REF} /workspace

FROM ubuntu:24.04

ARG REPO_URL
ARG REPO_REF
ARG PYTHON_VERSION=3.10.22
ARG AMIE_VERSION=3.5.1
ARG AMIE_SHA256=b56292aca715de1cfd1c359e6d29930bede08326b1732bdead923d9aee59e1d4

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYENV_ROOT=/opt/pyenv \
    PATH=/opt/pyenv/shims:/opt/pyenv/bin:$PATH \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build dependencies of CPython (pyenv compiles it from source), Java 21 for
# AMIE3, git for the clone and the uploads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git \
        libbz2-dev libffi-dev liblzma-dev libncursesw5-dev libreadline-dev \
        libsqlite3-dev libssl-dev libxml2-dev libxmlsec1-dev tk-dev \
        uuid-dev xz-utils zlib1g-dev \
        openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/pyenv/pyenv.git "$PYENV_ROOT" \
    && pyenv install "$PYTHON_VERSION" \
    && pyenv global "$PYTHON_VERSION" \
    && pip install --upgrade pip setuptools wheel

# Only requirements.txt first, so a commit that leaves it unchanged reuses the
# installed packages (about 4 GB with the CUDA libraries).
COPY --from=repo /workspace/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# AMIE3 jar, untracked in git. run_amie.py (and mine_datasets.py through it)
# uses $AMIE_JAR as its default --jar.
RUN mkdir -p /opt/amie \
    && curl -fsSL -o /opt/amie/amie${AMIE_VERSION}.jar \
        "https://github.com/dig-team/amie/releases/download/v${AMIE_VERSION}/amie${AMIE_VERSION}.jar" \
    && echo "${AMIE_SHA256}  /opt/amie/amie${AMIE_VERSION}.jar" | sha256sum -c -

# The image's own user, uid 1000, in place of Ubuntu's default "ubuntu".
RUN userdel -r ubuntu && useradd -m -u 1000 -s /bin/bash skgg \
    && git config --system --add safe.directory '*'

# The clone stays in /opt/skgg/repo; the entrypoint copies it into /workspace, a
# volume, the first time the container starts with an empty one.
COPY --from=repo --chown=skgg:skgg /workspace /opt/skgg/repo
# Installed out of the clone, so the update at start never rewrites the running script.
RUN install -m 755 /opt/skgg/repo/docker/entrypoint.sh /usr/local/bin/skgg-entrypoint \
    && install -d -o skgg -g skgg /workspace

ENV AMIE_JAR=/opt/amie/amie${AMIE_VERSION}.jar \
    REPO_URL=${REPO_URL} \
    REPO_REF=${REPO_REF} \
    PYTHONPATH=/workspace/src \
    MPLBACKEND=Agg

USER skgg
WORKDIR /workspace
# Code, results and checkpoints. Without -v, Docker still keeps them in an
# anonymous volume that removing the container leaves behind.
VOLUME /workspace
ENTRYPOINT ["skgg-entrypoint"]
CMD ["--skip-models", "CompGCN"]
