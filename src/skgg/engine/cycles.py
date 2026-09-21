"""Breaks rule cycles that completion can never start on its own.

EDB generation only seeds extensional predicates, so a predicate whose every producing
rule depends on the predicate itself (`p -> p`) or on a rule that depends back on it
(`A -> B -> A`) stays empty after `engine.completion.complete_graph`. `break_cycles`
finds those *stale* cycles (`core.rules.find_stale_cycles`) and seeds the
cycle-predicate atoms of one rule of one cycle as if they were extensional. It does not
complete the graph: the pipeline (`cli.main`) alternates `break_cycles` and
`complete_graph` until nothing more is seeded. Seeds are written to the synthetic graph
only; the EDB is left untouched.
"""

import logging
from dataclasses import dataclass

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    get_atom_bindings,
    get_predicate_frequencies,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule, find_stale_cycles, get_relation_graph
from skgg.engine.generator import sample_groundings
from skgg.engine.metrics import PredicateProfile
from skgg.utils import short_term

logger = logging.getLogger(__name__)

# Rows fetched per needed grounding when joining the seed with grounded atoms.
_ROWS_PER_GROUNDING = 10


@dataclass(frozen=True, slots=True)
class SeedCandidate:
    """A rule whose ungrounded body atoms can be instantiated to break a cycle.

    Attributes:
        rule: The rule to seed.
        seed_atoms: Body atoms over ungrounded predicates; these get new triples.
        grounded_atoms: Remaining body atoms, joined against existing triples.
    """

    rule: HornRule
    seed_atoms: tuple[Atom, ...]
    grounded_atoms: tuple[Atom, ...]


def get_grounded_predicates(client: SPARQLWrapper, graph_uri: str) -> set[str]:
    """Predicates with at least one triple in the graph, as bracketed `<uri>` terms
    (the form used by rules and profiles)."""
    return {
        p if p.startswith("<") else f"<{p}>"
        for p in get_predicate_frequencies(client, graph_uri)
    }


def _seed_predicates_usable(
    atoms: tuple[Atom, ...], profiles: dict[str, PredicateProfile], rule_id: str
) -> bool:
    """Whether every seed predicate still has budget to create triples."""
    for pred in {a.predicate for a in atoms}:
        profile = profiles.get(pred)
        if profile is None:
            logger.warning(
                "Breaking cycles: no profile for %s in rule %s (absent from the "
                "source graph or a predicate format mismatch); skipping the rule.",
                pred,
                rule_id,
            )
            return False
        if (
            profile.closed
            or profile.frequency <= 0
            or not profile.domain
            or not profile.range
        ):
            return False
    return True


def rule_is_recursive(rule: HornRule) -> bool:
    """Whether the rule's head predicate also appears in its body."""
    return rule.head.predicate in rule.get_body_predicates()


def rank_seed_candidates(
    cycle: list[str],
    rules: dict[str, HornRule],
    grounded_preds: set[str],
    profiles: dict[str, PredicateProfile],
) -> list[SeedCandidate]:
    """Eligible rules for seeding a stale cycle, best first.

    Candidates are the rules with an edge inside the cycle. A rule is skipped when it is
    already closed or when any predicate it would seed is closed, unprofiled or out of
    budget. Ranking: fewest seed atoms (least new triples), non-recursive before
    recursive, most restrictive (largest body) first, then lowest rule id.

    Intensional dependencies (`get_intensional_dependencies`) deliberately do not gate
    eligibility: in a stale cycle the non-recursive rules a recursive rule would wait
    for can never fire, so waiting would deadlock exactly the rule that must go first.
    Their "restrictive first" idea is only used as a tie-breaker. If the pipeline ever
    moves to `generate_idb`, its dependency gate needs the same exemption.
    """
    graph = get_relation_graph(rules)
    rule_ids: set[str] = set()
    for src, dst in zip(cycle, cycle[1:] + cycle[:1], strict=True):
        rule_ids |= graph[src][dst]["rule_ids"]

    candidates: list[SeedCandidate] = []
    for rule_id in rule_ids:
        rule = rules[rule_id]
        if rule.closed:
            continue
        seed = tuple(sorted(a for a in rule.body if a.predicate not in grounded_preds))
        grounded = tuple(sorted(a for a in rule.body if a.predicate in grounded_preds))
        if seed and _seed_predicates_usable(seed, profiles, rule_id):
            candidates.append(SeedCandidate(rule, seed, grounded))

    return sorted(
        candidates,
        key=lambda c: (
            len(c.seed_atoms),
            rule_is_recursive(c.rule),
            -len(c.rule),
            c.rule.rule_id,
        ),
    )


