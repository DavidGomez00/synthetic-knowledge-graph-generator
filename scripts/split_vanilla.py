"""Splits datasets 80/20 into train and test with VANILLA's own code.

The split is the one in VANILLA's Validated_KG_Completion/KGC.py: the triples file
is loaded with KGC.load_dataset (no inverse triples) and split with PyKEEN's
`TriplesFactory.split(random_state=1235)`, its default 80/20 ratio. The two parts are
written as KGC.py writes them, to `train` and `test` (tab-separated, no header, no
index). KGC.py has no validation set, so there is no `valid`, and a `valid` left by
an earlier split is removed; train_kge.py and report_results.py read a missing
`valid` as empty.

PyKEEN's split also moves triples into train so that every entity and relation is
in train, so the shares can differ slightly from 80/20. The result depends on the
order of the lines in the input file and on the PyKEEN version (1.11.1 here).

Each input is a triples file; its dataset is named after the folder that holds it,
and its splits go to `<root>/<dataset>/`, by default next to the file in
`exp_datasets/<dataset>/`, replacing the splits there.

Usage:
    python scripts/split_vanilla.py exp_datasets/french/french.unified.tsv \
        exp_datasets/french_ours_pca1/french_ours_pca1.tsv
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

DEFAULT_VANILLA = (
    Path(__file__).resolve().parents[2] / "VANILLA" / "Validated_KG_Completion"
)
# KGC.py's default and the value in its input_KGC.json.
SEED = 1235

logger = logging.getLogger("split_vanilla")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("files", nargs="+", type=Path, help="triples files to split")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="folder of the split datasets (default: %(default)s)",
    )
    parser.add_argument(
        "--vanilla",
        type=Path,
        default=DEFAULT_VANILLA,
        help="VANILLA's Validated_KG_Completion folder (default: %(default)s)",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not (args.vanilla / "KGC.py").is_file():
        parser.error(f"No KGC.py in {args.vanilla}.")
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        parser.error(f"No file {', '.join(missing)}.")
    sys.path.insert(0, str(args.vanilla))
    from KGC import load_dataset  # VANILLA's loader, unchanged

    for source in args.files:
        name = source.resolve().parent.name
        folder = args.root / name
        folder.mkdir(parents=True, exist_ok=True)
        tf, _, entities, relations = load_dataset(
            str(source), create_inverse_triples=False
        )
        # Same call and same saving as KGC.py.
        training, testing = tf.split(random_state=args.seed)
        pd.DataFrame(training.triples).to_csv(
            folder / "train", index=False, sep="\t", header=False
        )
        pd.DataFrame(testing.triples).to_csv(
            folder / "test", index=False, sep="\t", header=False
        )
        stale = folder / "valid"
        if stale.exists():
            stale.unlink()
            logger.info("%s: removed its valid file, left by an earlier split.", name)
        total = training.num_triples + testing.num_triples
        logger.info(
            "%s: %d triples, %d entities, %d relations; "
            "train %d (%.1f%%), test %d (%.1f%%).",
            name,
            total,
            len(entities),
            len(relations),
            training.num_triples,
            100 * training.num_triples / total,
            testing.num_triples,
            100 * testing.num_triples / total,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
