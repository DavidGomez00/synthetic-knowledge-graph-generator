"""Writes a cleaned copy of an .nt/.tsv file, in both .tsv and .nt formats:
duplicate triples are dropped (keeping the first occurrence), and so are its
"literals": triples whose object is never typed (never the subject of a type
triple), plus every triple of a predicate whose objects are always literals
(e.g. `name`). Type triples themselves are always kept. Also reports every term
used as a subject that is never typed."""

import argparse
import json
import logging
from collections.abc import Collection, Iterator
from pathlib import Path
from urllib.parse import unquote

from skgg.config import GraphConfig
from skgg.utils import (
    build_term_mapping,
    format_term,
    resolve_config_path,
    setup_logging,
    short_term,
)

logger = logging.getLogger(__name__)

TSV_TYPE = "type"
NT_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"

# Predicates whose objects are always literals, even when an object happens to
# equal a typed term (e.g. a person whose name is their entity ID).
LITERAL_PREDICATES = frozenset({"name"})


def _iter_triples(path: Path) -> Iterator[tuple[str, str, str]]:
    """Yields `(subject, predicate, object)` for every triple in an .nt/.tsv
    file, skipping blank and comment lines."""
    suffix = path.suffix.lower()
    if suffix not in (".nt", ".tsv"):
        raise ValueError(f"Invalid input file '{path}'. Expected a .nt/.tsv file.")

    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if suffix == ".tsv":
                subject, predicate, obj = stripped.split("\t")
            else:
                # The object may be a literal containing spaces.
                subject, predicate, rest = stripped.split(" ", 2)
                obj = rest.removesuffix(".").rstrip()
            yield subject, predicate, obj


def _output_paths(output: Path) -> tuple[Path, Path]:
    """Returns the `(.tsv, .nt)` output paths for `output`, ignoring any
    .tsv/.nt suffix it already has."""
    if output.suffix.lower() in (".tsv", ".nt"):
        output = output.with_suffix("")
    return output.with_name(f"{output.name}.tsv"), output.with_name(
        f"{output.name}.nt"
    )


