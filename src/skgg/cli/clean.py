"""Writes a cleaned copy of an .nt/.tsv file as a .tsv file: every '/' in a term's own name becomes '_' (e.g. `Matilda_of_Saxony_1172_1209/10`,
which would otherwise shorten to `10`), duplicate triples are dropped (keeping
the first occurrence), and so are its "literals": triples whose object is never
typed (never the subject of a type triple), plus every triple of a predicate
whose objects are always literals (e.g. `name`). Type triples themselves are
always kept. Also reports every term used as a subject that is never typed."""

import argparse
import logging
import re
from collections.abc import Collection, Iterator
from pathlib import Path
from urllib.parse import unquote

from skgg.core.utils import setup_logging, short_term

logger = logging.getLogger(__name__)

TSV_TYPE = "type"
NT_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"

# Predicates whose objects are always literals, even when an object happens to
# equal a typed term (e.g. a person whose name is their entity ID).
LITERAL_PREDICATES = frozenset({"name"})

# A '/' inside an IRI's last segment, which only appears percent-encoded.
ENCODED_SLASH = re.compile("%2F", re.IGNORECASE)


def _clean_iri(iri: str) -> str:
    """Replaces every '%2F' in an IRI's last path/fragment segment (the one
    `utils.short_term` keeps) with '_', leaving the '/' between its segments."""
    stem = iri.rstrip("/#")
    cut = max(stem.rfind("/"), stem.rfind("#")) + 1
    return iri[:cut] + ENCODED_SLASH.sub("_", iri[cut:])


def _clean_term(term: str) -> str:
    """Replaces every '/' in a term's own name with '_'.

    A bare .tsv term is all name, so each of its '/' is replaced. In an IRI
    (`<...>`, or a .tsv term starting with 'http') only the last segment is
    the name, where a '/' is written as '%2F'. Literals and blank nodes are
    returned unchanged.
    """
    if term.startswith("<") and term.endswith(">"):
        return f"<{_clean_iri(term[1:-1])}>"
    if term.startswith("http"):
        return _clean_iri(term)
    if term.startswith(('"', "_:")):
        return term
    return term.replace("/", "_")


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


def _output_path(output: Path) -> Path:
    """Returns `output` with a .tsv suffix, replacing a .nt suffix."""
    suffix = output.suffix.lower()
    if suffix == ".tsv":
        return output
    if suffix == ".nt":
        output = output.with_suffix("")
    return output.with_name(f"{output.name}.tsv")


def clean(
    input_file: str | Path,
    output: str | Path,
    literal_predicates: Collection[str] = LITERAL_PREDICATES,
) -> tuple[int, int, int, int, set[str]]:
    """Writes `input_file` to the .tsv file `output`, replacing '/' with '_'
    in every term's own name (see `_clean_term`), then dropping duplicate
    triples, every triple of a `literal_predicates` predicate, and every
    non-type triple whose object is never typed. The IRIs of an .nt input
    become their last segment (`utils.short_term`).

    Args:
        input_file: The .nt/.tsv file to clean.
        output: Output .tsv path (a missing .tsv suffix is added, and a .nt
            suffix replaced).
        literal_predicates: Predicates (bare terms, matched against each
            predicate's `utils.short_term`) whose triples are always dropped.

    Returns:
        `(kept, duplicates, literals, literal_predicate_triples,
        untyped_subjects)`: the triples written; the duplicate, untyped-object
        and literal-predicate triples dropped; and the terms used as subjects
        that are never typed.

    Raises:
        ValueError: If the output path is the input file, two distinct terms
            differ only in '/' vs '_' (or '%2F' vs '_'), which cleaning would
            merge, or two distinct .nt IRIs share a short term, which the .tsv
            output would merge.
    """
    input_file = Path(input_file)
    tsv_file = _output_path(Path(output))
    if input_file.resolve() == tsv_file.resolve():
        raise ValueError("The output file must differ from the input file.")

    is_tsv = input_file.suffix.lower() == ".tsv"

    type_predicates = {TSV_TYPE, NT_TYPE}

    # Cleaned term -> the term it came from, to catch two terms cleaned into one.
    originals: dict[str, str] = {}

    def _clean_triples() -> Iterator[tuple[str, str, str]]:
        """The input triples, with '/' in their terms' names replaced."""
        for raw in _iter_triples(input_file):
            subject, predicate, obj = map(_clean_term, raw)
            for term, cleaned in zip(raw, (subject, predicate, obj), strict=True):
                if originals.setdefault(cleaned, term) != term:
                    raise ValueError(
                        f"{term} and {originals[cleaned]} both clean to '{cleaned}'."
                    )
            yield subject, predicate, obj

    typed: set[str] = set()
    subjects: set[str] = set()
    for subject, predicate, _ in _clean_triples():
        subjects.add(subject)
        if predicate in type_predicates:
            typed.add(subject)

    kept = duplicates = removed = literal_predicate_triples = 0
    seen: set[tuple[str, str, str]] = set()
    literals: set[str] = set()
    # .nt -> .tsv: short term -> the IRI it came from, to catch collisions.
    short_terms: dict[str, str] = {}

    def _shorten(term: str) -> str:
        """An .nt term as a .tsv term."""
        short = unquote(short_term(term))
        if short_terms.setdefault(short, term) != term:
            raise ValueError(
                f"{term} and {short_terms[short]} both shorten to '{short}'."
            )
        return short

    with tsv_file.open("w", encoding="utf-8") as tsv_out:
        for triple in _clean_triples():
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
            tsv_out.write("\t".join(tsv_triple) + "\n")
            kept += 1

    renamed = sorted(
        f"{term} -> {cleaned}" for cleaned, term in originals.items() if cleaned != term
    )
    if renamed:
        logger.info(
            "Replaced '/' with '_' in %d terms: %s", len(renamed), ", ".join(renamed)
        )

    logger.info(
        "Wrote %s: kept %d triples, removed %d duplicate triples, %d "
        "triples of literal predicates (%s) and %d untyped-object triples (%d "
        "distinct literals).",
        tsv_file,
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
        description="Write a .nt/.tsv file as .tsv, with '/' in term "
        "names replaced by '_', without duplicate triples or literals (objects "
        "that are never typed), and report subjects that are never typed."
    )
    parser.add_argument("input_file", type=Path, help="Path to the .nt/.tsv file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .tsv path (defaults to <stem>.no-literals.tsv next to the "
        "input).",
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
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level=args.log_level)

    input_file: Path = args.input_file
    clean(
        input_file,
        args.output or input_file.with_name(f"{input_file.stem}.no-literals.tsv"),
        literal_predicates=set(args.literal_predicates),
    )
