"""Computes the structural descriptors of the benchmark datasets (objective O1).

For each folder directly under the root (default `exp_datasets`) with a
`<folder>.unified.tsv`, reads its distinct triples and, if present, the rules
mined from it (`<folder>.csv`), and computes five groups of descriptors:

- size and density: entities, relations, triples, self-loops, mean degree
  (2|E|/|V|), density |E| / (|V|^2 |R|) and the share of entities in the largest
  weakly connected component;
- degree heterogeneity: for the in-degree and the out-degree of each entity
  (triples with the entity as object or subject, over all relations), the
  maximum, the coefficient of variation (std / mean), the Gini coefficient and
  the share of entities with degree 0;
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
  dataset grouped by dataset family.

`--set` picks the datasets as `train_kge.py` does: `base` (default), the
`_ours`, `_kbgen` or `_pygraft` copies (read from `<folder>/<folder>.tsv`), or
`all`. Only the Python standard library is used, and one dataset is read at a
time.

    python scripts/characterize_datasets.py
    python scripts/characterize_datasets.py --datasets french family
    python scripts/characterize_datasets.py --set all --output-dir /tmp/characteristics
"""

import argparse
import csv
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

COPY_SETS = ("ours", "kbgen", "pygraft")
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


def largest_component_share(graph: Graph) -> float:
    """Share of entities in the largest weakly connected component."""
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
    sizes = Counter(find(x) for x in range(len(parent)))
    return max(sizes.values()) / len(parent) if parent else 0.0


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


def dataset_set(name: str) -> str:
    for copy_set in COPY_SETS:
        if name.endswith(f"_{copy_set}"):
            return copy_set
    return "base"


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


def sort_key(row: dict[str, object]) -> tuple[int, str, int, str, str]:
    family = str(row["family"])
    variant = str(row["variant"])
    return (
        FAMILY_ORDER.index(family) if family in FAMILY_ORDER else len(FAMILY_ORDER),
        family,
        VARIANT_ORDER.index(variant)
        if variant in VARIANT_ORDER
        else len(VARIANT_ORDER),
        variant,
        str(row["set"]),
    )


def characterize(
    root: Path, name: str, threshold: float
) -> tuple[dict[str, object], list[dict[str, object]]]:
    copy_set = dataset_set(name)
    source = name.removesuffix(f"_{copy_set}") if copy_set != "base" else name
    triples_file = (
        root / name / (f"{name}.unified.tsv" if copy_set == "base" else f"{name}.tsv")
    )
    graph = read_graph(triples_file)
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
        "density": n_triples / (n_entities**2 * n_relations)
        if n_entities and n_relations
        else 0.0,
        "largest_wcc_share": largest_component_share(graph),
        # Degree heterogeneity.
        "in_degree_max": max(in_degree, default=0),
        "in_degree_cv": coefficient_of_variation(in_degree),
        "in_degree_gini": gini(in_degree),
        "in_degree_zero_share": in_degree.count(0) / n_entities if n_entities else 0.0,
        "out_degree_max": max(out_degree, default=0),
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
        r.update({"dataset": name, "set": copy_set})
    return row, relations


def write_csv(
    path: Path, rows: list[dict[str, object]], first: tuple[str, ...] = ()
) -> None:
    if not rows:
        return
    fields = list(first) + [k for k in rows[0] if k not in first]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: f"{v:.6g}" if isinstance(v, float) else v for k, v in row.items()}
            )


# Summary table: header, key of the dataset row, and format.
SUMMARY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("|V|", "entities", "int"),
    ("|R|", "relations", "int"),
    ("|E|", "triples", "int"),
    ("Mean deg.", "mean_degree", "1f"),
    ("Density", "density", "sci"),
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
    return label if row["set"] == "base" else f"{label} ({row['set']})"


