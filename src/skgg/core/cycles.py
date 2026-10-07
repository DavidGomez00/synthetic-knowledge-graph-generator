"""Rule cycles: the relation graph of a rule set and the removal of the fewest
rules that leave it with no cycle.

A rule set with a cycle in its relation graph (`p -> p`, `A -> B -> A`, ...) can
leave predicates that completion never derives, so `cli/main.py` removes cyclic
rules with `remove_minimal_cyclic_rules` before EDB generation. See section 5.2 of
`docs/algorithm.md` for the method.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator

import networkx as nx

from skgg.core.rules import HornRule, rule_sort_key
from skgg.core.utils import short_term

logger = logging.getLogger(__name__)

# Strongly connected components with more predicates than this lose every cyclic
# rule: the exact selection keeps 2^n states for n predicates.
MAX_EXACT_COMPONENT_SIZE = 20


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
        `cycle_predicates(get_relation_graph(rules))`.
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


def _cyclic_components(graph: nx.DiGraph) -> Iterator[tuple[set[str], set[str]]]:
    """Yields each strongly connected component of the relation graph that has a
    cycle, with the IDs of the rules behind its edges (a self-loop `p -> p`
    included). These are the cyclic rules: both predicates of the edge are in the
    same component."""
    for component in nx.strongly_connected_components(graph):
        rule_ids = {
            rule_id
            for _, _, ids in graph.subgraph(component).edges(data="rule_ids")
            for rule_id in ids
        }
        if rule_ids:
            yield component, rule_ids


def cycle_predicates(graph: nx.DiGraph) -> set[str]:
    """Returns the predicates on a cycle of the relation graph."""
    return {pred for component, _ in _cyclic_components(graph) for pred in component}


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
    every rule with an edge inside it.

    The head predicate of a removed rule becomes extensional, and EDB generation
    produces it, unless a remaining rule still derives it. Run it on the rule set a
    run actually uses (after `parse_rule_set`'s confidence filter), before EDB
    generation.

    Returns:
        The removed rules, identified by rule_id.
    """
    graph = get_relation_graph(rules)
    removed_ids: set[str] = set()

    for component, cyclic_ids in _cyclic_components(graph):
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
