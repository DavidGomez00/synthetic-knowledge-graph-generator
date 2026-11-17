"""Commits the finished runs of train_kge.py to the checked-out branch and pushes them.

Several machines train the benchmark, each on its own datasets, from checkouts of the
same branch. `exp_results/` is tracked by git (only `progress.csv`, which every
machine rewrites, is ignored), so each machine commits its finished runs to that
branch and pushes them, and a `git pull` brings in the runs of the others.

A run is uploaded once it has its `results.json`: its `<model>/` folder (the trained
model `trained_model.pkl`, `results.json`, the loss plot and PyKEEN's
`training_triples/`), `status/<model>.json`, its log `logs/<model>.log` and its
dataset's `settings.json`, `train`, `valid` and `test`. A run whose files git already
has unchanged is skipped. The runs that are not finished stay uncommitted, so
train_kge.py can resume them.

The runs go up in commits and pushes of at most BATCH_MB MB each, so a push neither
nears GitHub's 2 GB limit nor keeps losing the race with other machines' pushes. When
a push is refused because another machine pushed first, the branch is rebased on
theirs (`git pull --rebase --autostash`) and pushed again; machines train different
datasets, so their files don't collide. A commit whose push failed is pushed by the
next upload. An upload commits only the runs' files, whatever else is staged, and
refuses to push while the branch has unpushed commits that change files outside the
results folder, so it never publishes code by accident. A lock in the git folder
keeps the processes of one machine from uploading at once.

`train_kge.py --upload` calls `upload` after every finished run and at the end. Run
this script by hand to upload the runs finished before, or to retry after an error.

Needs only the Python standard library and git with push access to the remote.
"""

import argparse
import fcntl
import logging
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("upload_results")

REMOTE = "origin"
RESULTS_DIR = "exp_results"
LOCK_FILE = "upload_results.lock"
PUSH_ATTEMPTS = 5
# Most bytes of runs per commit and push. A push of a few hundred MB takes under
# a minute, so it rarely meets another machine's push and, if it does, its retry
# is short; GitHub refuses any push over 2 GB.
BATCH_MB = 200
DATASET_FILES = ("settings.json", "train", "valid", "test")
# Each run's own log, `<dataset>/logs/<model>.log` (train_kge.RUN_LOG_DIR).
RUN_LOG_DIR = "logs"
STATUS_DIR = "status"


def git(*args: str, cwd: Path) -> str:
    """Runs git and returns its output. Raises RuntimeError with git's message."""
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def repository_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=Path(__file__).parent))


@contextmanager
def upload_lock(root: Path, wait: bool = True) -> Iterator[bool]:
    """Holds the upload lock; yields whether it was taken. Without `wait`, it yields
    False at once if another process holds it."""
    git_dir = Path(
        git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
    )
    with (git_dir / LOCK_FILE).open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def current_branch(root: Path) -> str:
    """The checked-out branch. Raises RuntimeError if HEAD is detached."""
    done = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError("HEAD is detached: check out a branch to upload to.")
    return done.stdout.strip()


def finished_runs(results: Path) -> Iterator[tuple[str, str]]:
    """(dataset, model) of every run with its results file."""
    for path in sorted(results.glob("*/*/results.json")):
        yield path.parent.parent.name, path.parent.name


def run_paths(results: Path, dataset: str, model: str) -> list[Path]:
    """The files and folders of a run that exist, its dataset's files included."""
    folder = results / dataset
    paths = [folder / name for name in DATASET_FILES]
    paths += [
        folder / model,
        folder / STATUS_DIR / f"{model}.json",
        folder / RUN_LOG_DIR / f"{model}.log",
    ]
    return [p for p in paths if p.exists()]


def changed_paths(root: Path, results: Path) -> set[Path]:
    """The files under `results` that git doesn't have as they are: untracked or
    modified."""
    output = git(
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        str(results),
        cwd=root,
    )
    paths = set()
    entries = iter(output.split("\0"))
    for entry in entries:
        if not entry:
            continue
        if entry[0] in "RC":
            next(entries, None)  # A rename's source path follows its entry.
        paths.add(root / entry[3:])
    return paths


def pending_runs(root: Path, results: Path) -> list[tuple[str, str]]:
    """The finished runs that have a file git doesn't have as it is. A changed
    dataset file (settings.json or a split) makes every finished run of its
    dataset pending."""
    runs: set[tuple[str, str]] = set()
    datasets: set[str] = set()
    for path in changed_paths(root, results):
        parts = path.relative_to(results).parts
        if len(parts) < 2:
            continue
        if parts[1] in DATASET_FILES:
            datasets.add(parts[0])
        elif parts[1] in (STATUS_DIR, RUN_LOG_DIR):
            runs.add((parts[0], Path(parts[-1]).stem))
        else:
            runs.add((parts[0], parts[1]))
    return [
        (dataset, model)
        for dataset, model in finished_runs(results)
        if (dataset, model) in runs or dataset in datasets
    ]


def run_bytes(results: Path, dataset: str, model: str) -> int:
    """The size of a run's files."""
    total = 0
    for path in run_paths(results, dataset, model):
        files = path.rglob("*") if path.is_dir() else [path]
        total += sum(p.stat().st_size for p in files if p.is_file())
    return total


