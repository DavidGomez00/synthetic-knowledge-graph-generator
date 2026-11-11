"""Splits the synthetic copies of the benchmark datasets into train, valid and test.

A copy is a folder `<dataset><suffix>/` under the root with one triples file,
`<dataset><suffix>.tsv` (`cli/generate_datasets.py` writes the `_ours` copies; the
`_kbgen` and `_pygraft` ones come from other generators). train_kge.py needs the
`train`, `valid` and `test` files of the benchmark datasets, so this script writes
them for each copy:

- the copy's triples, without repeats, are shuffled with a fixed seed;
- `test` and `valid` get the same share of the triples as in the source dataset
  (`<dataset>/test` and `<dataset>/valid`), and `train` the rest;
- a valid or test triple whose head, tail or relation is not in `train` is moved to
  `train`, because no model has an embedding for it. A copy can lose some valid and
  test triples this way, and the script reports how many.

Lines that are not three tab-separated terms are skipped with a warning. A copy that
already has its three splits is skipped unless --force is given.

Needs only the Python standard library and merge_splits.py.
"""

import argparse
import logging
import random
import sys
from pathlib import Path

from merge_splits import SPLITS, read_split

logger = logging.getLogger("split_copies")

SEED = 1235
Triple = tuple[str, str, str]


def read_copy(path: Path) -> list[Triple]:
    """The copy's triples in file order, each kept once."""
    triples: dict[Triple, None] = {}
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 3 or not all(parts):
                logger.warning("%s:%d is not a triple, skipped.", path, number)
                continue
            triples[(parts[0], parts[1], parts[2])] = None
    return list(triples)


def split_sizes(source: Path, total: int) -> dict[str, int]:
    """Valid and test sizes with the source dataset's shares; train is the rest."""
    counts = {name: len(read_split(source / name, None)) for name in SPLITS}
    whole = sum(counts.values())
    sizes = {name: round(total * counts[name] / whole) for name in ("valid", "test")}
    sizes["train"] = total - sizes["valid"] - sizes["test"]
    return sizes


def split(triples: list[Triple], sizes: dict[str, int], seed: int) -> dict[str, list]:
    shuffled = list(triples)
    random.Random(seed).shuffle(shuffled)
    test = shuffled[: sizes["test"]]
    valid = shuffled[sizes["test"] : sizes["test"] + sizes["valid"]]
    train = shuffled[sizes["test"] + sizes["valid"] :]
    entities = {t[0] for t in train} | {t[2] for t in train}
    relations = {t[1] for t in train}

    def known(t: Triple) -> bool:
        return t[0] in entities and t[2] in entities and t[1] in relations

    # Moving triples to train only makes more terms known, so one pass suffices.
    moved = [t for t in valid + test if not known(t)]
    moved_set = set(moved)
    train += moved
    valid = [t for t in valid if t not in moved_set]
    test = [t for t in test if t not in moved_set]
    return {"train": train, "valid": valid, "test": test}


def write_split(path: Path, triples: list[Triple]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(t) + "\n" for t in triples)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument(
        "--suffix",
        default="_ours",
        help="suffix of the copies' folder names (default: %(default)s)",
    )
    parser.add_argument(
        "--datasets", nargs="+", help="copy folders to split (default: every copy)"
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--force", action="store_true", help="split copies that already have splits"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # merge_splits warns about the header rows of the sources, which are expected.
    logging.getLogger("merge_splits").setLevel(logging.ERROR)

    folders = sorted(
        d
        for d in args.root.iterdir()
        if d.is_dir()
        and d.name.endswith(args.suffix)
        and (d / f"{d.name}.tsv").is_file()
        and (args.datasets is None or d.name in args.datasets)
    )
    if args.datasets:
        missing = sorted(set(args.datasets) - {d.name for d in folders})
        if missing:
            parser.error(f"No copy {', '.join(missing)} under {args.root}.")
    if not folders:
        parser.error(f"No folder under {args.root} ends with {args.suffix}.")

    failed = []
    for folder in folders:
        if not args.force and all((folder / s).is_file() for s in SPLITS):
            logger.info("%s: already split, skipped.", folder.name)
            continue
        source = args.root / folder.name.removesuffix(args.suffix)
        if not all((source / s).is_file() for s in SPLITS):
            logger.error("%s: no splits of its source %s.", folder.name, source)
            failed.append(folder.name)
            continue
        triples = read_copy(folder / f"{folder.name}.tsv")
        sizes = split_sizes(source, len(triples))
        splits = split(triples, sizes, args.seed)
        for name in SPLITS:
            write_split(folder / name, splits[name])
        moved = len(splits["train"]) - sizes["train"]
        logger.info(
            "%s: %d triples, train %d, valid %d, test %d (%d moved to train for a "
            "term train lacked).",
            folder.name,
            len(triples),
            len(splits["train"]),
            len(splits["valid"]),
            len(splits["test"]),
            moved,
        )
    if failed:
        logger.error("Not split: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
