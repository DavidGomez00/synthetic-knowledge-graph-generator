"""Defines data structures and logic for Horn Rule-based systems.

Provides the HornRule dataclass, Pandas CSV parsing, and rule-set-level
helpers (rule ID order, impacted rules, extensional dependencies) used to drive
EDB/synthetic graph generation. Rule cycles are handled in `core.cycles`.
"""

import csv
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from skgg.core.utils import format_term

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


def rule_filter_label(pca_threshold: float | None) -> str:
    """Labels the confidence filter of a run for its graph URIs (see
    `GraphConfig`): `pca=0.9` for a PCA threshold, or `std=1` for the
    `DEFAULT_STD_THRESHOLD` kept when there is none. Thresholds are written
    without trailing zeros, so 1 and 1.0 give the same label."""
    if pca_threshold is not None:
        return f"pca={pca_threshold:g}"
    return f"std={DEFAULT_STD_THRESHOLD:g}"


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
