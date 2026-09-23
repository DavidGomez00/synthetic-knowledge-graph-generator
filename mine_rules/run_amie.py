#!/usr/bin/env python3
"""
Mine Horn rules from an RDF/TSV knowledge base using AMIE3 (via the command
line) and export the mined rules to a CSV file.

AMIE3 is invoked as a subprocess:

    java [-Xmx<HEAP>] -jar <jar> [amie options...] <input file(s)>

The jar is not tracked in git (it's ~96 MB): download `amie3.5.1.jar` from
https://github.com/dig-team/amie/releases into this folder, or point `--jar`
at it.

Its plain-text stdout table is parsed and written out as CSV with columns:

    rule, body, head, head_coverage, std_confidence, pca_confidence,
    positive_examples, body_size, pca_body_size, functional_variable

Example (see docs/Getting_started.md and README.md for this repo's own
worked examples):

    python run_amie.py data/french_royalty/normalized/french_royalty.tsv \\
        -o output/french_royalty/normalized_rules.csv \\
        --mins 1 --minis 1 --minhc 0

With no threshold flags, AMIE's own defaults are used (-mins/-minis 100,
-minhc 0.01) which is appropriate for large knowledge bases. For small or
logically-thin graphs those defaults will silently yield zero (or only
trivial) rules -- pass --mins 1 --minis 1 --minhc 0 (and optionally --minc 0
--minpca 0 to stop AMIE from filtering rules out by confidence too), then
judge by head_coverage/positive_examples whether what comes back is real
structure.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_JAR = SCRIPT_DIR / "amie3.5.1.jar"

CSV_FIELDS = [
    "rule",
    "body",
    "head",
    "head_coverage",
    "std_confidence",
    "pca_confidence",
    "positive_examples",
    "body_size",
    "pca_body_size",
    "functional_variable",
]

# AMIE prints one summary line per rule as 8 tab-separated fields:
#   <rule text> \t <head coverage> \t <std conf> \t <pca conf> \t
#   <positive examples> \t <body size> \t <pca body size> \t <functional var>
_RULE_NUM_FIELDS = 8


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = ["java"]
    if args.xmx:
        cmd += [f"-Xmx{args.xmx}", "-XX:-UseGCOverheadLimit"]
    cmd += ["-jar", str(args.jar)]

    if args.mins is not None:
        cmd += ["-mins", str(args.mins)]
    if args.minis is not None:
        cmd += ["-minis", str(args.minis)]
    if args.minhc is not None:
        cmd += ["-minhc", str(args.minhc)]
    if args.minc is not None:
        cmd += ["-minc", str(args.minc)]
    if args.minpca is not None:
        cmd += ["-minpca", str(args.minpca)]
    if args.maxad is not None:
        cmd += ["-maxad", str(args.maxad)]
    if args.threads is not None:
        cmd += ["-nc", str(args.threads)]
    if args.const:
        cmd += ["-const"]
    if args.verbose:
        cmd += ["-verbose"]
    cmd += args.amie_arg

    cmd += [str(p) for p in args.input]
    return cmd


def format_body(body_text: str) -> str:
    """Join body atoms with two spaces, one space within each atom.

    AMIE separates subject/predicate/object tokens with two spaces
    uniformly, so atom boundaries aren't visible from spacing alone --
    tokens are grouped into triples (or single tokens for "!="
    constraints) to recover them.
    """
    tokens = body_text.split()
    atoms = []
    i = 0
    while i < len(tokens):
        if "!=" in tokens[i]:
            atoms.append(tokens[i])
            i += 1
        else:
            atoms.append(" ".join(tokens[i : i + 3]))
            i += 3
    return "  ".join(atoms)


def parse_amie_output(text: str) -> list[dict]:
    """Parse AMIE3's stdout table into a list of rule dicts."""
    rules = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != _RULE_NUM_FIELDS:
            continue
        rule_text, hc, std_conf, pca_conf, pos_ex, body_size, pca_body_size, fvar = (
            fields
        )
        if "=>" not in rule_text:
            continue
        try:
            hc, std_conf, pca_conf = float(hc), float(std_conf), float(pca_conf)
            pos_ex, body_size, pca_body_size = (
                int(pos_ex),
                int(body_size),
                int(pca_body_size),
            )
        except ValueError:
            # Not a data row (e.g. the header line repeating these column names).
            continue

        body, _, head = rule_text.partition("=>")
        rules.append(
            {
                "rule": " ".join(rule_text.split()),
                "body": format_body(body),
                "head": " ".join(head.split()),
                "head_coverage": hc,
                "std_confidence": std_conf,
                "pca_confidence": pca_conf,
                "positive_examples": pos_ex,
                "body_size": body_size,
                "pca_body_size": pca_body_size,
                "functional_variable": fvar.strip(),
            }
        )
    return rules