def prepare_data(
    input_file: str | Path,
    output: str | Path,
    term_mapping: dict[str, str] | None = None,
    literal_predicates: Collection[str] = LITERAL_PREDICATES,
) -> tuple[int, int, int, int, set[str]]:
    """Writes `input_file` to `<output>.tsv` and `<output>.nt`, dropping duplicate
    triples, every triple of a `literal_predicates` predicate, and every
    non-type triple whose object is never typed.

    Args:
        input_file: The .nt/.tsv file to clean.
        output: Output path without extension (a .tsv/.nt suffix is ignored).
        term_mapping: Bare-term -> namespace mapping (see `utils.format_term`).
            Required when `input_file` is a .tsv file, to expand its terms into
            the .nt output ('/' in a bare term is written as '%2F'). Ignored
            for .nt files, whose IRIs become their last segment in the .tsv
            output (`utils.short_term`).
        literal_predicates: Predicates (bare terms, matched against each
            predicate's `utils.short_term`) whose triples are always dropped.

    Returns:
        `(kept, duplicates, literals, literal_predicate_triples,
        untyped_subjects)`: the triples written; the duplicate, untyped-object
        and literal-predicate triples dropped; and the terms used as subjects
        that are never typed.

    Raises:
        ValueError: If an output path is the input file, a .tsv input comes
            without a `term_mapping`, or two distinct .nt IRIs share a short
            term, which the .tsv output would merge.
    """
    input_file = Path(input_file)
    tsv_file, nt_file = _output_paths(Path(output))
    if input_file.resolve() in (tsv_file.resolve(), nt_file.resolve()):
        raise ValueError("The output files must differ from the input file.")

    is_tsv = input_file.suffix.lower() == ".tsv"
    if is_tsv and term_mapping is None:
        raise ValueError("A term_mapping is required to convert a .tsv file to .nt.")

    type_predicates = {TSV_TYPE, NT_TYPE}

    typed: set[str] = set()
    subjects: set[str] = set()
    for subject, predicate, _ in _iter_triples(input_file):
        subjects.add(subject)
        if predicate in type_predicates:
            typed.add(subject)

    kept = duplicates = removed = literal_predicate_triples = 0
    seen: set[tuple[str, str, str]] = set()
    literals: set[str] = set()
    # .nt -> .tsv: short term -> the IRI it came from, to catch collisions.
    short_terms: dict[str, str] = {}

    def _expand(term: str) -> str:
        """A .tsv term as an .nt term."""
        if not term.startswith("http"):
            term = term.replace("/", "%2F")
        return format_term(term, term_mapping)

    def _shorten(term: str) -> str:
        """An .nt term as a .tsv term."""
        short = unquote(short_term(term))
        if short_terms.setdefault(short, term) != term:
            raise ValueError(
                f"{term} and {short_terms[short]} both shorten to '{short}'."
            )
        return short

    with (
        tsv_file.open("w", encoding="utf-8") as tsv_out,
        nt_file.open("w", encoding="utf-8") as nt_out,
    ):
        for triple in _iter_triples(input_file):
            if triple in seen:
                duplicates += 1
                continue
            seen.add(triple)

            _, predicate, obj = triple
            if short_term(predicate) in literal_predicates:
                literal_predicate_triples += 1
                continue
            if predicate not in type_predicates and obj not in typed:
                literals.add(obj)
                removed += 1
                continue

            tsv_triple = triple if is_tsv else tuple(map(_shorten, triple))
            nt_triple = tuple(map(_expand, triple)) if is_tsv else triple
            tsv_out.write("\t".join(tsv_triple) + "\n")
            nt_out.write(" ".join(nt_triple) + " .\n")
            kept += 1

    logger.info(
        "Wrote %s and %s: kept %d triples, removed %d duplicate triples, %d "
        "triples of literal predicates (%s) and %d untyped-object triples (%d "
        "distinct literals).",
        tsv_file,
        nt_file,
        kept,
        duplicates,
        literal_predicate_triples,
        ", ".join(sorted(literal_predicates)) or "none",
        removed,
        len(literals),
    )

    untyped_subjects = subjects - typed
    if untyped_subjects:
        sample = sorted(untyped_subjects)
        logger.warning(
            "%d subject terms are never typed, e.g.: %s",
            len(sample),
            ", ".join(sample[:10]),
        )
        logger.debug("Untyped subjects:\n%s", "\n".join(sample))
    else:
        logger.info("Every subject term is typed.")

    return kept, duplicates, removed, literal_predicate_triples, untyped_subjects


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a .nt/.tsv file as both .tsv and .nt, without duplicate "
        "triples or literals (objects that are never typed), and report subjects "
        "that are never typed."
    )
    parser.add_argument("input_file", type=Path, help="Path to the .nt/.tsv file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path without extension; writes <output>.tsv and <output>.nt "
        "(defaults to <stem>.no-literals next to the input).",
    )
    parser.add_argument(
        "-f",
        "--config-file",
        default=None,
        help="Config file under configurations/ (or a path to one) whose "
        "graph.namespace/graph.term_namespaces map .tsv terms to IRIs. Required "
        "(or --namespace) for .tsv input; ignored for .nt input.",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Default namespace for .tsv terms (overrides the config file's "
        "graph.namespace).",
    )
    parser.add_argument(
        "--literal-predicates",
        nargs="*",
        default=sorted(LITERAL_PREDICATES),
        help="Predicates whose triples are always dropped, as their objects are "
        "always literals (default: %(default)s). Pass with no values to disable.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (e.g. DEBUG, INFO, WARNING).",
    )
    args = parser.parse_args()
    if (
        args.input_file.suffix.lower() == ".tsv"
        and args.config_file is None
        and args.namespace is None
    ):
        parser.error("a .tsv input needs -f/--config-file or --namespace.")
    return args


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level=args.log_level)

    term_namespaces: dict[str, str] = {}
    namespace: str | None = args.namespace
    if args.config_file is not None:
        # Only the graph section is needed, so skip RunConfig's other sections
        # (e.g. data.input_dir, which must exist).
        with resolve_config_path(args.config_file).open(encoding="utf-8") as f:
            graph = GraphConfig(**json.load(f)["graph"])
        term_namespaces = graph.term_namespaces
        namespace = namespace or graph.namespace

    input_file: Path = args.input_file
    prepare_data(
        input_file,
        args.output or input_file.with_name(f"{input_file.stem}.no-literals"),
        term_mapping=(
            build_term_mapping(term_namespaces, namespace)
            if namespace is not None
            else None
        ),
        literal_predicates=set(args.literal_predicates),
    )
