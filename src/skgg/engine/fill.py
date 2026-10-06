"""Generates the triples of the predicates still open after completion, keeping the
support of every closed rule and raising the support of open rules toward their
target.

`fill_open_predicates` runs two passes over the remaining budget of each open
predicate (source graph counts minus synthetic graph counts):

1. A rule-driven pass, for each open rule with an open body predicate: it builds
   groundings of the rule with `generator.sample_grounding_groups`, completing the
   bodies of head triples the rule doesn't explain yet when the head predicate is
   closed, or adding whole groundings with their head when it is open.
2. A random pass, which spends the budget left with draws weighted by each term's
   remaining count.

Every new triple goes through the head and body checks of `core/queries.py`
(`build_head_impact_query`, `build_body_impact_query`) for every rule it occurs in:
it may not change the support of a closed rule, may raise an open rule's support
only up to its target, and may not leave a body grounding without its head. See
"Filling open relations" in `docs/concepts.md`.
"""

import logging
import random
from collections import Counter
from dataclasses import dataclass, field

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    build_body_impact_query,
    build_head_impact_query,
    delete_triples_sparql,
    get_atom_bindings,
    get_domain,
    get_frequency,
    get_new_head_bindings,
    get_range,
    get_reflexivity,
    get_support,
    get_unsupported_head_bindings,
    has_solution,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule, rule_sort_key
from skgg.core.utils import format_triple, short_term
from skgg.engine.generator import decrement_counts, sample_grounding_groups
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)

# Draws in a row without an accepted triple after which a predicate is given up.
MAX_FILL_DRAWS = 1000
# Sampling rounds in a row without an accepted grounding after which a rule is
# given up.
MAX_EMPTY_ROUNDS = 3
# Fixed-binding rows fetched per missing head of a rule.
ROWS_PER_HEAD = 20

# Head values of a rule, in sorted head-variable order.
HeadValues = tuple[str, ...]


def _remaining(source: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    """Returns how many more triples each term needs to reach its source count."""
    return {
        term: count - current.get(term, 0)
        for term, count in source.items()
        if count > current.get(term, 0)
    }


def _parse_triple(triple: str) -> tuple[str, str, str]:
    """Splits a triple formatted by `format_triple` into its three terms."""
    subject, predicate, obj = triple.strip(" .").split(" ")
    return subject, predicate, obj


def _vars(atoms: list[Atom]) -> set[str]:
    """Returns the variables of `atoms`."""
    return {t for a in atoms for t in (a.subject, a.obj) if t.startswith("?")}


def _head_values(rule: HornRule, subject: str, obj: str) -> HeadValues:
    """Returns the head values a head triple `subject head-predicate obj` binds."""
    binding = {rule.head.subject: subject, rule.head.obj: obj}
    return tuple(binding[v] for v in sorted(rule.get_head_variables()))


@dataclass
class _FillState:
    """What both passes share: the connection, the rules with their current
    support, and the remaining budget of each open predicate."""

    client: SPARQLWrapper
    graph_uri: str
    term_mapping: dict[str, str]
    chunk_size: int
    rules: dict[str, HornRule]
    supports: dict[str, int]
    # Remaining budget per predicate: domain, range and frequency still missing,
    # and reflexive triples still allowed.
    budgets: dict[str, PredicateProfile]
    added: Counter[str] = field(default_factory=Counter)

    def is_open(self, predicate: str) -> bool:
        """Whether `predicate` still needs triples."""
        budget = self.budgets.get(predicate)
        return budget is not None and budget.frequency > 0

    def impact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        planned: tuple[str, HeadValues] | None = None,
    ) -> tuple[dict[str, int], str]:
        """Checks adding `subject predicate obj` against every rule it occurs in.

        Returns the support each open rule would gain, and an empty reason; or an
        empty dict and the reason the triple is rejected: it would change a closed
        rule's support, push an open rule past its target, or leave a body
        grounding without its head. `planned` (rule ID, head values) allows that
        one headless grounding, whose head the caller adds next.
        """
        gains: dict[str, int] = {}
        for r_id, rule in self.rules.items():
            if predicate not in rule.get_predicates():
                continue
            new: set[HeadValues] = set()
            if rule.head.predicate == predicate:
                query = build_head_impact_query(rule, subject, obj, self.graph_uri)
                if query is not None and has_solution(self.client, query):
                    new.add(_head_values(rule, subject, obj))
            for atom in sorted(rule.body):
                if atom.predicate != predicate:
                    continue
                query = build_body_impact_query(
                    rule, atom, subject, obj, self.graph_uri
                )
                if query is None:
                    continue
                for values, present in get_new_head_bindings(self.client, rule, query):
                    if present:
                        new.add(values)
                    elif planned != (r_id, values):
                        return {}, f"a grounding of rule {r_id} without its head"
            if not new:
                continue
            if rule.closed:
                return {}, f"the support of closed rule {r_id}"
            if self.supports[r_id] + len(new) > rule.support:
                return {}, f"rule {r_id} past its target support"
            gains[r_id] = len(new)
        return gains, ""

    def insert_group(
        self, triples: list[str], planned: tuple[str, HeadValues] | None = None
    ) -> Counter[str] | None:
        """Inserts `triples` one at a time, each checked with `impact` against the
        graph holding the ones before it. If one is rejected, the ones already
        inserted are deleted again.

        Returns the support gained per rule, or None if the group was rejected.
        """
        inserted: list[str] = []
        gained: Counter[str] = Counter()
        for triple in triples:
            subject, predicate, obj = _parse_triple(triple)
            gains, reason = self.impact(subject, predicate, obj, planned)
            if reason:
                logger.debug(
                    "[Fill] Rejected %s %s %s: would change %s.",
                    short_term(subject),
                    short_term(predicate),
                    short_term(obj),
                    reason,
                )
                delete_triples_sparql(self.client, self.graph_uri, inserted)
                for r_id, gain in gained.items():
                    self.supports[r_id] -= gain
                return None
            insert_triples_sparql(
                client=self.client,
                graph_uri=self.graph_uri,
                triple_stream=[triple],
                chunk_size=self.chunk_size,
            )
            inserted.append(triple)
            for r_id, gain in gains.items():
                self.supports[r_id] += gain
            gained.update(gains)

        for triple in triples:
            self.added[_parse_triple(triple)[1]] += 1
        for r_id in gained:
            rule = self.rules[r_id]
            if not rule.closed and self.supports[r_id] >= rule.support:
                rule.closed = True
                logger.info(
                    "[Fill] Rule %s reached its target support %d.",
                    r_id,
                    int(rule.support),
                )
        return gained

    def restore_budget(self, triples: list[str]) -> None:
        """Gives back the budget `sample_grounding_groups` took for `triples`."""
        for triple in triples:
            subject, predicate, obj = _parse_triple(triple)
            budget = self.budgets[predicate]
            budget.domain[subject] = budget.domain.get(subject, 0) + 1
            budget.range[obj] = budget.range.get(obj, 0) + 1
            budget.frequency += 1


