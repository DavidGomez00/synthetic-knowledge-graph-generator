"""Converts a triples file from N-Triples (.nt) to tab-separated (.tsv, one
`subject<TAB>predicate<TAB>object` row of bare terms per triple) or back, in
the direction given by the input file's suffix. Duplicate triples are written
once, and lines that aren't a triple are skipped with a warning. The conversion
itself is `core.triples.convert`, re-exported here."""

import argparse
from pathlib import Path

from skgg.core.triples import convert
from skgg.core.utils import load_term_mapping, setup_logging


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
        help="Config file under configurations/ (or a path to one; the .json "
        "extension is optional) whose "
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
