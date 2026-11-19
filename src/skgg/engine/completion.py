"""Forward-chains the rule set over a graph until no rule adds any more triples.

Used by `cli/main.py` to derive the synthetic graph (`GraphConfig.synthetic_uri`) from
F0, and again after each cycle-breaking round. Assumes rule bodies are
fully grounded.
"""

import logging
import random
from collections import Counter, defaultdict

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    get_domain,
    get_existing_triples,
    get_predicate_frequencies,
    get_range,
    get_reflexivity,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import HornRule, get_undirected_preds, rule_sort_key
from skgg.core.utils import format_triple, short_term
from skgg.engine.generator import (
    apply_rule,
    get_closed_preds,
    get_closed_rules,
    novel_head_pairs,
)
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph completion.
# ---------------------------------------------------------------------------
def _format_rule_counts(counts: dict[str, int]) -> str:
    """Formats triples added per rule as `rule 26 +10, rule 3 +4`, in rule order."""
    return ", ".join(
        f"rule {r_id} +{counts[r_id]}" for r_id in sorted(counts, key=rule_sort_key)
    )


def complete_graph(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    source: str,
    target_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile] | None = None,
    label: str = "Completion",
    caps: dict[str, int] | None = None,
    degree_targets: dict[str, PredicateProfile] | None = None,
) -> int:
    """Completes a graph by applying rules if able.

    `source` (a graph URI, or a .nt/.tsv file whose .tsv terms are resolved via
    `term_mapping`) is first copied into `target_uri`, unless it is `target_uri`
    itself; the rules are then applied to `target_uri` until a pass adds nothing.

    `label` prefixes this call's log lines, so the several completions of one
    pipeline run can be told apart. Each pass logs the triples each rule added,
    and one line sums them up per rule for the whole call.

    Returns:
        The total number of triples added.

    `profiles`, if given, carries the target frequency each predicate should
    reach (e.g. extracted from the original source graph) -- when omitted,
    predicate closure is simply skipped; rule closure always runs, since a
    rule's `support` target is intrinsic to it.

    `caps`, if given, maps a head predicate to the frequency it must not pass (the
    source frequency), and each pass is then a capped pass (`_capped_pass`): the
    head triples of every rule are pooled per relation and, when a relation has
    less budget left than the pool, only part of them is inserted, chosen by
    `degree_targets` (the source profiles, unspent). Without `caps`, every derived
    head triple is inserted, rule by rule.
    """

    if source != target_uri:
        initialize_graph(
            client=client,
            source=source,
            new_graph_uri=target_uri,
            chunk_size=chunk_size,
            term_mapping=term_mapping,
        )

    state = {r_id: 0 for r_id in rules.keys()}
    capped = _CapState.start(client, target_uri, rules, caps, degree_targets)
    step = 0
    total_added = 0
    while True:
        step += 1
        pass_counts: dict[str, int] = {}
        if capped is not None:
            added = _capped_pass(
                client, rules, term_mapping, target_uri, chunk_size, capped, pass_counts
            )
        else:
            for r_id, rule in rules.items():
                count = apply_rule(
                    client=client,
                    graph_uri=target_uri,
                    rule=rule,
                    term_mapping=term_mapping,
                    chunk_size=chunk_size,
                )
                if count:
                    pass_counts[r_id] = count
            added = sum(pass_counts.values())
        for r_id, count in pass_counts.items():
            state[r_id] += count

        if not added:
            logger.info("[%s] Pass %d: no triples added.", label, step)
            break

        total_added += added
        logger.info(
            "[%s] Pass %d: +%d triples (%s).",
            label,
            step,
            added,
            _format_rule_counts(pass_counts),
        )

    totals = {r_id: count for r_id, count in state.items() if count}
    logger.info(
        "[%s] +%d triples in %d passes (stale state)%s.",
        label,
        total_added,
        step,
        f": {_format_rule_counts(totals)}" if totals else "",
    )

    if capped is not None:
        capped.log(label)

    for rule_id in get_closed_rules(client, target_uri, rules):
        rules[rule_id].closed = True

    if profiles is not None:
        for predicate in get_closed_preds(client, target_uri, profiles):
            profiles[predicate].closed = True

    return total_added


# ---------------------------------------------------------------------------
# Capped completion.
# ---------------------------------------------------------------------------
class _CapState:
    """What a capped completion keeps between passes: each head predicate's
    frequency cap and current frequency, the source profiles that rank the
    candidates, and the derivable head triples the last pass left out."""

    def __init__(
        self,
        caps: dict[str, int],
        current: dict[str, int],
        degree_targets: dict[str, PredicateProfile],
        undirected: set[str],
    ) -> None:
        self.caps = caps
        self.current = current
        self.degree_targets = degree_targets
        self.undirected = undirected
        # Head predicate -> derivable head triples not inserted in the last pass.
        self.left_out: dict[str, int] = {}

    @classmethod
    def start(
        cls,
        client: SPARQLWrapper,
        graph_uri: str,
        rules: dict[str, HornRule],
        caps: dict[str, int] | None,
        degree_targets: dict[str, PredicateProfile] | None,
    ) -> "_CapState | None":
        if caps is None:
            return None
        current = {
            f"<{p}>": count
            for p, count in get_predicate_frequencies(client, graph_uri).items()
        }
        return cls(caps, current, degree_targets or {}, get_undirected_preds(rules))

    def remaining(self, predicate: str) -> int:
        if predicate not in self.caps:
            return 2**62
        return max(self.caps[predicate] - self.current.get(predicate, 0), 0)

    def log(self, label: str) -> None:
        """Logs each capped relation's final frequency against its cap and the
        derivable head triples left out of it."""
        for predicate in sorted(self.left_out, key=short_term):
            logger.info(
                "[%s] Cap %s: %d/%d, %d derivable head triples left out.",
                label,
                short_term(predicate),
                self.current.get(predicate, 0),
                self.caps[predicate],
                self.left_out[predicate],
            )


