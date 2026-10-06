"""Defines data structures and logic for Horn Rule-based systems.

Provides the HornRule dataclass, Pandas CSV parsing, and rule-set-level
operations (dependency graphs, cycle detection and cyclic rule removal) used to
drive EDB/synthetic graph generation.
"""

import csv
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import networkx as nx
import pandas as pd

from skgg.core.utils import format_term, short_term

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Atom:
    """Represents a triple composed of subject predicate object.

    The values of the each of these attributes are strings that represent a variable
    (e.g., ?a), a resource (e.g., ex:Patient), or a literal (e.g., 10.0).

    Attributes:
        subject: Subject of the triple.
        predicate: Represents the relation between subject and object.
        obj: Object of the triple.
    """

    subject: str
    predicate: str
    obj: str

    def __str__(self) -> str:
        """Returns a string of the atom as 'subject predicate object' string."""
        return f"{self.subject} {self.predicate} {self.obj}"

    def __lt__(self, other: "Atom") -> bool:
        """Enables native sorting of Atoms by their string representation."""
        if not isinstance(other, Atom):
            return NotImplemented
        return str(self) < str(other)


@dataclass(frozen=True, slots=True)
class RuleSignature:
    """Signature of a Horn Rule."""

    rule_id: str
    body: frozenset[Atom]
    head: Atom

    def get_variables(self) -> set[str]:
        """Return unique variables starting with '?' in this rule."""
        return {
            term
            for atom in (self.head, *self.body)
            for term in (atom.subject, atom.obj)
            if term.startswith("?")
        }

    def get_head_variables(self) -> set[str]:
        """Return unique variables starting with '?' in the rule's head."""
        return {
            term for term in (self.head.subject, self.head.obj) if term.startswith("?")
        }

    def get_body_variables(self) -> set[str]:
        """Return unique variables starting with '?' in the rule's body."""
        return {
            term
            for atom in self.body
            for term in (atom.subject, atom.obj)
            if term.startswith("?")
        }

    def get_predicates(self) -> set[str]:
        """Return the set of unique predicates in the rule."""
        return {atom.predicate for atom in (self.body | {self.head})}

    def get_body_predicates(self) -> set[str]:
        """Return the set of unique predicates in the body atoms."""
        return {atom.predicate for atom in self.body}

    def get_extensional_body(self, intensional_preds: set[str]) -> frozenset[Atom]:
        """Returns the rule's body excluding atoms with intensional predicates."""
        return frozenset(
            {atom for atom in self.body if atom.predicate not in intensional_preds}
        )

    def get_extensional_preds(self, intensional_preds: set[str]) -> set[str]:
        """Returns body predicates excluding the ones in 'intensional_preds'."""
        return {
            atom.predicate
            for atom in self.body
            if atom.predicate not in intensional_preds
        }


@dataclass(slots=True)
class HornRule:
    """Represents a Horn Rule.

    Attributes:
        signature: Representation of the rule that contains head and body.
        pca_confidence: PCA confidence score of this rule.
        support: Support of this rule.
        head_coverage: Head coverage of the rule.
    """

    signature: RuleSignature
    support: int | float
    head_coverage: float | None = None
    std_confidence: float | None = None
    pca_confidence: float | None = None
    classification: str = "UNKNOWN"
    closed: bool = False

    @property
    def head(self) -> Atom:
        """Exposes the head of the rule directly for convenience."""
        return self.signature.head

    @property
    def body(self) -> frozenset[Atom]:
        """Exposes the body of the rule directly for convenience."""
        return self.signature.body

    @property
    def rule_id(self) -> str:
        """Exposes the signature's id for convenience."""
        return self.signature.rule_id

    def get_body_predicates(self) -> set[str]:
        """Return the set of unique predicates in the body atoms."""
        return self.signature.get_body_predicates()

    def get_extensional_body(self, intensional_preds: set[str]) -> frozenset[Atom]:
        """Returns rule's body excluding atoms that contain intensional predicates."""
        return self.signature.get_extensional_body(intensional_preds)

    def get_predicates(self) -> set[str]:
        """Returns a set containing all predicates present in the rule"""
        return self.signature.get_predicates()

    def get_variables(self) -> set[str]:
        """Returns a set with all the variables present in the rule."""
        return self.signature.get_variables()

    def get_head_variables(self) -> set[str]:
        """Returns a set with the variables present in the rule's head."""
        return self.signature.get_head_variables()

    def get_extensional_preds(self, intensional_preds: set[str]) -> set[str]:
        """Returns body predicates excluding the ones in 'intensional_preds'."""
        return self.signature.get_extensional_preds(intensional_preds)

    def __len__(self) -> int:
        """Returns the number of atoms in the rule's body."""
        return len(self.body)


