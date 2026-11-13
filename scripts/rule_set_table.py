"""Writes the table of the rule set of each graph, the rules our generator used.

For each graph of `results_table.AGREEMENT_GRAPHS`, reads its mined rules
(`<graph>.csv`) and its triples (`<graph>.unified.tsv`) and reproduces the rule
selection of `cli/main.py` with its default filter: `parse_rule_set` keeps the
rules with std confidence 1, and `remove_minimal_cyclic_rules` removes the
fewest of them that leave the relation graph with no cycle, exempting the
symmetric rules whose relation has the same domain and range counts
(`get_undirected_rule_ids`, with the counts computed from the triple file
instead of the graph database). The rules left are the ones the generator used.

Per graph, the table gives the relations, the rules mined, the rules with std
confidence 1, the rules removed for cycles, the rules used and their patterns
(symmetry, inversion, hierarchy, composition, intersection, classified as in
`characterize_datasets.py`). For all mined rules and for the used ones, it
gives the total support (the triples each rule predicts, summed) and the share
of the graph's triples predicted by at least one of the rules. A rule's
predicted triples are found by checking every triple of its head relation. On
the graph as AMIE read it (`as_amie_reads`), their number must equal the rule's
`positive_examples` in the rules CSV; the table gives the counts on the graph
as it is.

Writes `rule_sets.csv` to `--analysis` (default `exp_analysis`) and
`rule_sets.tex` to `--output-dir` (default `exp_analysis/results_tables`).
Needs the `skgg` package (`pip install -e .`).

    python scripts/rule_set_table.py
"""

import argparse
import csv
import logging
from collections import Counter, defaultdict
from pathlib import Path

from characterize_datasets import RULE_PATTERNS, classify_rule
from results_table import AGREEMENT_GRAPHS, DECIMALS, HEADER, MISSING, latex_name

from skgg.core.cycles import remove_minimal_cyclic_rules
from skgg.core.rules import DEFAULT_STD_THRESHOLD, parse_rule_set
from skgg.core.utils import build_term_mapping, format_term
from skgg.engine.edb import get_undirected_rule_ids
from skgg.engine.metrics import PredicateProfile

# Namespace of the bare terms; any namespace works, since only the rules and
# the triples of the same graph are compared.
NAMESPACE = "http://skgg.org/rules/"
USED_PATTERNS = [p for p in RULE_PATTERNS if p != "other"]

# Table columns: header, key of the row, and whether the value is a share. The
# other patterns, and the relations and triples the used rules derive, are in
# the CSV only (CSV_ONLY).
COLUMNS = (
    ("$|R|$", "relations", False),
    ("Mined", "rules_mined", False),
    ("Std=1", "rules_std1", False),
    ("Removed", "rules_removed", False),
    ("Used", "rules_used", False),
    ("Sym.", "used_symmetry", False),
    ("Inv.", "used_inversion", False),
    ("Comp.", "used_composition", False),
    ("Support", "mined_support", False),
    ("Covered", "mined_covered_share", True),
    ("Support", "support", False),
    ("Covered", "covered_share", True),
)
# Columns written to rule_sets.csv only.
CSV_ONLY = (
    "used_hierarchy",
    "used_intersection",
    "used_other",
    "head_relations",
    "head_coverage",
    "covered_triples",
    "mined_covered_triples",
    "used_rule_ids",
)


Triple = tuple[str, str, str]


def read_profiles(
    path: Path, term_mapping: dict[str, str]
) -> tuple[dict[str, PredicateProfile], Counter[str], set[Triple]]:
    """The domain and range counts of each relation of a triple file, keyed by
    the relation as the rules write it, each relation's frequency, and the
    file's distinct triples, with bare terms."""
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    ranges: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[str, str, str]] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for line in f:
            terms = line.rstrip("\r\n").split("\t")
            if len(terms) != 3 or not all(terms) or tuple(terms) in seen:
                continue
            seen.add((terms[0], terms[1], terms[2]))
            s, p, o = terms
            predicate = format_term(p, term_mapping)
            domains[predicate][s] += 1
            ranges[predicate][o] += 1
    profiles = {
        p: PredicateProfile(
            domain=dict(domains[p]),
            range=dict(ranges[p]),
            frequency=sum(domains[p].values()),
        )
        for p in domains
    }
    frequencies = Counter({p: profile.frequency for p, profile in profiles.items()})
    return profiles, frequencies, seen


