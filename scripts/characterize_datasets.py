"""Computes the structural descriptors of the benchmark datasets (objective O1).

For each folder directly under the root (default `exp_datasets`) with a
`<folder>.unified.tsv` (a source dataset) or a `<folder>.tsv` (a copy), reads its
distinct triples and, if present, the rules mined from it (`<folder>.csv`), and
computes five groups of descriptors:

- size and density: entities, relations, triples, self-loops, mean degree
  (2|E|/|V|), density |E| / (|V| (|V| - 1)), the number of weakly connected
  components and the share of entities in the largest one;
- degree heterogeneity: for the in-degree and the out-degree of each entity
  (triples with the entity as object or subject, over all relations), the
  maximum, the standard deviation, the coefficient of variation (std / mean),
  the Gini coefficient and the share of entities with degree 0;
- relation frequency distribution: the Gini coefficient and normalized Shannon
  entropy of the relation frequencies, the share of the most frequent relation,
  and the smallest, median and largest frequency;
- relation cardinality: each relation is 1-1, 1-N, N-1 or N-M by the
  convention of Bordes et al. (2013): tails per head (|T_r| / distinct heads) and
  heads per tail (|T_r| / distinct tails) compared with 1.5. The number of
  relations and the share of triples of each class;
- relational patterns, measured on the data: the relations that are symmetric,
  have an inverse, or are contained in another relation (share of a relation's
  non-loop pairs found reversed in itself, reversed in another relation, or in
  the same direction in another relation, at least `--pattern-threshold`), and
  the mean share of entities in a relation's domain and range. Measured on the
  mined rules: the number of rules of each pattern (symmetry, inversion,
  hierarchy, composition, intersection, other) and of rules with std
  confidence 1, the ones the generator uses. A dataset without a rules file
  gets empty rule columns.

The datasets have no schema or type triples, so domain and range are described
only by how many entities each relation uses.

Writes to `--output-dir` (default `exp_analysis`):

- `dataset_characteristics.csv`: one row per dataset with every descriptor;
- `relation_characteristics.csv`: one row per relation of each dataset;
- `dataset_characteristics.md` and `.tex`: the summary table, one row per
  dataset grouped by dataset family;
- `structural_comparison.csv`, `.md` and `.tex`: the descriptors that compare a
  copy with its source (|E|, |V|, |L|, components, density, max and std of the
  in- and out-degree), one block per source dataset with the source first and
  then its copies;
- `structural_tables/<descriptor>_<level>.csv`, `.md` and `.tex`: one table per
  descriptor of the comparison and rule level of LEVELS, one row per source
  dataset and one column per method (Original, PyGraft, KBgen, NAME), with
  KBgen's and NAME's copies at that level and PyGraft's copy, made without rules,
  at every level. The full copies `<source>_kbgen` and `<source>_ours`, made from
  different rules, were dropped on 17 November 2026.
- `structural_tables/size_mse.csv`, `.md` and `.tex`: the relative mean squared
  error ((copy - original) / original)² of the number of entities and of
  triples, one row per rule level of the ours and kbgen copies (and `All`, over
  every level), one column per generator. A level's error is averaged over the
  source datasets with an ours or kbgen copy at that level, PyGraft's included.
  `--skip-levels pca1` leaves those copies out of this table only, written as
  `size_mse_without_pca1.*`. Std1 copies, if any, are left out of the
  tables of the rule levels (none is left since 19 November 2026).
- `structural_tables/size_error_<level>.csv`, `.md` and `.tex`, for each level
  of `--size-levels` (e.g. `--size-levels r100`): one row per source dataset,
  and for each generator the difference copy - original and the squared
  relative error of the number of entities and of triples.

`--set` picks the datasets: `base` (default), the `_ours`, `_kbgen` or
`_pygraft` copies (read from `<folder>/<folder>.tsv`), including their
rule-level copies (`<source>_<set>_r50`, `<source>_<set>_pca1`, ...), or `all`.
Only the Python standard library is used, and one dataset is read at a time.

The two CSV files are also a cache: each graph's row keeps the SHA-256 of its
triple file, the `--pattern-threshold` and DESCRIPTOR_VERSION, and a later run
reuses the row and the graph's relation rows when all three match, so only new
or changed graphs are read. Every table is then built from these rows. The rule
columns are always read again from `<folder>.csv`. `--recount` counts every
graph again.

    python scripts/characterize_datasets.py
    python scripts/characterize_datasets.py --datasets french family
    python scripts/characterize_datasets.py --set all
    python scripts/characterize_datasets.py --set all --size-levels r100
"""

import argparse
import csv
import hashlib
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

COPY_SETS = ("ours", "kbgen", "pygraft")
# Written in the `descriptor_version` column of each graph's row. Increase it
# when `characterize` or `relation_rows` change what they compute, so the rows
# of the CSV files counted before are counted again instead of reused.
DESCRIPTOR_VERSION = 1
CARDINALITY_THRESHOLD = 1.5
CARDINALITY_CLASSES = ("1-1", "1-N", "N-1", "N-M")
RULE_PATTERNS = (
    "symmetry",
    "inversion",
    "hierarchy",
    "composition",
    "intersection",
    "other",
)
FAMILY_NAMES = {
    "family": "Family",
    "french": "French Royalty",
    "sgkg": "SGKG",
    "wn18rr": "WN18RR",
    "yago3-10": "YAGO3-10",
}
FAMILY_ORDER = ("French Royalty", "Family", "WN18RR", "YAGO3-10", "SGKG")
VARIANT_ORDER = ("complete", "1-1", "1-N", "N-1", "N-M")
VARIANT_RE = re.compile(r"^(.*?)_?([1NM])_([1NM])$")
SGKG_RE = re.compile(r"^(SGKG)(\d+)$", re.IGNORECASE)
# A copy: `<source>_<set>`, or a rule-level copy `<source>_<set>_<level>`.
COPY_RE = re.compile(
    rf"^(?P<source>.+)_(?P<set>{'|'.join(COPY_SETS)})(?:_(?P<level>r\d+|std1|pca1))?$"
)


@dataclass
class Graph:
    """A dataset's distinct triples, with entities and relations as integers."""

    entities: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    # Per relation, its distinct (head, tail) pairs.
    pairs: list[list[tuple[int, int]]] = field(default_factory=list)
    lines: int = 0
    bad_lines: int = 0
    duplicates: int = 0

    @property
    def triple_count(self) -> int:
        return sum(len(p) for p in self.pairs)


