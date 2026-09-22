"""Defines data structures and logic for Horn Rule-based systems.

Provides the HornRule dataclass, Pandas CSV parsing, and rule-set-level
operations (dependency graphs, cycle detection) used to drive EDB/synthetic
graph generation.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import networkx as nx
import pandas as pd

from skgg.utils import format_term

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


class _RuleRow(Protocol):
    """Definines the expected structure of a rule DataFrame row."""

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
    rule_id: str,
    term_mapping: dict[str, str],
) -> HornRule:
    """Extracts a HornRule object from a pandas DataFrame row.

    Args:
        row: A named tuple representing a row form the rules DataFrame.
        rule_id: Assigned string identifier for the rule.

    Returns:
        A populated HornRule instance.
    """

    def _parse_metric(value: float | None) -> float:
        """Returns float or Python None for Pandas/Numpy NaNs securely."""
        return 0.0 if (pd.isna(value) or value is None) else float(value)

    rule = HornRule(
        signature=RuleSignature(
            rule_id=rule_id,
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
def parse_rule_set(
    rules_file: Path,
    term_mapping: dict[str, str],
    pca_threshold: float,
) -> dict[str, HornRule]:
    """Parse a rules CSV into a dict of HornRules identified by rule_id.

    Args:
        rules_file: Path to the rules CSV file.
        term_mapping: Mapping from ontology terms to their formatted form.
        pca_threshold: Minimum PCA confidence a rule must have to be classified
            "POSITIVE" and kept; rules below it, or with missing PCA
            confidence, are classified "NEGATIVE"/"UNKNOWN" respectively and
            dropped from the returned rule set entirely.

    Returns:
        A dict of the surviving (classification == "POSITIVE") HornRules,
        identified by rule_id.
    """
    rule_dataframe = pd.read_csv(rules_file)

    rule_dataframe["classification"] = "NEGATIVE"
    rule_dataframe.loc[
        rule_dataframe["pca_confidence"] >= pca_threshold, "classification"
    ] = "POSITIVE"
    rule_dataframe.loc[rule_dataframe["pca_confidence"].isna(), "classification"] = (
        "UNKNOWN"
    )

    n_total = len(rule_dataframe)
    rule_dataframe = rule_dataframe[
        rule_dataframe["classification"] == "POSITIVE"
    ].reset_index(drop=True)
    logger.info(
        "Kept %d/%d rules with PCA confidence >= %.3f (classification == "
        "POSITIVE); dropped %d (NEGATIVE or UNKNOWN).",
        len(rule_dataframe),
        n_total,
        pca_threshold,
        n_total - len(rule_dataframe),
    )

    rules: dict[str, HornRule] = {}

    for row_id, row in enumerate(rule_dataframe.itertuples(index=False), start=1):
        rule_id = str(row_id)
        rule = _parse_horn_rule(
            row=row,
            rule_id=rule_id,
            term_mapping=term_mapping,
        )

        rules[rule.rule_id] = rule

    return rules


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
        cycle
        for cycle in nx.simple_cycles(graph)
        if grounded_preds.isdisjoint(cycle)
    ]
    return sorted(cycles, key=lambda cycle: (len(cycle), cycle))
