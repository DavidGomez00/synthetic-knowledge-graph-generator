"""Uploads a graph from an .nt/.tsv file (by default `graph.triple_file` into
`graph.base_uri`; override with `--triple-file`/`--graph-uri`)."""

import argparse
import logging
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.config import RunConfig
from skgg.core.queries import get_triple_count, initialize_graph
from skgg.utils import (
    build_term_mapping,
    create_sparql_client,
    resolve_config_path,
    setup_logging,
)

logger = logging.getLogger(__name__)


def upload_graph(
    client: SPARQLWrapper,
    triple_file: str | Path,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int = 1000,
) -> int:
    """Overwrites `graph_uri` with the triples in `triple_file` (.nt or .tsv;
    .tsv terms are resolved via `term_mapping`). Returns the resulting triple
    count."""
    initialize_graph(
        client=client,
        source=str(triple_file),
        new_graph_uri=graph_uri,
        chunk_size=chunk_size,
        term_mapping=term_mapping,
    )
    count = get_triple_count(client, graph_uri)
    logger.info(
        "Inserted %s into <%s> with %d triples.", triple_file, graph_uri, count
    )
    return count


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a graph from a .nt/.tsv file into graph.base_uri "
        "(or --graph-uri)."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. french_royalty.json), "
        "or a path to one.",
    )
    parser.add_argument(
        "--triple-file",
        default=None,
        help="Override the config file's graph.triple_file. A bare filename is "
        "resolved under data.input_dir; a path containing '/' is used as given.",
    )
    parser.add_argument(
        "--graph-uri",
        default=None,
        help="Override the target graph URI (defaults to graph.base_uri).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    config = RunConfig.from_json(resolve_config_path(args.config_file))
    setup_logging(
        level=args.log_level if args.log_level is not None else config.logging.level
    )

    input_dir = config.data.input_dir

    triple_file = args.triple_file or config.graph.triple_file
    source = Path(triple_file) if "/" in triple_file else input_dir / triple_file
    target_uri = args.graph_uri or config.graph.base_uri

    term_mapping = build_term_mapping(
        term_namespaces=config.graph.term_namespaces,
        default_namespace=config.graph.namespace,
    )

    # SPARQL client
    client = create_sparql_client(config)

    upload_graph(client, source, target_uri, term_mapping)
