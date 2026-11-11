"""Generates synthetic Knowledge Graphs using extensional data and Horn rules.

This module provides the core pipeline for triple generation, applying a set of graph
metrics and logical rules to produce a complete, synthetic N-Triples dataset.
"""

import logging
import random
from collections.abc import Collection, Iterator
from itertools import islice

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    SparqlBinding,
    build_rule_query,
    execute_select_query,
    from_binding_row,
    get_existing_triples,
    get_frequency,
    get_support,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule
from skgg.core.utils import format_triple
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile update.
# ---------------------------------------------------------------------------
def decrement_counts(counts: dict[str, int], term: str) -> None:
    """Decrements a term's remaining frequency budget, dropping it once exhausted."""
    if term in counts:
        counts[term] -= 1
        if counts[term] == 0:
            del counts[term]
    else:
        logger.warning(
            "Decrementing the count of %s when it does not exist in counter.", term
        )


def update_closed_preds(profiles: dict[str, PredicateProfile]) -> bool:
    """Marks predicates whose remaining frequency budget is exhausted as closed.

    Returns:
        True if new predicates are closed.
    """
    new = False
    for profile in profiles.values():
        if profile.frequency <= 0 and not profile.closed:
            profile.closed = True
            new = True

    return new


def _largest_other(counts: dict[str, int], key: str) -> int:
    """The largest count of `counts` other than `key`'s, 0 if there is none."""
    top = max(counts.values(), default=0)
    # Whether `key` alone has it: stop at the second key that does.
    if (
        counts.get(key) == top
        and sum(1 for _ in islice(filter(top.__eq__, counts.values()), 2)) == 1
    ):
        top = max(filter(top.__gt__, counts.values()), default=0)
    return top


def _top_is_shared(
    counts: dict[str, int], others: dict[str, int], skip: set[str], top: int
) -> bool:
    """Whether a key of `counts` other than those of `skip`, with count `top`, is
    also a key of `others`. Stops at the first one, in C (`filter`, `islice`)."""
    hits = sum(1 for key in skip if key in others and counts.get(key) == top)
    shared = map(counts.__getitem__, filter(others.__contains__, counts))
    return sum(1 for _ in islice(filter(top.__eq__, shared), hits + 1)) > hits


def is_assignment_solvable(
    profile: PredicateProfile, subject: str, obj: str, loop_free: bool = True
) -> bool:
    """Checks if assigning a (subject, object) pair maintains graph solvability.

    Simulates the assigment, without mutating or copying the profile object, and
    checks the first Gale-Ryser condition on what is left: no subject needs more
    distinct objects than there are, and no object more distinct subjects.

    With `loop_free`, the rest of the relation can't have self-loops (`edb.py`
    places them first, see `place_self_loops`), so an entity in both the domain and
    the range has one partner fewer: itself. A self-loop assignment (`subject ==
    obj`) is checked the same way.

    Args:
        profile: Predicate profile tracking available domains and ranges.
        subject: Subject term to be assigned.
        obj: Object term to be assigned.
        loop_free: Whether the rest of the relation must be free of self-loops.
            Relations generated as undirected edges still have theirs to place
            (`edb._undirected_edges`), so they pass False.

    Returns:
        True if the assignment leaves the graph in a solvable state, False otherwise.
    """
    domain, range_ = profile.domain, profile.range
    if subject not in domain or obj not in range_:
        logger.warning("Bad triple: (%s, %s).\n%s", subject, obj, profile)
        return False

    subject_left = domain[subject] - 1
    obj_left = range_[obj] - 1
    domain_len = len(domain) - (subject_left == 0)
    range_len = len(range_) - (obj_left == 0)

    # The largest need on each side: a count, plus one for an entity in both the
    # domain and the range when loop-free. The entities other than the two assigned
    # keep their counts, so their largest ones come from C-level passes (`max`,
    # `filter` and `map` over the counts) instead of a loop in Python.
    specials = {subject, obj}
    in_both_s = loop_free and subject in range_
    in_both_o = loop_free and obj in domain

    domain_needs = [_largest_other(domain, subject)]
    if subject_left > 0:
        domain_needs.append(
            subject_left + (in_both_s and (subject != obj or obj_left > 0))
        )
    if obj != subject and in_both_o:
        domain_needs.append(domain[obj] + (obj_left > 0))
    range_needs = [_largest_other(range_, obj)]
    if obj_left > 0:
        range_needs.append(
            obj_left + (in_both_o and (obj != subject or subject_left > 0))
        )
    if subject != obj and in_both_s:
        range_needs.append(range_[subject] + (subject_left > 0))
    # The other entities have at most the largest count. One that is in both needs
    # one more, which only raises the largest need if its count is the largest.
    if loop_free and _top_is_shared(domain, range_, specials, domain_needs[0]):
        domain_needs.append(domain_needs[0] + 1)
    if loop_free and _top_is_shared(range_, domain, specials, range_needs[0]):
        range_needs.append(range_needs[0] + 1)

    max_domain_freq = max(domain_needs)
    max_range_freq = max(range_needs)

    return max_domain_freq <= range_len and max_range_freq <= domain_len


