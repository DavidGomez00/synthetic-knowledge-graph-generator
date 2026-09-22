"""Topological descriptors of a Knowledge Graph: per-predicate frequency, domain
and range distributions used to drive synthetic triple generation (`engine/edb.py`,
`engine/completion.py`) without needing continued access to the original graph.
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

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


def _sanitize_uri_for_filename(uri: str) -> str:
    """Turns a graph URI into a safe, readable filename stem."""
    return re.sub(r"[^A-Za-z0-9]+", "_", uri.strip("<>")).strip("_")


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


def _dump_metrics_for_debugging(metrics: "GraphMetrics", graph_uri: str) -> None:
    """Writes `metrics` to logs/metrics/<sanitized graph_uri>.json for ad hoc
    inspection -- nothing in the pipeline reads this back; it exists purely
    so predicate profiles can be consulted without re-querying the graph.
    """
    output_path = (
        Path("logs") / "metrics" / f"{_sanitize_uri_for_filename(graph_uri)}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w") as f:
            json.dump(asdict(metrics), f, indent=2, sort_keys=True)
    except OSError:
        logger.warning(
            "Could not write debug metrics dump to %s.", output_path, exc_info=True
        )


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

        As a side effect, also dumps the resulting metrics to
        logs/metrics/<graph_uri>.json for ad hoc debugging -- see
        `_dump_metrics_for_debugging`; nothing in the pipeline reads it back.
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

        metrics = cls(profiles=profiles, triple_count=triple_count)
        _dump_metrics_for_debugging(metrics, graph_uri)
        return metrics

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
