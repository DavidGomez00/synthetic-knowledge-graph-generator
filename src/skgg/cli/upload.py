"""Uploads a base graph from an .nt file. Then it creates a complete graph from the
set of rules."""

import logging
from pathlib import Path

from skgg.config import RunConfig
from skgg.core.queries import get_triple_count, initialize_graph
from skgg.core.rules import parse_rule_set
from skgg.engine.completion import complete_graph
from skgg.utils import create_sparql_client, get_term_mapping, setup_logging

### EDIT THIS PATH   vvv
graph_config = Path("configurations/french_royalty.json")
config = RunConfig.from_json(graph_config)

setup_logging(level=config.logging.level)
logger = logging.getLogger(__name__)

input_dir = config.data.input_dir
base_uri = config.graph.base_uri
complete_uri = config.graph.complete_uri

term_mapping = get_term_mapping(
    ontology_file=input_dir / config.graph.ontology_file,
    default_namespace=config.graph.namespace,
)

rules = parse_rule_set(
    rules_file=input_dir / config.rules.rules_file,
    term_mapping=term_mapping,
    pca_threshold=config.rules.pca_threshold,
)

rules = list(rules.values())

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

logger.info("Starting Graph Completion")

# Complete graph
complete_graph(
    client=client,
    rules=rules,
    term_mapping=term_mapping,
    base_uri=base_uri,
    complete_uri=complete_uri,
    chunk_size=config.db_config.chunk_size,
)

logger.info(
    "Complete graph in <%s> has %d triples.",
    complete_uri,
    get_triple_count(client, complete_uri),
)
