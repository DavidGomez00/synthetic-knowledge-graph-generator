"""Uploads the finished runs of train_kge.py to a git branch, or downloads them.

Several machines train the benchmark, each on its own datasets. Each uploads its
finished runs to the branch `kge-results` (BRANCH) of the repository's remote, and
`--download` copies every run of that branch into the local results folder, so the
results of every machine end up in one `exp_results/`.

The branch holds only results, in the layout of `exp_results/`: for each dataset
`exp_results/<dataset>/` its `settings.json`, the `train`, `valid` and `test` files
that train_kge.py wrote for it, and for each finished run `status/<model>.json` and the
`<model>/` folder (the trained model `trained_model.pkl`, `results.json`, the loss
plot and PyKEEN's `training_triples/`). `progress.csv` is left out, as every machine
rewrites it; `--download` rebuilds it. A run is uploaded once: one that the branch
already has is skipped, even if another machine trained it.

The branch is checked out in a git worktree, `.kge-results/` (WORKTREE) at the root of
the repository, and is created on the remote by the first upload. A lock on the
worktree keeps the processes of one machine from using it at once. When the push is
refused because another machine pushed first, the commit is rebased on theirs and
pushed again; machines train different datasets, so their files don't collide.

`train_kge.py --upload` calls `upload` after every finished run and at the end. Run
this script by hand to upload the runs finished before, or to retry after an error.

Needs only the Python standard library and git with push access to the remote.
"""

import argparse
import fcntl
import logging
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("upload_results")

