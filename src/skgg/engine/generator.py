"""Generates synthetic Knowledge Graphs using extensional data and Horn rules.

This module provides the core pipeline for triple generation, applying a set of graph
metrics and logical rules to produce a complete, synthetic N-Triples dataset.
"""

import logging
import random
from collections.abc import Iterator

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    SparqlBinding,
    build_rule_query,
    execute_select_query,
    from_binding_row,
    get_existing_triples,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule
from skgg.engine.metrics import PredicateProfile
from skgg.utils import format_triple

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


def is_assignment_solvable(profile: PredicateProfile, subject: str, obj: str) -> bool:
    """Checks if assigning a (subject, object) pair maintains graph solvability.

    Simulates the assigment to evaluate the Gale-Ryser / Havel-Hakimi conditions without
    mutating or copying the profile object.

    Args:
        profile: Predicate profile tracking available domains and ranges.
        subject: Subject term to be assigned.
        obj: Object term to be assigned.

    Returns:
        True if the assignment leaves the graph in a solvable state, False otherwise.
    """
    if subject not in profile.domain or obj not in profile.range:
        logger.warning("Bad triple: (%s, %s).\n%s", subject, obj, profile)
        return False

    s_domain_len = len(profile.domain) - (1 if profile.domain.get(subject) == 1 else 0)
    s_range_len = len(profile.range) - (1 if profile.range.get(obj) == 1 else 0)

    max_domain_freq = max(
        (count - 1 if k == subject else count for k, count in profile.domain.items()),
        default=0,
    )
    max_range_freq = max(
        (count - 1 if k == obj else count for k, count in profile.range.items()),
        default=0,
    )

    return max_domain_freq <= s_range_len and max_range_freq <= s_domain_len


# ---------------------------------------------------------------------------
# Sample groundings of a rule body.
# ---------------------------------------------------------------------------
# TODO: Change the way of controlling _MAX_EMPTY_BATCHES
_MAX_EMPTY_BATCHES = 3


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
) -> list[str]:
    """Constructs groundings of a rule body directly from predicate profiles.

    Variables shared between atoms are drawn once per grounding, from the intersection
    of the positions they occupy, so the atoms are forced to share them. Support counts
    distinct projections onto the head variables, so a grounding is accepted only if
    its head projection is new. Variables outside the head (existential) add no
    support; they are drawn weighted by remaining capacity, which favors reusing
    high-capacity entities as witnesses.

    Accepted groundings decrement the domain/range/frequency budget of `profiles`.

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        edb_uri: URI of the EDB, used to skip triples that already exist.
        atoms: Body atoms to ground; their predicates must be keys of `profiles`.
        head_vars: Head variables of the rule (those absent from `atoms` are ignored).
        profiles: Remaining budget per predicate; mutated for accepted groundings.
        missing_heads: Number of new head projections to produce.
        term_mapping: Mapping from a term to its corresponding prefix.
        chunk_size: Maximum number of triples per existence query.

    Returns:
        The new triples of the accepted groundings. Fewer than needed if a variable
        runs out of candidate values.
    """
    body_vars = {t for a in atoms for t in (a.subject, a.obj) if t.startswith("?")}
    projection = sorted(head_vars & body_vars)
    var_order = projection + sorted(body_vars - set(projection))

    seen: set[tuple[str, ...]] = set()
    chosen: set[str] = set()
    new_triples: list[str] = []
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

        draws = {
            # NOTE: Weightinh values per availability may have effects on the behaviour
            # of the algorithm.
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
            grounded = []
            for atom in atoms:
                s = binding.get(atom.subject, atom.subject)
                o = binding.get(atom.obj, atom.obj)
                grounded.append(
                    (
                        format_triple(s, atom.predicate, o, term_mapping),
                        atom.predicate,
                        s,
                        o,
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
                    or not is_assignment_solvable(profile, s, o)
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
            new_triples.extend(fresh)
            accepted += 1

        empty_batches = 0 if accepted else empty_batches + 1

    if len(seen) < missing_heads:
        logger.warning(
            "Built %d of %d needed groundings for %s.",
            len(seen),
            missing_heads,
            [str(a) for a in atoms],
        )

    return new_triples


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
    profile: PredicateProfile | None = None,
) -> int:
    """Inserts novel triples generated from the rule to 'graph_uri'. If a profile is
    provided, restricts triple generation to profile constraints.

    Args:
        client: SPARQLWrapper client.
        graph_uri: URI of the graph where data is queried and inserted.
        rule: Rule represented as a Horn Rule.
        term_mapping: Mapping from a term to its corresponding prefix.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        profile: Contains the constraints of the head predicate.

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
        """Helper generator. Yields novel and constraint-valid triples."""
        for triple in triples_from_bindings(
            bindings=raw_bindings,
            atoms=[rule.head],
            term_mapping=term_mapping,
        ):
            if triple in existing_triples:
                continue

            if profile is not None:
                subject, _predicate, obj = triple.strip(" .").split(sep=" ")
                if (
                    profile.frequency <= 0
                    or profile.domain.get(subject, 0) <= 0
                    or profile.range.get(obj, 0) <= 0
                    or not is_assignment_solvable(profile, subject, obj)
                ):
                    continue

            yield triple

    # Yield triples that do not exist already in the graph
    return insert_triples_sparql(
        graph_uri=graph_uri,
        client=client,
        triple_stream=filter_triples(),
        chunk_size=chunk_size,
    )