# ---------------------------------------------------------------------------
# Rule parsing.
# ---------------------------------------------------------------------------
ATOM_PATTERN = re.compile(r"(\?\w+)\s+(\S+)\s+(\S+)")
# Std confidence a rule needs to be kept when no PCA threshold is given.
DEFAULT_STD_THRESHOLD = 1.0


class _RuleRow(Protocol):
    """Definines the expected structure of a rule DataFrame row."""

    rule_id: str
    body: str
    head: str
    std_confidence: float
    positive_examples: float
    head_coverage: float
    pca_confidence: float
    classification: str


def _parse_body(body_str: str, term_mapping: dict[str, str]) -> frozenset[Atom]:
    """Parses a body string containing one or more atoms into a frozen set of atoms."""
    if not body_str:
        logger.warning("Parsing empty body. Is this supposed to happen?")
        return frozenset()

    return frozenset(
        Atom(
            format_term(m.group(1), term_mapping),
            format_term(m.group(2), term_mapping),
            format_term(m.group(3), term_mapping),
        )
        for m in ATOM_PATTERN.finditer(body_str)
    )


def _parse_head(head_str: str, term_mapping: dict[str, str]) -> Atom:
    """Parses a head string into an Atom."""
    if not head_str:
        raise ValueError("Head string format is not valid: Empty string.")

    parts = head_str.split()
    if len(parts) < 3:
        raise ValueError("Head string format is not valid: Too few components.")

    return Atom(
        format_term(parts[0], term_mapping),
        format_term(parts[1], term_mapping),
        format_term(parts[2], term_mapping),
    )


def _parse_horn_rule(
    row: _RuleRow,
    term_mapping: dict[str, str],
) -> HornRule:
    """Extracts a HornRule object from a pandas DataFrame row.

    Args:
        row: A named tuple representing a row form the rules DataFrame. Its
            `rule_id` becomes the rule's identifier.

    Returns:
        A populated HornRule instance.
    """

    def _parse_metric(value: float | None) -> float:
        """Returns float or Python None for Pandas/Numpy NaNs securely."""
        return 0.0 if (pd.isna(value) or value is None) else float(value)

    rule = HornRule(
        signature=RuleSignature(
            rule_id=row.rule_id.strip(),
            body=_parse_body(str(row.body), term_mapping),
            head=_parse_head(str(row.head), term_mapping),
        ),
        support=_parse_metric(row.positive_examples),
        head_coverage=_parse_metric(row.head_coverage),
        std_confidence=_parse_metric(row.std_confidence),
        pca_confidence=_parse_metric(row.pca_confidence),
        classification=row.classification,
    )

    return rule


# -----------------------------------------------------------------------------
# Rule set handling
# ---------------------------------------------------------------------------
def rule_sort_key(rule_id: str) -> tuple[int, int, str]:
    """Sort key for rule IDs: numeric IDs first in numeric order, then the rest as text."""
    if rule_id.isdigit():
        return (0, int(rule_id), "")
    return (1, 0, rule_id)


