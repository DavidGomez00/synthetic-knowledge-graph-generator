"""Moves a named graph between a local triples file and the graph database.

`upload` overwrites a graph (by default `graph.base_uri`) with an .nt/.tsv file
(by default `graph.triple_file`); `download` writes a graph to an .nt and a .tsv
file, through the store's native export endpoint (see `queries.export_graph_nt`).
"""

import argparse
import logging
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.cli.common import add_config_args, load_config
from skgg.core.queries import export_graph_nt, get_triple_count, initialize_graph
from skgg.core.triples import convert
from skgg.core.utils import config_term_mapping, create_sparql_client, short_term

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
    logger.info("Inserted %s into <%s> with %d triples.", triple_file, graph_uri, count)
    return count


def download_graph(
    client: SPARQLWrapper,
    graph_uri: str,
    output: str | Path,
    term_mapping: dict[str, str],
    page_size: int = 5000,
) -> int:
    """Writes `graph_uri` to `<output>.nt` and `<output>.tsv`, ignoring any
    .nt/.tsv suffix `output` already has. Parent folders are created. The .tsv
    is converted from the .nt by `triples.convert`, so IRIs are shortened only
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
        description="Upload a graph from an .nt/.tsv file into the graph database, "
        "or download one into an .nt and a .tsv file."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    upload = commands.add_parser(
        "upload",
        help="Overwrite graph.base_uri (or --graph-uri) with an .nt/.tsv file.",
    )
    add_config_args(upload)
    upload.add_argument(
        "--triple-file",
        default=None,
        help="Override the config file's graph.triple_file. A bare filename is "
        "resolved under data.input_dir; a path containing '/' is used as given.",
    )
    upload.add_argument(
        "--graph-uri",
        default=None,
        help="Override the target graph URI (defaults to graph.base_uri).",
    )

    download = commands.add_parser(
        "download",
        help="Write graph.base_uri (or --graph-uri) to an .nt and a .tsv file.",
    )
    add_config_args(download)
    download.add_argument(
        "--graph-uri",
        default=None,
        help="Graph URI to download (defaults to graph.base_uri).",
    )
    download.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path without suffix; writes <output>.nt and <output>.tsv "
        "(defaults to data.input_dir / the graph URI's last segment, e.g. "
        "data/fr/skgg for http://FrenchRoyalty.org/skgg).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    config = load_config(args)
    term_mapping = config_term_mapping(config)
    client = create_sparql_client(config)
    graph_uri = args.graph_uri or config.graph.base_uri

    if args.command == "upload":
        triple_file = args.triple_file or config.graph.triple_file
        source = (
            Path(triple_file)
            if "/" in triple_file
            else config.data.input_dir / triple_file
        )
        upload_graph(
            client, source, graph_uri, term_mapping, config.db_config.chunk_size
        )
    else:
        output = args.output or config.data.input_dir / short_term(graph_uri)
        download_graph(
            client, graph_uri, output, term_mapping, config.db_config.chunk_size
        )
