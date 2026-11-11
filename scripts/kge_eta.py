"""Shows the progress of train_kge.py and estimates when it finishes.

Reads the progress.csv that train_kge.py rewrites after every epoch. The time left of
the run in training comes from its own seconds per epoch. For a pending run it comes
from the finished runs of the same model: their seconds per epoch per 1,000 training
triples, the median over the finished runs on at least MIN_TRIPLES training triples
(smaller datasets carry a fixed overhead per epoch), times the run's training triples
and epochs. Evaluation is left out: it took at most 5 seconds per finished run.

The YAGO3-10 datasets have 4 to 15 times the training triples of the largest finished
dataset, so their figures are extrapolated and the least reliable.

With --workers N, as given to train_kge.py, the time left is the serial time divided
by N, but never less than the longest single run left, which one worker trains alone.
It assumes each worker keeps its speed; with 4 workers on an RTX 3090 each ran at
about the speed of a single process.

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
        rows = [
            r
            for r in csv.DictReader(f)
            if not any(r["dataset"].startswith(p) for p in args.exclude)
        ]

    triples: dict[str, int] = {}
    for dataset in {r["dataset"] for r in rows}:
        path = args.root / dataset / "train"
        triples[dataset] = count_lines(path) if path.is_file() else 0

    rates: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        n = triples[r["dataset"]]
        if r["status"] == "finished" and n >= MIN_TRIPLES and r["train_seconds"]:
            per_epoch = float(r["train_seconds"]) / int(r["epoch"])
            rates[r["model"]].append(per_epoch / n * 1000)
    rate = {m: statistics.median(v) for m, v in rates.items()}

    now = datetime.now(timezone.utc)
    left: dict[str, float] = defaultdict(float)
    longest_run = 0.0
    counts: dict[str, int] = defaultdict(int)
    unknown: list[str] = []
    for r in rows:
        counts[r["status"]] += 1
        if r["status"] == "finished":
            continue
        epochs_left = int(r["num_epochs"]) - int(r["epoch"] or 0)
        epoch = int(r["epoch"] or 0)
        if epoch and r["train_seconds"]:
            per_epoch = float(r["train_seconds"]) / epoch
        elif r["model"] in rate:
            per_epoch = rate[r["model"]] * triples[r["dataset"]] / 1000
        else:
            unknown.append(f"{r['dataset']} {r['model']}")
            continue
        left[r["dataset"]] += per_epoch * epochs_left
        longest_run = max(longest_run, per_epoch * epochs_left)
        if r["status"] in ("training", "evaluating"):
            updated = datetime.fromisoformat(r["updated_at"])
            idle = (now - updated).total_seconds()
            print(
                f"Now: {r['dataset']} {r['model']}, epoch {epoch}/{r['num_epochs']}, "
                f"{per_epoch:.0f} s per epoch, {duration(per_epoch * epochs_left)} "
                f"left for this model."
            )
            if idle > 3 * per_epoch + 600:
                print(
                    f"  No update for {duration(idle)}: train_kge.py is probably not "
                    "running. Run the same command to resume."
                )

    total = sum(counts.values())
    print(
        f"\nRuns: {counts['finished']} of {total} finished, "
        f"{counts['training'] + counts['evaluating']} in progress, "
        f"{counts['pending']} pending, {counts['failed']} failed."
    )
    if left:
        print("\nTime left per dataset:")
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
