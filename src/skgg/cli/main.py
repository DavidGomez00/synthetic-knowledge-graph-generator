"""End-to-end experiment entry point: metrics → EDB → IDB → synthetic graph.

See `run_synthetic_graph_experiment` and AGENTS.md's "Running an experiment"
section for the full pipeline description.
"""

import logging
import time
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.config import RunConfig
from skgg.core.queries import get_support, get_triple_count
from skgg.core.rules import HornRule, parse_rule_set
from skgg.engine.edb import generate_edb
from skgg.engine.idb import generate_idb
from skgg.engine.metrics import GraphMetrics, PredicateProfile
from skgg.utils import create_sparql_client, get_term_mapping, setup_logging

logger = logging.getLogger(__name__)


def _format_distribution(dist: dict[str, int], indent: str) -> str:
    """Formats a value->count distribution (a predicate's domain or range) as
    one 'value: count' entry per line, sorted by value, so it stays readable
    instead of dumping the raw dict repr onto one line."""
    if not dist:
        return f"{indent}(empty)"
    return "\n".join(
        f"{indent}{value}: {count}" for value, count in sorted(dist.items())
    )


def _format_graph_block(
    uri: str,
    triple_count: int,
    metrics: GraphMetrics,
    rules: dict[str, HornRule],
    supports: dict[str, int],
) -> str:
    """Formats one graph's stats: total triples, per-predicate domain/range/
    frequency, and per-rule support."""
    lines = [f"=== <{uri}> ===", f"\tTotal triples: {triple_count}", "\tPredicates:"]
    for pred in sorted(metrics.profiles):
        profile = metrics.profiles[pred]
        lines.append(f"\t\t{pred}:")
        lines.append("\t\t\tDomain:")
        lines.append(_format_distribution(profile.domain, "\t\t\t\t"))
        lines.append("\t\t\tRange:")
        lines.append(_format_distribution(profile.range, "\t\t\t\t"))
        lines.append(f"\t\t\tFrequency: {profile.frequency}")
    lines.append("\tRules:")
    for rule_id in sorted(rules):
        lines.append(f"\t\t{rule_id}:")
        lines.append(f"\t\t\tSupport: {supports[rule_id]}")
    return "\n".join(lines)


def _format_delta_block(
    og_triple_count: int,
    syn_triple_count: int,
    og_metrics: GraphMetrics,
    syn_metrics: GraphMetrics,
    rules: dict[str, HornRule],
    og_supports: dict[str, int],
    syn_supports: dict[str, int],
) -> str:
    """Formats synthetic-minus-original deltas for triples, per-predicate
    frequency/domain/range size, and per-rule support."""
    empty_profile = PredicateProfile()
    triple_delta = syn_triple_count - og_triple_count
    lines = [f"Triple count delta: {triple_delta}", "Predicates:"]
    all_preds = sorted(set(og_metrics.profiles) | set(syn_metrics.profiles))
    for pred in all_preds:
        og_profile = og_metrics.profiles.get(pred, empty_profile)
        syn_profile = syn_metrics.profiles.get(pred, empty_profile)
        freq_delta = syn_profile.frequency - og_profile.frequency
        domain_delta = len(syn_profile.domain) - len(og_profile.domain)
        range_delta = len(syn_profile.range) - len(og_profile.range)
        lines.append(f"    {pred}:")
        lines.append(f"        Frequency delta: {freq_delta}")
        lines.append(f"        Domain size delta: {domain_delta}")
        lines.append(f"        Range size delta: {range_delta}")
    lines.append("Rules:")
    for rule_id in sorted(rules):
        support_delta = syn_supports[rule_id] - og_supports[rule_id]
        lines.append(f"    {rule_id}: Support delta: {support_delta}")
    return "\n".join(lines)


