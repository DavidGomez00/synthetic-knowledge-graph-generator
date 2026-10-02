"""Converts a triples file from N-Triples (.nt) to tab-separated (.tsv, one
`subject<TAB>predicate<TAB>object` row of bare terms per triple) or back, in
the direction given by the input file's suffix. Duplicate triples are written
once, and lines that aren't a triple are skipped with a warning."""

import argparse
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from skgg.core.utils import format_term, load_term_mapping, setup_logging, short_term

logger = logging.getLogger(__name__)

# An N-Triples subject/object: an IRI, a blank node, or a literal with an
# optional language tag or datatype.
_NT_TERM = r'(<[^>]*>|_:\S+|"(?:[^"\\]|\\.)*"(?:@[A-Za-z0-9-]+|\^\^<[^>]*>)?)'
NT_TRIPLE = re.compile(rf"^\s*{_NT_TERM}\s+(<[^>]*>)\s+{_NT_TERM}\s*\.\s*$")

# Escape sequences undone when a literal is written to a .tsv cell. Tabs and
# line breaks become spaces, to keep each triple on one row.
LITERAL_ESCAPE = re.compile(r"\\(.)")
LITERAL_UNESCAPED = {"t": " ", "n": " ", "r": " ", '"': '"', "\\": "\\"}

# Characters percent-encoded when a bare .tsv term becomes an IRI: those
# N-Triples forbids in IRIs, '%' (so decoding gives the term back), and '/' and
# '#' (so the term stays the IRI's last segment, see `utils.short_term`).
IRI_ESCAPES = str.maketrans(
    {c: f"%{ord(c):02X}" for c in [*map(chr, range(0x21)), *'<>"{}|^`\\%/#']}
)


def _to_nt_term(term: str, term_mapping: dict[str, str]) -> str:
    """A .tsv term as an .nt term. Blank nodes and full IRIs are kept; a bare
    term is percent-encoded (see `IRI_ESCAPES`) under its namespace."""
    if term.startswith("_:"):
        return term
    if not term.startswith("http"):
        term = term.translate(IRI_ESCAPES)
    return format_term(term, term_mapping)


def _to_tsv_term(
    term: str, term_mapping: dict[str, str] | None, short_terms: dict[str, str]
) -> str:
    """An .nt term as a .tsv term.

    A blank node is kept, and a literal becomes its unescaped text, without
    language tag or datatype. With a `term_mapping`, an IRI becomes the bare
    term that `_to_nt_term` maps back to it, or else is kept in full. Without
    one, it becomes its decoded last segment (`utils.short_term`), and
    `short_terms` (short term -> IRI) catches two IRIs that would share one.
    """
    if term.startswith("_:"):
        return term
    if term.startswith('"'):
        text = term[1 : term.rindex('"')]  # without quotes, language tag, datatype
        return LITERAL_ESCAPE.sub(lambda m: LITERAL_UNESCAPED.get(m[1], m[0]), text)

    short = unquote(short_term(term))
    if term_mapping is not None:
        return short if _to_nt_term(short, term_mapping) == term else term[1:-1]
    if short_terms.setdefault(short, term) != term:
        raise ValueError(
            f"{term} and {short_terms[short]} both shorten to '{short}'; pass "
            "-f/--config-file or --namespace to shorten only that namespace's IRIs."
        )
    return short


def _nt_rows(path: Path, term_mapping: dict[str, str] | None) -> Iterator[str | None]:
    """Yields each triple of an .nt file as a .tsv row, or None for a line that
    isn't a triple. Blank and comment lines are skipped."""
    short_terms: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = NT_TRIPLE.match(line)
            if match is None:
                logger.warning("Skipping %s:%d: not an N-Triples triple.", path, number)
                yield None
                continue
            yield "\t".join(
                _to_tsv_term(term, term_mapping, short_terms) for term in match.groups()
            )


