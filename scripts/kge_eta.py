"""Shows the progress of train_kge.py and estimates when it finishes.

Reads the progress.csv that train_kge.py rewrites after every epoch. The time left of
the run in training comes from its own seconds per epoch. For a pending run it comes
from the finished runs of the same model: their seconds per epoch per 1,000 training
triples, the median over the finished runs on at least MIN_TRIPLES training triples
(smaller datasets carry a fixed overhead per epoch), times the run's training triples
and epochs. Evaluation is left out: it took at most 5 seconds per finished run.

The YAGO3-10 datasets have 4 to 15 times the training triples of the largest finished
dataset, so their figures are extrapolated and the least reliable.

Runs sharing the GPU are slower than a run alone: with 4 workers on an RTX 3090 each
epoch took 1.4 to 1.8 times as long. So the pending estimates are scaled by how much
slower the runs in progress are than the finished runs predict (the median ratio),
which is about 1 for a single process. With --workers N, as given to train_kge.py,
the time left is then divided by N, but is never less than the longest single run
left, which one worker trains alone.

A run left in training by a stopped process counts as stopped, not in progress.

Needs only the Python standard library.
"""

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_TRIPLES = 20_000
# As in train_kge.py: the suffixes of the folders of synthetic copies.
COPY_SETS = ("ours", "kbgen", "pygraft")


def dataset_set(name: str) -> str:
    for copy_set in COPY_SETS:
        if name.endswith(f"_{copy_set}"):
            return copy_set
    return "base"


def count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def duration(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 72 * 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("exp_results"),
        help="the --output folder of train_kge.py (default: %(default)s)",
    )
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        help="leave out datasets whose name starts with one of these, e.g. YAGO",
    )
    parser.add_argument(
        "--set",
        dest="dataset_set",
        choices=("all", "base", *COPY_SETS),
        default="all",
        help="estimate only the base datasets or one set of copies; the speeds come "
        "from every finished run (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="the --workers of train_kge.py (default: %(default)s)",
    )
    args = parser.parse_args()

    progress = args.results / "progress.csv"
    if not progress.is_file():
        parser.error(f"No {progress}: train_kge.py has not started.")
    with progress.open(encoding="utf-8") as f:
        all_rows = [
            r
            for r in csv.DictReader(f)
            if not any(r["dataset"].startswith(p) for p in args.exclude)
        ]
    rows = [
        r
        for r in all_rows
        if args.dataset_set == "all" or dataset_set(r["dataset"]) == args.dataset_set
    ]

    triples: dict[str, int] = {}
    for dataset in {r["dataset"] for r in all_rows}:
        path = args.root / dataset / "train"
        triples[dataset] = count_lines(path) if path.is_file() else 0

    # Speeds come from every set: the copies train like the base datasets.
    rates: dict[str, list[float]] = defaultdict(list)
    early: dict[str, list[float]] = defaultdict(list)
    for r in all_rows:
        n = triples[r["dataset"]]
        epoch = int(r["epoch"] or 0)
        if n < MIN_TRIPLES or not epoch or not r["train_seconds"]:
            continue
        per_1000 = float(r["train_seconds"]) / epoch / n * 1000
        if r["status"] == "finished":
            rates[r["model"]].append(per_1000)
        elif r["status"] == "training":
            early[r["model"]].append(per_1000)
    rate = {m: statistics.median(v) for m, v in rates.items()}
    # Before a model has a finished run, as on a new machine, its runs in progress
    # give its speed. They already share the GPU, so no slowdown is measured then.
    from_progress = {m for m in early if m not in rate}
    rate.update({m: statistics.median(early[m]) for m in from_progress})

    def predicted(r: dict[str, str]) -> float | None:
        """Seconds per epoch the finished runs predict for a run."""
        if r["model"] not in rate:
            return None
        return rate[r["model"]] * triples[r["dataset"]] / 1000

    now = datetime.now(timezone.utc)
    active: list[dict[str, str]] = []
    stopped: list[dict[str, str]] = []
    ratios: list[float] = []
    for r in rows:
        if r["status"] not in ("training", "evaluating"):
            continue
        epoch = int(r["epoch"] or 0)
        if not r["updated_at"]:
            continue
        per_epoch = float(r["train_seconds"] or 0) / epoch if epoch else None
        idle = (now - datetime.fromisoformat(r["updated_at"])).total_seconds()
        if idle > 3 * (per_epoch or 600) + 600:
            stopped.append(r)
            continue
        active.append(r)
        expected = predicted(r)
        if per_epoch and expected and r["model"] not in from_progress:
            ratios.append(per_epoch / expected)
    slowdown = statistics.median(ratios) if ratios else 1.0

    left: dict[str, float] = defaultdict(float)
    longest_run = 0.0
    counts: dict[str, int] = defaultdict(int)
    unknown: list[str] = []
    for r in rows:
        counts[r["status"]] += 1
        if r["status"] == "finished":
            continue
        epoch = int(r["epoch"] or 0)
        epochs_left = int(r["num_epochs"]) - epoch
        expected = predicted(r)
        if r in active and epoch and r["train_seconds"]:
            per_epoch = float(r["train_seconds"]) / epoch
        elif expected is not None:
            per_epoch = expected * slowdown
        else:
            unknown.append(f"{r['dataset']} {r['model']}")
            continue
        left[r["dataset"]] += per_epoch * epochs_left
        longest_run = max(longest_run, per_epoch * epochs_left)
        if r in active:
            print(
                f"Now: {r['dataset']} {r['model']}, epoch {epoch}/{r['num_epochs']}, "
                f"{per_epoch:.0f} s per epoch, {duration(per_epoch * epochs_left)} "
                f"left for this model."
            )

    total = sum(counts.values())
    print(
        f"\nRuns: {counts['finished']} of {total} finished, {len(active)} in progress, "
        f"{len(stopped)} stopped partway, {counts['pending']} pending, "
        f"{counts['failed']} failed."
    )
    if stopped:
        names = ", ".join(f"{r['dataset']} {r['model']}" for r in stopped)
        print(
            f"Stopped partway, resumed when a worker reaches them: {names}. If no run "
            "is in progress, train_kge.py is not running: run the same command."
        )
    if ratios:
        print(
            f"Runs in progress take {slowdown:.2f} times the seconds per epoch of the "
            "finished runs; pending runs are estimated with that factor."
        )
    if left:
        print("\nTraining time left per dataset, before sharing it among workers:")
        for dataset, seconds in sorted(left.items(), key=lambda kv: triples[kv[0]]):
            print(f"  {dataset:16}{triples[dataset]:>11,} triples  {duration(seconds)}")
    remaining = sum(left.values())
    if args.workers > 1:
        remaining = max(remaining / args.workers, longest_run)
    finish = now + timedelta(seconds=remaining)
    print(
        f"\nTime left: {duration(remaining)}, finishing around "
        f"{finish.astimezone():%Y-%m-%d %H:%M} if it runs without stopping"
        + (f", with {args.workers} workers." if args.workers > 1 else ".")
    )
    if unknown:
        print(f"No estimate (no finished run of the model): {', '.join(unknown)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