def read_graph(path: Path) -> Graph:
    """Reads the distinct triples of a `subject<TAB>predicate<TAB>object` file."""
    graph = Graph()
    entity_ids: dict[str, int] = {}
    relation_ids: dict[str, int] = {}
    seen: list[set[tuple[int, int]]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for line in f:
            graph.lines += 1
            terms = line.rstrip("\r\n").split("\t")
            if len(terms) != 3 or not all(terms):
                graph.bad_lines += 1
                continue
            s, p, o = terms
            ids = []
            for term in (s, o):
                i = entity_ids.get(term)
                if i is None:
                    i = entity_ids[term] = len(graph.entities)
                    graph.entities.append(term)
                ids.append(i)
            r = relation_ids.get(p)
            if r is None:
                r = relation_ids[p] = len(graph.relations)
                graph.relations.append(p)
                graph.pairs.append([])
                seen.append(set())
            pair = (ids[0], ids[1])
            if pair in seen[r]:
                graph.duplicates += 1
                continue
            seen[r].add(pair)
            graph.pairs[r].append(pair)
    return graph


def gini(values: list[int]) -> float:
    """Gini coefficient of non-negative values: 0 when all are equal, near 1
    when one value holds almost the whole total."""
    n = len(values)
    total = sum(values)
    if n == 0 or total == 0:
        return 0.0
    weighted = sum(i * v for i, v in enumerate(sorted(values), start=1))
    return 2 * weighted / (n * total) - (n + 1) / n


def coefficient_of_variation(values: list[int]) -> float:
    mean = statistics.fmean(values) if values else 0.0
    return statistics.pstdev(values) / mean if mean else 0.0


def normalized_entropy(values: list[int]) -> float:
    """Shannon entropy of the distribution divided by its maximum, log(n)."""
    total = sum(values)
    if len(values) < 2 or total == 0:
        return 0.0
    h = -sum(v / total * math.log(v / total) for v in values if v)
    return h / math.log(len(values))


def component_sizes(graph: Graph) -> list[int]:
    """Sizes of the weakly connected components, ignoring direction and relation."""
    parent = list(range(len(graph.entities)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pairs in graph.pairs:
        for h, t in pairs:
            a, b = find(h), find(t)
            if a != b:
                parent[a] = b
    return list(Counter(find(x) for x in range(len(parent))).values())


def cardinality(tails_per_head: float, heads_per_tail: float) -> str:
    many_tails = tails_per_head >= CARDINALITY_THRESHOLD
    many_heads = heads_per_tail >= CARDINALITY_THRESHOLD
    if many_tails and many_heads:
        return "N-M"
    if many_tails:
        return "1-N"
    if many_heads:
        return "N-1"
    return "1-1"


def relation_rows(graph: Graph, threshold: float) -> list[dict[str, object]]:
    """One row of descriptors per relation, with the pattern measures.

    For each non-loop pair (h, t) of relation r, looks up which relations hold
    (t, h) and (h, t): the share of r's non-loop pairs found reversed in r is its
    symmetry, reversed in r2 its inversion towards r2, and in the same direction
    in r2 its inclusion in r2.
    """
    n = len(graph.entities)
    holders: dict[int, list[int]] = defaultdict(list)
    for r, pairs in enumerate(graph.pairs):
        for h, t in pairs:
            holders[h * n + t].append(r)

    rows = []
    for r, pairs in enumerate(graph.pairs):
        reversed_in: Counter[int] = Counter()
        included_in: Counter[int] = Counter()
        loops = 0
        for h, t in pairs:
            if h == t:
                loops += 1
                continue
            reversed_in.update(holders.get(t * n + h, ()))
            included_in.update(x for x in holders[h * n + t] if x != r)
        non_loops = len(pairs) - loops
        symmetry = reversed_in.pop(r, 0) / non_loops if non_loops else 0.0
        inverse, inverse_share = _best(reversed_in, non_loops)
        parent, parent_share = _best(included_in, non_loops)
        heads = Counter(h for h, _ in pairs)
        tails = Counter(t for _, t in pairs)
        tph = len(pairs) / len(heads)
        hpt = len(pairs) / len(tails)
        rows.append(
            {
                "relation": graph.relations[r],
                "triples": len(pairs),
                "heads": len(heads),
                "tails": len(tails),
                "tails_per_head": tph,
                "heads_per_tail": hpt,
                "cardinality": cardinality(tph, hpt),
                "self_loops": loops,
                "domain_share": len(heads) / n,
                "range_share": len(tails) / n,
                "out_degree_gini": gini(list(heads.values())),
                "in_degree_gini": gini(list(tails.values())),
                "symmetry": symmetry,
                "symmetric": symmetry >= threshold,
                "inverse": graph.relations[inverse] if inverse is not None else "",
                "inverse_share": inverse_share,
                "has_inverse": inverse_share >= threshold,
                "contained_in": graph.relations[parent] if parent is not None else "",
                "contained_share": parent_share,
                "is_contained": parent_share >= threshold,
            }
        )
    return rows


def _best(counts: Counter[int], total: int) -> tuple[int | None, float]:
    if not counts or not total:
        return None, 0.0
    r, count = counts.most_common(1)[0]
    return r, count / total


def classify_rule(body: str, head: str) -> str:
    """The pattern of an AMIE rule, from its body and head atoms."""
    head_atom = head.split()
    tokens = body.split()
    if len(head_atom) != 3 or not tokens or len(tokens) % 3:
        return "other"
    atoms = [tokens[i : i + 3] for i in range(0, len(tokens), 3)]
    x, r, y = head_atom
    if len(atoms) == 1:
        u, r1, v = atoms[0]
        if (u, v) == (x, y):
            return "hierarchy"
        if (u, v) == (y, x):
            return "symmetry" if r1 == r else "inversion"
        return "other"
    if len(atoms) == 2:
        variables = [{a[0], a[2]} for a in atoms]
        if all(v == {x, y} for v in variables):
            return "intersection"
        shared = variables[0] & variables[1]
        if len(shared) == 1 and not shared & {x, y}:
            ends = (variables[0] | variables[1]) - shared
            if ends == {x, y} and all(len(v & {x, y}) == 1 for v in variables):
                return "composition"
    return "other"


def rule_descriptors(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {
            "rules_mined": "",
            "rules_std1": "",
            **{f"rules_{p}": "" for p in RULE_PATTERNS},
        }
    counts: Counter[str] = Counter()
    mined = std1 = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            mined += 1
            std1 += float(row["std_confidence"]) >= 1.0
            counts[classify_rule(row["body"], row["head"])] += 1
    return {
        "rules_mined": mined,
        "rules_std1": std1,
        **{f"rules_{p}": counts[p] for p in RULE_PATTERNS},
    }


def parse_name(name: str) -> tuple[str, str, str]:
    """The set (base, ours, kbgen or pygraft), source dataset and rule level
    ("" for a source or a default copy) of a dataset folder."""
    match = COPY_RE.match(name)
    if match is None:
        return "base", name, ""
    return match["set"], match["source"], match["level"] or ""


def level_key(level: str) -> tuple[int, int]:
    """Default copy first, then std1 and pca1, then the rule levels by threshold."""
    if not level:
        return (0, 0)
    if level == "std1":
        return (1, 0)
    if level == "pca1":
        return (1, 1)
    return (2, int(level[1:]))


def triples_path(root: Path, name: str) -> Path:
    copy_set = parse_name(name)[0]
    return (
        root / name / (f"{name}.unified.tsv" if copy_set == "base" else f"{name}.tsv")
    )


def family_and_variant(name: str) -> tuple[str, str]:
    """The dataset family (French Royalty, Family, ...) and the variant
    (complete, 1-1, 1-N, N-1, N-M, or the SGKG number) of a source dataset."""
    match = SGKG_RE.match(name)
    if match:
        return "SGKG", match.group(2)
    match = VARIANT_RE.match(name)
    if match:
        base = match.group(1)
        sides = {match.group(2), match.group(3)}
        variant = {
            frozenset({"1"}): "1-1",
            frozenset({"N", "M"}): "N-M",
        }.get(frozenset(sides))
        if variant is None:
            variant = "1-N" if match.group(2) == "1" else "N-1"
    else:
        base, variant = name, "complete"
    return FAMILY_NAMES.get(base.lower(), base), variant


def sort_key(
    row: dict[str, object],
) -> tuple[int, str, int, str, int, str, tuple[int, int]]:
    family = str(row["family"])
    variant = str(row["variant"])
    copy_set = str(row["set"])
    return (
        FAMILY_ORDER.index(family) if family in FAMILY_ORDER else len(FAMILY_ORDER),
        family,
        VARIANT_ORDER.index(variant)
        if variant in VARIANT_ORDER
        else len(VARIANT_ORDER),
        variant,
        ("base", *COPY_SETS).index(copy_set),
        copy_set,
        level_key(str(row["level"])),
    )


def characterize(
    root: Path, name: str, threshold: float
) -> tuple[dict[str, object], list[dict[str, object]]]:
    copy_set, source, level = parse_name(name)
    graph = read_graph(triples_path(root, name))
    family, variant = family_and_variant(source)

    n_entities = len(graph.entities)
    n_relations = len(graph.relations)
    n_triples = graph.triple_count
    out_degree = [0] * n_entities
    in_degree = [0] * n_entities
    for pairs in graph.pairs:
        for h, t in pairs:
            out_degree[h] += 1
            in_degree[t] += 1
    components = component_sizes(graph)

    relations = relation_rows(graph, threshold)
    frequencies = [int(r["triples"]) for r in relations]  # type: ignore[call-overload]
    by_class = Counter(str(r["cardinality"]) for r in relations)
    triples_by_class: Counter[str] = Counter()
    for r in relations:
        triples_by_class[str(r["cardinality"])] += int(r["triples"])  # type: ignore[call-overload]

    row: dict[str, object] = {
        "dataset": name,
        "set": copy_set,
        "source": source,
        "level": level,
        "family": family,
        "variant": variant,
        "lines": graph.lines,
        "bad_lines": graph.bad_lines,
        "duplicates": graph.duplicates,
        # Size and density.
        "entities": n_entities,
        "relations": n_relations,
        "triples": n_triples,
        "self_loops": sum(int(r["self_loops"]) for r in relations),  # type: ignore[call-overload]
        "mean_degree": 2 * n_triples / n_entities if n_entities else 0.0,
        "density": n_triples / (n_entities * (n_entities - 1))
        if n_entities > 1
        else 0.0,
        "components": len(components),
        "largest_wcc_share": max(components) / n_entities if n_entities else 0.0,
        # Degree heterogeneity.
        "in_degree_max": max(in_degree, default=0),
        "in_degree_std": statistics.pstdev(in_degree) if in_degree else 0.0,
        "in_degree_cv": coefficient_of_variation(in_degree),
        "in_degree_gini": gini(in_degree),
        "in_degree_zero_share": in_degree.count(0) / n_entities if n_entities else 0.0,
        "out_degree_max": max(out_degree, default=0),
        "out_degree_std": statistics.pstdev(out_degree) if out_degree else 0.0,
        "out_degree_cv": coefficient_of_variation(out_degree),
        "out_degree_gini": gini(out_degree),
        "out_degree_zero_share": out_degree.count(0) / n_entities
        if n_entities
        else 0.0,
        # Relation frequency distribution.
        "relation_freq_gini": gini(frequencies),
        "relation_freq_entropy": normalized_entropy(frequencies),
        "relation_freq_top_share": max(frequencies) / n_triples if n_triples else 0.0,
        "relation_freq_min": min(frequencies, default=0),
        "relation_freq_median": statistics.median(frequencies) if frequencies else 0,
        "relation_freq_max": max(frequencies, default=0),
        # Relation cardinality.
        **{f"relations_{c}": by_class[c] for c in CARDINALITY_CLASSES},
        **{
            f"triples_share_{c}": triples_by_class[c] / n_triples if n_triples else 0.0
            for c in CARDINALITY_CLASSES
        },
        # Relational patterns measured on the data.
        "symmetric_relations": sum(bool(r["symmetric"]) for r in relations),
        "relations_with_inverse": sum(bool(r["has_inverse"]) for r in relations),
        "contained_relations": sum(bool(r["is_contained"]) for r in relations),
        "mean_domain_share": statistics.fmean(
            float(r["domain_share"]) for r in relations
        )
        if relations
        else 0.0,  # type: ignore[arg-type]
        "mean_range_share": statistics.fmean(float(r["range_share"]) for r in relations)
        if relations
        else 0.0,  # type: ignore[arg-type]
        # Relational patterns of the mined rules.
        **rule_descriptors(root / name / f"{name}.csv"),
    }
    for r in relations:
        r.update({"dataset": name, "set": copy_set, "level": level})
    return row, relations


def write_csv(
    path: Path, rows: list[dict[str, object]], first: tuple[str, ...] = ()
) -> None:
    """Floats are written in full (`repr`), so a row read back from the cache
    gives the same tables as the row counted."""
    if not rows:
        return
    fields = list(first) + [k for k in rows[0] if k not in first]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: repr(v) if isinstance(v, float) else v for k, v in row.items()}
            )


# Summary table: header, key of the dataset row, and format.
SUMMARY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("|V|", "entities", "int"),
    ("|R|", "relations", "int"),
    ("|E|", "triples", "int"),
    ("Mean deg.", "mean_degree", "1f"),
    ("Density", "density", "sci"),
    ("Comp.", "components", "int"),
    ("WCC", "largest_wcc_share", "2f"),
    ("Gini in", "in_degree_gini", "2f"),
    ("Gini out", "out_degree_gini", "2f"),
    ("Max in", "in_degree_max", "int"),
    ("Max out", "out_degree_max", "int"),
    ("Gini rel.", "relation_freq_gini", "2f"),
    ("H rel.", "relation_freq_entropy", "2f"),
    ("1-1", "relations_1-1", "int"),
    ("1-N", "relations_1-N", "int"),
    ("N-1", "relations_N-1", "int"),
    ("N-M", "relations_N-M", "int"),
    ("Sym.", "symmetric_relations", "int"),
    ("Inv.", "relations_with_inverse", "int"),
    ("Incl.", "contained_relations", "int"),
    ("Rules", "rules_mined", "int"),
    ("R sym.", "rules_symmetry", "int"),
    ("R inv.", "rules_inversion", "int"),
    ("R hier.", "rules_hierarchy", "int"),
    ("R comp.", "rules_composition", "int"),
)

# Comparison of the copies with their source.
COMPARISON_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("|E|", "triples", "int"),
    ("|V|", "entities", "int"),
    ("|L|", "relations", "int"),
    ("Comp.", "components", "int"),
    ("Density", "density", "sci"),
    ("Max in", "in_degree_max", "int"),
    ("Max out", "out_degree_max", "int"),
    ("Std in", "in_degree_std", "2f"),
    ("Std out", "out_degree_std", "2f"),
)
# Columns of the per-descriptor tables: header and set. NAME is this generator,
# as in `results_table.py`.
METHOD_COLUMNS = (
    ("Original", "base"),
    ("PyGraft", "pygraft"),
    ("KBgen", "kbgen"),
    ("NAME", "ours"),
)
# The rule levels of the structural tables (docs/rule-levels.md).
LEVELS = ("pca1", "r25", "r50", "r75", "r100")
# The level being replaced by pca1, left out of every table of the rule levels.
IGNORED_LEVEL = "std1"
METRIC_TITLES = {
    "triples": "Number of triples |E|",
    "entities": "Number of entities |V|",
    "relations": "Number of relations |L|",
    "components": "Number of weakly connected components",
    "density": "Density |E| / (|V| (|V| - 1))",
    "in_degree_max": "Maximum in-degree",
    "out_degree_max": "Maximum out-degree",
    "in_degree_std": "Standard deviation of the in-degree",
    "out_degree_std": "Standard deviation of the out-degree",
}


