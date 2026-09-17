"""Uploads a base graph from an .nt/.tsv file. Optionally also runs rule-based
completion to build the "complete" graph used as the source for metric
extraction (see `--complete`)."""

import argparse
import logging

from skgg.config import RunConfig
from skgg.core.queries import get_triple_count, initialize_graph
from skgg.core.rules import parse_rule_set
from skgg.engine.completion import complete_graph
from skgg.utils import (
    create_sparql_client,
    get_term_mapping,
    resolve_config_path,
    setup_logging,
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a base graph and, optionally, run rule-based "
        "graph completion."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. french_royalty.json), "
        "or a path to one.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Also run rule-based graph completion after uploading the base "
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
    base_uri = config.graph.base_uri
    complete_uri = config.graph.complete_uri

    term_mapping = get_term_mapping(
        ontology_file=input_dir / config.graph.ontology_file,
        default_namespace=config.graph.namespace,
    )

    # SPARQL client
    client = create_sparql_client(config)

    # Initialize base graph
    initialize_graph(
        client=client,
        source=str(input_dir / config.graph.triple_file),
        new_graph_uri=base_uri,
        chunk_size=1000,
        term_mapping=term_mapping,
    )

    base_count = get_triple_count(client, base_uri)
    logger.info("Inserted base graph to <%s> with %d triples.", base_uri, base_count)

    if args.complete:
        logger.info("Starting Graph Completion")

        rules = parse_rule_set(
            rules_file=input_dir / config.rules.rules_file,
            term_mapping=term_mapping,
            pca_threshold=config.rules.pca_threshold,
        )

        # Complete graph
        complete_graph(
            client=client,
            rules=rules,
            term_mapping=term_mapping,
            initial_uri=base_uri,
            complete_uri=complete_uri,
            chunk_size=config.db_config.chunk_size,
        )

        logger.info(
            "Complete graph in <%s> has %d triples.",
            complete_uri,
            get_triple_count(client, complete_uri),
        )
    else:
        logger.info("Skipping graph completion (pass --complete to run it).")
