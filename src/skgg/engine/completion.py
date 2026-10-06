"""Forward-chains the rule set over a graph until no rule adds any more triples.

Used by `cli/main.py` to derive the synthetic graph (`graph.synthetic_uri`) from
the EDB, and again after each cycle-breaking round. Assumes rule bodies are
fully grounded.
"""

import logging

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import get_predicate_frequencies, initialize_graph
from skgg.core.rules import HornRule, rule_sort_key
from skgg.engine.generator import apply_rule, get_closed_preds, get_closed_rules
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
    """

    if source != target_uri:
        initialize_graph(
            client=client,
            source=source,
            new_graph_uri=target_uri,
            chunk_size=chunk_size,
            term_mapping=term_mapping,
        )

    grounded_preds = set(get_predicate_frequencies(client, target_uri).keys())
    state = {r_id: 0 for r_id in rules.keys()}
    step = 0
    total_added = 0
    while True:
        step += 1
        pass_counts: dict[str, int] = {}
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
                state[r_id] += count
                grounded_preds.add(rule.head.predicate)

        if not pass_counts:
            logger.info("[%s] Pass %d: no triples added.", label, step)
            break

        added = sum(pass_counts.values())
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

    for rule_id in get_closed_rules(client, target_uri, rules):
        rules[rule_id].closed = True

    if profiles is not None:
        for predicate in get_closed_preds(client, target_uri, profiles):
            profiles[predicate].closed = True

    return total_added
