#!/usr/bin/env python3
"""
Convert a triples file from N-Triples (.nt) to tab-separated (.tsv, one
`subject<TAB>predicate<TAB>object` row of bare terms per triple) or back, in
the direction given by the input file's suffix.

This is a standalone version of `skgg.cli.convert`: it only uses the Python
standard library and imports nothing from `skgg`, so it can be copied and run
outside the project. Unlike `skgg.cli.convert`, it reads no config file and
always requires a default namespace (`--namespace`).

Duplicate triples are written once, and lines that aren't a triple are skipped
with a warning. In the .tsv, a literal becomes its text, without quotes,
language tag or datatype, with tabs and line breaks turned into spaces.

Every bare term is put under `--namespace`, except `type`, which maps to
`rdf:type`. In an .nt input, an IRI is shortened to a bare term only when that
term maps back to the same IRI, and other IRIs are written in full.

Examples:

    # -> path/to/graph.nt
    python scripts/format_change.py path/to/graph.tsv --namespace http://example.org/

    # -> path/to/graph.tsv (or -o path/to/out.tsv)
    python scripts/format_change.py path/to/graph.nt --namespace http://example.org/
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger("format_change")

Triple = tuple[str, str, str]

# Namespaces of bare terms that aren't in the default namespace.
DEFAULT_PREFIXES: dict[str, str] = {
    "type": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}

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
# '#' (so the term stays the IRI's last segment, see `short_term`).
IRI_ESCAPES = str.maketrans(
    {c: f"%{ord(c):02X}" for c in [*map(chr, range(0x21)), *'<>"{}|^`\\%/#']}
)


# ---------------------------------------------------------------------------
# Term mapping
# ---------------------------------------------------------------------------
def build_term_mapping(namespace: str) -> dict[str, str]:
    """Builds the bare-term -> namespace mapping: `DEFAULT_PREFIXES`, with
    `namespace` as the "default" fallback for every other term."""
    return {**DEFAULT_PREFIXES, "default": namespace}


def short_term(term: str) -> str:
    """Shortens an IRI (bare or in brackets) to its last path/fragment segment,
    e.g. `<http://FrenchRoyalty.org/child>` -> `child`. Other terms are returned
    unchanged."""
    bare = term[1:-1] if term.startswith("<") and term.endswith(">") else term
    if not bare.startswith("http"):
        return term
    return bare.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def format_term(term: str, term_mapping: dict[str, str]) -> str:
    """A term as a bracketed IRI, under its namespace when it is a bare term."""
    if term.startswith("<") and term.endswith(">"):
        return term
    if term.startswith("http"):
        return f"<{term}>"
    return f"<{term_mapping.get(term, term_mapping['default'])}{term}>"


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def read_nt(path: Path) -> Iterator[tuple[int, Triple | None]]:
    """Yields `(line number, (subject, predicate, object))` for each triple of an
    .nt file, with the terms as written, or `(line number, None)` for a line that
    isn't a triple. Blank and comment lines are skipped."""
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = NT_TRIPLE.match(line)
            if match is None:
                yield number, None
                continue
            subject, predicate, obj = match.groups()
            yield number, (subject, predicate, obj)


def read_tsv(path: Path) -> Iterator[tuple[int, Triple | None]]:
    """Yields `(line number, (subject, predicate, object))` for each row of a .tsv
    file, with its fields stripped, or `(line number, None)` for a line without
    three non-empty tab-separated fields. Blank and comment lines are skipped."""
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = [field.strip() for field in line.rstrip("\r\n").split("\t")]
            if len(fields) != 3 or not all(fields):
                yield number, None
                continue
            subject, predicate, obj = fields
            yield number, (subject, predicate, obj)


# ---------------------------------------------------------------------------
# Term conversion
# ---------------------------------------------------------------------------
def tsv_term_to_nt(term: str, term_mapping: dict[str, str]) -> str:
    """A .tsv term as an .nt term. Blank nodes and full IRIs are kept; a bare
    term is percent-encoded (see `IRI_ESCAPES`) under its namespace."""
    if term.startswith("_:"):
        return term
    if not term.startswith("http"):
        term = term.translate(IRI_ESCAPES)
    return format_term(term, term_mapping)


def nt_term_to_tsv(term: str, term_mapping: dict[str, str]) -> str:
    """An .nt term as a .tsv term.

    A blank node is kept, and a literal becomes its unescaped text, without
    language tag or datatype. An IRI becomes the bare term that `tsv_term_to_nt`
    maps back to it, or else is kept in full.
    """
    if term.startswith("_:"):
        return term
    if term.startswith('"'):
        text = term[1 : term.rindex('"')]  # without quotes, language tag, datatype
        return LITERAL_ESCAPE.sub(lambda m: LITERAL_UNESCAPED.get(m[1], m[0]), text)

    short = unquote(short_term(term))
    return short if tsv_term_to_nt(short, term_mapping) == term else term[1:-1]


def _nt_rows(path: Path, term_mapping: dict[str, str]) -> Iterator[str | None]:
    """Yields each triple of an .nt file as a .tsv row, or None (with a warning)
    for a line that isn't a triple."""
    for number, triple in read_nt(path):
        if triple is None:
            logger.warning("Skipping %s:%d: not an N-Triples triple.", path, number)
            yield None
            continue
        yield "\t".join(nt_term_to_tsv(term, term_mapping) for term in triple)


def _tsv_rows(path: Path, term_mapping: dict[str, str]) -> Iterator[str | None]:
    """Yields each triple of a .tsv file as an .nt line, or None (with a warning)
    for a line without three non-empty fields."""
    for number, triple in read_tsv(path):
        if triple is None:
            logger.warning(
                "Skipping %s:%d: expected 3 non-empty tab-separated fields.",
                path,
                number,
            )
            yield None
            continue
        yield " ".join(tsv_term_to_nt(term, term_mapping) for term in triple) + " ."


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def convert(
    input_file: str | Path,
    output_file: str | Path,
    namespace: str,
) -> tuple[int, int, int]:
    """Writes the triples of an .nt file to a .tsv file, or of a .tsv file to
    an .nt file, depending on `input_file`'s suffix. Parent folders of
    `output_file` are created. Bare terms are put under `namespace` (see
    `build_term_mapping`).

    Returns:
        `(written, duplicates, invalid)`: the triples written, the duplicate
        triples dropped, and the lines skipped for not being a triple.

    Raises:
        ValueError: If `input_file` isn't a .nt/.tsv file, `output_file` is
            `input_file`.
    """
    input_file, output_file = Path(input_file), Path(output_file)
    suffix = input_file.suffix.lower()
    if suffix not in (".nt", ".tsv"):
        raise ValueError(
            f"Invalid input file '{input_file}'. Expected a .nt/.tsv file."
        )
    if output_file.resolve() == input_file.resolve():
        raise ValueError("The output file must differ from the input file.")

    term_mapping = build_term_mapping(namespace)
    if suffix == ".nt":
        rows = _nt_rows(input_file, term_mapping)
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
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
        "--namespace",
        required=True,
        help="Default namespace of bare terms, e.g. http://example.org/. .tsv "
        "terms become IRIs under it, and .nt IRIs under it are shortened.",
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
    return args


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s | %(name)-8s | %(levelname)-6s | %(message)s",
        datefmt="%H:%M:%S",
    )

    input_file: Path = args.input_file
    output_suffix = ".tsv" if input_file.suffix.lower() == ".nt" else ".nt"
    convert(
        input_file,
        args.output or input_file.with_suffix(output_suffix),
        args.namespace,
    )


if __name__ == "__main__":
    main()
