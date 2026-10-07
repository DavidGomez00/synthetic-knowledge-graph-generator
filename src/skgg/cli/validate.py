"""Checks a graph against the SHACL-SPARQL shapes of a `<dataset>.shapes.ttl`
file and reports every triple that violates one.

The source is a graph URI (by default the config's base graph) or a .tsv
triples file. A graph URI is queried as it is. A .tsv file is loaded into a
temporary named graph, which is cleared again after the check. Each shape's
`sh:select` runs against the graph (`core/queries.build_shape_query`), and every
triple a result row names (`?this ?path ?value`, `?this2 ?path2 ?value2`, ...)
is written to a report, with its line number when the source is a .tsv file.
Neither the source graph nor the source file is modified."""

import argparse
import logging
import math
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.cli.common import add_config_args, load_config
from skgg.core.queries import (
    build_shape_query,
    clear_graph,
    execute_select_query,
    get_triple_count,
    insert_graph,
)
from skgg.core.shapes import load_shapes, triples_in_row
from skgg.core.triples import Triple, read_tsv, tsv_term_to_nt
from skgg.core.utils import (
    config_term_mapping,
    create_sparql_client,
    graph_file_stem,
    short_term,
)

logger = logging.getLogger(__name__)

REPORT_HEADER = ("line", "subject", "predicate", "object", "shape", "message")


def _index_tsv(
    tsv_file: Path, term_mapping: dict[str, str]
) -> dict[Triple, tuple[int, Triple]]:
    """Maps each triple of a .tsv file, as N-Triples terms, to its first line
    number and its bare terms. Terms are turned into IRIs as `queries.insert_graph`
    does it (`triples.tsv_term_to_nt`), so the keys match the uploaded triples."""
    index: dict[Triple, tuple[int, Triple]] = {}
    for number, triple in read_tsv(tsv_file):
        if triple is None:
            raise ValueError(
                f"{tsv_file}:{number}: expected 3 non-empty tab-separated fields."
            )
        subject, predicate, obj = triple
        key = (
            tsv_term_to_nt(subject, term_mapping),
            tsv_term_to_nt(predicate, term_mapping),
            tsv_term_to_nt(obj, term_mapping),
        )
        index.setdefault(key, (number, triple))
    return index


def _nt_term(binding: dict[str, str] | None) -> str:
    """A SPARQL JSON result term as written in N-Triples (empty if unbound)."""
    if binding is None:
        return ""
    if binding["type"] == "uri":
        return f"<{binding['value']}>"
    if binding["type"] == "bnode":
        return f"_:{binding['value']}"
    return f'"{binding["value"]}"'


def _bare_term(binding: dict[str, str] | None) -> str:
    """A SPARQL JSON result term cut to its last segment (empty if unbound)."""
    if binding is None:
        return ""
    if binding["type"] == "uri":
        return short_term(binding["value"])
    return binding["value"]


def is_tsv_source(source: str | Path) -> bool:
    """Whether `source` names a .tsv file rather than a graph URI."""
    return str(source).lower().endswith(".tsv")