def classify_open_rule(rule: HornRule, state: _FillState) -> str:
    """Returns how fill can raise an open rule's support: "A" if every body
    predicate is closed (it can't), "B1" if a body predicate is open and the head
    predicate is closed (complete bodies for head triples the rule doesn't explain
    yet), "B2" if both are open (add new groundings with their head)."""
    if not any(state.is_open(p) for p in rule.get_body_predicates()):
        return "A"
    return "B2" if state.is_open(rule.head.predicate) else "B1"


def _fill_rule(state: _FillState, r_id: str) -> None:
    """Rule-driven pass for one open rule: builds groundings until the rule
    reaches its target support, its case becomes A, or `MAX_EMPTY_ROUNDS`
    rounds in a row accept nothing."""
    rule = state.rules[r_id]
    start = state.supports[r_id]
    added_before = state.added.copy()
    case = classify_open_rule(rule, state)
    empty_rounds = 0

    while not rule.closed and empty_rounds < MAX_EMPTY_ROUNDS:
        case = classify_open_rule(rule, state)
        if case == "A":
            break
        missing = int(rule.support) - state.supports[r_id]
        open_atoms = [a for a in sorted(rule.body) if state.is_open(a.predicate)]
        closed_atoms = [a for a in sorted(rule.body) if a not in open_atoms]
        atoms = open_atoms + ([rule.head] if case == "B2" else [])
        head_vars = rule.get_head_variables()
        # Variables of the existing facts that the new triples must join with.
        joined = _vars(atoms) | head_vars
        limit = ROWS_PER_HEAD * missing

        fixed: list[dict[str, str]] | None = None
        if case == "B1":
            fixed = get_unsupported_head_bindings(
                state.client,
                rule,
                closed_atoms,
                (_vars(closed_atoms) | head_vars) & joined,
                state.graph_uri,
                limit,
            )
        elif closed_atoms:
            fixed = get_atom_bindings(
                state.client,
                closed_atoms,
                _vars(closed_atoms) & joined,
                state.graph_uri,
                limit,
            )
        if fixed is not None and not fixed:
            logger.debug("[Fill] Rule %s: no existing facts to join with.", r_id)
            break

        groups = sample_grounding_groups(
            client=state.client,
            target_uri=state.graph_uri,
            atoms=atoms,
            head_vars=head_vars,
            profiles=state.budgets,
            missing_heads=missing,
            term_mapping=state.term_mapping,
            chunk_size=state.chunk_size,
            fixed_bindings=fixed,
        )
        accepted = 0
        for group in groups:
            planned = None
            if case == "B2":
                head_triples = [
                    t for t in group if _parse_triple(t)[1] == rule.head.predicate
                ]
                if head_triples:
                    subject, _, obj = _parse_triple(head_triples[-1])
                    planned = (r_id, _head_values(rule, subject, obj))
            if rule.closed or state.insert_group(group, planned) is None:
                state.restore_budget(group)
            else:
                accepted += 1
        empty_rounds = 0 if accepted else empty_rounds + 1

    added = state.added - added_before
    logger.info(
        "[Fill] Rule %s (%s): support %d -> %d/%d (%s)%s.",
        r_id,
        case,
        start,
        state.supports[r_id],
        int(rule.support),
        "closed" if rule.closed else "open",
        ", "
        + ", ".join(f"{short_term(p)} +{n}" for p, n in sorted(added.items()))
        if added
        else "",
    )