def batches(
    results: Path, runs: list[tuple[str, str]], batch_bytes: int
) -> Iterator[list[tuple[str, str]]]:
    """`runs` in order, grouped into batches of at most `batch_bytes` (a larger
    run makes a batch of its own)."""
    batch: list[tuple[str, str]] = []
    size = 0
    for run in runs:
        run_size = run_bytes(results, *run)
        if batch and size + run_size > batch_bytes:
            yield batch
            batch, size = [], 0
        batch.append(run)
        size += run_size
    if batch:
        yield batch


def identity_args(root: Path, machine: str) -> list[str]:
    """`-c user.name/email` for a machine whose git has no identity set."""
    done = subprocess.run(
        ["git", "config", "user.email"], cwd=root, capture_output=True, text=True
    )
    if done.returncode == 0 and done.stdout.strip():
        return []
    return [
        "-c",
        f"user.name=train_kge on {machine}",
        "-c",
        f"user.email=train_kge@{machine}",
    ]


def unpushed_commits(root: Path, remote: str, branch: str, results: Path) -> int:
    """How many commits the branch has that its remote lacks, as after a failed push.

    Raises RuntimeError if any of them changes a file outside `results`: those are
    code commits, which an upload must not push.
    """
    remote_ref = f"refs/remotes/{remote}/{branch}"
    if not git("ls-remote", "--heads", remote, branch, cwd=root):
        raise RuntimeError(f"{remote} has no branch {branch}: push it first.")
    git("fetch", "-q", remote, f"+refs/heads/{branch}:{remote_ref}", cwd=root)
    count = int(git("rev-list", "--count", f"{remote_ref}..HEAD", cwd=root))
    if count:
        changed = git("diff", "--name-only", f"{remote_ref}...HEAD", cwd=root)
        prefix = results.relative_to(root).as_posix() + "/"
        code = [f for f in changed.splitlines() if not f.startswith(prefix)]
        if code:
            raise RuntimeError(
                f"{branch} has {count} unpushed commits that change files outside "
                f"{prefix} ({', '.join(code[:5])}{', ...' if len(code) > 5 else ''}): "
                "push or remove them before uploading results."
            )
    return count


def push(root: Path, remote: str, branch: str) -> None:
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            git("push", "-q", remote, f"{branch}:refs/heads/{branch}", cwd=root)
            return
        except RuntimeError:
            if attempt == PUSH_ATTEMPTS:
                raise
            # Another machine pushed first: put these commits on top of theirs.
            try:
                git("pull", "--rebase", "--autostash", "-q", remote, branch, cwd=root)
            except RuntimeError:
                # A conflict, as when two machines trained the same run: leave the
                # checkout as it was, so training and later uploads go on.
                subprocess.run(
                    ["git", "rebase", "--abort"], cwd=root, capture_output=True
                )
                raise


def upload(
    results: Path = Path(RESULTS_DIR),
    remote: str = REMOTE,
    machine: str | None = None,
    batch_mb: int = BATCH_MB,
    wait: bool = True,
) -> int:
    """
    Commits the finished runs that git lacks to the checked-out branch, pushes them
    and returns how many, in commits and pushes of at most `batch_mb` MB of runs
    each. The batches pushed before an error stay uploaded; the next upload sends
    the rest, and first pushes a commit whose push failed.

    Without `wait`, it returns 0 at once if another process of this machine is
    uploading. Every upload sends every finished run git lacks, so a run that
    finished meanwhile goes with a later upload: train_kge.py's workers upload this
    way after each run, instead of queuing for the lock.
    """
    machine = machine or socket.gethostname()
    root = repository_root()
    results = results.resolve()
    if not results.is_relative_to(root):
        raise RuntimeError(f"{results} is outside the repository {root}.")
    with upload_lock(root, wait) as locked:
        if not locked:
            logger.debug(
                "Another process is uploading; a later upload sends these runs."
            )
            return 0
        branch = current_branch(root)
        if unpushed_commits(root, remote, branch, results):
            push(root, remote, branch)
            logger.info("Pushed the runs a failed push had left.")
        new = pending_runs(root, results)
        if not new:
            return 0
        groups = list(batches(results, new, batch_mb * 1024 * 1024))
        uploaded = 0
        for number, batch in enumerate(groups, start=1):
            paths = [
                str(path.relative_to(root))
                for run in batch
                for path in run_paths(results, *run)
            ]
            names = ", ".join(f"{d}/{m}" for d, m in batch)
            git("add", "-A", "--", *paths, cwd=root)
            # With paths, the commit takes only these, and leaves anything else
            # that is staged for a later commit.
            git(
                *identity_args(root, machine),
                "commit",
                "-q",
                "-m",
                f"Add {len(batch)} runs trained on {machine}\n\n{names}",
                "--",
                *paths,
                cwd=root,
            )
            push(root, remote, branch)
            uploaded += len(batch)
            logger.info(
                "Batch %d/%d: uploaded %d runs to %s/%s (%d of %d): %s.",
                number,
                len(groups),
                len(batch),
                remote,
                branch,
                uploaded,
                len(new),
                names,
            )
        return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--results", type=Path, default=Path(RESULTS_DIR))
    parser.add_argument("--remote", default=REMOTE)
    parser.add_argument(
        "--batch-mb",
        type=int,
        default=BATCH_MB,
        help="most MB of runs per commit and push (default: %(default)s)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        count = upload(args.results, args.remote, batch_mb=args.batch_mb)
        if not count:
            logger.info("Nothing to upload: git has every finished run.")
    except RuntimeError as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
