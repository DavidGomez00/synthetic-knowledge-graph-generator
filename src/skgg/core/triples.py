"""Reads and converts local triples files: N-Triples (.nt) and tab-separated
(.tsv, one `subject<TAB>predicate<TAB>object` row of bare terms per triple).

No graph database is involved. `core/queries.py` (uploads), `cli/clean.py`,
`cli/convert.py`, `cli/graph.py` (downloads) and `cli/validate.py` read and write
triples files through this module, so a bare .tsv term always becomes the same IRI.
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from skgg.core.utils import format_term, short_term

logger = logging.getLogger(__name__)

Triple = tuple[str, str, str]

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


def tsv_term_to_nt(term: str, term_mapping: dict[str, str]) -> str:
    """A .tsv term as an .nt term. Blank nodes and full IRIs are kept; a bare
    term is percent-encoded (see `IRI_ESCAPES`) under its namespace."""
    if term.startswith("_:"):
        return term
    if not term.startswith("http"):
        term = term.translate(IRI_ESCAPES)
    return format_term(term, term_mapping)


def tsv_triple_to_nt(triple: Triple, term_mapping: dict[str, str]) -> str:
    """A .tsv triple as an .nt line (without line break)."""
    return " ".join(tsv_term_to_nt(term, term_mapping) for term in triple) + " ."


def nt_term_to_tsv(
    term: str, term_mapping: dict[str, str] | None, short_terms: dict[str, str]
) -> str:
    """An .nt term as a .tsv term.

    A blank node is kept, and a literal becomes its unescaped text, without
    language tag or datatype. With a `term_mapping`, an IRI becomes the bare
    term that `tsv_term_to_nt` maps back to it, or else is kept in full. Without
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
        return short if tsv_term_to_nt(short, term_mapping) == term else term[1:-1]
    if short_terms.setdefault(short, term) != term:
        raise ValueError(
            f"{term} and {short_terms[short]} both shorten to '{short}'; pass "
            "-f/--config-file or --namespace to shorten only that namespace's IRIs."
        )
    return short


def _nt_rows(path: Path, term_mapping: dict[str, str] | None) -> Iterator[str | None]:
    """Yields each triple of an .nt file as a .tsv row, or None (with a warning)
    for a line that isn't a triple."""
    short_terms: dict[str, str] = {}
    for number, triple in read_nt(path):
        if triple is None:
            logger.warning("Skipping %s:%d: not an N-Triples triple.", path, number)
            yield None
            continue
        yield "\t".join(
            nt_term_to_tsv(term, term_mapping, short_terms) for term in triple
        )


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
        yield tsv_triple_to_nt(triple, term_mapping)


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