def _tsv_rows(path: Path, term_mapping: dict[str, str]) -> Iterator[str | None]:
    """Yields each triple of a .tsv file as an .nt line, or None for a line
    without three non-empty fields. Blank lines are skipped."""
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            fields = [field.strip() for field in line.rstrip("\r\n").split("\t")]
            if len(fields) != 3 or not all(fields):
                logger.warning(
                    "Skipping %s:%d: expected 3 non-empty tab-separated fields.",
                    path,
                    number,
                )
                yield None
                continue
            yield " ".join(_to_nt_term(term, term_mapping) for term in fields) + " ."


def convert(
    input_file: str | Path,
    output_file: str | Path,
    term_mapping: dict[str, str] | None = None,
) -> tuple[int, int, int]:
    """Writes the triples of an .nt file to a .tsv file, or of a .tsv file to
    an .nt file, depending on `input_file`'s suffix. Parent folders of
    `output_file` are created.

    Args:
        input_file: The .nt/.tsv file to convert.
        output_file: The file to write, in the other format.
        term_mapping: Bare-term -> namespace mapping (see `utils.format_term`).
            Required for a .tsv input, to expand its terms into IRIs. For an
            .nt input, only IRIs that a bare term maps to are shortened, and
            the others are written in full; without a mapping, every IRI is cut
            to its last segment.

    Returns:
        `(written, duplicates, invalid)`: the triples written, the duplicate
        triples dropped, and the lines skipped for not being a triple.

    Raises:
        ValueError: If `input_file` isn't a .nt/.tsv file, `output_file` is
            `input_file`, a .tsv input comes without a `term_mapping`, or two
            IRIs of an .nt input without a `term_mapping` share a last segment.
    """
    input_file, output_file = Path(input_file), Path(output_file)
    suffix = input_file.suffix.lower()
    if suffix not in (".nt", ".tsv"):
        raise ValueError(
            f"Invalid input file '{input_file}'. Expected a .nt/.tsv file."
        )
    if output_file.resolve() == input_file.resolve():
        raise ValueError("The output file must differ from the input file.")

    if suffix == ".nt":
        rows = _nt_rows(input_file, term_mapping)
    elif term_mapping is None:
        raise ValueError("A term_mapping is required to convert a .tsv file to .nt.")
    else:
        rows = _tsv_rows(input_file, term_mapping)

    written = duplicates = invalid = 0
    seen: set[str] = set()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as out:
        for row in rows:
            if row is None:
                invalid += 1
            elif row in seen:
                duplicates += 1
            else:
                seen.add(row)
                out.write(row + "\n")
                written += 1

    logger.info(
        "Wrote %d triples to %s, skipping %d duplicate triples and %d invalid lines.",
        written,
        output_file,
        duplicates,
        invalid,
    )
    return written, duplicates, invalid


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a triples file from .nt to .tsv, or from .tsv to .nt, "
        "depending on its suffix."
    )
    parser.add_argument("input_file", type=Path, help="Path to the .nt/.tsv file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file (defaults to the input path with its suffix swapped).",
    )
    parser.add_argument(
        "-f",
        "--config-file",
        default=None,
        help="Config file under configurations/ (or a path to one) whose "
        "graph.namespace/graph.term_namespaces map .tsv terms to IRIs. Required "
        "(or --namespace) for .tsv input. For .nt input, only IRIs in these "
        "namespaces are shortened; without it or --namespace, every IRI is.",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Default namespace for .tsv terms (overrides the config file's "
        "graph.namespace).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (e.g. DEBUG, INFO, WARNING).",
    )
    args = parser.parse_args()
    suffix = args.input_file.suffix.lower()
    if suffix not in (".nt", ".tsv"):
        parser.error("the input file must be a .nt or .tsv file.")
    if suffix == ".tsv" and args.config_file is None and args.namespace is None:
        parser.error("a .tsv input needs -f/--config-file or --namespace.")
    return args


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level=args.log_level)

    input_file: Path = args.input_file
    output_suffix = ".tsv" if input_file.suffix.lower() == ".nt" else ".nt"
    convert(
        input_file,
        args.output or input_file.with_suffix(output_suffix),
        load_term_mapping(args.config_file, args.namespace),
    )