def _capped_pass(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    graph_uri: str,
    chunk_size: int,
    state: _CapState,
    pass_counts: dict[str, int],
) -> int:
    """Applies every rule to one snapshot of the graph, pools the new head triples
    per relation and inserts, per relation, at most the budget left under its cap.

    Pooling lets the rules that derive one relation share its budget instead of the
    first rule taking it all. When the pool is larger than the budget, the triples
    are taken greedily, in tiers: first those whose subject is below its source
    out-degree and whose object is below its source in-degree for that relation
    (counted as triples are taken), then those where one of the two is, then the
    rest. Within a tier, a triple derived by more rules comes first, then a random
    order. A self-loop is taken only while the relation has fewer than the source's
    self-loops. A relation of `state.undirected` is taken in pairs (a, b) + (b, a),
    so it stays symmetric.

    `pass_counts` gets, per rule, the inserted triples it derives. Returns the
    number of triples inserted.
    """
    # Head predicate -> (subject, object) -> rules deriving it.
    pools: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for r_id, rule in rules.items():
        for pair in novel_head_pairs(client, graph_uri, rule, term_mapping, chunk_size):
            pools[rule.head.predicate][pair].add(r_id)

    inserted = 0
    state.left_out = {}
    for predicate in sorted(pools, key=short_term):
        pool = pools[predicate]
        units = _units(
            client, graph_uri, predicate, pool, state, term_mapping, chunk_size
        )
        if predicate not in state.caps:
            chosen = [t for u in units for t in u]
        else:
            budget = state.remaining(predicate)
            chosen = _choose(client, graph_uri, predicate, units, pool, budget, state)

        if chosen:
            count = insert_triples_sparql(
                graph_uri=graph_uri,
                client=client,
                triple_stream=(
                    format_triple(s, predicate, o, term_mapping) for s, o in chosen
                ),
                chunk_size=chunk_size,
            )
            inserted += count
            state.current[predicate] = state.current.get(predicate, 0) + len(chosen)
            for pair in chosen:
                for r_id in pool.get(pair, ()):
                    pass_counts[r_id] = pass_counts.get(r_id, 0) + 1
        missed = len(pool.keys() - set(chosen))
        if predicate in state.caps and missed:
            state.left_out[predicate] = missed
    return inserted


def _units(
    client: SPARQLWrapper,
    graph_uri: str,
    predicate: str,
    pool: dict[tuple[str, str], set[str]],
    state: _CapState,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> list[list[tuple[str, str]]]:
    """Groups a relation's new head triples into the units that are taken or left
    together: one triple each, or for an undirected relation the triple and its
    reverse when the graph lacks the reverse."""
    if predicate not in state.undirected:
        return [[pair] for pair in pool]
    reverses = {(o, s) for s, o in pool if (o, s) not in pool}
    existing = get_existing_triples(
        client=client,
        graph_uri=graph_uri,
        candidate_triples=(
            format_triple(s, predicate, o, term_mapping) for s, o in reverses
        ),
        term_mapping=term_mapping,
        chunk_size=chunk_size,
    )
    units: list[list[tuple[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for s, o in pool:
        if (s, o) in seen:
            continue
        unit = [(s, o)]
        seen.add((s, o))
        if s != o and (o, s) not in seen:
            reverse = format_triple(o, predicate, s, term_mapping)
            if (o, s) in pool or reverse not in existing:
                unit.append((o, s))
                seen.add((o, s))
        units.append(unit)
    return units


def _choose(
    client: SPARQLWrapper,
    graph_uri: str,
    predicate: str,
    units: list[list[tuple[str, str]]],
    pool: dict[tuple[str, str], set[str]],
    budget: int,
    state: _CapState,
) -> list[tuple[str, str]]:
    """Picks the triples of `units` to insert, within `budget` (see
    `_capped_pass` for the order)."""
    if budget <= 0:
        return []
    target = state.degree_targets.get(predicate, PredicateProfile())
    out_degree = Counter(get_domain(client, graph_uri, predicate))
    in_degree = Counter(get_range(client, graph_uri, predicate))
    loops = get_reflexivity(client, graph_uri, predicate)

    order = sorted(
        units,
        key=lambda u: (-max(len(pool.get(t, ())) for t in u), random.random()),
    )

    def fit(unit: list[tuple[str, str]]) -> int:
        return min(
            (out_degree[s] < target.domain.get(s, 0))
            + (in_degree[o] < target.range.get(o, 0))
            for s, o in unit
        )

    chosen: list[tuple[str, str]] = []
    taken = [False] * len(order)
    for tier in (2, 1, 0):
        for i, unit in enumerate(order):
            if taken[i] or len(unit) > budget:
                continue
            self_loops = sum(s == o for s, o in unit)
            if self_loops and loops + self_loops > target.reflexivity:
                continue
            if fit(unit) < tier:
                continue
            taken[i] = True
            chosen.extend(unit)
            budget -= len(unit)
            loops += self_loops
            for s, o in unit:
                out_degree[s] += 1
                in_degree[o] += 1
            if budget == 0:
                return chosen
    return chosen
