"""Merges the train, valid and test splits of each dataset folder into one .tsv file.

For every folder directly under the root (default `exp_datasets`), writes
`<folder>/<folder>.unified.tsv` with the triples of `train`, `valid` and `test`, in
that order, keeping the first occurrence of a repeated triple. Line endings are
normalized to LF. A first line reading `0 1 2` is a saved column header, and is
skipped. Files already in the folder are left alone.

Also checks that train and test share no triple. A dataset that fails the check
still gets its file, and the script exits with status 1 once every dataset is done.

Only needs the Python standard library.
"""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SPLITS = ("train", "valid", "test")
MAX_LOGGED_TRIPLES = 10
# Column names of a split saved with its header, found as the first line of some.
HEADER_ROW = ["0", "1", "2"]

Triple = tuple[str, str, str]


def read_split(path: Path) -> list[Triple]:
    """Returns the triples of a split file, skipping blank lines and a header
    row on the first line.

    Raises:
        ValueError: If a line doesn't have three non-empty tab-separated fields.
    """
    triples: list[Triple] = []
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if number == 1 and fields == HEADER_ROW:
                logger.warning("Skipping the header row of %s.", path)
                continue
            if len(fields) != 3 or not all(fields):
                raise ValueError(f"{path}:{number} is not a triple: {line!r}")
            triples.append((fields[0], fields[1], fields[2]))
    return triples


def merge_dataset(folder: Path) -> set[Triple]:
    """Writes the unified .tsv file of a dataset folder.

    Returns:
        The triples that train and test share.
    """
    splits = {name: read_split(folder / name) for name in SPLITS}
    shared = set(splits["train"]) & set(splits["test"])

    total = sum(len(triples) for triples in splits.values())
    unified = dict.fromkeys(triple for name in SPLITS for triple in splits[name])

    output = folder / f"{folder.name}.unified.tsv"
    with output.open("w", encoding="utf-8", newline="\n") as out:
        out.writelines("\t".join(triple) + "\n" for triple in unified)

    logger.info(
        "%s: wrote %d triples to %s (train %d, valid %d, test %d, %d duplicates "
        "dropped).",
        folder.name,
        len(unified),
        output,
        len(splits["train"]),
        len(splits["valid"]),
        len(splits["test"]),
        total - len(unified),
    )
    if shared:
        logger.error(
            "%s: train and test share %d triples, e.g.: %s",
            folder.name,
            len(shared),
            sorted(shared)[:MAX_LOGGED_TRIPLES],
        )
    return shared


def merge_all(root: Path) -> list[str]:
    """Merges every dataset folder directly under `root`, skipping (with a
    warning) the folders without all three splits.

    Returns:
        The names of the datasets whose train and test share triples.

    Raises:
        ValueError: If no folder under `root` holds the three splits.
    """
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    datasets = []
    for folder in folders:
        missing = [name for name in SPLITS if not (folder / name).is_file()]
        if missing:
            logger.warning("Skipping %s: no %s file.", folder, ", ".join(missing))
        else:
            datasets.append(folder)
    if not datasets:
        raise ValueError(f"No folder under {root} has {', '.join(SPLITS)} files.")

    overlapping = [folder.name for folder in datasets if merge_dataset(folder)]
    logger.info(
        "Merged %d datasets; train and test are disjoint in %d.",
        len(datasets),
        len(datasets) - len(overlapping),
    )
    if overlapping:
        logger.error(
            "Train and test share triples in %d datasets: %s",
            len(overlapping),
            ", ".join(overlapping),
        )
    return overlapping


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge the train, valid and test files of each dataset folder "
        "into <folder>/<folder>.unified.tsv and check that train and test share no "
        "triple."
    )
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path("exp_datasets"),
        help="Folder holding one subfolder per dataset (default: %(default)s).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    sys.exit(1 if merge_all(_parse_args().root) else 0)
