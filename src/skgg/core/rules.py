"""Defines data structures and logic for Horn Rule-based systems.

Provides the HornRule dataclass, Pandas CSV parsing, and rule-set-level
helpers (rule ID order, impacted rules, non-deducible dependencies) used to drive
F0/synthetic graph generation. Rule cycles are handled in `core.cycles`.
"""

import csv
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from skgg.core.utils import format_term

logger = logging.getLogger(__name__)

# PCA confidence that makes a rule complete (`HornRule.is_complete`), and that a rule
# needs to be kept when no PCA threshold is given (`rule_thresholds`). Until
# 2026-11-19 the generator also had a std setting, where std confidence 1 made a
# rule complete; commit 2c7dfd5d has it.
COMPLETE_CONFIDENCE = 1.0


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

    def get_non_deducible_body(self, deducible_preds: set[str]) -> frozenset[Atom]:
        """Returns the rule's body excluding atoms with deducible predicates."""
        return frozenset(
            {atom for atom in self.body if atom.predicate not in deducible_preds}
        )

    def get_non_deducible_preds(self, deducible_preds: set[str]) -> set[str]:
        """Returns body predicates excluding the ones in 'deducible_preds'."""
        return {
            atom.predicate
            for atom in self.body
            if atom.predicate not in deducible_preds
        }

    def is_symmetric(self) -> bool:
        """Returns whether the rule has the shape `p(x,y) => p(y,x)`, with `x` and
        `y` two different variables."""
        if len(self.body) != 1:
            return False
        atom = next(iter(self.body))
        return (
            atom.predicate == self.head.predicate
            and atom.subject == self.head.obj
            and atom.obj == self.head.subject
            and atom.subject != atom.obj
            and atom.subject.startswith("?")
            and atom.obj.startswith("?")
        )


@dataclass(slots=True)
class HornRule:
    """Represents a Horn Rule.

    Attributes:
        signature: Representation of the rule that contains head and body.
        pca_confidence: PCA confidence score of this rule.
        support: Support of this rule.
        head_coverage: Head coverage of the rule.
        functional_variable: The head variable AMIE computed the PCA confidence
            on (e.g. `?b`), or None if the rules CSV doesn't give it.
    """

    signature: RuleSignature
    support: int | float
    head_coverage: float | None = None
    std_confidence: float | None = None
    pca_confidence: float | None = None
    functional_variable: str | None = None
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

    def get_non_deducible_body(self, deducible_preds: set[str]) -> frozenset[Atom]:
        """Returns rule's body excluding atoms that contain deducible predicates."""
        return self.signature.get_non_deducible_body(deducible_preds)

    def get_predicates(self) -> set[str]:
        """Returns a set containing all predicates present in the rule"""
        return self.signature.get_predicates()

    def get_variables(self) -> set[str]:
        """Returns a set with all the variables present in the rule."""
        return self.signature.get_variables()

    def get_head_variables(self) -> set[str]:
        """Returns a set with the variables present in the rule's head."""
        return self.signature.get_head_variables()

    def get_non_deducible_preds(self, deducible_preds: set[str]) -> set[str]:
        """Returns body predicates excluding the ones in 'deducible_preds'."""
        return self.signature.get_non_deducible_preds(deducible_preds)

    def is_symmetric(self) -> bool:
        """Returns whether the rule has the shape `p(x,y) => p(y,x)`."""
        return self.signature.is_symmetric()

    @property
    def is_complete(self) -> bool:
        """Whether the rule is complete: its PCA confidence is >=
        `COMPLETE_CONFIDENCE` (1), so it holds for every body grounding of the source
        whose functional variable has a value for the head relation. Completion
        forward-chains the complete rules only; the others are partial rules, whose
        groundings initial-fact generation builds together with their heads (see
        `grounding_target`)."""
        return (self.pca_confidence or 0.0) >= COMPLETE_CONFIDENCE

    @property
    def grounding_target(self) -> int:
        """The support the generator builds for the rule: the whole support for a
        complete rule, and PCA confidence x support, rounded half up, for a partial
        rule (support 300 at PCA confidence 0.5 gives 150)."""
        if self.is_complete:
            return int(self.support)
        return math.floor((self.pca_confidence or 0.0) * self.support + 0.5)

    def get_initial_fact_atoms(self, deducible_preds: set[str]) -> frozenset[Atom]:
        """Returns the atoms initial-fact generation grounds for the rule: the
        non-deducible body of a complete rule, whose head completion derives later, or
        the whole body and the head of a partial rule, which nothing derives later."""
        if self.is_complete:
            return self.get_non_deducible_body(deducible_preds)
        return self.body | {self.head}

    def __len__(self) -> int:
        """Returns the number of atoms in the rule's body."""
        return len(self.body)