def write_csv(rules: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rules)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mine rules from a knowledge base with AMIE3 and export them to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "input",
        nargs="+",
        type=Path,
        help="Input knowledge base file(s) (TTL, N3, TSV/CSV)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: <input stem>_rules.csv next to the first input file)",
    )
    p.add_argument(
        "--jar", type=Path, default=DEFAULT_JAR, help="Path to the AMIE3 jar file"
    )
    p.add_argument("--java", default="java", help="Java executable to use")
    p.add_argument("--xmx", default=None, help='Java max heap size, e.g. "2G"')

    p.add_argument(
        "--mins", default=None, help="AMIE -mins (min-support). AMIE default: 100"
    )
    p.add_argument(
        "--minis",
        default=None,
        help="AMIE -minis (min-initial-support). AMIE default: 100",
    )
    p.add_argument(
        "--minhc",
        default=None,
        help="AMIE -minhc (min-head-coverage). AMIE default: 0.01",
    )
    p.add_argument(
        "--minc",
        default=None,
        help="AMIE -minc (min-std-confidence). AMIE default: 0.0",
    )
    p.add_argument(
        "--minpca",
        default=None,
        help="AMIE -minpca (min-pca-confidence). AMIE default: 0.0",
    )
    p.add_argument(
        "--maxad", default=None, help="AMIE -maxad (max rule length). AMIE default: 3"
    )
    p.add_argument("--threads", "-n", default=None, help="AMIE -nc (number of threads)")
    p.add_argument(
        "--const", action="store_true", help="Pass AMIE -const (allow constants)"
    )
    p.add_argument("--verbose", action="store_true", help="Pass AMIE -verbose")
    p.add_argument(
        "--amie-arg",
        action="append",
        default=[],
        help="Extra raw AMIE argument, repeatable, e.g. --amie-arg -htr --amie-arg enemyOf",
    )

    p.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Optional path to save AMIE's full raw stdout/stderr for debugging",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout in seconds for the AMIE process",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AMIE command that would run and exit",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for f in args.input:
        if not f.exists():
            print(f"error: input file not found: {f}", file=sys.stderr)
            return 1
    if not args.jar.exists():
        print(f"error: AMIE jar not found: {args.jar}", file=sys.stderr)
        return 1

    if args.output is None:
        args.output = args.input[0].with_name(args.input[0].stem + ".csv")

    cmd = build_command(args)
    cmd[0] = args.java
    print("Running:", " ".join(cmd))

    if args.dry_run:
        return 0

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=args.timeout, check=False
        )
    except FileNotFoundError:
        print(
            f"error: could not run '{args.java}'. Is Java installed and on PATH?",
            file=sys.stderr,
        )
        return 1
    except subprocess.TimeoutExpired:
        print(f"error: AMIE timed out after {args.timeout}s", file=sys.stderr)
        return 1

    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(
            "$ "
            + " ".join(cmd)
            + "\n\n--- stdout ---\n"
            + proc.stdout
            + "\n--- stderr ---\n"
            + proc.stderr
        )
        print(f"Full AMIE log saved to {args.log}")

    if proc.stderr.strip():
        print("--- AMIE stderr ---", file=sys.stderr)
        print(proc.stderr.strip(), file=sys.stderr)

    if proc.returncode != 0:
        print(f"error: AMIE exited with status {proc.returncode}", file=sys.stderr)
        if not args.log:
            print(proc.stdout[-4000:], file=sys.stderr)
        return proc.returncode

    rules = parse_amie_output(proc.stdout)
    write_csv(rules, args.output)

    print(f"Parsed {len(rules)} rules -> {args.output}")

    if not rules:
        print(
            "warning: 0 rules mined.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