def format_value(value: object, fmt: str, latex: bool = False) -> str:
    if value == "" or value is None:
        return "–"
    if fmt == "int":
        text = f"{int(value):,}"  # type: ignore[call-overload]
        return text.replace(",", "{,}") if latex else text
    if fmt == "sci":
        mantissa, exponent = f"{float(value):.1e}".split("e")  # type: ignore[arg-type]
        return (
            f"${mantissa}\\times10^{{{int(exponent)}}}$"
            if latex
            else f"{mantissa}e{int(exponent)}"
        )
    return f"{float(value):.{fmt[0]}f}"  # type: ignore[arg-type]


def dataset_label(row: dict[str, object]) -> str:
    label = str(row["variant"]) if row["family"] != "SGKG" else f"SGKG{row['variant']}"
    if row["family"] != "SGKG":
        label = (
            f"{row['family']} {label}" if label != "complete" else str(row["family"])
        )
    if row["set"] == "base":
        return label
    copy = f"{row['set']}, {row['level']}" if row["level"] else str(row["set"])
    return f"{label} ({copy})"


# Description of every descriptor for `dataset_characteristics.md`. The source
# lines are wrapped; `descriptor_lines` joins each paragraph and list item into
# one line. `{threshold}` and `{cardinality}` are filled in.
DESCRIPTORS = """\
## Descriptors

Each dataset is a graph G = (V, R, E): V is the set of entities, R the set of
relations and E the set of distinct triples (h, r, t), with h, t in V and r in R.
Repeated triples are counted once. A source dataset is read from
`<dataset>.unified.tsv` and a copy from `<dataset>.tsv`. A copy is labelled with
its generator (ours, kbgen, pygraft) and, for a rule-level copy, its level
(pca1, std1, or r25 to r100, listed in `exp_datasets/rule_levels.tsv`). The table
shows the main descriptors, and `dataset_characteristics.csv` has every one of
them under the column name given in parentheses.

### Size and density

- |V| (`entities`): the number of distinct entities, those that appear as the
  subject or the object of a triple.
- |R| (`relations`): the number of distinct relations.
- |E| (`triples`): the number of distinct triples.
- Self-loops (`self_loops`, CSV only): the number of triples with h = t.
- Mean deg. (`mean_degree`): 2|E| / |V|, the mean number of triples an entity
  takes part in, as subject or object.
- Density (`density`): |E| / (|V| (|V| − 1)), the number of triples over the
  number of ordered pairs of distinct entities, the density of a directed
  graph without relation labels. |E| counts distinct triples, so a pair linked
  by several relations counts once per relation.
- Comp. (`components`): the number of weakly connected components, found with
  union-find over the triples, ignoring their direction and relation.
- WCC (`largest_wcc_share`): the share of entities in the largest weakly
  connected component.

### Degree heterogeneity

The in-degree of an entity is the number of triples with the entity as object,
and its out-degree the number with the entity as subject, over all relations.
Every entity of V is counted, including those with degree 0.

- Max in, Max out (`in_degree_max`, `out_degree_max`): the largest in- and
  out-degree.
- Std in, Std out (`in_degree_std`, `out_degree_std`, CSV and comparison
  table): the population standard deviation of the degrees.
- CV (`in_degree_cv`, `out_degree_cv`, CSV only): the coefficient of variation,
  the standard deviation of the degrees divided by their mean (|E| / |V|).
- Gini in, Gini out (`in_degree_gini`, `out_degree_gini`): the Gini coefficient
  of the degrees, G = 2 Σᵢ i·x₍ᵢ₎ / (n Σᵢ xᵢ) − (n + 1) / n, with the n degrees
  x₍₁₎ ≤ … ≤ x₍ₙ₎ sorted in ascending order. It is 0 when every entity has the
  same degree and approaches 1 when a few entities hold almost every triple.
- Zero share (`in_degree_zero_share`, `out_degree_zero_share`, CSV only): the
  share of entities that are never an object (in) or never a subject (out).

### Relation frequency distribution

The frequency f_r of a relation r is its number of triples.

- Gini rel. (`relation_freq_gini`): the Gini coefficient of the frequencies,
  computed as for the degrees.
- H rel. (`relation_freq_entropy`): the normalized Shannon entropy
  −Σ_r p_r ln p_r / ln |R|, with p_r = f_r / |E|. It is 1 when every relation
  has the same frequency and lower when a few relations dominate.
- Top share (`relation_freq_top_share`, CSV only): the share of triples of the
  most frequent relation.
- Min, median, max (`relation_freq_min`, `relation_freq_median`,
  `relation_freq_max`, CSV only): the smallest, median and largest frequency.

### Relation cardinality

For each relation r, tph_r = f_r / (number of distinct heads of r) is its mean
number of tails per head, and hpt_r = f_r / (number of distinct tails of r) its
mean number of heads per tail. Following Bordes et al. (2013), a relation is 1-1
if both are below {cardinality}, 1-N if only tph_r reaches {cardinality}, N-1 if
only hpt_r does, and N-M if both do. The classes use means, so a 1-1 relation
can have some heads with several tails.

- 1-1, 1-N, N-1, N-M (`relations_1-1`, …, `relations_N-M`): the number of
  relations of each class.
- Triple share (`triples_share_1-1`, …, `triples_share_N-M`, CSV only): the
  share of triples whose relation is of each class.

`relation_characteristics.csv` gives tph_r, hpt_r and the class of each
relation.

### Relational patterns in the data

For each relation r, only its pairs (h, t) with h ≠ t are counted:

- symmetry: the share of these pairs whose reverse (t, h) is also in r;
- inverse: the largest share, over every other relation r', of the pairs whose
  reverse (t, h) is in r';
- inclusion: the largest share, over every other relation r', of the pairs
  (h, t) that are also in r'.

The table counts the relations whose share reaches {threshold}:

- Sym. (`symmetric_relations`): symmetric relations.
- Inv. (`relations_with_inverse`): relations with an inverse.
- Incl. (`contained_relations`): relations contained in another relation.
- Domain, range (`mean_domain_share`, `mean_range_share`, CSV only): the mean,
  over the relations, of the share of entities that are a head (domain) or a
  tail (range) of the relation. The datasets have no schema or type triples, so
  domain and range are described only by how many entities each relation uses.

`relation_characteristics.csv` gives each relation's symmetry, its closest
inverse and the relation that contains the most of it, with their shares.

### Relational patterns in the mined rules

The rules are those of `<dataset>.csv`, mined by AMIE with its default settings:
closed rules of at most three atoms, head coverage at least 0.01, and standard
and PCA confidence at least 0.1. Each rule is classified by the shape of its
atoms, with head r(x, y):

- symmetry: r(y, x) ⇒ r(x, y);
- inversion: r'(y, x) ⇒ r(x, y), with r' ≠ r;
- hierarchy: r'(x, y) ⇒ r(x, y);
- composition: two body atoms that form a path from x to y through a third
  variable z, in any direction, such as r₁(x, z) ∧ r₂(z, y) ⇒ r(x, y);
- intersection: two body atoms, both over x and y, such as
  r₁(x, y) ∧ r₂(y, x) ⇒ r(x, y);
- other: any other shape.

The columns are:

- Rules (`rules_mined`): the number of mined rules.
- Std = 1 (`rules_std1`, CSV only): the rules with standard confidence 1, the
  ones the generator keeps by default.
- R sym., R inv., R hier., R comp. (`rules_symmetry`, `rules_inversion`,
  `rules_hierarchy`, `rules_composition`): the rules of each pattern.
  `rules_intersection` and `rules_other` are in the CSV only.
"""