# ---------------------------------------------------------------------------
# Rule parsing.
# ---------------------------------------------------------------------------
ATOM_PATTERN = re.compile(r"(\?\w+)\s+(\S+)\s+(\S+)")
# Std confidence of the rules that hold on every body grounding of a graph, the rule
# set `cli/complete.py` completes a real graph with.
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

    # Optional column: rules CSVs not written by run_amie.py may lack it.
    functional_variable = getattr(row, "functional_variable", None)

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
        functional_variable=(
            None
            if pd.isna(functional_variable)
            else str(functional_variable).strip() or None
        ),
        classification=row.classification,
    )

    return rule


# -----------------------------------------------------------------------------
# Rule set handling
# ---------------------------------------------------------------------------
def rule_sort_key(rule_id: str) -> tuple[int, int, str]:
    """Sort rule ID keys: numeric IDs first in numeric order, then the rest as text."""
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


def rule_thresholds(pca_threshold: float | None) -> dict[str, float]:
    """The thresholds `parse_rule_set` keeps a run's rules with: the PCA threshold
    if one is given, or else the complete rules, PCA confidence >=
    `COMPLETE_CONFIDENCE` (1)."""
    return {
        "pca_threshold": COMPLETE_CONFIDENCE if pca_threshold is None else pca_threshold
    }


def rule_filter_label(pca_threshold: float | None) -> str:
    """Labels the confidence filter of a run for its graph URIs (see
    `GraphConfig`): `pca=0.9` for a PCA threshold, or `pca=1` for the complete
    rules kept when there is none. Thresholds are written without trailing zeros,
    so 1 and 1.0 give the same label. Until 2026-11-19 the std setting labelled its
    runs `std=1` and `pca=<t>`, and the PCA setting `pca/pca=<t>`; graphs of those
    runs keep their URIs."""
    threshold = COMPLETE_CONFIDENCE if pca_threshold is None else pca_threshold
    return f"pca={threshold:g}"


def write_used_rules(
    rules_file: Path, rule_ids: Iterable[str], output: Path | None = None
) -> Path:
    """Copies the rows of `rules_file` whose rule_id is in `rule_ids` to
    `output`, by default `<stem>_used<suffix>` next to it, e.g. `rules.csv` ->
    `rules_used.csv`.

    Rows keep the input's columns, order, values and line endings, so the
    output can be read back with `parse_rule_set`.

    Returns:
        The path written.
    """
    if output is None:
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


def get_deducible_preds(rules: dict[str, HornRule]) -> set[str]:
    """Returns the head predicates of the complete rules (`HornRule.is_complete`,
    in the rules' setting) that are not symmetric, the ones completion derives. A
    symmetric rule can't produce the first triple of its relation, so a relation
    that only its symmetric rule derives is non-deducible, and so is the head of a
    partial rule, whose heads initial-fact generation places itself."""
    return {
        rule.head.predicate
        for rule in rules.values()
        if rule.is_complete and not rule.is_symmetric()
    }


def get_undirected_preds(rules: dict[str, HornRule]) -> set[str]:
    """Returns the head predicates of the complete symmetric rules, whose relations
    are written as undirected edges. Only the symmetric rules that
    `edb.get_undirected_rule_ids` exempts from cycle removal are left in a run's
    rule set, so these are the relations it generates as undirected edges."""
    return {
        rule.head.predicate
        for rule in rules.values()
        if rule.is_complete and rule.is_symmetric()
    }


def get_non_deducible_dependencies(
    rules: dict[str, HornRule],
) -> dict[str, set[str]]:
    """Builds a dictionary that represents non-deducible predicate dependencies. A
    rule depends on any other rule through a non-deducible predicate if they share a
    predicate of the atoms initial-fact generation grounds for them
    (`HornRule.get_initial_fact_atoms`).

    From all the rules that share such a predicate, the rules with more atoms to
    ground are more restrictive; ties go to the rule with the smaller grounding
    target.
    """

    deducible_preds = get_deducible_preds(rules)
    initial_fact_atoms = {
        r_id: r.get_initial_fact_atoms(deducible_preds) for r_id, r in rules.items()
    }
    initial_fact_preds = {
        r_id: {a.predicate for a in s} for r_id, s in initial_fact_atoms.items()
    }

    rule_dependency: dict[str, set[str]] = {r_id: set() for r_id in rules}

    # Sort from fewest to most atoms to ground
    sorted_ids = sorted(rules, key=lambda r_id: len(initial_fact_atoms[r_id]))

    for i, current_id in enumerate(sorted_ids):
        current_preds = initial_fact_preds[current_id]

        if not current_preds:
            continue

        for next_id in sorted_ids[i + 1 :]:
            if current_preds & initial_fact_preds[next_id]:
                c_length = len(initial_fact_atoms[current_id])
                n_length = len(initial_fact_atoms[next_id])
                c_target = rules[current_id].grounding_target
                n_target = rules[next_id].grounding_target

                if c_length == n_length and n_target > c_target:
                    rule_dependency[next_id].add(current_id)
                else:
                    rule_dependency[current_id].add(next_id)

    return rule_dependency
