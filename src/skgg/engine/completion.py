"""Forward-chains the rule set over a graph until no rule adds any more triples.

Used by `cli/main.py` to derive the synthetic graph (`graph.synthetic_uri`) from
the EDB, and again after each cycle-breaking round. Assumes rule bodies are
fully grounded.
"""

import logging

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import get_predicate_frequencies, initialize_graph
from skgg.core.rules import HornRule
from skgg.engine.generator import apply_rule, get_closed_preds, get_closed_rules
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph completion.
# ---------------------------------------------------------------------------
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

    `label` prefixes this call's log lines, so the several completions of one
    pipeline run can be told apart. Per-pass detail is logged at DEBUG; one INFO
    line summarizes the whole call.

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
        )

    grounded_preds = set(get_predicate_frequencies(client, target_uri).keys())
    state = {r_id: 0 for r_id in rules.keys()}
    step = 0
    total_added = 0
    while True:
        step += 1
        added = 0
        for r_id, rule in rules.items():
            count = apply_rule(
                client=client,
                graph_uri=target_uri,
                rule=rule,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
            )
            if count:
                logger.debug("Rule %s added %d triples.", r_id, count)
                state[r_id] += count
                added += count
                grounded_preds.add(rule.head.predicate)

        if added:
            state_msg = " \n".join(
                [
                    f"\tRule {r_id} added {state[r_id]} triples."
                    for r_id in rules.keys()
                    if state[r_id] > 0
                ]
            )
            total_added += added
            logger.debug("[%s] Pass %d: added %d triples.", label, step, added)
            logger.debug("\n%s", state_msg)

        else:
            logger.debug("[%s] Pass %d: no triples added.", label, step)
            break

    logger.info(
        "[%s] +%d triples in %d passes (stale state).", label, total_added, step
    )

    for rule_id in get_closed_rules(client, target_uri, rules):
        rules[rule_id].closed = True

    if profiles is not None:
        for predicate in get_closed_preds(client, target_uri, profiles):
            profiles[predicate].closed = True

    return total_added
