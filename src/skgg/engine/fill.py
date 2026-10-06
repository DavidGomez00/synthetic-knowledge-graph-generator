"""Generates the triples of the predicates still open after completion, without
changing the support of any rule.

`fill_open_predicates` draws each missing triple from the predicate's remaining
domain and range frequencies (source graph minus synthetic graph) and keeps it
only if it passes the head and body checks of `core/queries.py`
(`build_head_impact_query`, `build_body_impact_query`) for every rule it could
impact. See "Filling open relations" in `docs/concepts.md` for why both checks
are needed and why they are enough.
"""

import logging
import random

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    build_body_impact_query,
    build_head_impact_query,
    get_atom_bindings,
    get_domain,
    get_frequency,
    get_range,
    get_reflexivity,
    has_solution,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule
from skgg.core.utils import format_triple, short_term
from skgg.engine.generator import decrement_counts

logger = logging.getLogger(__name__)

# Draws in a row without an accepted triple after which a predicate is given up.
MAX_FILL_DRAWS = 1000


def _remaining(source: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    """Returns how many more triples each term needs to reach its source count."""
    return {
        term: count - current.get(term, 0)
        for term, count in source.items()
        if count > current.get(term, 0)
    }


def _blocking_rule(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    predicate: str,
    subject: str,
    obj: str,
    graph_uri: str,
) -> str | None:
    """Returns the ID of the first rule whose support would change, or which the
    graph would no longer be closed under, if `subject predicate obj` were added;
    None if the triple is safe."""
    for r_id, rule in rules.items():
        queries = []
        if rule.head.predicate == predicate:
            queries.append(build_head_impact_query(rule, subject, obj, graph_uri))
        queries += [
            build_body_impact_query(rule, atom, subject, obj, graph_uri)
            for atom in sorted(rule.body)
            if atom.predicate == predicate
        ]
        if any(q is not None and has_solution(client, q) for q in queries):
            return r_id
    return None


def _fill_predicate(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    predicate: str,
    target: int,
    source_uri: str,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> int:
    """Adds triples of `predicate` to `graph_uri` until it reaches `target`, its
    remaining domain or range runs out, or `MAX_FILL_DRAWS` draws in a row add
    nothing. Returns the number of triples added."""
    current = get_frequency(client, predicate, graph_uri)
    domain = _remaining(
        get_domain(client, source_uri, predicate),
        get_domain(client, graph_uri, predicate),
    )
    p_range = _remaining(
        get_range(client, source_uri, predicate),
        get_range(client, graph_uri, predicate),
    )
    reflexive = get_reflexivity(client, source_uri, predicate) - get_reflexivity(
        client, graph_uri, predicate
    )
    # Only the rules a triple of this predicate could impact are checked.
    impacted = {r_id: r for r_id, r in rules.items() if predicate in r.get_predicates()}

    existing = {
        (row["?s"], row["?o"])
        for row in get_atom_bindings(
            client, [Atom("?s", predicate, "?o")], ["?s", "?o"], graph_uri, current + 1
        )
    }
    rejected: set[tuple[str, str]] = set()
    added = 0
    draws = 0
    stop_reason = "target reached"

    while current + added < target:
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
        if (subject == obj and reflexive <= 0) or pair in existing or pair in rejected:
            continue

        if r_id := _blocking_rule(
            client, impacted, predicate, subject, obj, graph_uri
        ):
            rejected.add(pair)
            logger.debug(
                "[Fill] Rejected %s %s %s: would impact rule %s.",
                short_term(subject),
                short_term(predicate),
                short_term(obj),
                r_id,
            )
            continue

        # Inserted at once, so that the checks of the next triples see it.
        insert_triples_sparql(
            client=client,
            graph_uri=graph_uri,
            triple_stream=[format_triple(subject, predicate, obj, term_mapping)],
            chunk_size=chunk_size,
        )
        existing.add(pair)
        decrement_counts(domain, subject)
        decrement_counts(p_range, obj)
        if subject == obj:
            reflexive -= 1
        added += 1
        draws = 0

    total = current + added
    logger.info(
        "[Fill] %s: +%d triples (%d rejected), %d/%d (%s)%s.",
        short_term(predicate),
        added,
        len(rejected),
        total,
        target,
        "closed" if total >= target else "open",
        "" if total >= target else f", stopped: {stop_reason}",
    )
    return added


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
    reaches its target frequency in `targets`, without changing the support of
    any rule in `rules` and keeping `graph_uri` closed under them.

    Each triple is drawn with its subject and object weighted by how many more
    triples they need to match their counts in `source_uri`, and kept only if
    the head and body checks pass for every rule the predicate occurs in.
    Assumes `graph_uri` is already closed under `rules` (see `complete_graph`).

    Returns:
        The number of triples added per predicate.
    """
    return {
        pred: _fill_predicate(
            client,
            rules,
            pred,
            targets[pred],
            source_uri,
            graph_uri,
            term_mapping,
            chunk_size,
        )
        for pred in sorted(open_preds, key=short_term)
    }