class TripleIndex:
    """The triples of a graph, indexed for rule grounding: per relation, the
    objects of each subject and the subjects of each object."""

    def __init__(self, triples: set[Triple]) -> None:
        self.objects: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self.subjects: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self.pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for s, p, o in triples:
            self.objects[p][s].add(o)
            self.subjects[p][o].add(s)
            self.pairs[p].append((s, o))

    def neighbours(
        self, side: tuple[str, bool], value: str, empty: frozenset[str]
    ) -> set[str] | frozenset[str]:
        """The entities linked to `value` by the relation of `side`: its objects
        if `value` is the atom's subject, else its subjects."""
        p, is_subject = side
        table = self.objects[p] if is_subject else self.subjects[p]
        return table.get(value, empty)

    def holds(self, atoms: list[tuple[str, str, str]], binding: dict[str, str]) -> bool:
        """Whether the atoms have a grounding that extends `binding`. Atoms with a
        bound variable are grounded first, through the index."""
        if not atoms:
            return True

        def bound(atom: tuple[str, str, str]) -> int:
            return (atom[0] in binding) + (atom[2] in binding)

        atom = max(atoms, key=bound)
        rest = [a for a in atoms if a is not atom]
        s_var, p, o_var = atom
        s, o = binding.get(s_var), binding.get(o_var)
        if s is not None and o is not None:
            return o in self.objects[p].get(s, ()) and self.holds(rest, binding)
        if s is not None:
            candidates = [(s, x) for x in self.objects[p].get(s, ())]
        elif o is not None:
            candidates = [(x, o) for x in self.subjects[p].get(o, ())]
        else:
            candidates = self.pairs[p]
        for cs, co in candidates:
            if s_var == o_var and cs != co:
                continue
            if self.holds(rest, {**binding, s_var: cs, o_var: co}):
                return True
        return False


Atom = tuple[str, str, str]
# An atom seen from one of its variables: (relation, whether that variable is
# the atom's subject).
Side = tuple[str, bool]


def path_atoms(atoms: list[Atom], x: str, y: str) -> tuple[Side, Side] | None:
    """For a body of two atoms that link x to y through a third variable z, in
    any direction, how each atom reaches z: from x and from y. None for any
    other body."""
    if len(atoms) != 2 or x == y:
        return None
    variables = {v for atom in atoms for v in (atom[0], atom[2])}
    if len(variables) != 3 or not {x, y} < variables:
        return None
    (z,) = variables - {x, y}
    sides: dict[str, Side] = {}
    for s_var, p, o_var in atoms:
        if {s_var, o_var} == {x, z}:
            sides[x] = (p, s_var == x)
        elif {s_var, o_var} == {y, z}:
            sides[y] = (p, s_var == y)
    if set(sides) != {x, y}:
        return None
    return sides[x], sides[y]


def rule_predictions(body: str, head: str, index: TripleIndex) -> set[Triple]:
    """The triples of the graph that a rule predicts: the head triples whose
    body holds for the same head variables. Their number is the rule's support
    (AMIE's `positive_examples`). `body` and `head` are written as in the rules
    CSV, e.g. `?a father ?f  ?b mother ?f` and `?a husband ?b`. Each triple of
    the head relation is checked once, so a rule costs at most its head
    relation's triples times the neighbours the body looks up."""
    tokens = body.split()
    atoms = [
        (tokens[i], tokens[i + 1], tokens[i + 2]) for i in range(0, len(tokens), 3)
    ]
    x, r, y = head.split()
    predicted = set()
    path = path_atoms(atoms, x, y)
    if path is not None:
        # x - z - y: the rule predicts (s, r, o) when the z values reached from s
        # and from o meet, checked as one set intersection.
        from_x, from_y = path
        empty: frozenset[str] = frozenset()
        for s, o in index.pairs[r]:
            if not index.neighbours(from_x, s, empty).isdisjoint(
                index.neighbours(from_y, o, empty)
            ):
                predicted.add((s, r, o))
        return predicted
    for s, o in index.pairs[r]:
        if x == y and s != o:
            continue
        if index.holds(atoms, {x: s, y: o}):
            predicted.add((s, r, o))
    return predicted


def as_amie_reads(triples: set[Triple]) -> set[Triple]:
    """The triples as AMIE 3.5.1 loads a `.tsv` file: it drops a `.` that ends a
    line, as if it closed an N-Triples statement, so an object such as
    `Douglas_Fairbanks,_Jr.` becomes `Douglas_Fairbanks,_Jr` and no longer
    matches the same entity as a subject."""
    return {(s, p, o[:-1] if o.endswith(".") else o) for s, p, o in triples}


def coverage(
    rule_ids: list[str],
    mined: dict[str, dict[str, str]],
    index: TripleIndex,
    amie_index: TripleIndex,
    graph: str,
) -> tuple[int, set[Triple]]:
    """The total support of the rules on the graph and the distinct triples
    they predict. Raises ValueError if a rule's predicted triples on the graph
    as AMIE read it (`amie_index`) differ from its `positive_examples` in the
    rules CSV."""
    support = 0
    covered: set[Triple] = set()
    for rule_id in rule_ids:
        row = mined[rule_id]
        predicted = rule_predictions(row["body"], row["head"], index)
        amie_count = (
            len(predicted)
            if amie_index is index
            else len(rule_predictions(row["body"], row["head"], amie_index))
        )
        if amie_count != int(float(row["positive_examples"])):
            raise ValueError(
                f"{graph} rule {rule_id}: {amie_count} predicted triples, "
                f"{row['positive_examples']} positive examples"
            )
        support += len(predicted)
        covered |= predicted
    return support, covered


