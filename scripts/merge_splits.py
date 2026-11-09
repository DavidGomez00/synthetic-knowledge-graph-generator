"""Merges the train, valid and test splits of each dataset folder into one .tsv file.

For every folder directly under the root (default `exp_datasets`), writes
`<folder>/<folder>.unified.tsv` with the triples of `train`, `valid` and `test`, in
that order, keeping the first occurrence of a repeated triple. Line endings are
normalized to LF. A first line reading `0 1 2` is a saved column header, and is
skipped, a term wrapped as `<http:DATASET/name>` is written as `name`, and
`http://YAGO3.org/name` is written as `name`. A `subject predicate object` row is a
header left in the data, and is skipped on any line. If the folder has an
`id_to_name.json` file, its integer IDs are replaced by names. The `type` triples of
a folder whose name starts with `french` are left out. Files already in the folder
are left alone.

Also checks that train and test share no triple. A dataset that fails the check
still gets its file, and the script exits with status 1 once every dataset is done.

Only needs the Python standard library.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SPLITS = ("train", "valid", "test")
MAX_LOGGED_TRIPLES = 10
# Column names of a split saved with its header, found as the first line of some.
HEADER_ROW = ["0", "1", "2"]
# A header row left in the data, on line 119225 of YAGO3-10_1_1/train.
NAMED_HEADER_ROW = ["subject", "predicate", "object"]
# Names of a dataset's integer IDs: {"entity_ids": {id: name}, "relation_ids": ...}.
ID_NAMES_FILE = "id_to_name.json"
# The french datasets type every person and relation, which is not part of the graph
# that rules are mined from.
TYPED_DATASET_PREFIX = "french"
TYPE_RELATION = "type"
# A term wrapped in a placeholder namespace, e.g. `<http:WN18RR/_hypernym>`. A real
# IRI (`<http://...>`) does not match.
WRAPPED_TERM = re.compile(r"<http:[^/>]+/(.+)>")
# Namespace of the IRIs in YAGO3-10_N_M. Only this exact prefix is removed: the rest
# of a term can hold more `/` (`de/SC_Leipzig`), and cutting it to its last segment
# would merge different entities.
YAGO_NAMESPACE = "http://YAGO3.org/"

Triple = tuple[str, str, str]
IdNames = tuple[dict[str, str], dict[str, str]]


def read_id_names(folder: Path) -> IdNames | None:
    """Returns the entity and relation names of a folder's IDs, if it has any."""
    path = folder / ID_NAMES_FILE
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        names = json.load(f)
    return names["entity_ids"], names["relation_ids"]


def _unwrap(term: str) -> str:
    """A term without its placeholder wrapper or YAGO namespace, if it has one."""
    match = WRAPPED_TERM.fullmatch(term)
    return match[1] if match else term.removeprefix(YAGO_NAMESPACE)


def read_split(path: Path, id_names: IdNames | None = None) -> list[Triple]:
    """Returns the triples of a split file, skipping blank lines and header rows,
    and removing the wrapper of wrapped terms.

    Args:
        path: The split file.
        id_names: Entity and relation names that replace the file's integer IDs.

    Raises:
        ValueError: If a line doesn't have three non-empty tab-separated fields.
        KeyError: If an ID has no name in `id_names`.
    """
    triples: list[Triple] = []
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if (number == 1 and fields == HEADER_ROW) or fields == NAMED_HEADER_ROW:
                logger.warning("Skipping the header row at %s:%d.", path, number)
                continue
            if len(fields) != 3 or not all(fields):
                raise ValueError(f"{path}:{number} is not a triple: {line!r}")
            subject, predicate, obj = (_unwrap(field) for field in fields)
            if id_names:
                entities, relations = id_names
                subject, predicate, obj = (
                    entities[subject],
                    relations[predicate],
                    entities[obj],
                )
            triples.append((subject, predicate, obj))
    return triples


def merge_dataset(folder: Path) -> set[Triple]:
    """Writes the unified .tsv file of a dataset folder.

    Returns:
        The triples that train and test share.
    """
    id_names = read_id_names(folder)
    splits = {name: read_split(folder / name, id_names) for name in SPLITS}
    if folder.name.startswith(TYPED_DATASET_PREFIX):
        splits = {
            name: [triple for triple in triples if triple[1] != TYPE_RELATION]
            for name, triples in splits.items()
        }
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