# Description of every descriptor for `dataset_characteristics.md`. The source
# lines are wrapped; `descriptor_lines` joins each paragraph and list item into
# one line. `{threshold}` and `{cardinality}` are filled in.
DESCRIPTORS = """\
## Descriptors

Each dataset is a graph G = (V, R, E): V is the set of entities, R the set of
relations and E the set of distinct triples (h, r, t), with h, t in V and r in R.
Repeated triples are counted once. The table shows the main descriptors, and
`dataset_characteristics.csv` has every one of them under the column name given
in parentheses.

### Size and density

- |V| (`entities`): the number of distinct entities, those that appear as the
  subject or the object of a triple.
- |R| (`relations`): the number of distinct relations.
- |E| (`triples`): the number of distinct triples.
- Self-loops (`self_loops`, CSV only): the number of triples with h = t.
- Mean deg. (`mean_degree`): 2|E| / |V|, the mean number of triples an entity
  takes part in, as subject or object.
- Density (`density`): |E| / (|V|² |R|), the share of all possible labelled
  triples that are in the graph. Knowledge graphs are very sparse, so it is
  between 10⁻⁷ and 10⁻⁴ here.
- WCC (`largest_wcc_share`): the share of entities in the largest weakly
  connected component, found with union-find over the triples, ignoring their
  direction and relation.

### Degree heterogeneity

The in-degree of an entity is the number of triples with the entity as object,
and its out-degree the number with the entity as subject, over all relations.
Every entity of V is counted, including those with degree 0.

- Max in, Max out (`in_degree_max`, `out_degree_max`): the largest in- and
  out-degree.
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


def write_markdown(path: Path, rows: list[dict[str, object]], threshold: float) -> None:
    headers = ["Dataset"] + [h.replace("|", "\\|") for h, _, _ in SUMMARY_COLUMNS]
    lines = [
        "# Dataset characteristics",
        "",
        "Generated by `scripts/characterize_datasets.py`; "
        "`dataset_characteristics.csv` has every descriptor and "
        "`relation_characteristics.csv` one row per relation.",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] + ["---:"] * len(SUMMARY_COLUMNS)) + "|",
    ]
    for row in rows:
        cells = [dataset_label(row)] + [
            format_value(row[k], f) for _, k, f in SUMMARY_COLUMNS
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", *descriptor_lines(threshold)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, object]]) -> None:
    headers = ["Dataset"] + [
        f"${h}$" if h.startswith("|") else h for h, _, _ in SUMMARY_COLUMNS
    ]
    lines = [
        "% Generated by scripts/characterize_datasets.py",
        "\\begin{tabular}{l" + "r" * len(SUMMARY_COLUMNS) + "}",
        "\\toprule",
        " & ".join(headers) + " \\\\",
        "\\midrule",
    ]
    previous = None
    for row in rows:
        if previous is not None and row["family"] != previous:
            lines.append("\\midrule")
        previous = row["family"]
        cells = [dataset_label(row).replace("_", "\\_")] + [
            format_value(row[k], f, latex=True) for _, k, f in SUMMARY_COLUMNS
        ]
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_datasets(root: Path, chosen_set: str, names: list[str] | None) -> list[str]:
    found = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        name = folder.name
        copy_set = dataset_set(name)
        triples = folder / (
            f"{name}.unified.tsv" if copy_set == "base" else f"{name}.tsv"
        )
        if not triples.is_file():
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
    args = parser.parse_args()

    names = find_datasets(args.root, args.set, args.datasets)
    if not names:
        sys.exit(f"No dataset found under {args.root}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows, relation_table = [], []
    for name in names:
        start = time.perf_counter()
        row, relations = characterize(args.root, name, args.pattern_threshold)
        rows.append(row)
        relation_table.extend(relations)
        print(
            f"{name}: {row['entities']:,} entities, {row['relations']} relations, "
            f"{row['triples']:,} triples ({time.perf_counter() - start:.1f} s)",
            flush=True,
        )

    rows.sort(key=sort_key)
    order = {row["dataset"]: i for i, row in enumerate(rows)}
    relation_table.sort(key=lambda r: (order[r["dataset"]], -int(r["triples"])))  # type: ignore[call-overload]

    out = args.output_dir
    write_csv(out / "dataset_characteristics.csv", rows)
    write_csv(
        out / "relation_characteristics.csv", relation_table, first=("dataset", "set")
    )
    write_markdown(out / "dataset_characteristics.md", rows, args.pattern_threshold)
    write_latex(out / "dataset_characteristics.tex", rows)
    print(
        "Wrote dataset_characteristics.{csv,md,tex} and "
        f"relation_characteristics.csv to {out}"
    )


if __name__ == "__main__":
    main()