def validate(
    client: SPARQLWrapper,
    source: str | Path,
    shapes_file: Path,
    term_mapping: dict[str, str],
    graph_uri: str | None,
    chunk_size: int,
    output: Path,
    keep_graph: bool = False,
) -> int:
    """Runs every SPARQL constraint of `shapes_file` against `source` and
    writes the flagged triples to `output` as a TSV report: one row per triple
    and shape, sorted by line number.

    Args:
        client: SPARQL client for the store.
        source: The graph URI to check, queried in place, or a .tsv file,
            loaded into `graph_uri` first.
        shapes_file: The Turtle file with the SHACL-SPARQL shapes.
        term_mapping: Bare-term -> namespace mapping (see `utils.format_term`).
        graph_uri: Named graph a .tsv source is loaded into. Its contents are
            replaced, and cleared after the check unless `keep_graph`. Unused
            for a graph URI source.
        chunk_size: Triples per insert batch.
        output: The report file to write.
        keep_graph: Leave the loaded graph in the store, to inspect it.

    Returns:
        The number of distinct triples that violate at least one shape.

    Raises:
        ValueError: If `source` is a .tsv file and no `graph_uri` is given.
    """
    constraints = load_shapes(shapes_file)
    load_file = is_tsv_source(source)
    if load_file:
        if graph_uri is None:
            raise ValueError("A graph_uri is required to check a .tsv file.")
        tsv_file = Path(source)
        index = _index_tsv(tsv_file, term_mapping)
        total = len(index)
        logger.info(
            "Reading triples from file %s: loading its %d distinct triples into "
            "temporary graph <%s> at %s.",
            tsv_file,
            total,
            graph_uri,
            client.endpoint,
        )
        insert_graph(client, graph_uri, chunk_size, tsv_file, term_mapping)
    else:
        graph_uri = str(source).strip("<>")
        index = {}
        total = get_triple_count(client, graph_uri)
        logger.info(
            "Reading triples from graph <%s> at %s (%d triples), queried in place.",
            graph_uri,
            client.endpoint,
            total,
        )

    # (line, subject, predicate, object, shape, message) rows, without repeats.
    rows: set[tuple[int | None, str, str, str, str, str]] = set()
    flagged: set[Triple] = set()

    try:
        for constraint in constraints:
            shape = short_term(constraint.shape)
            results = execute_select_query(
                client, build_shape_query(constraint, graph_uri)
            )
            shape_triples: set[Triple] = set()
            for result in results:
                for this, path, value in triples_in_row(result):
                    key = (_nt_term(this), _nt_term(path), _nt_term(value))
                    if path is None or value is None:
                        logger.warning(
                            "%s: a result names focus node %s without ?path/?value.",
                            shape,
                            key[0],
                        )
                    line, terms = index.get(
                        key,
                        (None, (_bare_term(this), _bare_term(path), _bare_term(value))),
                    )
                    rows.add((line, *terms, shape, constraint.message))
                    shape_triples.add(key)
            flagged |= shape_triples
            logger.info(
                "%s: %d results, %d triples flagged (%s).",
                shape,
                len(results),
                len(shape_triples),
                constraint.message,
            )
    finally:
        if load_file:
            if keep_graph:
                logger.info("Keeping temporary graph <%s> (--keep-graph).", graph_uri)
            else:
                clear_graph(client, graph_uri)
                logger.info("Cleared temporary graph <%s>.", graph_uri)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as out:
        out.write("\t".join(REPORT_HEADER) + "\n")
        for line, *fields in sorted(
            rows, key=lambda row: (math.inf if row[0] is None else row[0], *row[1:])
        ):
            out.write("\t".join(["" if line is None else str(line), *fields]) + "\n")

    logger.info(
        "%d of %d triples in %s violate a shape of %s. Report: %s",
        len(flagged),
        total,
        (
            f"file {source} (loaded into <{graph_uri}>)"
            if load_file
            else f"graph <{graph_uri}>"
        ),
        shapes_file,
        output,
    )
    return len(flagged)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a graph URI or a .tsv file against the SHACL-SPARQL "
        "shapes of a <dataset>.shapes.ttl file and report every triple that "
        "violates one."
    )
    add_config_args(
        parser,
        config_help="Gives the database connection, the term mapping and the "
        "default source.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Graph to check: a graph URI, queried in place, or a .tsv file, "
        "loaded into --graph-uri (defaults to the config's base graph, e.g. "
        "http://FrenchRoyalty.org/base).",
    )
    parser.add_argument(
        "--shapes",
        type=Path,
        default=None,
        help="Shapes file (defaults to data.input_dir / <stem of graph.triple_file>"
        ".shapes.ttl, e.g. data/french_royalty/french_royalty.shapes.ttl).",
    )
    parser.add_argument(
        "--graph-uri",
        default=None,
        help="Named graph a .tsv source is loaded into; its contents are replaced "
        "and then cleared (defaults to <namespace>/validation, e.g. "
        "http://FrenchRoyalty.org/validation). The base graph and the pipeline's "
        "graphs (<namespace>/skgg/...) are refused.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Report file (defaults to a .tsv source's path with suffix "
        ".violations.tsv, or for a graph URI to data.input_dir / the triple file's "
        "stem and the URI's path under the namespace, e.g. "
        "data/french_royalty/french_royalty.skgg.pca=0.9.violations.tsv).",
    )
    parser.add_argument(
        "--keep-graph",
        action="store_true",
        help="Leave the graph a .tsv source is loaded into in the store after "
        "the check.",
    )
    args = parser.parse_args()
    if args.source is not None:
        if is_tsv_source(args.source):
            if not Path(args.source).is_file():
                parser.error(f"source file {args.source} does not exist.")
        elif not args.source.strip("<>").startswith(("http:", "https:")):
            parser.error("--source must be a graph URI or a .tsv file.")
    return args


if __name__ == "__main__":
    args = _parse_args()

    config = load_config(args)

    input_dir = config.data.input_dir
    shapes_file: Path = (
        args.shapes or input_dir / f"{Path(config.graph.triple_file).stem}.shapes.ttl"
    )
    if not shapes_file.is_file():
        raise SystemExit(f"Shapes file {shapes_file} does not exist.")

    graph = config.graph
    source: str = args.source or graph.base_uri
    graph_uri: str | None = None
    if is_tsv_source(source):
        graph_uri = args.graph_uri or f"{graph.root_uri}/validation"
        bare_uri = graph_uri.strip("<>")
        if bare_uri == graph.base_uri or bare_uri.startswith(f"{graph.skgg_uri}/"):
            raise SystemExit(
                f"--graph-uri <{bare_uri}> is the base graph or a pipeline graph; it "
                "would be overwritten and cleared."
            )
        default_output = Path(source).with_suffix(".violations.tsv")
    else:
        if args.graph_uri is not None or args.keep_graph:
            logger.warning(
                "--graph-uri and --keep-graph only apply to a .tsv source; "
                "<%s> is queried in place.",
                source.strip("<>"),
            )
        default_output = input_dir / f"{graph_file_stem(graph, source)}.violations.tsv"

    validate(
        client=create_sparql_client(config),
        source=source,
        shapes_file=shapes_file,
        term_mapping=config_term_mapping(config),
        graph_uri=graph_uri,
        chunk_size=config.db_config.chunk_size,
        output=args.output or default_output,
        keep_graph=args.keep_graph,
    )