def parse_rule_set(
    rules_file: Path,
    term_mapping: dict[str, str],
    pca_threshold: float | None = None,
    std_threshold: float | None = None,
) -> dict[str, HornRule]:
    """Parse a rules CSV into a dict of HornRules identified by rule_id.

    Rule IDs are read from the CSV's `rule_id` column, so a rule keeps its ID
    whatever the thresholds.

    Args:
        rules_file: Path to the rules CSV file.
        term_mapping: Mapping from ontology terms to their formatted form.
        pca_threshold: Minimum PCA confidence a rule must have to be kept, or
            None to not filter on PCA confidence.
        std_threshold: Minimum std confidence a rule must have to be kept, or
            None to not filter on std confidence.

    A rule that meets every threshold given is classified "POSITIVE" and kept.
    A rule missing one of the confidences filtered on is classified "UNKNOWN",
    and any other rule "NEGATIVE"; both are dropped from the returned rule set.

    Returns:
        A dict of the surviving (classification == "POSITIVE") HornRules,
        identified by rule_id.

    Raises:
        ValueError: If the CSV has no `rule_id` column, or a rule_id is empty
            or repeated.
    """
    rule_dataframe = pd.read_csv(rules_file, dtype={"rule_id": str})

    if "rule_id" not in rule_dataframe.columns:
        raise ValueError(f"{rules_file} has no 'rule_id' column.")
    rule_ids = rule_dataframe["rule_id"].str.strip()
    if rule_ids.isna().any() or (rule_ids == "").any():
        raise ValueError(f"{rules_file} has rules with an empty rule_id.")
    duplicates = sorted(set(rule_ids[rule_ids.duplicated()]), key=rule_sort_key)
    if duplicates:
        raise ValueError(f"{rules_file} repeats rule_ids: {', '.join(duplicates)}.")

    thresholds = {
        column: threshold
        for column, threshold in (
            ("pca_confidence", pca_threshold),
            ("std_confidence", std_threshold),
        )
        if threshold is not None
    }
    positive = pd.Series(True, index=rule_dataframe.index)
    unknown = pd.Series(False, index=rule_dataframe.index)
    for column, threshold in thresholds.items():
        positive &= rule_dataframe[column] >= threshold
        unknown |= rule_dataframe[column].isna()
    rule_dataframe["classification"] = "NEGATIVE"
    rule_dataframe.loc[positive, "classification"] = "POSITIVE"
    rule_dataframe.loc[unknown, "classification"] = "UNKNOWN"

    n_total = len(rule_dataframe)
    rule_dataframe = rule_dataframe[
        rule_dataframe["classification"] == "POSITIVE"
    ].reset_index(drop=True)
    criteria = (
        " and ".join(
            f"{column.replace('_', ' ').replace('pca', 'PCA')} >= {threshold:.3f}"
            for column, threshold in thresholds.items()
        )
        or "no confidence threshold"
    )
    logger.info(
        "Kept %d/%d rules with %s (classification == POSITIVE); dropped %d "
        "(NEGATIVE or UNKNOWN).",
        len(rule_dataframe),
        n_total,
        criteria,
        n_total - len(rule_dataframe),
    )

    rules: dict[str, HornRule] = {}

    for row in rule_dataframe.itertuples(index=False):
        rule = _parse_horn_rule(
            row=row,
            term_mapping=term_mapping,
        )

        rules[rule.rule_id] = rule

    return rules


def write_used_rules(rules_file: Path, rule_ids: Iterable[str]) -> Path:
    """Copies the rows of `rules_file` whose rule_id is in `rule_ids` to
    `<stem>_used<suffix>` next to it, e.g. `rules.csv` -> `rules_used.csv`.

    Rows keep the input's columns, order, values and line endings, so the
    output can be read back with `parse_rule_set`.

    Returns:
        The path written.
    """
    output = rules_file.with_name(f"{rules_file.stem}_used{rules_file.suffix}")
    keep = set(rule_ids)
    with rules_file.open(newline="") as f:
        text = f.read()
    header, *rows = csv.reader(text.splitlines(keepends=True))
    id_column = header.index("rule_id")
    used = [row for row in rows if row and row[id_column].strip() in keep]

    line_end = "\r\n" if "\r\n" in text else "\n"
    with output.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator=line_end)
        writer.writerow(header)
        writer.writerows(used)

    logger.info("Saved %d used rules to `%s`.", len(used), output)
    return output


