"""Visualization helpers for graph structures used in this project (currently
just the predicate `relation_graph` from `core.rules`).

Uses `matplotlib` + `networkx`'s drawing helpers — both already pinned in
`requirements.txt` — so no new dependency is introduced. The `Agg` backend is
forced at import time so this works headlessly (no display required), since
experiments typically run against a Dockerized graph DB on a server/CI box.
"""

import logging
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402 (backend must be set first)
import networkx as nx  # noqa: E402

logger = logging.getLogger(__name__)


def _local_name(predicate: str) -> str:
    """Returns the last path/fragment segment of a predicate URI, stripped of
    its surrounding '<...>' brackets, for compact display labels (e.g.
    '<http://xmlns.com/foaf/0.1/knows>' -> 'knows'). Falls back to the
    stripped URI itself if it has no '/' or '#' to split on."""
    uri = predicate.strip("<>")
    return re.split(r"[/#]", uri)[-1] or uri


def plot_relation_graph(
    graph: nx.DiGraph,
    output_path: Path,
    title: str | None = None,
) -> Path:
    """Renders a predicate relation graph (e.g. from `core.rules.relation_graph`)
    to a PNG file, coloring any predicate involved in a cycle in red so cyclic
    rule dependencies — including self-loops from recursive rules — are
    immediately visible. Each edge is labeled with the id(s) of the rule(s)
    that produced it.

    Args:
        graph: A predicate dependency graph, as built by
            `core.rules.relation_graph`: nodes are predicates, edges go from a
            rule's body predicate(s) to its head predicate, and each edge
            carries a `rule_ids` attribute.
        output_path: Where to save the rendered PNG. Parent directories are
            created if they don't exist.
        title: Optional plot title.

    Returns:
        `output_path`, for convenience/chaining.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cycle_nodes = {node for cycle in nx.simple_cycles(graph) for node in cycle}

    node_count = graph.number_of_nodes()
    fig_size = max(6.0, min(node_count * 0.8, 20.0))
    fig, ax = plt.subplots(figsize=(fig_size, fig_size))

    pos = nx.spring_layout(graph, seed=0)
    node_colors = ["#f28b82" if n in cycle_nodes else "#cfe8ff" for n in graph.nodes]

    nx.draw_networkx_nodes(graph, pos, ax=ax, node_color=node_colors, node_size=1800)
    labels = {node: _local_name(node) for node in graph.nodes}
    nx.draw_networkx_labels(graph, pos, ax=ax, labels=labels, font_size=8)
    nx.draw_networkx_edges(
        graph,
        pos,
        ax=ax,
        connectionstyle="arc3,rad=0.15",
        arrowsize=15,
        node_size=1800,
        min_source_margin=15,
        min_target_margin=15,
    )
    edge_labels = {
        (u, v): ",".join(sorted(d["rule_ids"])) for u, v, d in graph.edges(data=True)
    }
    nx.draw_networkx_edge_labels(graph, pos, ax=ax, edge_labels=edge_labels, font_size=6)

    if title:
        ax.set_title(title)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    logger.info(
        "Saved relation graph visualization (%d predicates, %d in a cycle) to <%s>",
        node_count,
        len(cycle_nodes),
        output_path,
    )
    return output_path