# ---------------------------------------------------------------------------
# Closure checks (via SPARQL).
# ---------------------------------------------------------------------------
def get_closed_rules(
    client: SPARQLWrapper, graph_uri: str, rules: dict[str, HornRule]
) -> set[str]:
    """Queries the database to find which rules reached their target support.

    A rule is considered 'closed' when the count of distinct bindings
    satisfying both its body and head in the graph meets or exceeds
    its defined support threshold.
    """
    closed_rules: set[str] = set()

    # Check support
    for r_id, rule in rules.items():
        support = get_support(client, rule, graph_uri)
        if support:
            if support >= rule.support:
                closed_rules.add(r_id)

    return closed_rules


def get_closed_preds(
    client: SPARQLWrapper, graph_uri: str, profiles: dict[str, PredicateProfile]
) -> set[str]:
    """Queries the database to find which predicates reached their target frequency.

    A predicate is considered 'closed' when the count of distinct triples containing the
    predicate is equal to the predicate's frequency.
    """

    closed_predicates: set[str] = set()

    for predicate, profile in profiles.items():
        frequency = get_frequency(client, predicate, graph_uri)
        if frequency >= profile.frequency:
            closed_predicates.add(predicate)

    return closed_predicates


# ---------------------------------------------------------------------------
# Sample groundings of a rule body.
# ---------------------------------------------------------------------------
# TODO: Change the way of controlling _MAX_EMPTY_BATCHES
_MAX_EMPTY_BATCHES = 3