def get_impacted_rules(
    preds: Iterable[str], rules: dict[str, HornRule]
) -> dict[str, tuple[list[str], list[str]]]:
    """Returns, for each predicate, the IDs of the rules with it as head and the IDs
    of the rules with it in their body, each sorted with `rule_sort_key`. A new
    triple with that predicate can only change the support of these rules."""
    impacted: dict[str, tuple[list[str], list[str]]] = {}
    for pred in preds:
        head_of = [r_id for r_id, r in rules.items() if r.head.predicate == pred]
        body_of = [r_id for r_id, r in rules.items() if pred in r.get_body_predicates()]
        impacted[pred] = (
            sorted(head_of, key=rule_sort_key),
            sorted(body_of, key=rule_sort_key),
        )
    return impacted


def get_extensional_dependencies(
    rules: dict[str, HornRule],
) -> dict[str, set[str]]:
    """Builds a dictionary that represents extensional predicate dependencies. A rule is
    dependent of any other rule extensional-wise if they share an extensional predicate.

    From all the rules that share an extensional predicate, the larger rules are more
    restrictive.
    """

    intensional_preds = {rule.head.predicate for rule in rules.values()}

    rule_dependency: dict[str, set[str]] = {r_id: set() for r_id in rules}

    # Sort from smallest to largest body counting only extensional preds
    sorted_ids = sorted(
        (rule_id for rule_id in rules.keys()),
        key=lambda r_id: len(rules[r_id].get_extensional_body(intensional_preds)),
    )

    for i, current_id in enumerate(sorted_ids):
        current_rule = rules[current_id]
        current_ext_preds = current_rule.get_extensional_preds(intensional_preds)

        if not current_ext_preds:
            continue

        for next_id in sorted_ids[i + 1 :]:
            next_rule = rules[next_id]
            next_ext_preds = next_rule.get_extensional_preds(intensional_preds)

            if any(pred in next_ext_preds for pred in current_ext_preds):
                c_length = len(current_rule.get_extensional_body(intensional_preds))
                n_length = len(next_rule.get_extensional_body(intensional_preds))

                if c_length == n_length and next_rule.support > current_rule.support:
                    rule_dependency[next_id].add(current_id)
                else:
                    rule_dependency[current_id].add(next_id)

    return rule_dependency


def get_relation_graph(rules: dict[str, HornRule]) -> nx.DiGraph:
    """Builds a directed graph over predicates ("relation types") in a rule
    set, to reveal cyclic dependencies between rules.

    Every predicate appearing anywhere in `rules` becomes a node. For each
    rule, an edge is added from each of its body predicates to its head
    predicate; a rule whose head predicate also appears in its own body
    produces a self-loop (e.g. a recursive rule like `p(x,y) :- p(x,z),
    q(z,y)` adds a p -> p self-loop and a q -> p edge). Each edge carries a
    `rule_ids` attribute (the set of rule ids that produced it), so a
    detected cycle can be traced back to the rules responsible.

    Args:
        rules: Dict of rule_id -> HornRule, e.g. from `parse_rule_set`.

    Returns:
        A `networkx.DiGraph` suitable for cycle detection, e.g.
        `list(nx.simple_cycles(relation_graph(rules)))`.
    """
    graph = nx.DiGraph()

    for rule in rules.values():
        head_pred = rule.head.predicate
        graph.add_node(head_pred)
        for body_pred in rule.get_body_predicates():
            graph.add_node(body_pred)
            if graph.has_edge(body_pred, head_pred):
                graph[body_pred][head_pred]["rule_ids"].add(rule.rule_id)
            else:
                graph.add_edge(body_pred, head_pred, rule_ids={rule.rule_id})

    return graph


def find_stale_cycles(
    rules: dict[str, HornRule], grounded_preds: set[str]
) -> list[list[str]]:
    """Finds cycles of the relation graph that completion can never ground.

    A cycle (a self-loop `p -> p`, a 2-cycle `A -> B -> A`, or a longer one) is stale
    when none of its predicates has triples yet: every rule producing them needs
    one of them first, so forward chaining cannot start. A cyclic predicate that is
    already grounded (by another rule or by the EDB) is an ordinary recursive rule and
    needs no seed.

    Args:
        rules: Dict of rule_id -> HornRule, e.g. from `parse_rule_set`.
        grounded_preds: Predicates (in the rules' bracketed `<uri>` form) that
            currently have at least one triple in the graph.

    Returns:
        The stale cycles as lists of predicates in traversal order, sorted for
        determinism.
    """
    graph = get_relation_graph(rules)
    cycles = [
        cycle for cycle in nx.simple_cycles(graph) if grounded_preds.isdisjoint(cycle)
    ]
    return sorted(cycles, key=lambda cycle: (len(cycle), cycle))