def _fill_predicate(state: _FillState, predicate: str, target: int) -> None:
    """Random pass for one predicate: adds triples until it reaches `target`, its
    remaining domain or range runs out, or `MAX_FILL_DRAWS` draws in a row add
    nothing."""
    budget = state.budgets[predicate]
    domain, p_range = budget.domain, budget.range
    current = get_frequency(state.client, predicate, state.graph_uri)
    existing = {
        (row["?s"], row["?o"])
        for row in get_atom_bindings(
            state.client,
            [Atom("?s", predicate, "?o")],
            ["?s", "?o"],
            state.graph_uri,
            current + 1,
        )
    }
    rejected: set[tuple[str, str]] = set()
    raised: Counter[str] = Counter()
    added = 0
    draws = 0
    stop_reason = "target reached"

    while budget.frequency > 0:
        if not domain or not p_range:
            stop_reason = "no domain or range left"
            break
        if draws >= MAX_FILL_DRAWS:
            stop_reason = f"{MAX_FILL_DRAWS} draws in a row without a safe triple"
            break
        draws += 1

        subject = random.choices(list(domain), weights=list(domain.values()))[0]
        obj = random.choices(list(p_range), weights=list(p_range.values()))[0]
        pair = (subject, obj)
        if (
            (subject == obj and budget.reflexivity <= 0)
            or pair in existing
            or pair in rejected
        ):
            continue

        triple = format_triple(subject, predicate, obj, state.term_mapping)
        if (gained := state.insert_group([triple])) is None:
            rejected.add(pair)
            continue

        raised.update(gained)
        existing.add(pair)
        decrement_counts(domain, subject)
        decrement_counts(p_range, obj)
        budget.frequency -= 1
        if subject == obj:
            budget.reflexivity -= 1
        added += 1
        draws = 0

    total = current + added
    logger.info(
        "[Fill] %s: +%d random triples (%d rejected), %d/%d (%s)%s%s.",
        short_term(predicate),
        added,
        len(rejected),
        total,
        target,
        "closed" if total >= target else "open",
        "" if total >= target else f", stopped: {stop_reason}",
        "; raised the support of "
        + ", ".join(
            f"rule {r_id} +{n}" for r_id, n in sorted(raised.items())
        )
        if raised
        else "",
    )


def fill_open_predicates(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    open_preds: set[str],
    targets: dict[str, int],
    source_uri: str,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> dict[str, int]:
    """Adds triples of each predicate in `open_preds` to `graph_uri` until it
    reaches its target frequency in `targets`, keeping the support of every closed
    rule in `rules` (`HornRule.closed`) and `graph_uri` closed under all of them.

    Open rules may gain support up to their target and are marked closed when they
    reach it. First, for each open rule (in rule ID order) with an open body
    predicate, groundings are built from the predicates' remaining budgets; then
    the budget left is spent at random (see the module docstring). Subjects and
    objects are weighted by how many more triples they need to match their counts
    in `source_uri`. Assumes `graph_uri` is already closed under `rules` (see
    `complete_graph`).

    Returns:
        The number of triples added per predicate.
    """
    budgets: dict[str, PredicateProfile] = {}
    for pred in open_preds:
        budgets[pred] = PredicateProfile(
            domain=_remaining(
                get_domain(client, source_uri, pred),
                get_domain(client, graph_uri, pred),
            ),
            range=_remaining(
                get_range(client, source_uri, pred),
                get_range(client, graph_uri, pred),
            ),
            frequency=targets[pred] - get_frequency(client, pred, graph_uri),
            reflexivity=get_reflexivity(client, source_uri, pred)
            - get_reflexivity(client, graph_uri, pred),
        )
    state = _FillState(
        client=client,
        graph_uri=graph_uri,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
        rules=rules,
        supports={},
        budgets=budgets,
    )
    open_rules = sorted((r for r in rules if not rules[r].closed), key=rule_sort_key)
    for r_id in rules:
        state.supports[r_id] = get_support(client, rules[r_id], graph_uri)

    for r_id in open_rules:
        rule = rules[r_id]
        case = classify_open_rule(rule, state)
        logger.info(
            "[Fill] Open rule %s (%s): support %d/%d.",
            r_id,
            case,
            state.supports[r_id],
            int(rule.support),
        )
        if case == "A":
            logger.warning(
                "[Fill] Rule %s can't reach its target support: every body "
                "predicate is closed.",
                r_id,
            )

    for r_id in open_rules:
        if not rules[r_id].closed and classify_open_rule(rules[r_id], state) != "A":
            _fill_rule(state, r_id)

    for pred in sorted(open_preds, key=short_term):
        if state.is_open(pred):
            _fill_predicate(state, pred, targets[pred])

    return {pred: state.added[pred] for pred in open_preds}