def summary(
    client: SPARQLWrapper,
    original_uri: str,
    synthetic_uri: str,
    rules: dict[str, HornRule],
) -> None:
    """Creates a summary in the logs that compare the original metrics with the created
    graph metrics."""
    # OG triples
    og_triple_count = get_triple_count(client, original_uri)
    syn_triple_count = get_triple_count(client, synthetic_uri)

    # Profiles and frequencies
    og_metrics = GraphMetrics.from_uri(client, original_uri)
    syn_metrics = GraphMetrics.from_uri(client, synthetic_uri)

    # Rule support
    og_supports = {
        rid: get_support(client, rule, original_uri) for rid, rule in rules.items()
    }
    syn_supports = {
        rid: get_support(client, rule, synthetic_uri) for rid, rule in rules.items()
    }

    og_block = _format_graph_block(
        original_uri, og_triple_count, og_metrics, rules, og_supports
    )
    syn_block = _format_graph_block(
        synthetic_uri, syn_triple_count, syn_metrics, rules, syn_supports
    )
    logger.info("Original graph summary:\n%s", og_block)
    logger.info("Synthetic graph summary:\n%s", syn_block)
    logger.info(
        "Comparison (synthetic - original):\n%s",
        _format_delta_block(
            og_triple_count,
            syn_triple_count,
            og_metrics,
            syn_metrics,
            rules,
            og_supports,
            syn_supports,
        ),
    )


def run_synthetic_graph_experiment(
    config_file: Path,
    source: str | None = None,
    skip_edb_generation: bool = False,
) -> None:
    """Runs a Synthetic Graph generation experiment.

    If `skip_edb_generation` is True, EDB generation is skipped entirely and
    the IDB step reuses whatever triples already sit at `config.graph.edb_uri`
    in the database (e.g. from a previous run) instead of regenerating them.
    """

    ## ------ Setup ------
    config = RunConfig.from_json(config_file)
    setup_logging(level=config.logging.level)
    logger.info("Confifuration correctly initialized.")

    input_dir = config.data.input_dir
    rules_file = input_dir / config.rules.rules_file

    # SPARQL client
    client = create_sparql_client(config)

    ## ------ Extraction of predicate profiles from original graph -------
    # Graph metrics
    graph_metrics = GraphMetrics.from_uri(client, config.graph.complete_uri)
    profiles = graph_metrics.profiles

    ## ------ Previous evaluation of rules ------
    term_mapping = get_term_mapping(
        ontology_file=input_dir / config.graph.ontology_file,
        default_namespace=config.graph.namespace,
    )

    rules = parse_rule_set(
        rules_file=rules_file,
        term_mapping=term_mapping,
        pca_threshold=config.rules.pca_threshold,
    )

    ## ------ EDB Generation  ------
    chunk_size = config.db_config.chunk_size
    edb_uri = config.graph.edb_uri
    synthetic_uri = config.graph.synthetic_uri
    if source is None:
        source = config.graph.complete_uri

    start_time = time.time()

    if skip_edb_generation:
        logger.info(
            "Skipping EDB generation, reusing existing EDB at <%s> with %d triples",
            edb_uri,
            get_triple_count(client, edb_uri),
        )
    else:
        logger.info("Generating EDB...")

        generate_edb(
            client=client,
            term_mapping=term_mapping,
            rules=rules,
            edb_uri=edb_uri,
            chunk_size=chunk_size,
            profiles=graph_metrics.profiles,
        )

        edb_time = time.time() - start_time

        logger.info(
            "Finished EDB generation after %f s at <%s> with %d triples",
            edb_time,
            edb_uri,
            get_triple_count(client, edb_uri),
        )

    ## ------ Graph Completion  ------
    generate_idb(
        client=client,
        rules=rules,
        term_mapping=term_mapping,
        edb_uri=edb_uri,
        synthetic_uri=synthetic_uri,
        chunk_size=chunk_size,
        profiles=profiles,
    )

    summary(client, config.graph.complete_uri, synthetic_uri, rules)

    logger.info("Execution finished after %d s.", time.time() - start_time)


if __name__ == "__main__":
    mario_config = Path("configurations/mario.json")
    fr_config = Path("configurations/french_royalty.json")
    run_synthetic_graph_experiment(fr_config, skip_edb_generation=True)
