"""Downloads a named graph (by default `graph.base_uri`; override with
`--graph-uri`) from the graph database into an .nt and a .tsv file, through the
store's native export endpoint (see `queries.export_graph_nt`)."""

import argparse
import logging
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.cli.convert import convert
from skgg.core.config import RunConfig
from skgg.core.queries import export_graph_nt, get_triple_count
from skgg.core.utils import (
    build_term_mapping,
    create_sparql_client,
    resolve_config_path,
    setup_logging,
    short_term,
)

logger = logging.getLogger(__name__)


def download_graph(
    client: SPARQLWrapper,
    graph_uri: str,
    output: str | Path,
    term_mapping: dict[str, str],
    page_size: int = 5000,
) -> int:
    """Writes `graph_uri` to `<output>.nt` and `<output>.tsv`, ignoring any
    .nt/.tsv suffix `output` already has. Parent folders are created. The .tsv
    is converted from the .nt by `convert.convert`, so IRIs are shortened only
    when `term_mapping` maps the bare term back to the same IRI. `page_size`
    is the number of triples per query when the store is read in pages (see
    `queries.export_graph_nt`).

    Returns:
        The number of triples downloaded.

    Raises:
        RuntimeError: If the store exported a different number of triples than
            the graph holds. The partial .nt file is deleted.
    """
    output = Path(output)
    if output.suffix.lower() in (".nt", ".tsv"):
        output = output.with_suffix("")
    nt_path = output.with_name(f"{output.name}.nt")
    tsv_path = output.with_name(f"{output.name}.tsv")
    nt_path.parent.mkdir(parents=True, exist_ok=True)

    # The count checks the export below, which a store cap can cut short.
    expected = get_triple_count(client, graph_uri)
    if expected == 0:
        nt_path.write_text("", encoding="utf-8")
        tsv_path.write_text("", encoding="utf-8")
        logger.warning(
            "<%s> is empty; wrote empty %s and %s.", graph_uri, nt_path, tsv_path
        )
        return 0

    written = export_graph_nt(client, graph_uri, nt_path, page_size)
    if written != expected:
        nt_path.unlink()
        raise RuntimeError(
            f"Exported {written} triples from <{graph_uri}> to {nt_path}, but the "
            f"graph holds {expected}."
        )

    convert(nt_path, tsv_path, term_mapping)
    logger.info(
        "Downloaded <%s>: %d triples to %s and %s.",
        graph_uri,
        written,
        nt_path,
        tsv_path,
    )
    return written


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a graph (graph.base_uri, or --graph-uri) into an .nt "
        "and a .tsv file."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. family.source), or a path "
        "to one. The .json extension is optional.",
    )
    parser.add_argument(
        "--graph-uri",
        default=None,
        help="Graph URI to download (defaults to graph.base_uri).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path without suffix; writes <output>.nt and <output>.tsv "
        "(defaults to data.input_dir / the graph URI's last segment, e.g. "
        "data/family/skgg for http://Family.org/skgg).",
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

    graph_uri = args.graph_uri or config.graph.base_uri
    output = args.output or config.data.input_dir / short_term(graph_uri)

    term_mapping = build_term_mapping(
        term_namespaces=config.graph.term_namespaces,
        default_namespace=config.graph.namespace,
    )

    client = create_sparql_client(config)

    download_graph(
        client, graph_uri, output, term_mapping, config.db_config.chunk_size
    )
