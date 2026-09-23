"""Uploads a graph from an .nt/.tsv file (by default `graph.triple_file` into
`graph.base_uri`; override with `--triple-file`/`--graph-uri`). Optionally also
runs rule-based completion to build the "complete" graph used as the source for
metric extraction (see `--complete`)."""

import argparse
import logging
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.config import RunConfig
from skgg.core.queries import get_triple_count, initialize_graph
from skgg.core.rules import parse_rule_set
from skgg.engine.completion import complete_graph
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
        description="Upload a graph from a .nt/.tsv file and, optionally, run "
        "rule-based graph completion."
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
    parser.add_argument(
        "--pca-threshold",
        type=float,
        default=None,
        help="Override the config file's rules.pca_threshold for this run only.",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Also run rule-based graph completion after uploading the "
        "graph, producing graph.complete_uri.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    config = RunConfig.from_json(resolve_config_path(args.config_file))
    setup_logging(
        level=args.log_level if args.log_level is not None else config.logging.level
    )

    input_dir = config.data.input_dir
    complete_uri = config.graph.complete_uri

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

    if args.complete:
        logger.info("Starting Graph Completion")

        rules = parse_rule_set(
            rules_file=input_dir / config.rules.rules_file,
            term_mapping=term_mapping,
            pca_threshold=(
                args.pca_threshold
                if args.pca_threshold is not None
                else config.rules.pca_threshold
            ),
        )

        # Complete graph
        complete_graph(
            client=client,
            rules=rules,
            term_mapping=term_mapping,
            source=target_uri,
            target_uri=complete_uri,
            chunk_size=config.db_config.chunk_size,
        )

        logger.info(
            "Complete graph in <%s> has %d triples.",
            complete_uri,
            get_triple_count(client, complete_uri),
        )
    else:
        logger.info("Skipping graph completion (pass --complete to run it).")
