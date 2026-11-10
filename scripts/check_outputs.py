"""Compares each synthetic copy of a benchmark dataset with its source.

For each folder directly under the root (default `exp_datasets`) with a
`<folder>.unified.tsv`, reads `<folder>_ours/<folder>_ours.tsv`, the copy that
`cli/generate_datasets.py` writes, and prints one row per dataset:

- the triples of the source and the lines of the copy, its duplicate lines and its
  lines that aren't a triple;
- the entities of the copy that aren't in the source, and the relations missing
  from or added to it;
- how many relations have the source's frequency, the source's degrees (each
  entity's count as subject and as object) and the source's number of self-loops.

Below a row, each relation that differs from the source gets a line. Only the
Python standard library is used, and one dataset is read at a time.

Exits with status 1 if a copy is missing, or has duplicate lines or lines that
aren't a triple, after checking every dataset.

    python scripts/check_outputs.py                       # every dataset under exp_datasets/
    python scripts/check_outputs.py --datasets french WN18RR
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT_SUFFIX = "_ours"


def read_triples(path: Path) -> tuple[set[tuple[str, str, str]], int, int]:
    """Returns the distinct triples of a `.tsv` file, its line count and how many
    lines aren't a `subject<TAB>predicate<TAB>object` triple."""
    triples: set[tuple[str, str, str]] = set()
    lines = bad = 0
    with path.open(encoding="utf-8", newline="") as f:
        for line in f:
            lines += 1
            terms = line.rstrip("\r\n").split("\t")
            if len(terms) != 3 or not all(terms):
                bad += 1
                continue
            triples.add((terms[0], terms[1], terms[2]))
    return triples, lines, bad


def relation_profiles(
    triples: set[tuple[str, str, str]],
) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, Counter[str]], Counter[str]]:
    """Returns each relation's frequency, subject counts, object counts and
    number of self-loops."""
    frequency: Counter[str] = Counter()
    subjects: dict[str, Counter[str]] = defaultdict(Counter)
    objects: dict[str, Counter[str]] = defaultdict(Counter)
    loops: Counter[str] = Counter()
    for s, p, o in triples:
        frequency[p] += 1
        subjects[p][s] += 1
        objects[p][o] += 1
        loops[p] += s == o
    return frequency, subjects, objects, loops


def check_dataset(root: Path, name: str) -> tuple[bool, bool]:
    """Prints the comparison of one dataset.

    Returns:
        Whether the copy is well formed, and whether every relation matches.
    """
    output = root / f"{name}{OUTPUT_SUFFIX}" / f"{name}{OUTPUT_SUFFIX}.tsv"
    if not output.is_file():
        print(f"{name} | missing {output}")
        return False, False

    source, _, _ = read_triples(root / name / f"{name}.unified.tsv")
    copy, lines, bad = read_triples(output)
    duplicates = lines - bad - len(copy)

    src_freq, src_subj, src_obj, src_loops = relation_profiles(source)
    out_freq, out_subj, out_obj, out_loops = relation_profiles(copy)
    relations = src_freq.keys() | out_freq.keys()
    new_entities = {e for s, _, o in copy for e in (s, o)} - {
        e for s, _, o in source for e in (s, o)
    }

    freq_ok = sum(src_freq[p] == out_freq[p] for p in src_freq)
    degrees_ok = sum(
        src_subj[p] == out_subj[p] and src_obj[p] == out_obj[p] for p in src_freq
    )
    loops_ok = sum(src_loops[p] == out_loops[p] for p in src_freq)
    total = len(src_freq)
    print(
        f"{name} | {len(source)} | {lines} | {duplicates} | {bad} | "
        f"{len(new_entities)} | -{len(src_freq.keys() - out_freq.keys())}"
        f"/+{len(out_freq.keys() - src_freq.keys())} | {freq_ok}/{total} | "
        f"{degrees_ok}/{total} | {loops_ok}/{total} | "
        f"{sum(src_loops.values())}/{sum(out_loops.values())}"
    )

    matches = not new_entities and set(out_freq) == set(src_freq)
    for p in sorted(relations):
        notes = []
        if src_freq[p] != out_freq[p]:
            notes.append(f"frequency {src_freq[p]} -> {out_freq[p]}")
        elif src_subj[p] != out_subj[p] or src_obj[p] != out_obj[p]:
            notes.append("degrees differ")
        if src_loops[p] != out_loops[p]:
            notes.append(f"self-loops {src_loops[p]} -> {out_loops[p]}")
        if notes:
            matches = False
            print(f"    {p}: {', '.join(notes)}")
    return duplicates == 0 and bad == 0, matches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare each <folder>_ours/<folder>_ours.tsv with its "
        "<folder>/<folder>.unified.tsv."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="Folder holding one subfolder per dataset (default: %(default)s).",
    )
    parser.add_argument(
        "--datasets", nargs="+", help="Only check these dataset folders."
    )
    args = parser.parse_args()

    names = args.datasets or sorted(
        folder.name
        for folder in args.root.iterdir()
        if folder.is_dir() and (folder / f"{folder.name}.unified.tsv").is_file()
    )
    print(
        "dataset | source triples | copy lines | duplicates | not a triple | "
        "new entities | relations missing/added | frequency ok | degrees ok | "
        "self-loops ok | self-loops source/copy"
    )
    failed = []
    matching = 0
    for name in names:
        well_formed, matches = check_dataset(args.root, name)
        if not well_formed:
            failed.append(name)
        matching += matches
        sys.stdout.flush()

    print(f"{matching} of {len(names)} copies match their source on every relation.")
    if failed:
        print(f"Missing or malformed copies: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
