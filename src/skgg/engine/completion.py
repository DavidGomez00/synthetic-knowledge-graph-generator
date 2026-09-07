"""Builds the "complete" graph used as the source for metric extraction.

Used by `cli/upload.py` after a base graph (`.nt` file) is uploaded: forward-chains
every rule over the base graph, assuming rule bodies are fully grounded, until no
rule can add any more triples. The result (`graph.complete_uri`) is what
`engine.metrics.GraphMetrics.from_uri` later profiles for EDB/IDB generation.
"""

import logging

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import initialize_graph
from skgg.core.rules import HornRule
from skgg.engine.generator import apply_rule
from skgg.engine.metrics import GraphMetrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph completion.
# ---------------------------------------------------------------------------
def complete_graph(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    base_uri: str,
    complete_uri: str,
    chunk_size: int,
) -> None:
    """Completes a graph using only the given rules assuming they are all complete."""

    # Initialize complete graph from base URI
    initialize_graph(
        client=client,
        source=base_uri,
        new_graph_uri=complete_uri,
        chunk_size=chunk_size,
    )

    # Get the initial grounded preds
    graph_metrics = GraphMetrics.from_uri(client, complete_uri)
    grounded_preds = set(graph_metrics.profiles.keys())

    def is_ready(rule: HornRule) -> bool:
        """Returns True if the body from 'rule' is grounded."""
        body_preds = rule.get_body_predicates()
        return True if body_preds.issubset(grounded_preds) else False

    state = dict()
    for r_id in rules.keys():
        state[r_id] = 0

    step = 0
    while True:
        step += 1
        added = 0
        for r_id, rule in rules.items():
            count = apply_rule(
                client=client,
                graph_uri=complete_uri,
                rule=rule,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
            )
            logger.debug("%s added %d triples.", r_id, count)
            if count:
                state[r_id] += count
                added += added
                grounded_preds.add(rule.head.predicate)

        state_msg = "\n".join(
            [f"\t{r_id}: {state[r_id]}" for r_id in rules.keys() if state[r_id] > 0]
        )
        logger.info("[Step %d]: Added %d triples\n%s", step, added, state_msg)

        if not added:
            logger.info(
                "[Step %d]: No more triples to add. Graph completion ended.", step
            )
            break