def descriptor_lines(threshold: float) -> list[str]:
    """The descriptor section of the Markdown file, one paragraph or list item
    per line."""
    text = DESCRIPTORS.format(
        threshold=f"{threshold:.0%}", cardinality=CARDINALITY_THRESHOLD
    )
    lines: list[str] = []
    joinable = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            joinable = False
        elif joinable and not stripped.startswith(("- ", "#")):
            lines[-1] += " " + stripped
        else:
            lines.append(stripped)
            joinable = not stripped.startswith("#")
    return lines


def markdown_table(
    rows: list[dict[str, object]], columns: tuple[tuple[str, str, str], ...]
) -> list[str]:
    headers = ["Dataset"] + [h.replace("|", "\\|") for h, _, _ in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] + ["---:"] * len(columns)) + "|",
    ]
    for row in rows:
        cells = [dataset_label(row)] + [format_value(row[k], f) for _, k, f in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def write_markdown(path: Path, rows: list[dict[str, object]], threshold: float) -> None:
    lines = [
        "# Dataset characteristics",
        "",
        "Generated by `scripts/characterize_datasets.py`; "
        "`dataset_characteristics.csv` has every descriptor and "
        "`relation_characteristics.csv` one row per relation.",
        "",
        *markdown_table(rows, SUMMARY_COLUMNS),
        "",
        *descriptor_lines(threshold),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_latex(
    path: Path,
    rows: list[dict[str, object]],
    columns: tuple[tuple[str, str, str], ...] = SUMMARY_COLUMNS,
    group: str = "family",
) -> None:
    """A tabular of `rows`, with a rule between rows of different `group`."""
    headers = ["Dataset"] + [
        f"${h}$" if h.startswith("|") else h for h, _, _ in columns
    ]
    lines = [
        "% Generated by scripts/characterize_datasets.py",
        "\\begin{tabular}{l" + "r" * len(columns) + "}",
        "\\toprule",
        " & ".join(headers) + " \\\\",
        "\\midrule",
    ]
    previous = None
    for row in rows:
        if previous is not None and row[group] != previous:
            lines.append("\\midrule")
        previous = row[group]
        cells = [dataset_label(row).replace("_", "\\_")] + [
            format_value(row[k], f, latex=True) for _, k, f in columns
        ]
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def comparison_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """The rows of the comparison, one block per source: the source dataset, then
    its copies. A copy whose source is not among the rows keeps its own block."""
    sources = {str(r["dataset"]) for r in rows if r["set"] == "base"}
    missing = sorted({str(r["source"]) for r in rows if r["set"] != "base"} - sources)
    if missing:
        print(f"Copies without their source in the run: {', '.join(missing)}")
    columns = ("dataset", "set", "level", "source", "family", "variant")
    keys = columns + tuple(k for _, k, _ in COMPARISON_COLUMNS)
    # `rows` is sorted by family, variant, set and level, so each source comes
    # before its copies; the stable sort only groups the rows by source.
    order = {
        source: i
        for i, source in enumerate(dict.fromkeys(str(r["source"]) for r in rows))
    }
    picked = [{k: r[k] for k in keys} for r in rows]
    return sorted(picked, key=lambda r: order[str(r["source"])])


def write_comparison(out: Path, rows: list[dict[str, object]]) -> None:
    write_csv(out / "structural_comparison.csv", rows)
    lines = [
        "# Structural comparison of the datasets and their copies",
        "",
        "Generated by `scripts/characterize_datasets.py`. Each block is a source "
        "dataset followed by its copies: PyGraft's, then the rule-level copies of "
        "ours and kbgen. The descriptors are defined in "
        "`dataset_characteristics.md`; Std is the population standard deviation of "
        "the in- or out-degree over every entity, and Density is "
        "|E| / (|V| (|V| − 1)).",
        "",
    ]
    previous = None
    block: list[dict[str, object]] = []
    for row in [*rows, None]:
        source = None if row is None else row["source"]
        if block and source != previous:
            lines += [f"## {dataset_label(block[0]).split(' (')[0]}", ""]
            lines += [*markdown_table(block, COMPARISON_COLUMNS), ""]
            block = []
        if row is not None:
            block.append(row)
            previous = source
    (out / "structural_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    write_latex(
        out / "structural_comparison.tex", rows, COMPARISON_COLUMNS, group="source"
    )


def method_rows(rows: list[dict[str, object]], level: str) -> list[dict[str, object]]:
    """One row per source dataset, in `sort_key` order, with its own descriptors
    and those of its copies under `<set>:<key>` ("" for a missing copy): ours and
    kbgen at the rule level `level`, PyGraft's made without rules."""
    copies = {
        (str(r["source"]), str(r["set"])): r
        for r in rows
        if (r["set"] == "pygraft" and not r["level"])
        or (r["set"] in ("ours", "kbgen") and r["level"] == level)
    }
    table = []
    for row in rows:
        if row["set"] != "base":
            continue
        source = str(row["dataset"])
        out: dict[str, object] = {
            "dataset": source,
            "label": dataset_label(row),
            "family": row["family"],
        }
        for _, copy_set in METHOD_COLUMNS:
            found = row if copy_set == "base" else copies.get((source, copy_set))
            for _, key, _ in COMPARISON_COLUMNS:
                out[f"{copy_set}:{key}"] = "" if found is None else found[key]
        table.append(out)
    return table


def write_metric_tables(out: Path, rows: list[dict[str, object]]) -> None:
    """`structural_tables/<key>_<level>.csv`, `.md` and `.tex` for each descriptor
    of `COMPARISON_COLUMNS` and level of LEVELS: datasets as rows, methods as
    columns."""
    for level in LEVELS:
        write_level_tables(out, rows, level)


def write_level_tables(out: Path, rows: list[dict[str, object]], level: str) -> None:
    """The tables of `write_metric_tables` at one rule level."""
    table = method_rows(rows, level)
    if not table:
        return
    folder = out / "structural_tables"
    folder.mkdir(parents=True, exist_ok=True)
    names = [name for name, _ in METHOD_COLUMNS]
    for _, key, fmt in COMPARISON_COLUMNS:
        title = f"{METRIC_TITLES[key]}, level {level}"
        stem = f"{key}_{level}"
        values = [[r[f"{s}:{key}"] for _, s in METHOD_COLUMNS] for r in table]
        with (folder / f"{stem}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["dataset", "label", *(s for _, s in METHOD_COLUMNS)])
            for r, vals in zip(table, values, strict=True):
                writer.writerow(
                    [
                        r["dataset"],
                        r["label"],
                        *(repr(v) if isinstance(v, float) else v for v in vals),
                    ]
                )
        md = [
            f"# {title}",
            "",
            "Generated by `scripts/characterize_datasets.py`. One row per source "
            f"dataset; KBgen and NAME (this generator) at rule level {level}, "
            "PyGraft made without rules. – marks a copy that does not exist.",
            "",
            "| Dataset | " + " | ".join(names) + " |",
            "|---|" + "---:|" * len(names),
        ]
        for r, vals in zip(table, values, strict=True):
            cells = [format_value(v, fmt) for v in vals]
            md.append(f"| {r['label']} | " + " | ".join(cells) + " |")
        (folder / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        tex = [
            "% Generated by scripts/characterize_datasets.py",
            f"% {title}",
            "\\begin{tabular}{l" + "r" * len(names) + "}",
            "\\toprule",
            "Dataset & " + " & ".join(names) + " \\\\",
            "\\midrule",
        ]
        previous = None
        for r, vals in zip(table, values, strict=True):
            if previous is not None and r["family"] != previous:
                tex.append("\\midrule")
            previous = r["family"]
            cells = [format_value(v, fmt, latex=True) for v in vals]
            label = str(r["label"]).replace("_", "\\_")
            tex.append(f"{label} & " + " & ".join(cells) + " \\\\")
        tex += ["\\bottomrule", "\\end{tabular}"]
        (folder / f"{stem}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")


# The size descriptors of the size MSE table: header and key.
SIZE_COLUMNS = (("|V|", "entities"), ("|E|", "triples"))
# The levels that `--skip-levels` can leave out of the size MSE table.
SKIPPABLE_LEVELS = ("pca1",)


def size_mse(rows: list[dict[str, object]], skip: set[str]) -> list[dict[str, object]]:
    """The relative mean squared error of the entity and triple counts of each
    generator's copies, ((copy - original) / original)² averaged over the source
    datasets, one group of rows per rule level of the ours and kbgen copies
    (except the levels of `skip`) and a last group, level `All`, over every
    (source, level) pair of those levels. A level's sources are those with an
    ours or kbgen copy at that level; each generator is measured on those of
    them where it has a copy, `n` of them. PyGraft, made without rules, is thus
    measured on the same sources as the level's rule-based copies."""
    levels = sorted(
        {
            str(r["level"])
            for r in rows
            if r["set"] in ("ours", "kbgen")
            and r["level"]
            and r["level"] not in skip
            and r["level"] != IGNORED_LEVEL
        },
        key=level_key,
    )
    generators = [s for _, s in METHOD_COLUMNS if s != "base"]
    errors: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for level in levels:
        for row in method_rows(rows, level):
            if row["ours:entities"] == "" and row["kbgen:entities"] == "":
                continue
            for copy_set in generators:
                for _, key in SIZE_COLUMNS:
                    copy, original = row[f"{copy_set}:{key}"], row[f"base:{key}"]
                    if copy == "":
                        continue
                    error = (float(copy) - float(original)) / float(original)  # type: ignore[arg-type]
                    for group in (level, "All"):
                        errors[group, copy_set, key].append(error**2)
    return [
        {
            "level": level,
            "generator": copy_set,
            "descriptor": key,
            "mse": statistics.fmean(values)
            if (values := errors[level, copy_set, key])
            else "",
            "n": len(values),
        }
        for level in ([*levels, "All"] if levels else [])
        for copy_set in generators
        for _, key in SIZE_COLUMNS
    ]


def write_size_mse(out: Path, table: list[dict[str, object]], skip: set[str]) -> str:
    """`structural_tables/size_mse.csv`, `.md` and `.tex` (`size_mse_without_<levels>`
    when levels are skipped) from the rows of `size_mse`: one row per level,
    one column per generator and size descriptor, the smallest error of each row
    and descriptor in bold in LaTeX. Returns the stem written."""
    folder = out / "structural_tables"
    folder.mkdir(parents=True, exist_ok=True)
    stem = "size_mse" + "".join(f"_without_{s}" for s in sorted(skip, key=level_key))
    write_csv(folder / f"{stem}.csv", table)
    cells = {
        (str(r["level"]), str(r["generator"]), str(r["descriptor"])): r for r in table
    }
    levels = list(dict.fromkeys(str(r["level"]) for r in table))
    methods = [(name, s) for name, s in METHOD_COLUMNS if s != "base"]
    skipped = (
        f" The {' and '.join(sorted(skip, key=level_key))} copies are left out."
        if skip
        else ""
    )
    md = [
        "# Relative MSE of the number of entities and triples",
        "",
        "Generated by `scripts/characterize_datasets.py`. Each cell is the mean, over "
        "the source datasets with an ours or kbgen copy at that level, of "
        "((copy − original) / original)², for the number of entities |V| and of "
        "distinct triples |E|; n is the number of copies counted. PyGraft, made "
        "without rules, is measured on the same sources at every level. All pools "
        f"every (source, level) pair of the levels above.{skipped}",
        "",
        "| Level | "
        + " | ".join(
            f"{name} {h.replace('|', chr(92) + '|')}"
            for name, _ in methods
            for h, _ in SIZE_COLUMNS
        )
        + " | "
        + " | ".join(f"{name} n" for name, _ in methods)
        + " |",
        "|---|" + "---:|" * (len(methods) * (len(SIZE_COLUMNS) + 1)),
    ]
    for level in levels:
        values = [
            format_value(cells[level, s, key]["mse"], "sci")
            for _, s in methods
            for _, key in SIZE_COLUMNS
        ]
        counts = [str(cells[level, s, SIZE_COLUMNS[0][1]]["n"]) for _, s in methods]
        md.append(f"| {level} | " + " | ".join(values + counts) + " |")
    (folder / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    n = len(SIZE_COLUMNS)
    tex = [
        "% Generated by scripts/characterize_datasets.py",
        "% Relative MSE ((copy - original) / original)^2 of the number of entities "
        f"and triples, per rule level.{skipped}",
        "\\begin{tabular}{l" + "|".join(["r" * n] * len(methods)) + "}",
        "\\toprule",
        " & "
        + " & ".join(
            f"\\multicolumn{{{n}}}{{c{'|' if i < len(methods) - 1 else ''}}}{{{name}}}"
            for i, (name, _) in enumerate(methods)
        )
        + " \\\\",
        "Level & "
        + " & ".join(f"${h}$" for _ in methods for h, _ in SIZE_COLUMNS)
        + " \\\\",
        "\\midrule",
    ]
    for level in levels:
        if level == "All":
            tex.append("\\midrule")
        best = {
            key: min(
                (
                    float(v)  # type: ignore[arg-type]
                    for _, s in methods
                    if (v := cells[level, s, key]["mse"]) != ""
                ),
                default=None,
            )
            for _, key in SIZE_COLUMNS
        }
        row = []
        for _, s in methods:
            for _, key in SIZE_COLUMNS:
                value = cells[level, s, key]["mse"]
                text = format_value(value, "sci", latex=True)
                if value != "" and float(value) == best[key]:  # type: ignore[arg-type]
                    text = f"$\\mathbf{{{text.strip('$')}}}$"
                row.append(text)
        tex.append(f"{level} & " + " & ".join(row) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (folder / f"{stem}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")
    return stem


def size_errors(rows: list[dict[str, object]], level: str) -> list[dict[str, object]]:
    """Per source dataset, generator and size descriptor at the rule level
    `level`: the original and copy values, their difference copy - original and
    the squared relative error ((copy - original) / original)², the quantity
    `size_mse` averages. Empty cells for a missing copy; PyGraft's copy, made
    without rules, is the same at every level."""
    table = []
    for row in method_rows(rows, level):
        for _, copy_set in METHOD_COLUMNS[1:]:
            for _, key in SIZE_COLUMNS:
                original = int(float(row[f"base:{key}"]))  # type: ignore[arg-type]
                copy = row[f"{copy_set}:{key}"]
                difference: object = ""
                squared_error: object = ""
                if copy != "":
                    copy = int(float(copy))  # type: ignore[arg-type]
                    difference = copy - original
                    squared_error = (difference / original) ** 2
                table.append(
                    {
                        "dataset": row["dataset"],
                        "label": row["label"],
                        "family": row["family"],
                        "level": level,
                        "generator": copy_set,
                        "descriptor": key,
                        "original": original,
                        "copy": copy,
                        "difference": difference,
                        "squared_error": squared_error,
                    }
                )
    return table


def format_difference(value: object, latex: bool = False) -> str:
    """A signed difference with thousands separators, `–` when missing."""
    if value == "":
        return "–"
    text = f"{int(value):+,}"  # type: ignore[call-overload]
    return f"${text.replace(',', '{,}')}$" if latex else text


def write_size_errors(out: Path, table: list[dict[str, object]], level: str) -> str:
    """`structural_tables/size_error_<level>.csv`, `.md` and `.tex` from the rows of
    `size_errors`: one row per source dataset, and for each generator and size
    descriptor the difference copy - original (Δ) and the squared relative error
    (SE), the smallest SE of each row and descriptor in bold in LaTeX. Returns the
    stem written."""
    folder = out / "structural_tables"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"size_error_{level}"
    write_csv(folder / f"{stem}.csv", table)
    cells = {
        (str(r["dataset"]), str(r["generator"]), str(r["descriptor"])): r for r in table
    }
    sources = list(dict.fromkeys(str(r["dataset"]) for r in table))
    first = {str(r["dataset"]): r for r in reversed(table)}
    methods = [(name, s) for name, s in METHOD_COLUMNS if s != "base"]
    md = [
        f"# Entity and triple counts of each copy against its original, level {level}",
        "",
        "Generated by `scripts/characterize_datasets.py`. One row per source "
        "dataset; KBgen and NAME (this generator) at rule level "
        f"{level}, PyGraft made without rules. For the number of entities |V| and "
        "of distinct triples |E|, Δ is copy − original and SE the squared "
        "relative error ((copy − original) / original)², the quantity averaged in "
        "`size_mse.md`. – marks a copy that does not exist.",
        "",
        "| Dataset | "
        + " | ".join(
            f"{name} {sym} {h.replace('|', chr(92) + '|')}"
            for name, _ in methods
            for h, _ in SIZE_COLUMNS
            for sym in ("Δ", "SE")
        )
        + " |",
        "|---|" + "---:|" * (len(methods) * len(SIZE_COLUMNS) * 2),
    ]
    for source in sources:
        values = []
        for _, s in methods:
            for _, key in SIZE_COLUMNS:
                cell = cells[source, s, key]
                values.append(format_difference(cell["difference"]))
                values.append(format_value(cell["squared_error"], "sci"))
        md.append(f"| {first[source]['label']} | " + " | ".join(values) + " |")
    (folder / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    n = 2 * len(SIZE_COLUMNS)
    tex = [
        "% Generated by scripts/characterize_datasets.py",
        f"% Entity and triple counts of each copy against its original, level {level}:",
        "% difference copy - original and squared relative error "
        "((copy - original) / original)^2.",
        "\\begin{tabular}{l" + "|".join(["r" * n] * len(methods)) + "}",
        "\\toprule",
        " & "
        + " & ".join(
            f"\\multicolumn{{{n}}}{{c{'|' if i < len(methods) - 1 else ''}}}{{{name}}}"
            for i, (name, _) in enumerate(methods)
        )
        + " \\\\",
        "Dataset & "
        + " & ".join(
            f"${sym}{h}$"
            for _ in methods
            for h, _ in SIZE_COLUMNS
            for sym in ("\\Delta", "\\mathrm{SE}\\,")
        )
        + " \\\\",
        "\\midrule",
    ]
    previous = None
    for source in sources:
        if previous is not None and first[source]["family"] != previous:
            tex.append("\\midrule")
        previous = first[source]["family"]
        best = {
            key: min(
                (
                    float(v)  # type: ignore[arg-type]
                    for _, s in methods
                    if (v := cells[source, s, key]["squared_error"]) != ""
                ),
                default=None,
            )
            for _, key in SIZE_COLUMNS
        }
        row = []
        for _, s in methods:
            for _, key in SIZE_COLUMNS:
                cell = cells[source, s, key]
                row.append(format_difference(cell["difference"], latex=True))
                value = cell["squared_error"]
                text = format_value(value, "sci", latex=True)
                if value != "" and float(value) == best[key]:  # type: ignore[arg-type]
                    text = f"$\\mathbf{{{text.strip('$')}}}$"
                row.append(text)
        label = str(first[source]["label"]).replace("_", "\\_")
        tex.append(f"{label} & " + " & ".join(row) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (folder / f"{stem}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")
    return stem


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def load_cache(
    out: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, list[dict[str, object]]]]:
    """The rows of the `dataset_characteristics.csv` and
    `relation_characteristics.csv` written by an earlier run, by dataset, with
    their values as the strings written. Empty if a file is missing; a row
    without `sha256` (written before the cache) never matches."""
    rows: dict[str, dict[str, object]] = {}
    relations: dict[str, list[dict[str, object]]] = defaultdict(list)
    graph_csv = out / "dataset_characteristics.csv"
    relation_csv = out / "relation_characteristics.csv"
    if not graph_csv.is_file() or not relation_csv.is_file():
        return rows, relations
    with graph_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["dataset"]] = dict(row)
    with relation_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            relations[row["dataset"]].append(dict(row))
    return rows, relations


def find_datasets(root: Path, chosen_set: str, names: list[str] | None) -> list[str]:
    found = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        name = folder.name
        copy_set = parse_name(name)[0]
        if not triples_path(root, name).is_file():
            continue
        if names is not None:
            if name in names:
                found.append(name)
        elif chosen_set in ("all", copy_set):
            found.append(name)
    if names is not None:
        missing = sorted(set(names) - set(found))
        if missing:
            sys.exit(f"No triple file for: {', '.join(missing)}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="folder of the dataset folders",
    )
    parser.add_argument(
        "--set",
        default="base",
        choices=("base", *COPY_SETS, "all"),
        help="datasets to describe when --datasets is not given",
    )
    parser.add_argument(
        "--datasets", nargs="+", help="dataset folders to describe, of any set"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exp_analysis"),
        help="folder of the output files",
    )
    parser.add_argument(
        "--pattern-threshold",
        type=float,
        default=0.9,
        help="share of a relation's pairs that a symmetry, inverse or "
        "inclusion needs (default 0.9)",
    )
    parser.add_argument(
        "--skip-levels",
        nargs="+",
        default=[],
        choices=SKIPPABLE_LEVELS,
        help="copies left out of the size MSE table (structural_tables/size_mse_"
        "without_<level>.*); every other output keeps them",
    )
    parser.add_argument(
        "--size-levels",
        nargs="+",
        default=[],
        metavar="LEVEL",
        help="rule levels (e.g. r100) whose per-graph size table "
        "structural_tables/size_error_<level>.* is written: difference and squared "
        "relative error of |V| and |E| of each copy",
    )
    parser.add_argument(
        "--recount",
        action="store_true",
        help="count every graph again instead of reusing the rows of the "
        "existing CSV files whose triple file is unchanged",
    )
    args = parser.parse_args()

    names = find_datasets(args.root, args.set, args.datasets)
    if not names:
        sys.exit(f"No dataset found under {args.root}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cached_rows, cached_relations = (
        ({}, {}) if args.recount else load_cache(args.output_dir)
    )
    rows: list[dict[str, object]] = []
    relation_table: list[dict[str, object]] = []
    counted = 0
    for name in names:
        start = time.perf_counter()
        digest = file_sha256(triples_path(args.root, name))
        cached = cached_rows.get(name)
        if (
            cached is not None
            and cached.get("sha256") == digest
            and cached.get("descriptor_version") == str(DESCRIPTOR_VERSION)
            and float(str(cached.get("pattern_threshold") or "nan"))
            == args.pattern_threshold
        ):
            row = cached
            row.update(rule_descriptors(args.root / name / f"{name}.csv"))
            relations = cached_relations.get(name, [])
            how = "cached"
        else:
            row, relations = characterize(args.root, name, args.pattern_threshold)
            row["sha256"] = digest
            row["pattern_threshold"] = args.pattern_threshold
            row["descriptor_version"] = DESCRIPTOR_VERSION
            counted += 1
            how = f"{time.perf_counter() - start:.1f} s"
        rows.append(row)
        relation_table.extend(relations)
        print(
            f"{name}: {int(str(row['entities'])):,} entities, "
            f"{row['relations']} relations, "
            f"{int(str(row['triples'])):,} triples ({how})",
            flush=True,
        )
    print(f"Counted {counted} graphs, reused {len(names) - counted} from the CSV")

    rows.sort(key=sort_key)
    order = {row["dataset"]: i for i, row in enumerate(rows)}
    relation_table.sort(key=lambda r: (order[r["dataset"]], -int(r["triples"])))  # type: ignore[call-overload]

    out = args.output_dir
    write_csv(out / "dataset_characteristics.csv", rows)
    write_csv(
        out / "relation_characteristics.csv",
        relation_table,
        first=("dataset", "set", "level"),
    )
    write_markdown(out / "dataset_characteristics.md", rows, args.pattern_threshold)
    write_latex(out / "dataset_characteristics.tex", rows)
    write_comparison(out, comparison_rows(rows))
    write_metric_tables(out, rows)
    skip = set(args.skip_levels)
    mse_table = size_mse(rows, skip)
    if mse_table:
        stem = write_size_mse(out, mse_table, skip)
        print(f"Wrote structural_tables/{stem}.{{csv,md,tex}}")
    for level in args.size_levels:
        if not any(r["set"] in ("ours", "kbgen") and r["level"] == level for r in rows):
            print(f"No ours or kbgen copy at level {level}; no size table written")
            continue
        stem = write_size_errors(out, size_errors(rows, level), level)
        print(f"Wrote structural_tables/{stem}.{{csv,md,tex}}")
    print(
        "Wrote dataset_characteristics.{csv,md,tex}, structural_comparison."
        "{csv,md,tex}, structural_tables/ and relation_characteristics.csv "
        f"to {out}"
    )


if __name__ == "__main__":
    main()
