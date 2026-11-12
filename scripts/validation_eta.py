"""Shows the progress of kgc_benchmark/report_results.py and estimates when it finishes.

Reads the files a validation keeps in its output folder (default exp_analysis/):
`progress.csv`, one row per run with its status (pending, ranking, ranked, failed or
left_out), its held-out triples and the seconds its ranking took, and
`validation_state.json`, the stage (ranking, analysis of a family, done or
interrupted) and the run being ranked.

The time left is the held-out triples still to rank divided by the speed of the
latest ranked runs (their median held-out triples per second over the last
RECENT_RUNS). Writing the families' tables and plots afterwards takes a few more
minutes, which the estimate leaves out.

The validation is running while a process holds the lock `.validation.lock` in the
output folder. Linux lists the held locks in /proc/locks, which this script reads
without taking any lock. Runs that train_kge.py finished after the validation
started are counted apart: running the validation again ranks just those.

Needs only the Python standard library.
"""

import argparse
import csv
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECENT_RUNS = 20
PROGRESS_NAME = "progress.csv"
STATE_NAME = "validation_state.json"
LOCK_NAME = ".validation.lock"
# As in train_kge.py: the suffixes of the folders of synthetic copies.
COPY_SETS = ("ours", "kbgen", "pygraft")


def held_locks() -> set[tuple[int, int, int]] | None:
    """(major, minor, inode) of every file locked by some process, None off Linux."""
    try:
        text = Path("/proc/locks").read_text(encoding="ascii")
    except OSError:
        return None
    held = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[1] == "->":
            continue
        try:
            major, minor, inode = fields[5].split(":")
            held.add((int(major, 16), int(minor, 16), int(inode)))
        except ValueError:
            continue
    return held


def is_running(lock: Path) -> bool | None:
    """Whether a process holds `lock`; None when that can't be told."""
    locks = held_locks()
    if locks is None or not lock.exists():
        return None
    stat = lock.stat()
    return (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino) in locks


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def run_label(dataset: str) -> str:
    """`french_kbgen` as `french/kbgen`, `french` as `french/original`."""
    for variant in COPY_SETS:
        if dataset.endswith(f"_{variant}"):
            return f"{dataset[: -len(variant) - 1]}/{variant}"
    return f"{dataset}/original"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("exp_analysis"),
        help="the --output folder of report_results.py (default: %(default)s)",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("exp_results"),
        help="train_kge.py's output, to count the runs finished since "
        "(default: %(default)s)",
    )
    args = parser.parse_args()

    progress_file = args.output / PROGRESS_NAME
    if not progress_file.is_file():
        parser.error(f"No {progress_file}: no validation has started there.")
    with progress_file.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    state_file = args.output / STATE_NAME
    state = (
        json.loads(state_file.read_text(encoding="utf-8"))
        if state_file.is_file()
        else {}
    )

    running = is_running(args.output / LOCK_NAME)
    stage = state.get("stage", "unknown")
    now = datetime.now(timezone.utc)
    if running is False:
        status = "stopped" if stage not in ("done",) else "finished"
        print(f"Validation {status} (last stage: {stage}).")
        if stage != "done":
            print("  Run the same command to resume: the runs ranked so far are kept.")
    else:
        print(
            f"Validation running, stage: {stage}"
            + (f", {state['run']}" if state.get("run") else "")
            + "."
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    order = ("ranked", "ranking", "pending", "failed", "left_out")
    print(
        f"\nRuns: {len(rows)}, "
        + ", ".join(f"{counts.get(s, 0)} {s.replace('_', ' ')}" for s in order)
        + "."
    )

    ranked = [r for r in rows if r["status"] == "ranked" and r["seconds"]]
    ranked.sort(key=lambda r: r["updated_at"])
    speeds = [
        int(r["held_out"]) / float(r["seconds"])
        for r in ranked[-RECENT_RUNS:]
        if float(r["seconds"]) > 0
    ]
    rate = statistics.median(speeds) if speeds else None
    left_rows = [r for r in rows if r["status"] in ("pending", "ranking")]
    left = sum(int(r["held_out"]) for r in left_rows)
    current = next((r for r in rows if r["status"] == "ranking"), None)
    if current is not None and running is not False:
        started = datetime.fromisoformat(current["updated_at"])
        elapsed = (now - started).total_seconds()
        line = (
            f"Now: {current['family']}/{current['variant']}/{current['model']}, "
            f"{int(current['held_out']):,} held-out triples, {duration(elapsed)} so far"
        )
        if rate:
            remaining = max(int(current["held_out"]) / rate - elapsed, 0)
            line += f", about {duration(remaining)} left"
        print(line + ".")
        left -= min(int(current["held_out"]), int(rate * elapsed) if rate else 0)

    if left_rows:
        print(f"Left to rank: {len(left_rows)} run(s), {left:,} held-out triples.")
        if rate:
            finish = now + timedelta(seconds=left / rate)
            print(
                f"Speed: {rate:.0f} held-out triples/s (median of the last "
                f"{len(speeds)} "
                f"ranked runs). Ranking left: {duration(left / rate)}, until about "
                f"{finish.astimezone():%Y-%m-%d %H:%M}, then the families' tables and "
                "plots."
            )
        else:
            print("No run ranked in this validation yet, so no estimate.")
    elif stage.startswith("analysis"):
        print("Every run is ranked; writing the families' tables and plots.")

    for status in ("failed", "left_out"):
        bad = [
            f"{r['family']}/{r['variant']}/{r['model']}"
            for r in rows
            if r["status"] == status
        ]
        if bad:
            print(f"{status.replace('_', ' ').capitalize()}: {', '.join(bad)}.")

    known = {(r["family"], r["variant"], r["model"]) for r in rows}
    families, variants = state.get("families"), state.get("variants")
    new = []
    for results_json in sorted(args.results.glob("*/*/results.json")):
        family, variant = run_label(results_json.parent.parent.name).split("/")
        if (families and family not in families) or (
            variants and variant not in variants
        ):
            continue
        if (family, variant, results_json.parent.name) not in known:
            new.append(f"{family}/{variant}/{results_json.parent.name}")
    if new:
        print(
            f"\n{len(new)} run(s) finished training after this validation started; "
            "run it "
            "again to rank them."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