def _usable_fixed_rows(
    atoms: list[Atom],
    profiles: dict[str, PredicateProfile],
    fixed_bindings: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Keeps the fixed rows whose values sit in the profile position (domain for
    subjects, range for objects) of every atom they bind."""
    if not fixed_bindings:
        return []
    usable = []
    for row in fixed_bindings:
        if all(
            (a.subject not in row or row[a.subject] in profiles[a.predicate].domain)
            and (a.obj not in row or row[a.obj] in profiles[a.predicate].range)
            for a in atoms
        ):
            usable.append(row)
    return usable


def _variable_pools(
    atoms: list[Atom], profiles: dict[str, PredicateProfile]
) -> dict[str, dict[str, int]]:
    """Maps each variable in `atoms` to its candidate values and their capacity.

    A value is a candidate only if it appears in every profile position (domain for
    subjects, range for objects) the variable occupies, so a variable shared between
    atoms is drawn from the intersection of those positions. The capacity of a value
    is the smallest remaining count across those positions.
    """
    pools: dict[str, dict[str, int]] = {}
    for atom in atoms:
        profile = profiles[atom.predicate]
        for term, counts in ((atom.subject, profile.domain), (atom.obj, profile.range)):
            if not term.startswith("?"):
                continue
            if term in pools:
                pools[term] = {
                    v: min(c, counts[v]) for v, c in pools[term].items() if v in counts
                }
            else:
                pools[term] = dict(counts)
    return pools


def sample_groundings(
    client: SPARQLWrapper,
    target_uri: str,
    atoms: list[Atom],
    head_vars: set[str],
    profiles: dict[str, PredicateProfile],
    missing_heads: int,
    term_mapping: dict[str, str],
    chunk_size: int,
    undirected: Collection[str] = (),
    skip_reflexivity: bool = False,
) -> list[str]:
    """Returns the new triples of `sample_grounding_groups`'s groundings, as one
    list. Takes the same arguments, without `fixed_bindings`."""
    return [
        triple
        for group in sample_grounding_groups(
            client,
            target_uri,
            atoms,
            head_vars,
            profiles,
            missing_heads,
            term_mapping,
            chunk_size,
            undirected=undirected,
            skip_reflexivity=skip_reflexivity,
        )
        for triple in group
    ]


def sample_grounding_groups(
    client: SPARQLWrapper,
    target_uri: str,
    atoms: list[Atom],
    head_vars: set[str],
    profiles: dict[str, PredicateProfile],
    missing_heads: int,
    term_mapping: dict[str, str],
    chunk_size: int,
    fixed_bindings: list[dict[str, str]] | None = None,
    undirected: Collection[str] = (),
    skip_reflexivity: bool = False,
) -> list[list[str]]:
    """Constructs groundings of a rule body directly from predicate profiles.

    Variables shared between atoms are drawn once per grounding, from the intersection
    of the positions they occupy, so the atoms are forced to share them. Support counts
    distinct projections onto the head variables, so a grounding is accepted only if
    its head projection is new. Variables outside the head (existential) add no
    support; they are drawn weighted by remaining capacity, which favors reusing
    high-capacity entities as witnesses.

    Accepted groundings decrement the domain/range/frequency budget of `profiles`.

    A grounding never binds an atom's subject and object to the same value: the
    self-loops of a relation are placed on their own, by `edb.place_self_loops`, or
    by `edb._undirected_edges` for a relation generated as undirected edges. With
    `skip_reflexivity` (the generator before the self-loop fix), only the atoms of
    undirected relations refuse such a binding, so the other relations get
    self-loops by chance, and the solvability check allows them.

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        edb_uri: URI of the EDB, used to skip triples that already exist.
        atoms: Body atoms to ground; their predicates must be keys of `profiles`.
        head_vars: Head variables of the rule (those absent from `atoms` are ignored).
        profiles: Remaining budget per predicate; mutated for accepted groundings.
        missing_heads: Number of new head projections to produce.
        term_mapping: Mapping from a term to its corresponding prefix.
        chunk_size: Maximum number of triples per existence query.
        fixed_bindings: Optional rows binding some variables to existing terms (e.g.
            from the rule's already grounded body atoms). Every candidate copies a
            random row, and only the remaining variables are drawn from the profiles,
            so the new triples join with what already exists. Those variables also
            count in the head projection. No usable row means nothing is built.
        undirected: Predicates generated as undirected edges (see
            `edb.get_undirected_rule_ids`). A grounding adds both directions of
            their triples and spends the budget of both.
        skip_reflexivity: Whether to use the logic before the self-loop fix (see
            above), which `cli/main.py --skip-reflexivity` selects.

    Returns:
        The new triples of each accepted grounding, in the order of `atoms`. Fewer
        groundings than needed if a variable runs out of candidate values.
    """
    body_vars = {t for a in atoms for t in (a.subject, a.obj) if t.startswith("?")}
    usable_rows = _usable_fixed_rows(atoms, profiles, fixed_bindings)
    fixed_vars = set(fixed_bindings[0]) if fixed_bindings else set()
    if fixed_bindings is not None and not usable_rows:
        logger.warning(
            "No fixed binding fits the profiles of %s; skipping.",
            [str(a) for a in atoms],
        )
        return []

    projection = sorted(head_vars & (body_vars | fixed_vars))
    free_vars = sorted(body_vars - fixed_vars)
    var_order = [v for v in projection if v not in fixed_vars] + [
        v for v in free_vars if v not in projection
    ]

    seen: set[tuple[str, ...]] = set()
    chosen: set[str] = set()
    groups: list[list[str]] = []
    empty_batches = 0

    while len(seen) < missing_heads and empty_batches < _MAX_EMPTY_BATCHES:
        ## ----- Create a list of candidate groundings -----
        pools = _variable_pools(atoms, profiles)
        if exhausted := [var for var in var_order if not pools[var]]:
            # E.g. earlier rules used up the entities that `father` and `mother`
            # shared. Not an error: the remaining budget is left for the random
            # assignment step.
            logger.warning(
                "No candidate values left for %s in %s; skipping the rule.",
                exhausted,
                [str(a) for a in atoms],
            )
            break

        batch_size = min(max(2 * (missing_heads - len(seen)), 50), chunk_size)

        rows = random.choices(usable_rows, k=batch_size) if usable_rows else []
        draws = {
            var: random.choices(
                list(pools[var]), weights=list(pools[var].values()), k=batch_size
            )
            for var in var_order
        }

        # Each candidate is a list of (triple, predicate, subject, object).
        candidates: list[list[tuple[str, str, str, str]]] = []
        keys: list[tuple[str, ...]] = []
        for i in range(batch_size):
            binding = {var: draws[var][i] for var in var_order}
            if usable_rows:
                binding.update(rows[i])
            if any(
                (not skip_reflexivity or atom.predicate in undirected)
                and binding.get(atom.subject, atom.subject)
                == binding.get(atom.obj, atom.obj)
                for atom in atoms
            ):
                continue
            grounded = []
            for atom in atoms:
                s = binding.get(atom.subject, atom.subject)
                o = binding.get(atom.obj, atom.obj)
                directions = [(s, o)]
                if atom.predicate in undirected:
                    directions.append((o, s))
                for subject, obj in directions:
                    grounded.append(
                        (
                            format_triple(subject, atom.predicate, obj, term_mapping),
                            atom.predicate,
                            subject,
                            obj,
                        )
                    )
            candidates.append(grounded)
            keys.append(tuple(binding[v] for v in projection))

        ## ----- Filter candidates  -----
        existing = get_existing_triples(
            client=client,
            graph_uri=target_uri,
            candidate_triples=(g[0] for cand in candidates for g in cand),
            term_mapping=term_mapping,
            chunk_size=chunk_size,
        )

        accepted = 0
        for grounded, key in zip(candidates, keys, strict=True):
            if len(seen) >= missing_heads:
                break
            if key in seen:
                continue

            fresh = {
                g[0]: g for g in grounded if g[0] not in existing and g[0] not in chosen
            }
            if not fresh:
                # Fully present already: its projection is counted in the current
                # support, so it would add nothing.
                continue

            undo: list[tuple[PredicateProfile, str, str, int, int]] = []
            valid = True
            for _triple, predicate, s, o in fresh.values():
                profile = profiles[predicate]
                if (
                    profile.frequency <= 0
                    or s not in profile.domain
                    or o not in profile.range
                    or not is_assignment_solvable(
                        profile,
                        s,
                        o,
                        loop_free=not skip_reflexivity and predicate not in undirected,
                    )
                ):
                    valid = False
                    break
                undo.append((profile, s, o, profile.domain[s], profile.range[o]))
                decrement_counts(profile.domain, s)
                decrement_counts(profile.range, o)
                profile.frequency -= 1

            if not valid:
                # `decrement_counts` drops exhausted keys, so restore by assignment.
                for profile, s, o, dom_count, rng_count in reversed(undo):
                    profile.domain[s] = dom_count
                    profile.range[o] = rng_count
                    profile.frequency += 1
                continue

            seen.add(key)
            chosen.update(fresh)
            groups.append(list(fresh))
            accepted += 1

        empty_batches = 0 if accepted else empty_batches + 1

    if len(seen) < missing_heads:
        logger.warning(
            "Built %d of %d needed groundings for %s.",
            len(seen),
            missing_heads,
            [str(a) for a in atoms],
        )

    return groups


# ---------------------------------------------------------------------------
# Apply rules.
# ---------------------------------------------------------------------------
def triples_from_bindings(
    bindings: list[SparqlBinding], atoms: list[Atom], term_mapping: dict[str, str]
) -> Iterator[str]:
    """Maps bindings to RDF formatted triples using the patterns in 'atoms'.

    Args:
        bindings: A list of SPARQL binding rows to evaluate.
        atoms: A list of body atoms providing the triple patterns.
        term_mapping: Mapping of terms to their string representations.

    Returns:
        An iterator of formatted triple strings, one per (atom, binding row) pair.
    """
    return (
        format_triple(
            subject=from_binding_row(atom.subject, binding_row)[0],
            predicate=atom.predicate,
            obj=from_binding_row(atom.obj, binding_row)[0],
            term_mapping=term_mapping,
        )
        for atom in atoms
        for binding_row in bindings
    )


def apply_rule(
    client: SPARQLWrapper,
    graph_uri: str,
    rule: HornRule,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> int:
    """Inserts novel triples generated from the rule to 'graph_uri'. The head
    predicate's profile doesn't cap them.

    Args:
        client: SPARQLWrapper client.
        graph_uri: URI of the graph where data is queried and inserted.
        rule: Rule represented as a Horn Rule.
        term_mapping: Mapping from a term to its corresponding prefix.
        chunk_size: Maximum number of triples to insert per SPARQL query.

    Returns:
        Number of novel triples inserted to the graph.
    """

    # Retrieve bindings.
    query = build_rule_query(rule=rule.signature, graph_uri=graph_uri)
    if not (raw_bindings := execute_select_query(client, query)):
        return 0

    # Get the candidate triples that already exist in the graph
    existing_triples = get_existing_triples(
        client=client,
        graph_uri=graph_uri,
        candidate_triples=triples_from_bindings(
            bindings=raw_bindings,
            atoms=[rule.head],
            term_mapping=term_mapping,
        ),
        term_mapping=term_mapping,
        chunk_size=chunk_size,
    )

    def filter_triples() -> Iterator[str]:
        """Helper generator. Yields the triples not already in the graph."""
        for triple in triples_from_bindings(
            bindings=raw_bindings,
            atoms=[rule.head],
            term_mapping=term_mapping,
        ):
            if triple not in existing_triples:
                yield triple

    # Yield triples that do not exist already in the graph
    return insert_triples_sparql(
        graph_uri=graph_uri,
        client=client,
        triple_stream=filter_triples(),
        chunk_size=chunk_size,
    )
