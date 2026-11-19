"""Counts the witnesses of each complete rule in a source dataset and in a copy.

A rule's support counts distinct head pairs, but a head pair can be reached
through several body groundings (witnesses), e.g. `?a brother ?f & ?b mother ?f
=> ?a son ?b` is reached once per sibling `?f` of `?a`. Initial-fact generation grounds
a rule `support` times with one witness each, and the random facts add joins that reach
new head pairs, so a copy of a graph with many witnesses per pair derives more
head facts than its source (`family`, section 8 of `docs/algorithm.md`).

For each folder directly under the root (default `exp_datasets`) with a
`<folder>.unified.tsv`, or each folder of `--datasets`, reads the copy
`<folder><suffix>/<folder><suffix>.tsv` and the rules it used,
`<folder><suffix>/<folder><suffix>_used.csv`, and keeps the complete rules (std
confidence 1). For each rule it prints the body groundings, the distinct head
pairs and their ratio on the source and on the copy, then the totals of the
dataset. Only the Python standard library is used.

    python scripts/rule_witnesses.py --datasets family
    python scripts/rule_witnesses.py --suffix _ours_r25 --totals-only
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

Atom = tuple[str, str, str]
Index = dict[str, set[tuple[str, str]]]


def read_index(path: Path) -> Index:
    """Returns the distinct `(subject, object)` pairs of each relation of a file."""
    index: Index = defaultdict(set)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                index[parts[1]].add((parts[0], parts[2]))
    return index


def parse_atoms(text: str) -> list[Atom]:
    """Splits `?a brother ?f  ?b mother ?f` into its atoms."""
    terms = text.split()
    if len(terms) % 3:
        raise ValueError(f"Not a list of atoms: {text!r}")
    return [(terms[i], terms[i + 1], terms[i + 2]) for i in range(0, len(terms), 3)]


def is_var(term: str) -> bool:
    return term.startswith("?")


def body_groundings(index: Index, body: list[Atom]) -> list[dict[str, str]]:
    """Returns every binding of the body's variables that the graph satisfies."""
    bindings: list[dict[str, str]] = [{}]
    for subj, rel, obj in body:
        pairs = index.get(rel, set())
        by_subject: dict[str, list[str]] = defaultdict(list)
        by_object: dict[str, list[str]] = defaultdict(list)
        for s, o in pairs:
            by_subject[s].append(o)
            by_object[o].append(s)
        extended: list[dict[str, str]] = []
        for binding in bindings:
            s_val = binding.get(subj) if is_var(subj) else subj
            o_val = binding.get(obj) if is_var(obj) else obj
            if s_val is not None and o_val is not None:
                if (s_val, o_val) in pairs:
                    extended.append(binding)
            elif s_val is not None:
                for o in by_subject.get(s_val, ()):
                    if subj == obj and o != s_val:
                        continue
                    extended.append({**binding, obj: o})
            elif o_val is not None:
                for s in by_object.get(o_val, ()):
                    extended.append({**binding, subj: s})
            else:
                for s, o in pairs:
                    if subj == obj:
                        if s == o:
                            extended.append({**binding, subj: s})
                    else:
                        extended.append({**binding, subj: s, obj: o})
        bindings = extended
    return bindings


def witnesses(index: Index, body: list[Atom], head: Atom) -> tuple[int, int]:
    """Returns the body groundings of a rule and the distinct head pairs they reach."""
    groundings = body_groundings(index, body)

    def value(term: str, binding: dict[str, str]) -> str:
        return binding[term] if is_var(term) else term

    pairs = {(value(head[0], b), value(head[2], b)) for b in groundings}
    return len(groundings), len(pairs)


def ratio(groundings: int, pairs: int) -> str:
    return f"{groundings / pairs:.2f}" if pairs else "n/a"


def counts(groundings: int, pairs: int) -> str:
    return f"{groundings:>10d} {pairs:>8d} {ratio(groundings, pairs):>6}"


HEADER = (
    f"  {'rule':>4}  {'groundings':>10} {'pairs':>8} {'ratio':>6}"
    f"  {'groundings':>10} {'pairs':>8} {'ratio':>6}  (source, then copy)"
)


def dataset_names(root: Path) -> list[str]:
    """Returns the folders under the root that have a `<folder>.unified.tsv`."""
    files = root.glob("*/*.unified.tsv")
    return sorted(p.parent.name for p in files if p.stem == f"{p.parent.name}.unified")


def read_complete_rules(path: Path) -> list[tuple[str, list[Atom], Atom]]:
    """Returns the ID, body and head of each rule with std confidence 1."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            (row["rule_id"], parse_atoms(row["body"]), parse_atoms(row["head"])[0])
            for row in csv.DictReader(handle)
            if float(row["std_confidence"]) >= 1
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument(
        "--datasets",
        nargs="+",
        help="Folders under the root (default every one with a .unified.tsv).",
    )
    parser.add_argument(
        "--suffix", default="_ours_r25", help="Suffix of the copy (default _ours_r25)"
    )
    parser.add_argument(
        "--totals-only", action="store_true", help="Print only one line per dataset."
    )
    args = parser.parse_args()

    status = 0
    for name in args.datasets or dataset_names(args.root):
        source_file = args.root / name / f"{name}.unified.tsv"
        copy_name = f"{name}{args.suffix}"
        copy_file = args.root / copy_name / f"{copy_name}.tsv"
        rules_file = args.root / copy_name / f"{copy_name}_used.csv"
        missing = [p for p in (source_file, copy_file, rules_file) if not p.is_file()]
        if missing:
            if args.datasets:
                listed = ", ".join(map(str, missing))
                print(f"{name}: missing {listed}", file=sys.stderr)
                status = 1
            continue
        rules = read_complete_rules(rules_file)
        if not rules:
            print(f"{copy_name}: no complete rule")
            continue
        source, copy = read_index(source_file), read_index(copy_file)
        n_source = sum(map(len, source.values()))
        n_copy = sum(map(len, copy.values()))
        sg = sp = cg = cp = 0
        lines = []
        for rule_id, body, head in rules:
            rsg, rsp = witnesses(source, body, head)
            rcg, rcp = witnesses(copy, body, head)
            sg, sp, cg, cp = sg + rsg, sp + rsp, cg + rcg, cp + rcp
            rule = " & ".join(" ".join(a) for a in body) + " => " + " ".join(head)
            row = f"{counts(rsg, rsp)}  {counts(rcg, rcp)}"
            lines.append(f"  {rule_id:>4}  {row}  {rule}")
        print(
            f"{copy_name}: {len(rules)} complete rules,"
            f" triples {n_source} -> {n_copy} ({n_copy / n_source:.2f}x),"
            f" groundings/pairs source {sg}/{sp} ({ratio(sg, sp)}),"
            f" copy {cg}/{cp} ({ratio(cg, cp)})"
        )
        if not args.totals_only:
            print(HEADER)
            print("\n".join(lines))
    return status


if __name__ == "__main__":
    sys.exit(main())
