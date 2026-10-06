"""Checks a graph against the SHACL-SPARQL shapes of a `<dataset>.shapes.ttl`
file and reports every triple that violates one.

The source is a graph URI (by default the config's `graph.base_uri`) or a .tsv
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

from skgg.core.config import RunConfig
from skgg.core.queries import (
    build_shape_query,
    clear_graph,
    execute_select_query,
    get_triple_count,
    insert_graph,
)
from skgg.core.shapes import load_shapes, triples_in_row
from skgg.core.utils import (
    build_term_mapping,
    create_sparql_client,
    format_term,
    resolve_config_path,
    setup_logging,
    short_term,
)

logger = logging.getLogger(__name__)

REPORT_HEADER = ("line", "subject", "predicate", "object", "shape", "message")

Triple = tuple[str, str, str]


def _index_tsv(
    tsv_file: Path, term_mapping: dict[str, str]
) -> dict[Triple, tuple[int, Triple]]:
    """Maps each triple of a .tsv file, as N-Triples terms, to its first line
    number and its bare terms. Lines are read as `queries.insert_graph` reads
    them, so the keys match the uploaded triples."""
    index: dict[Triple, tuple[int, Triple]] = {}
    with tsv_file.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            subject, predicate, obj = stripped.split("\t")
            key = (
                format_term(subject, term_mapping),
                format_term(predicate, term_mapping),
                format_term(obj, term_mapping),
            )
            index.setdefault(key, (number, (subject, predicate, obj)))
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
        insert_graph(client, graph_uri, chunk_size, tsv_file, term_mapping)
        total = len(index)
    else:
        graph_uri = str(source).strip("<>")
        index = {}
        total = get_triple_count(client, graph_uri)

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
        if load_file and not keep_graph:
            clear_graph(client, graph_uri)

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
        source if load_file else f"<{graph_uri}>",
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
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. fr), or a path to one; the "
        ".json extension is optional. Gives the database connection, the term mapping and the "
        "default source.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Graph to check: a graph URI, queried in place, or a .tsv file, "
        "loaded into --graph-uri (defaults to the config's graph.base_uri).",
    )
    parser.add_argument(
        "--shapes",
        type=Path,
        default=None,
        help="Shapes file (defaults to data.input_dir / <folder name>.shapes.ttl, "
        "e.g. data/fr/fr.shapes.ttl).",
    )
    parser.add_argument(
        "--graph-uri",
        default=None,
        help="Named graph a .tsv source is loaded into; its contents are replaced "
        "and then cleared (defaults to graph.namespace + 'validation').",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Report file (defaults to a .tsv source's path with suffix "
        ".violations.tsv, or for a graph URI to data.input_dir / <last segment of "
        "the URI>.violations.tsv).",
    )
    parser.add_argument(
        "--keep-graph",
        action="store_true",
        help="Leave the graph a .tsv source is loaded into in the store after "
        "the check.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
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

    config = RunConfig.from_json(resolve_config_path(args.config_file))
    setup_logging(
        level=args.log_level if args.log_level is not None else config.logging.level
    )

    input_dir = config.data.input_dir
    shapes_file: Path = args.shapes or input_dir / f"{input_dir.name}.shapes.ttl"
    if not shapes_file.is_file():
        raise SystemExit(f"Shapes file {shapes_file} does not exist.")

    graph = config.graph
    source: str = args.source or graph.base_uri
    graph_uri: str | None = None
    if is_tsv_source(source):
        graph_uri = args.graph_uri or graph.namespace.rstrip("/#") + "/validation"
        if graph_uri in (graph.base_uri, graph.edb_uri, graph.synthetic_uri):
            raise SystemExit(
                f"--graph-uri <{graph_uri}> is one of the config's graphs; it would "
                "be overwritten and cleared."
            )
        default_output = Path(source).with_suffix(".violations.tsv")
    else:
        if args.graph_uri is not None or args.keep_graph:
            logger.warning(
                "--graph-uri and --keep-graph only apply to a .tsv source; "
                "<%s> is queried in place.",
                source.strip("<>"),
            )
        default_output = input_dir / f"{short_term(source.strip('<>'))}.violations.tsv"

    validate(
        client=create_sparql_client(config),
        source=source,
        shapes_file=shapes_file,
        term_mapping=build_term_mapping(graph.term_namespaces, graph.namespace),
        graph_uri=graph_uri,
        chunk_size=config.db_config.chunk_size,
        output=args.output or default_output,
        keep_graph=args.keep_graph,
    )
