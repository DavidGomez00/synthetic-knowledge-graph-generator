#!/usr/bin/env python3
"""Mines Horn rules from every benchmark dataset with AMIE3.

For every folder directly under the root that has a
`<folder>.unified.tsv` file, runs
`run_amie.py` on that file and writes the rules to `<folder>/<folder>.csv`, with
AMIE's full output in `<log dir>/<folder>.log`.

A dataset that already has its CSV is skipped unless `--force` is given, so an
interrupted batch can be resumed. A dataset that fails doesn't stop the others, and
the script exits with status 1 once every dataset is done.

AMIE runs with its default thresholds. Options this script doesn't define are
passed to `run_amie.py`:

    python mine_rules/mine_datasets.py
    python mine_rules/mine_datasets.py --datasets french family --force
    python mine_rules/mine_datasets.py --minhc 0.05 --xmx 24G --timeout 7200
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

import run_amie

logger = logging.getLogger(__name__)


def triple_file(folder: Path) -> Path:
    return folder / f"{folder.name}.unified.tsv"


def rules_file(folder: Path) -> Path:
    return folder / f"{folder.name}.csv"


def find_datasets(root: Path, names: list[str] | None) -> list[Path]:
    """Returns the dataset folders to mine, sorted by name.

    Args:
        root: Folder holding one subfolder per dataset.
        names: Folder names to keep, or None to keep every dataset.

    Raises:
        ValueError: If a name is not a dataset under `root`, or `root` has no dataset.
    """
    datasets = {}
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        if triple_file(folder).is_file():
            datasets[folder.name] = folder
        else:
            logger.warning("Skipping %s: no %s.", folder, triple_file(folder).name)
    if not datasets:
        raise ValueError(f"No folder under {root} has a .unified.tsv file.")
    if names is None:
        return list(datasets.values())

    unknown = sorted(set(names) - set(datasets))
    if unknown:
        raise ValueError(f"Not datasets under {root}: {', '.join(unknown)}")
    return [folder for name, folder in datasets.items() if name in names]


def mine_dataset(folder: Path, log_dir: Path, amie_args: list[str]) -> bool:
    """Runs AMIE on one dataset.

    Returns:
        Whether AMIE finished without an error.
    """
    output = rules_file(folder)
    argv = [
        str(triple_file(folder)),
        *amie_args,
        "--output",
        str(output),
        "--log",
        str(log_dir / f"{folder.name}.log"),
    ]
    start = time.monotonic()
    status = run_amie.main(argv)
    elapsed = time.monotonic() - start
    if status != 0:
        logger.error("%s: failed after %.0f s.", folder.name, elapsed)
        return False

    if output.is_file():
        with output.open(newline="", encoding="utf-8") as f:
            count = sum(1 for _ in csv.DictReader(f))
        logger.info("%s: %d rules in %.0f s.", folder.name, count, elapsed)
    return True


def mine_all(
    root: Path,
    names: list[str] | None,
    force: bool,
    log_dir: Path,
    amie_args: list[str],
) -> list[str]:
    """Mines the datasets under `root`.

    Returns:
        The names of the datasets that failed.
    """
    datasets = find_datasets(root, names)
    failed = []
    for number, folder in enumerate(datasets, start=1):
        if rules_file(folder).exists() and not force:
            logger.info(
                "[%d/%d] %s: %s exists, skipping.",
                number,
                len(datasets),
                folder.name,
                rules_file(folder).name,
            )
            continue
        logger.info("[%d/%d] %s: mining.", number, len(datasets), folder.name)
        if not mine_dataset(folder, log_dir, amie_args):
            failed.append(folder.name)

    logger.info("%d of %d datasets ok.", len(datasets) - len(failed), len(datasets))
    if failed:
        logger.error("Failed datasets: %s", ", ".join(failed))
    return failed


def _parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Mine rules with AMIE3 from every <folder>/<folder>.unified.tsv "
        "and write them to <folder>/<folder>.csv, with AMIE's default thresholds. "
        "Other options are passed to run_amie.py (--minhc, --maxad, --xmx, "
        "--timeout, ...).",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="Folder holding one subfolder per dataset (default: %(default)s).",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        help="Only mine these dataset folders (default: all of them).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Mine the datasets that already have a rules CSV again.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs/amie"),
        help="Folder for each dataset's AMIE output (default: %(default)s).",
    )
    return parser.parse_known_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    args, passthrough = _parse_args()
    sys.exit(
        1
        if mine_all(args.root, args.datasets, args.force, args.log_dir, passthrough)
        else 0
    )
