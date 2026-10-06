"""Completes a graph with a rule set: copies a base graph (a graph URI, or a
.nt/.tsv file loaded directly) into a separate complete graph, then applies every
rule to it until a pass adds nothing (`engine.completion.complete_graph`).

Settings come from flags, from a config file (`-f`), or from both, in which case
the flags win. Without a config file the source, rules file, PCA threshold and
namespace are required, and the database connection falls back to the
`DataConfig`/`DatabaseAuthConfig` defaults."""

import argparse
import logging
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper
from yarl import URL

from skgg.core.config import DatabaseAuthConfig, DataConfig, RunConfig
from skgg.core.queries import get_triple_count
from skgg.core.rules import HornRule, parse_rule_set
from skgg.core.utils import (
    build_sparql_client,
    load_term_mapping,
    resolve_config_path,
    setup_logging,
)
from skgg.engine.completion import complete_graph

logger = logging.getLogger(__name__)

TRIPLE_FILE_SUFFIXES = (".nt", ".tsv")


def complete(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    source: str,
    complete_uri: str,
    chunk_size: int,
) -> int:
    """Overwrites `complete_uri` with `source` (a graph URI or a .nt/.tsv file)
    completed with `rules`, applying them until a pass adds nothing.

    Returns:
        The number of triples the rules added.
    """
    added = complete_graph(
        client=client,
        rules=rules,
        term_mapping=term_mapping,
        source=source,
        target_uri=complete_uri,
        chunk_size=chunk_size,
        label="complete",
    )
    total = get_triple_count(client, complete_uri)
    logger.info(
        "Completed %s into <%s>: %d source triples + %d derived = %d triples.",
        source,
        complete_uri,
        total - added,
        added,
        total,
    )
    return added


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Complete a graph (a graph URI or a .nt/.tsv file) with a rule "
        "set into a separate graph, applying every rule until a pass adds nothing. "
        "Flags override the config file's values."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        default=None,
        help="Config file under configurations/ (e.g. family.source), or a path "
        "to one; the .json extension is optional. Supplies every setting below except --complete-uri.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Base graph: a graph URI or a .nt/.tsv file, loaded directly into "
        "--complete-uri (defaults to the config's graph.base_uri).",
    )
    parser.add_argument(
        "--complete-uri",
        required=True,
        help="Graph URI the completed graph is written to. Its contents are replaced.",
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        default=None,
        help="Rules CSV, used as given (defaults to the config's data.input_dir / "
        "rules.rules_file).",
    )
    parser.add_argument(
        "--pca-threshold",
        type=float,
        default=None,
        help="Minimum PCA confidence of the rules applied (defaults to the config's "
        "rules.pca_threshold).",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Default namespace for bare terms in the rules and .tsv files "
        "(overrides the config's graph.namespace).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=f"Database base URL (config: data.database_url; default "
        f"{DataConfig.database_url}).",
    )
    parser.add_argument(
        "--sparql-endpoint",
        default=None,
        help=f"SPARQL endpoint path under --database-url, e.g. repositories/Family "
        f"(config: data.sparql_endpoint; default {DataConfig.sparql_endpoint}).",
    )
    parser.add_argument(
        "--auth-type",
        choices=["DIGEST", "BASIC", "NONE"],
        default=None,
        help="DIGEST for Virtuoso, BASIC for GraphDB (config: db_config.auth_type; "
        f"default {DatabaseAuthConfig.auth_type}).",
    )
    parser.add_argument(
        "--user", default=None, help="Database user (config: db_config.user)."
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Database password (config: db_config.password).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level, e.g. DEBUG, INFO, WARNING (config: logging.level; "
        "default INFO).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = (
        RunConfig.from_json(resolve_config_path(args.config_file))
        if args.config_file is not None
        else None
    )
    setup_logging(
        level=args.log_level or (config.logging.level if config else logging.INFO)
    )

    source: str | None = args.source or (config.graph.base_uri if config else None)
    rules_file: Path | None = args.rules_file or (
        config.data.input_dir / config.rules.rules_file if config else None
    )
    pca_threshold: float | None = (
        args.pca_threshold
        if args.pca_threshold is not None
        else (config.rules.pca_threshold if config else None)
    )
    term_mapping = load_term_mapping(args.config_file, args.namespace)

    without_config = "is required without -f/--config-file"
    if source is None:
        parser.error(f"--source {without_config}.")
    if rules_file is None:
        parser.error(f"--rules-file {without_config}.")
    if pca_threshold is None:
        parser.error(f"--pca-threshold {without_config}.")
    if term_mapping is None:
        parser.error(f"--namespace {without_config}.")

    if source.lower().endswith(TRIPLE_FILE_SUFFIXES) and not Path(source).is_file():
        parser.error(f"source file {source} does not exist.")
    if source.strip("<>") == args.complete_uri.strip("<>"):
        parser.error("--complete-uri must differ from the source graph.")
    if not rules_file.is_file():
        parser.error(f"rules file {rules_file} does not exist.")

    data = config.data if config else None
    auth = config.db_config if config else DatabaseAuthConfig()
    database_url = args.database_url or (
        data.database_url if data else DataConfig.database_url
    )
    sparql_endpoint = args.sparql_endpoint or (
        data.sparql_endpoint if data else DataConfig.sparql_endpoint
    )
    client = build_sparql_client(
        str(URL(str(database_url)) / sparql_endpoint),
        args.auth_type or auth.auth_type,
        args.user if args.user is not None else auth.user,
        args.password if args.password is not None else auth.password,
    )

    rules = parse_rule_set(rules_file, term_mapping, pca_threshold)
    complete(
        client,
        rules,
        term_mapping,
        source,
        args.complete_uri,
        auth.chunk_size,
    )


if __name__ == "__main__":
    main()
