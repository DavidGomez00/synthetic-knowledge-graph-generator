"""Estimates how long tune_kge.py takes on this machine, from a short calibration run.

Run a few trials of tune_kge.py on one small and one medium dataset first, writing to a
throwaway folder (docs/kge_tuning.md, "Estimating the run time", gives the commands).
This script reads the trial durations of that run from its `trials.tsv` files and
measures, for each model, the seconds a trial takes per 1,000 training triples. It
then scales that rate by the number of training triples of every dataset of a set:

- the search of a dataset: rate x training triples x trials, per model;
- the final run of a dataset: one trial's worth, rate x training triples, per model.

Trial time grows with the number of training triples, the main cost, but also with
the number of entities, so the YAGO figures, extrapolated from smaller graphs, are the
least reliable. The first trials of a study are pruned less than later ones, so the
estimate tends to be high.

Needs only the Python standard library and tune_kge's SEARCHES and SEARCH_TRIALS.
"""

import argparse
import csv
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from tune_kge import NO_COMPGCN, SEARCH_TRIALS, SEARCHES, TRIALS

DURATION = re.compile(r"(?:(\d+) days )?(\d+):(\d+):([\d.]+)")


def seconds(duration: str) -> float | None:
    """Seconds of a pandas Timedelta string such as `0 days 00:01:02.5`."""
    match = DURATION.fullmatch(duration.strip())
    if not match:
        return None
    days, hours, minutes, secs = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(secs)


def count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def train_triples(root: Path, dataset: str) -> int | None:
    path = root / dataset / "train"
    return count_lines(path) if path.is_file() else None


def calibration_rates(calibration: Path, root: Path) -> dict[str, list[float]]:
    """Seconds per trial and per 1,000 training triples, for each model."""
    rates: dict[str, list[float]] = defaultdict(list)
    for path in sorted(calibration.glob("*/*/trials.tsv")):
        model, dataset = path.parent.name, path.parent.parent.name
        triples = train_triples(root, dataset)
        if not triples:
            print(f"Skipping {path}: no {root / dataset / 'train'}.", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row["state"] not in ("COMPLETE", "PRUNED"):
                    continue
                duration = seconds(row.get("duration") or "")
                if duration is not None:
                    rates[model].append(duration / triples * 1000)
    return rates


def hours(value: float) -> str:
    return f"{value / 3600:.1f} h" if value < 72 * 3600 else f"{value / 86400:.1f} d"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "calibration",
        type=Path,
        help="the --hpo-output folder of the calibration run",
    )
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument(
        "--suffix",
        default="",
        help="suffix of the set's dataset names, e.g. _ours (default: the base set)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="models to estimate (default: those of the calibration run)",
    )
    parser.add_argument("--trials", type=int, help="trials per study, as in tune_kge")
    args = parser.parse_args()

    rates = calibration_rates(args.calibration, args.root)
    models = args.models or sorted(rates)
    missing = [m for m in models if not rates.get(m)]
    if missing:
        parser.error(f"No calibration trials for {', '.join(missing)}.")
    rate = {m: statistics.mean(rates[m]) for m in models}
    print("Seconds per trial per 1,000 training triples (mean, trials measured):")
    for m in models:
        print(f"  {m:8} {rate[m]:7.2f}  ({len(rates[m])} trials)")

    header = ("search dataset", "triples", "trials", "search", "final")
    print("\n" + f"{header[0]:22}" + "".join(f"{h:>10}" for h in header[1:]))
    total_search = total_final = 0.0
    for search, trained in SEARCHES.items():
        name = search + args.suffix
        triples = train_triples(args.root, name) or train_triples(args.root, search)
        if triples is None:
            print(f"  {name}: no train split, skipped", file=sys.stderr)
            continue
        trials = args.trials or SEARCH_TRIALS.get(search, TRIALS)
        search_models = [
            m for m in models if not (m == "CompGCN" and search in NO_COMPGCN)
        ]
        search_time = sum(rate[m] for m in search_models) * triples / 1000 * trials
        final_time = 0.0
        for dataset in trained:
            n = train_triples(args.root, dataset + args.suffix)
            n = n or train_triples(args.root, dataset) or triples
            final_time += sum(rate[m] for m in search_models) * n / 1000
        total_search += search_time
        total_final += final_time
        print(
            f"{name:22}{triples:>10,}{trials:>10}"
            f"{hours(search_time):>10}{hours(final_time):>10}"
        )
    print(
        f"\nSearch {hours(total_search)}, final runs {hours(total_final)}, "
        f"total {hours(total_search + total_final)} for this set."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
