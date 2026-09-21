"""Builds the "complete" graph used as the source for metric extraction.

Used by `cli/upload.py` after a base graph (`.nt` file) is uploaded: forward-chains
every rule over the base graph, assuming rule bodies are fully grounded, until no
rule can add any more triples. The result (`graph.complete_uri`) is what
`engine.metrics.GraphMetrics.from_uri` later profiles for EDB/IDB generation.
"""

import logging

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import get_predicate_frequencies, initialize_graph
from skgg.core.rules import HornRule
from skgg.engine.generator import apply_rule
from skgg.engine.idb import get_closed_preds, get_closed_rules
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
) -> None:
    """Completes a graph by applying rules if able.

    `profiles`, if given, carries the target frequency each predicate should
    reach (e.g. extracted from the original source graph) -- when omitted
    (as from `cli/upload.py`'s initial completion step, before any such
    targets exist), predicate closure is simply skipped; rule closure always
    runs, since a rule's `support` target is intrinsic to it.
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
            logger.info("[Step %d] Added %d triples.", step, added)
            logger.debug("\n%s", state_msg)

        else:
            logger.info("[Step %d]: No triples added. Reached stale state.", step)
            break

    for rule_id in get_closed_rules(client, target_uri, rules):
        rules[rule_id].closed = True

    if profiles is not None:
        for predicate in get_closed_preds(client, target_uri, profiles):
            profiles[predicate].closed = True
