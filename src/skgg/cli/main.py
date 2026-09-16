"""End-to-end experiment entry point: metrics → EDB → IDB → synthetic graph.

See `run_synthetic_graph_experiment` and AGENTS.md's "Running an experiment"
section for the full pipeline description.
"""

import argparse
import logging
import time
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.config import RunConfig
from skgg.core.queries import get_support, get_triple_count
from skgg.core.rules import HornRule, parse_rule_set
from skgg.engine.completion import complete_graph
from skgg.engine.edb import generate_edb
from skgg.engine.metrics import GraphMetrics, PredicateProfile
from skgg.utils import (
    create_sparql_client,
    get_term_mapping,
    resolve_config_path,
    setup_logging,
)

logger = logging.getLogger(__name__)


def _format_graph_block(
    uri: str,
    triple_count: int,
    rules: dict[str, HornRule],
    supports: dict[str, int],
) -> str:
    """Formats one graph's stats: total triples and per-rule support. Omits
    per-predicate domain/range/frequency detail, which doesn't scale to large
    KGs — see `_format_delta_block` for the compact predicate-level deltas
    instead."""
    lines = [f"=== <{uri}> ===", f"\tTotal triples: {triple_count}", "\tRules:"]
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
    frequency/domain/range size, and per-rule support as an aligned,
    human-readable table."""
    empty_profile = PredicateProfile()
    triple_delta = syn_triple_count - og_triple_count
    pct = f", {triple_delta / og_triple_count:+.1%}" if og_triple_count else ""
    lines = [
        f"Triples: {og_triple_count} -> {syn_triple_count} ({triple_delta:+d}{pct})",
        "",
        "Predicates (Δfrequency / Δdomain size / Δrange size):",
    ]
    all_preds = sorted(set(og_metrics.profiles) | set(syn_metrics.profiles))
    name_width = max((len(p) for p in all_preds), default=0)

    pred_deltas = []
    for pred in all_preds:
        og_profile = og_metrics.profiles.get(pred, empty_profile)
        syn_profile = syn_metrics.profiles.get(pred, empty_profile)
        pred_deltas.append(
            (
                pred,
                syn_profile.frequency - og_profile.frequency,
                len(syn_profile.domain) - len(og_profile.domain),
                len(syn_profile.range) - len(og_profile.range),
            )
        )
    # Size each numeric column to its widest value (sign included) instead of
    # a fixed width, so columns stay aligned however many digits deltas have.
    freq_width = max((len(f"{d[1]:+d}") for d in pred_deltas), default=1)
    domain_width = max((len(f"{d[2]:+d}") for d in pred_deltas), default=1)
    range_width = max((len(f"{d[3]:+d}") for d in pred_deltas), default=1)

    for pred, freq_delta, domain_delta, range_delta in pred_deltas:
        lines.append(
            f"  {pred.ljust(name_width)}  "
            f"Δfreq: {freq_delta:+{freq_width}d}  "
            f"Δdomain: {domain_delta:+{domain_width}d}  "
            f"Δrange: {range_delta:+{range_width}d}"
        )
    lines.append("")
    lines.append("Rules (support deltas):")
    rule_ids = sorted(rules)
    rule_id_width = max((len(rid) for rid in rule_ids), default=0)
    og_width = max((len(str(og_supports[rid])) for rid in rule_ids), default=1)
    syn_width = max((len(str(syn_supports[rid])) for rid in rule_ids), default=1)
    delta_width = max(
        (len(f"{syn_supports[rid] - og_supports[rid]:+d}") for rid in rule_ids),
        default=1,
    )
    for rule_id in rule_ids:
        og_support = og_supports[rule_id]
        syn_support = syn_supports[rule_id]
        support_delta = syn_support - og_support
        lines.append(
            f"  {rule_id.ljust(rule_id_width)}  "
            f"{og_support:>{og_width}} -> {syn_support:>{syn_width}}"
            f"  ({support_delta:+{delta_width}d})"
        )
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

    og_block = _format_graph_block(original_uri, og_triple_count, rules, og_supports)
    syn_block = _format_graph_block(
        synthetic_uri, syn_triple_count, rules, syn_supports
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
    skip_edb_generation: bool = False,
    log_level: int | str | None = None,
) -> None:
    """Runs a Synthetic Graph generation experiment.

    If `skip_edb_generation` is True, EDB generation is skipped entirely and
    the IDB step reuses whatever triples already sit at `config.graph.edb_uri`
    in the database (e.g. from a previous run) instead of regenerating them.

    `log_level`, if given, overrides `config.logging.level` for this run.
    """

    ## ------ Setup ------
    config = RunConfig.from_json(config_file)
    setup_logging(level=log_level if log_level is not None else config.logging.level)
    logger.info("Confifuration correctly initialized.")

    input_dir = config.data.input_dir
    rules_file = input_dir / config.rules.rules_file

    # SPARQL client
    client = create_sparql_client(config)

    ## ------ Extraction of predicate profiles from original graph -------
    # Graph metrics
    graph_metrics = GraphMetrics.from_uri(client, config.graph.base_uri)

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

    # ## ------ Graph Completion  ------
    # generate_idb(
    #     client=client,
    #     rules=rules,
    #     term_mapping=term_mapping,
    #     edb_uri=edb_uri,
    #     synthetic_uri=synthetic_uri,
    #     chunk_size=chunk_size,
    #     profiles=profiles,
    # )

    complete_graph(
        client=client,
        rules=rules,
        term_mapping=term_mapping,
        base_uri=edb_uri,
        complete_uri=synthetic_uri,
        chunk_size=chunk_size,
    )

    summary(client, config.graph.complete_uri, synthetic_uri, rules)

    logger.info("Execution finished after %d s.", time.time() - start_time)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Synthetic Knowledge Graph generation experiment."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. french_royalty.json), "
        "or a path to one.",
    )
    parser.add_argument(
        "--skip-edb",
        action="store_true",
        help="Skip EDB generation and reuse the existing EDB graph.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_synthetic_graph_experiment(
        resolve_config_path(args.config_file),
        skip_edb_generation=args.skip_edb,
        log_level=args.log_level,
    )
