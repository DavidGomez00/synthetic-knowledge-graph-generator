"""Topological descriptors of a Knowledge Graph: per-predicate frequency, domain
and range distributions used to drive synthetic triple generation (`engine/edb.py`,
`engine/idb.py`) without needing continued access to the original graph.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from rdflib import Graph
from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    get_domain,
    get_predicate_frequencies,
    get_range,
    get_reflexivity,
    get_triple_count,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Knowledge Graph Metrics
# ---------------------------------------------------------------------------
@dataclass
class PredicateProfile:
    """Tracks subject and object frequency distributions for a specific predicate."""

    domain: dict[str, int] = field(default_factory=dict)
    range: dict[str, int] = field(default_factory=dict)
    frequency: int = 0
    reflexivity: int = 0
    closed: bool = False


@dataclass
class GraphMetrics:
    """A structured container for RDF graph metrics and properties."""

    profiles: dict[str, PredicateProfile]
    triple_count: int

    @classmethod
    def from_uri(cls, client: SPARQLWrapper, graph_uri: str) -> "GraphMetrics":
        """Instantiates GraphMetrics by delegating aggregation to the SPARQL endpoint.

        Scales efficiently by querying distributions per-predicate, avoiding massive
        data transfers and database ResultSetMaxRows limits.
        """

        triple_count = get_triple_count(client, graph_uri)
        logger.debug(
            "Retrieving metrics from <%s> (%d triples).", graph_uri, triple_count
        )
        profiles: dict[str, PredicateProfile] = {}

        predicates = get_predicate_frequencies(client, graph_uri) or {}

        for predicate, frequency in predicates.items():
            if not predicate.startswith("<"):
                predicate = f"<{predicate}>"

            reflexivity = get_reflexivity(client, graph_uri, predicate)
            domain = get_domain(client, graph_uri, predicate)
            p_range = get_range(client, graph_uri, predicate)

            profiles[predicate] = PredicateProfile(
                frequency=frequency,
                domain=domain,
                range=p_range,
                reflexivity=reflexivity,
            )

        for predicate, profile in profiles.items():
            if "?f" in profile.domain.keys():
                raise ValueError(f"Error ?f en {predicate} domain.")

        return cls(profiles=profiles, triple_count=triple_count)

    @classmethod
    def from_rdflib(cls, graph: Graph) -> "GraphMetrics":
        """Calculates frequency and cardinality metrics for a graph.

        Args:
            kg_file: Path to file with KG triples.

        Returns:
            GraphMetrics dataclass containing cardinalities and frequency distributions.
        """

        # Counters and mappings
        profiles: dict[str, PredicateProfile] = defaultdict(PredicateProfile)
        triple_count = 0

        # Single pass through the graph
        for s, p, o in graph:
            s_str = str(s)
            p_str = f"<{str(p)}>"
            o_str = str(o)

            triple_count += 1
            profiles[p_str].frequency += 1
            profiles[p_str].domain[s_str] += 1
            profiles[p_str].range[o_str] += 1

            if s_str == o_str:
                profiles[p_str].reflexivity += 1

        metrics = GraphMetrics(
            profiles=dict(profiles),
            triple_count=triple_count,
        )

        reflexive_preds = 0
        for _, profile in profiles.items():
            if profile.reflexivity > 0:
                reflexive_preds += 1

        logger.debug("Loaded graph metrics for %d predicates.", len(profiles))
        return metrics