BRANCH = "kge-results"
REMOTE = "origin"
WORKTREE = ".kge-results"
RESULTS_DIR = "exp_results"
PUSH_ATTEMPTS = 5
# Most bytes of runs per commit and push. A push of a few hundred MB takes under
# a minute, so it rarely meets another machine's push and, if it does, its retry
# is short; GitHub refuses any push over 2 GB.
BATCH_MB = 200
DATASET_FILES = ("settings.json", "train", "valid", "test")


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
def worktree_lock(root: Path) -> Iterator[None]:
    with (root / f"{WORKTREE}.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def remote_has_branch(root: Path, remote: str, branch: str) -> bool:
    return bool(git("ls-remote", "--heads", remote, branch, cwd=root))


def open_worktree(root: Path, remote: str, branch: str) -> Path:
    """Returns the worktree of the branch, creating it, and brings it up to date."""
    worktree = root / WORKTREE
    exists = remote_has_branch(root, remote, branch)
    if not (worktree / ".git").exists():
        if exists:
            git("fetch", remote, f"{branch}:refs/remotes/{remote}/{branch}", cwd=root)
            git(
                "worktree",
                "add",
                "-B",
                branch,
                str(worktree),
                f"{remote}/{branch}",
                cwd=root,
            )
        else:
            # An orphan branch, so it shares no files with the code branches. No
            # checkout, which would write the whole repository to the worktree.
            git("worktree", "add", "--no-checkout", "--detach", str(worktree), cwd=root)
            git("checkout", "--orphan", branch, cwd=worktree)
            git("rm", "-rq", "--cached", "--ignore-unmatch", ".", cwd=worktree)
        logger.info("Checked out %s in %s.", branch, worktree)
    else:
        # Files left by an upload that stopped before its commit.
        git("clean", "-fdxq", cwd=worktree)
        if exists:
            git("pull", "--rebase", "-q", remote, branch, cwd=worktree)
    return worktree


def uploaded_runs(worktree: Path) -> set[tuple[str, str]]:
    """(dataset, model) of every run committed to the branch."""
    files = git("ls-files", f"{RESULTS_DIR}/*/*/results.json", cwd=worktree)
    return {(f.split("/")[1], f.split("/")[2]) for f in files.splitlines()}


def has_commits(worktree: Path) -> bool:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def unpushed(worktree: Path, remote: str, branch: str) -> bool:
    """Whether the branch has commits its remote lacks, as after a failed push."""
    if not has_commits(worktree):
        return False
    if not remote_has_branch(worktree, remote, branch):
        return True
    git("fetch", "-q", remote, f"{branch}:refs/remotes/{remote}/{branch}", cwd=worktree)
    return git("rev-list", "--count", f"{remote}/{branch}..HEAD", cwd=worktree) != "0"


def push(worktree: Path, remote: str, branch: str) -> None:
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            git("push", "-q", "-u", remote, branch, cwd=worktree)
            return
        except RuntimeError:
            if attempt == PUSH_ATTEMPTS:
                raise
            # Another machine pushed first: put this commit on top of theirs.
            git("pull", "--rebase", "-q", remote, branch, cwd=worktree)


def finished_runs(results: Path) -> Iterator[tuple[str, str]]:
    """(dataset, model) of every run with its results file."""
    for path in sorted(results.glob("*/*/results.json")):
        yield path.parent.parent.name, path.parent.name


def copy_run(results: Path, target: Path, dataset: str, model: str) -> None:
    """Copies a run, and its dataset's files if the target lacks them."""
    source, destination = results / dataset, target / dataset
    destination.mkdir(parents=True, exist_ok=True)
    for name in DATASET_FILES:
        if (source / name).is_file() and not (destination / name).exists():
            shutil.copy2(source / name, destination / name)
    status = source / "status" / f"{model}.json"
    if status.is_file():
        (destination / "status").mkdir(exist_ok=True)
        shutil.copy2(status, destination / "status" / status.name)
    shutil.copytree(source / model, destination / model, dirs_exist_ok=True)


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


def run_bytes(results: Path, dataset: str, model: str) -> int:
    """The size of a run's files: its model folder and its status file."""
    files = [p for p in (results / dataset / model).rglob("*") if p.is_file()]
    files.append(results / dataset / "status" / f"{model}.json")
    return sum(p.stat().st_size for p in files if p.is_file())


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


def upload(
    results: Path = Path(RESULTS_DIR),
    remote: str = REMOTE,
    branch: str = BRANCH,
    machine: str | None = None,
    batch_mb: int = BATCH_MB,
) -> int:
    """
    Uploads the finished runs the branch lacks and returns how many, in
    commits and pushes of at most `batch_mb` MB of runs each, so a large
    upload neither nears GitHub's 2 GB limit per push nor keeps losing the
    race with other machines' pushes. The batches pushed before an error stay
    uploaded; the next upload sends the rest.
    """
    machine = machine or socket.gethostname()
    results = results.resolve()
    root = repository_root()
    with worktree_lock(root):
        worktree = open_worktree(root, remote, branch)
        target = worktree / RESULTS_DIR
        done = uploaded_runs(worktree)
        new = [run for run in finished_runs(results) if run not in done]
        if not new:
            if unpushed(worktree, remote, branch):
                push(worktree, remote, branch)
                logger.info("Pushed the runs a failed push had left.")
            return 0
        # A commit left by a failed push goes first, so batches stack on it.
        if unpushed(worktree, remote, branch):
            push(worktree, remote, branch)
        groups = list(batches(results, new, batch_mb * 1024 * 1024))
        uploaded = 0
        for number, batch in enumerate(groups, start=1):
            for dataset, model in batch:
                copy_run(results, target, dataset, model)
            names = ", ".join(f"{d}/{m}" for d, m in batch)
            # -f: the code branches' .gitignore lists exp_results.
            git("add", "-A", "-f", RESULTS_DIR, cwd=worktree)
            git(
                *identity_args(root, machine),
                "commit",
                "-q",
                "-m",
                f"Add {len(batch)} runs trained on {machine}\n\n{names}",
                cwd=worktree,
            )
            push(worktree, remote, branch)
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


def download(
    results: Path = Path(RESULTS_DIR), remote: str = REMOTE, branch: str = BRANCH
) -> int:
    """Copies the runs of the branch that `results` lacks; returns how many."""
    root = repository_root()
    with worktree_lock(root):
        if not remote_has_branch(root, remote, branch):
            raise RuntimeError(
                f"{remote} has no branch {branch}: nothing uploaded yet."
            )
        worktree = open_worktree(root, remote, branch)
        source = worktree / RESULTS_DIR
        copied = 0
        for dataset, model in finished_runs(source):
            if (results / dataset / model / "results.json").is_file():
                continue
            settings = results / dataset / "settings.json"
            if (
                settings.is_file()
                and settings.read_bytes()
                != (source / dataset / "settings.json").read_bytes()
            ):
                logger.warning(
                    "%s/%s skipped: its settings.json differs from the local one.",
                    dataset,
                    model,
                )
                continue
            copy_run(source, results, dataset, model)
            copied += 1
    if copied:
        # train_kge.py imports PyKEEN and PyTorch, so it is only imported here.
        from train_kge import write_progress

        write_progress(results)
    logger.info("Copied %d runs from %s/%s into %s.", copied, remote, branch, results)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--download",
        action="store_true",
        help="copy the branch's runs into --results instead of uploading",
    )
    parser.add_argument("--results", type=Path, default=Path(RESULTS_DIR))
    parser.add_argument("--remote", default=REMOTE)
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument(
        "--batch-mb",
        type=int,
        default=BATCH_MB,
        help="most MB of runs per commit and push (default: %(default)s)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.download:
            download(args.results, args.remote, args.branch)
        else:
            count = upload(
                args.results, args.remote, args.branch, batch_mb=args.batch_mb
            )
            if not count:
                logger.info("Nothing to upload: the branch has every finished run.")
    except RuntimeError as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