def remove_cyclic_rules(rules: dict[str, HornRule]) -> dict[str, HornRule]:
    """Deletes from `rules`, in place, every rule with an edge on a cycle of the
    relation graph (`get_relation_graph`), so that no cycle is left.

    An edge `body predicate -> head predicate` is on a cycle when both predicates
    are in the same strongly connected component (a self-loop `p -> p` included).
    The head predicate of a removed rule becomes extensional, and EDB generation
    produces it, unless a remaining rule still derives it.

    Run it on the rule set a run actually uses (after `parse_rule_set`'s confidence
    filter), before EDB generation.

    Returns:
        The removed rules, identified by rule_id.
    """
    graph = get_relation_graph(rules)
    components = list(nx.strongly_connected_components(graph))
    component_of = {pred: i for i, preds in enumerate(components) for pred in preds}

    # Rule ids with an edge inside each component, keyed by the component's index.
    cyclic_ids: dict[int, set[str]] = defaultdict(set)
    for src, dst, rule_ids in graph.edges(data="rule_ids"):
        if component_of[src] == component_of[dst]:
            cyclic_ids[component_of[src]] |= rule_ids

    if not cyclic_ids:
        return {}

    for i, rule_ids in sorted(
        cyclic_ids.items(),
        key=lambda item: sorted(map(short_term, components[item[0]])),
    ):
        logger.info(
            "Removed rules %s: on the cycle of %s.",
            ", ".join(sorted(rule_ids, key=rule_sort_key)),
            ", ".join(sorted(short_term(p) for p in components[i])),
        )
    removed_ids = set().union(*cyclic_ids.values())
    return _pop_rules(rules, removed_ids)


def _pop_rules(rules: dict[str, HornRule], rule_ids: set[str]) -> dict[str, HornRule]:
    """Deletes `rule_ids` from `rules` and logs which head predicates become
    extensional, which stay intensional and through which rules.

    Returns:
        The removed rules, identified by rule_id.
    """
    removed = {
        rule_id: rules.pop(rule_id) for rule_id in sorted(rule_ids, key=rule_sort_key)
    }

    producers: dict[str, list[str]] = defaultdict(list)
    for rule in rules.values():
        producers[rule.head.predicate].append(rule.rule_id)
    removed_heads = {rule.head.predicate for rule in removed.values()}
    extensional = sorted(short_term(p) for p in removed_heads if p not in producers)
    if extensional:
        logger.info(
            "Predicates made extensional by cyclic rule removal: %s",
            ", ".join(extensional),
        )
    for pred in sorted(removed_heads & producers.keys(), key=short_term):
        logger.info(
            "%s stays intensional: derived by rules %s.",
            short_term(pred),
            ", ".join(sorted(producers[pred], key=rule_sort_key)),
        )
    logger.info("Removed %d cyclic rules; %d rules left.", len(removed), len(rules))
    return removed


# Strongly connected components with more predicates than this fall back to
# removing every cyclic rule: the exact selection keeps 2^n states for n predicates.
MAX_EXACT_COMPONENT_SIZE = 20