def describe(root: Path, graph: str) -> dict[str, object]:
    rules_file = root / graph / f"{graph}.csv"
    triples_file = root / graph / f"{graph}.unified.tsv"
    row: dict[str, object] = {"graph": graph}
    if not rules_file.is_file() or not triples_file.is_file():
        return row

    term_mapping = build_term_mapping({}, NAMESPACE)
    profiles, frequencies, triples = read_profiles(triples_file, term_mapping)
    with rules_file.open(newline="", encoding="utf-8") as f:
        mined = {r["rule_id"]: r for r in csv.DictReader(f)}

    rules = parse_rule_set(
        rules_file=rules_file,
        term_mapping=term_mapping,
        std_threshold=DEFAULT_STD_THRESHOLD,
    )
    std1 = len(rules)
    removed = remove_minimal_cyclic_rules(
        rules, exempt=get_undirected_rule_ids(rules, profiles)
    )
    patterns = Counter(classify_rule(mined[r]["body"], mined[r]["head"]) for r in rules)
    heads = {rule.head.predicate for rule in rules.values()}
    total = sum(frequencies.values())
    index = TripleIndex(triples)
    amie_triples = as_amie_reads(triples)
    amie_index = index if amie_triples == triples else TripleIndex(amie_triples)
    support, covered = coverage(list(rules), mined, index, amie_index, graph)
    mined_support, mined_covered = coverage(
        list(mined), mined, index, amie_index, graph
    )
    row.update(
        relations=len(frequencies),
        rules_mined=len(mined),
        rules_std1=std1,
        rules_removed=len(removed),
        rules_used=len(rules),
        **{f"used_{p}": patterns[p] for p in RULE_PATTERNS},
        head_relations=len(heads),
        head_coverage=sum(frequencies[h] for h in heads) / total if total else 0.0,
        used_rule_ids=" ".join(sorted(rules, key=int)),
        support=support,
        covered_triples=len(covered),
        covered_share=len(covered) / total if total else 0.0,
        mined_support=mined_support,
        mined_covered_triples=len(mined_covered),
        mined_covered_share=len(mined_covered) / total if total else 0.0,
    )
    return row


def build_table(rows: list[dict[str, object]], groups: list[int]) -> str:
    """The LaTeX table, with a rule after each group of `groups` rows."""
    lines = [
        "% Generated by scripts/rule_set_table.py",
        "\\begin{table}[t]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        "\\caption{Rule set of each graph. $|R|$: relations; Mined: rules mined "
        "by AMIE; Std=1: rules with standard confidence 1, the generator's "
        "filter; Removed: rules removed to break cycles between relations; Used: "
        "rules the generator used, and how many of them are symmetry, inversion "
        "and composition rules. Support: total support of the rules, the "
        "triples each one predicts, summed; Covered: share of the graph's "
        "triples predicted by at least one of the rules; both for all mined rules "
        "and for the rules the generator used.}",
        "\\label{tab:rule_sets}",
        "\\resizebox{\\columnwidth}{!}{%",
        "\\begin{tabular}{l|r||rrrr||rrr||rr||rr}",
        "\\hline",
        " & " * 9
        + "\\multicolumn{2}{c||}{Mined rules} & \\multicolumn{2}{c}{Used rules} \\\\",
        " & ".join([HEADER, *(h for h, _, _ in COLUMNS)]) + " \\\\",
        "\\hline\\hline",
    ]
    start = 0
    for size in groups:
        for row in rows[start : start + size]:
            cells = [latex_name(str(row["graph"]))]
            for _, key, share in COLUMNS:
                value = row.get(key)
                if value is None:
                    cells.append(MISSING)
                elif share:
                    cells.append(f"{float(value):.{DECIMALS}f}")  # type: ignore[arg-type]
                else:
                    cells.append(str(value))
            lines.append(" & ".join(cells) + " \\\\")
        lines.append("\\hline")
        start += size
    lines += ["\\end{tabular}%", "}", "\\end{table}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument("--analysis", type=Path, default=Path("exp_analysis"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("exp_analysis/results_tables")
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    rows, groups = [], []
    for graphs in AGREEMENT_GRAPHS:
        groups.append(len(graphs))
        for graph in graphs:
            row = describe(args.root, graph)
            rows.append(row)
            print(
                f"{graph}: {row.get('rules_std1', MISSING)} std=1 rules, "
                f"{row.get('rules_used', MISSING)} used",
                flush=True,
            )

    fields = [
        "graph",
        *(k for _, k, _ in COLUMNS),
        *CSV_ONLY,
    ]
    with (args.analysis / "rule_sets.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "rule_sets.tex").write_text(
        build_table(rows, groups), encoding="utf-8"
    )
    print(
        f"Wrote {args.analysis / 'rule_sets.csv'} and "
        f"{args.output_dir / 'rule_sets.tex'}"
    )


if __name__ == "__main__":
    main()