def _seed_rule(
    client: SPARQLWrapper,
    candidate: SeedCandidate,
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    graph_uri: str,
    chunk_size: int,
) -> int:
    """Instantiates the candidate's seed atoms in `graph_uri`.

    Returns:
        The number of triples inserted; 0 if the candidate cannot be seeded.
    """
    rule = candidate.rule
    # The seed predicates are empty, so the rule yields no head yet: the whole
    # support is missing. Each grounding adds one triple per seed atom, so the
    # smallest remaining frequency among the seed predicates caps it.
    budget = min(profiles[a.predicate].frequency for a in candidate.seed_atoms)
    missing_heads = min(int(rule.support), budget)
    if missing_heads <= 0:
        return 0

    fixed_bindings: list[dict[str, str]] | None = None
    if candidate.grounded_atoms:
        seed_vars = {
            t for a in candidate.seed_atoms for t in (a.subject, a.obj) if t.startswith("?")
        }
        grounded_vars = {
            t
            for a in candidate.grounded_atoms
            for t in (a.subject, a.obj)
            if t.startswith("?")
        }
        fixed_bindings = get_atom_bindings(
            client,
            candidate.grounded_atoms,
            grounded_vars & (seed_vars | rule.get_head_variables()),
            graph_uri,
            limit=_ROWS_PER_GROUNDING * missing_heads,
        )
        if not fixed_bindings:
            logger.debug("Rule %s: nothing to join the seed with.", rule.rule_id)
            return 0

    triples = sample_groundings(
        client=client,
        target_uri=graph_uri,
        atoms=list(candidate.seed_atoms),
        head_vars=rule.get_head_variables(),
        profiles=profiles,
        missing_heads=missing_heads,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
        fixed_bindings=fixed_bindings,
    )
    return insert_triples_sparql(
        client=client,
        graph_uri=graph_uri,
        triple_stream=iter(triples),
        chunk_size=chunk_size,
    )


def _format_cycle(cycle: list[str]) -> str:
    return " -> ".join(short_term(p) for p in [*cycle, cycle[0]])


def break_cycles(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    synthetic_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile],
) -> int:
    """Breaks one stale rule cycle in the synthetic graph and returns.

    Detects the stale cycles and, for the first one that can be seeded, inserts the
    triples of its best candidate rule. Cycles that cannot be seeded are skipped with
    a warning. Completing the graph afterwards is the caller's job: seeding one cycle
    may ground others, so call this again after each completion until it returns 0.

    Returns:
        The number of seed triples inserted; 0 if no stale cycle could be broken.
    """
    grounded = get_grounded_predicates(client, synthetic_uri)

    for cycle in find_stale_cycles(rules, grounded):
        candidates = rank_seed_candidates(cycle, rules, grounded, profiles)
        for candidate in candidates:
            if inserted := _seed_rule(
                client, candidate, profiles, term_mapping, synthetic_uri, chunk_size
            ):
                logger.info(
                    "Broke cycle %s: seeded %d triples for rule %s.",
                    _format_cycle(cycle),
                    inserted,
                    candidate.rule.rule_id,
                )
                return inserted
        logger.warning(
            "Cannot break cycle %s: no rule of it can be seeded (%d candidates "
            "tried; closed or exhausted predicates, or nothing to join with).",
            _format_cycle(cycle),
            len(candidates),
        )

    return 0