def _best_order(preds: list[str], rules: list[HornRule]) -> tuple[list[str], set[str]]:
    """Finds the order of `preds` that keeps the most of `rules`, where a rule is
    kept when every body predicate of it in `preds` comes before its head.

    Dynamic programming over the subsets of `preds`: the best result for the set
    of predicates placed so far doesn't depend on their order, so each subset keeps
    only its best (rule count, total support). Placing predicate v after subset S
    keeps the rules with head v whose body predicates in `preds` are all in S.
    Ties keep the first candidate found, with `preds` in a fixed order, so the
    result is deterministic. A rule with its head in its own body is never kept.

    Returns:
        The order, and the IDs of the kept rules.
    """
    index = {pred: i for i, pred in enumerate(preds)}
    # Per head predicate: (bitmask of the rule's body predicates in `preds`, rule).
    by_head: list[list[tuple[int, HornRule]]] = [[] for _ in preds]
    for rule in rules:
        head = index[rule.head.predicate]
        mask = 0
        for pred in rule.get_body_predicates():
            if pred in index:
                mask |= 1 << index[pred]
        if not mask >> head & 1:
            by_head[head].append((mask, rule))

    n = len(preds)
    best: list[tuple[int, float]] = [(-1, 0.0)] * (1 << n)
    best[0] = (0, 0.0)
    placed_last = [-1] * (1 << n)
    for subset in range(1 << n):
        count, support = best[subset]
        for v in range(n):
            if subset >> v & 1:
                continue
            gained = [r for mask, r in by_head[v] if mask & ~subset == 0]
            candidate = (count + len(gained), support + sum(r.support for r in gained))
            extended = subset | 1 << v
            if candidate > best[extended]:
                best[extended] = candidate
                placed_last[extended] = v

    order: list[str] = []
    subset = (1 << n) - 1
    while subset:
        v = placed_last[subset]
        order.append(preds[v])
        subset &= ~(1 << v)
    order.reverse()

    position = {pred: i for i, pred in enumerate(order)}
    kept = {
        rule.rule_id
        for rule in rules
        if all(
            position[p] < position[rule.head.predicate]
            for p in rule.get_body_predicates()
            if p in position
        )
    }
    return order, kept


def remove_minimal_cyclic_rules(rules: dict[str, HornRule]) -> dict[str, HornRule]:
    """Deletes from `rules`, in place, the fewest rules that leave the relation
    graph (`get_relation_graph`) with no cycle.

    The rules left are the largest rule set with no cycle; among sets of the same
    size, the one with the most total support, then a fixed predicate order, so the
    same rules always give the same result. A recursive rule (its head predicate in
    its own body) is always removed. See "Removing the fewest rules" in
    `docs/algorithm.md` for the method.

    Each strongly connected component of the relation graph is solved on its own
    (`_best_order`), since only predicates in the same component constrain each
    other. A component with more than `MAX_EXACT_COMPONENT_SIZE` predicates loses
    every rule with an edge inside it, as in `remove_cyclic_rules`.

    Returns:
        The removed rules, identified by rule_id.
    """
    graph = get_relation_graph(rules)
    removed_ids: set[str] = set()

    for component in nx.strongly_connected_components(graph):
        cyclic_ids = {
            rule_id
            for src, dst, ids in graph.subgraph(component).edges(data="rule_ids")
            for rule_id in ids
        }
        if not cyclic_ids:
            continue
        names = ", ".join(sorted(short_term(p) for p in component))
        if len(component) > MAX_EXACT_COMPONENT_SIZE:
            logger.warning(
                "Cycle of %d predicates (%s) is too large for the exact selection; "
                "removing all its %d cyclic rules.",
                len(component),
                names,
                len(cyclic_ids),
            )
            removed_ids |= cyclic_ids
            continue

        order, kept = _best_order(
            sorted(component), [rules[rule_id] for rule_id in cyclic_ids]
        )
        position = {pred: i for i, pred in enumerate(order)}
        logger.info(
            "Cycle of %s: keeping %d of %d cyclic rules with the order %s.",
            names,
            len(kept),
            len(cyclic_ids),
            " < ".join(short_term(p) for p in order),
        )
        for rule_id in sorted(cyclic_ids - kept, key=rule_sort_key):
            rule = rules[rule_id]
            head = rule.head.predicate
            if head in rule.get_body_predicates():
                reason = "recursive"
            else:
                later = sorted(
                    short_term(p)
                    for p in rule.get_body_predicates()
                    if p in position and position[p] > position[head]
                )
                reason = f"{', '.join(later)} after {short_term(head)} in the order"
            logger.info("Removed rule %s: %s.", rule_id, reason)
        removed_ids |= cyclic_ids - kept

    if not removed_ids:
        return {}
    return _pop_rules(rules, removed_ids)
