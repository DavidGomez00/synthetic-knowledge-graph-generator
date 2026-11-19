"""Writes one table per dataset with the network statistics that Cytoscape's
NetworkAnalyzer computed for the dataset and its copies.

The statistics are read from a saved Cytoscape session (`--session`, a `.cys`
zip file). NetworkAnalyzer keeps the results of each analysed network as JSON in
the `statistics` column of the network's hidden table, `tables/<SUID>-<name>/
HIDDEN-org.cytoscape.model.CyNetwork-<SUID>+hidden+network.cytable`, with the
network's title (`french1_1.unified.tsv (directed)`) in `networkTitle`.

Each dataset under `--root` (default `exp_datasets`) with a `<folder>.unified.tsv`
gets four columns: the original, PyGraft's copy (`<folder>_pygraft`), kbgen's and
our copy at rule level `--level` (default `r100`, `<folder>_kbgen_<level>` and
`<folder>_ours_<level>`). A copy whose folder does not exist (PyGraft has none
of the YAGO3-10 datasets) has no column. A network is matched to a graph by its
title, without `.tsv`, `.unified`, Cytoscape's `_1` suffix of a network imported
twice, and case. Its node and edge counts must equal the distinct entities and
triples of the graph's file, so a network loaded from the wrong file is refused.

The YAGO3-10 datasets (`COMPUTED_DATASETS`) are too large for NetworkAnalyzer, so
their statistics are computed here, with NetworkAnalyzer's definitions for
directed graphs, from the distinct triples of each graph's file. Radius, diameter
and characteristic path length need the shortest paths between all pairs of
nodes and are left out of their tables (`PATH_STATISTICS`). `--check-python`
computes the other statistics for every graph of the session as well and fails
if one differs from NetworkAnalyzer's value.

Writes to `--output-dir` (default `exp_analysis/cytoscape`) `<dataset>.csv`
(unrounded), `.md` and `.tex`, one row per statistic, and `all.csv`, one row per
graph with its `source` (`cytoscape` or `python`). The `.tex` table has the
layout of `results_table.py`'s tables (`[H]`, `\\tabcolsep` 3pt, `\\arraystretch`
0.8, `||` between columns, `\\hline` rules), with the dataset's name
(`Family N:M`) and the rule level in the caption. `--no-pygraft` leaves the
PyGraft column out of each dataset's tables; `all.csv` keeps every graph. A
graph of a column without statistics gets `-`, is listed at the end, and makes
the script exit with status 1.

Usage:
    python scripts/cytoscape_tables.py
    python scripts/cytoscape_tables.py --no-pygraft
    python scripts/cytoscape_tables.py --check-python
    python scripts/cytoscape_tables.py --session path/to/session.cys --level r25
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_SESSION = Path.home() / "Cytoscape" / "Sessions" / "exp_datasets.cys"
HIDDEN_NETWORK_TABLE = re.compile(
    r"/HIDDEN-org\.cytoscape\.model\.CyNetwork-[^/]*\.cytable$"
)

# NetworkAnalyzer's JSON key, the row label and the format: "int" for counts,
# "sci" for scientific notation, "4g" for 4 significant digits.
STATISTICS = (
    ("edgeCount", "Number of edges", "int"),
    ("nodeCount", "Number of nodes", "int"),
    ("radius", "Radius", "int"),
    ("diameter", "Diameter", "int"),
    ("density", "Density", "sci"),
    ("avNeighbors", "Average number of neighbors", "4g"),
    ("avSpl", "Characteristic path length", "4g"),
    ("cc", "Clustering coefficient", "4g"),
    ("ncc", "Connected components", "int"),
    ("mnp", "Multi-edge node pairs", "int"),
    ("nsl", "Self-loops", "int"),
)
# The statistics that need the shortest paths between all pairs of nodes, which
# `compute_statistics` does not compute.
PATH_STATISTICS = frozenset({"radius", "diameter", "avSpl"})
# Datasets whose graphs are too large for NetworkAnalyzer: their statistics are
# computed by `compute_statistics` instead of read from the session.
COMPUTED_DATASETS = ("YAGO3-10",)
# Column header and the suffix of the graph's folder; NAME is this generator, as
# in `results_table.py` and `characterize_datasets.py`.
METHODS = (
    ("Original", ""),
    ("PyGraft", "_pygraft"),
    ("KBgen", "_kbgen_{level}"),
    ("NAME", "_ours_{level}"),
)
# Datasets in the order of the result tables: family, then variant.
FAMILY_ORDER = ("french", "family", "wn18rr", "yago3-10", "sgkg")
VARIANT_ORDER = ("", "1_1", "1_n", "n_1", "n_m")
# Caption names of the families and variants, as in `results_table.py`; an SGKG
# graph keeps its folder name.
FAMILY_NAMES = {
    "french": "French Royalty",
    "family": "Family",
    "wn18rr": "WN18RR",
    "yago3-10": "YAGO3-10",
}
VARIANT_NAMES = {"1_1": "1:1", "1_n": "1:N", "n_1": "N:1", "n_m": "N:M", "m_n": "N:M"}
# Caption text of the copies at each rule level (docs/rule-levels.md).
LEVEL_TEXT = {
    "std1": "made using the rules with std confidence 1",
    **{
        f"r{share}": f"made using about {share}\\% of the mined rules, those of "
        "highest PCA confidence"
        for share in (25, 50, 75)
    },
    "r100": "made using all the rules",
}
COMPUTED_NOTE = (
    "Radius, diameter and characteristic path length are not computed for graphs "
    "of this size."
)


def read_statistics(session: Path) -> list[tuple[str, dict[str, str]]]:
    """(network title, statistics) of every analysed network of the session."""
    found: list[tuple[str, dict[str, str]]] = []
    with zipfile.ZipFile(session) as archive:
        for name in archive.namelist():
            if not HIDDEN_NETWORK_TABLE.search(name):
                continue
            text = archive.read(name).decode("utf-8")
            rows = list(csv.reader(io.StringIO(text)))
            # CyCSV: version, column names, column types, mutability, table title,
            # then the rows.
            header = rows[1]
            if "statistics" not in header:
                continue
            column = header.index("statistics")
            for row in rows[5:]:
                if len(row) > column and row[column].strip():
                    stats = json.loads(row[column])
                    found.append((stats.get("networkTitle", name), stats))
    return found


def graph_name(title: str) -> str:
    """`french1_1.unified.tsv (directed)` -> `french1_1`, lowercased."""
    name = re.sub(r"\s*\((un)?directed\)$", "", title.strip())
    name = re.sub(r"\.tsv(_\d+)?$", "", name)
    return name.removesuffix(".unified").lower()


def graph_file(root: Path, graph: str) -> Path:
    unified = root / graph / f"{graph}.unified.tsv"
    return unified if unified.exists() else root / graph / f"{graph}.tsv"


def read_triples(path: Path) -> set[tuple[str, str, str]]:
    """The distinct triples of a tab-separated triples file."""
    triples = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                triples.add((parts[0], parts[1], parts[2]))
    return triples


def count_graph(path: Path) -> tuple[int, int]:
    """Distinct entities and distinct triples of a tab-separated triples file."""
    triples = read_triples(path)
    entities = {term for h, _, t in triples for term in (h, t)}
    return len(entities), len(triples)


def compute_statistics(triples: set[tuple[str, str, str]]) -> dict[str, str]:
    """The statistics of STATISTICS but PATH_STATISTICS, with NetworkAnalyzer's
    definitions for a directed graph whose edges are `triples`, as strings."""
    nodes = {term for h, _, t in triples for term in (h, t)}
    n = len(nodes)
    # Distinct neighbors in either direction, distinct directed pairs and the
    # edges of each unordered pair, all without self-loops: NetworkAnalyzer does
    # not count a node with several self-loops as a multi-edge node pair.
    neighbors: dict[str, set[str]] = defaultdict(set)
    pairs: set[tuple[str, str]] = set()
    edges_per_pair: Counter[frozenset[str]] = Counter()
    for h, _, t in triples:
        if h != t:
            neighbors[h].add(t)
            neighbors[t].add(h)
            pairs.add((h, t))
            edges_per_pair[frozenset((h, t))] += 1

    # Clustering coefficient: per node, the directed pairs among its k neighbors
    # over k(k - 1), averaged over every node (0 for k < 2). A pair (a, b) counts
    # for every common neighbor of a and b.
    linked: Counter[str] = Counter()
    for a, b in pairs:
        small, large = neighbors[a], neighbors[b]
        if len(small) > len(large):
            small, large = large, small
        linked.update(v for v in small if v in large)
    clustering = sum(
        count / (len(neighbors[v]) * (len(neighbors[v]) - 1))
        for v, count in linked.items()
    )

    # Weakly connected components.
    seen: set[str] = set()
    components = 0
    for start in nodes:
        if start in seen:
            continue
        components += 1
        seen.add(start)
        stack = [start]
        while stack:
            for other in neighbors[stack.pop()]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)

    return {
        "nodeCount": str(n),
        "edgeCount": str(len(triples)),
        "density": repr(len(pairs) / (n * (n - 1))) if n > 1 else "0.0",
        "avNeighbors": repr(sum(len(neighbors[v]) for v in nodes) / n) if n else "0.0",
        "cc": repr(clustering / n) if n else "0.0",
        "ncc": str(components),
        "mnp": str(sum(1 for count in edges_per_pair.values() if count > 1)),
        "nsl": str(sum(1 for h, _, t in triples if h == t)),
    }


def compare_statistics(cytoscape: dict[str, str], python: dict[str, str]) -> list[str]:
    """The statistics of `python` that differ from `cytoscape`'s: integers
    exactly, floats to a relative 1e-9."""
    counts = {key for key, _, fmt in STATISTICS if fmt == "int"}
    differ = []
    for key, value in python.items():
        expected = float(cytoscape[key])
        same = (
            int(value) == int(expected)
            if key in counts
            else math.isclose(float(value), expected, rel_tol=1e-9, abs_tol=1e-15)
        )
        if not same:
            differ.append(f"{key} {cytoscape[key]} != {value}")
    return differ


def is_computed(dataset: str) -> bool:
    return dataset.startswith(COMPUTED_DATASETS)


def dataset_key(name: str) -> tuple[int, int, str]:
    lower = name.lower()
    family = next(
        (i for i, f in enumerate(FAMILY_ORDER) if lower.startswith(f)),
        len(FAMILY_ORDER),
    )
    rest = lower[len(FAMILY_ORDER[family]) :] if family < len(FAMILY_ORDER) else lower
    rest = rest.lstrip("_")
    variant = VARIANT_ORDER.index(rest) if rest in VARIANT_ORDER else len(VARIANT_ORDER)
    return family, variant, lower


def display_name(name: str) -> str:
    """`FamilyM_N` -> `Family N:M`, `YAGO3-10_1_N` -> `YAGO3-10 1:N`, `french` ->
    `French Royalty`, `SGKG1` as is."""
    lower = name.lower()
    for prefix, family in FAMILY_NAMES.items():
        if lower.startswith(prefix):
            rest = lower[len(prefix) :].lstrip("_")
            if not rest:
                return family
            if rest in VARIANT_NAMES:
                return f"{family} {VARIANT_NAMES[rest]}"
    return name


def find_datasets(root: Path) -> list[str]:
    names = [p.name for p in root.iterdir() if (p / f"{p.name}.unified.tsv").exists()]
    return sorted(names, key=dataset_key)


def format_value(value: object, fmt: str, latex: bool = False) -> str:
    if value is None:
        return "-"
    number = float(value)  # type: ignore[arg-type]
    if fmt == "int":
        text = f"{int(number):,}"
        return text.replace(",", "{,}") if latex else text
    text = f"{number:.3e}" if fmt == "sci" else f"{number:.{fmt}}"
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    if latex:
        return f"${mantissa}\\times10^{{{int(exponent)}}}$"
    return f"{mantissa}e{int(exponent)}"


def write_tables(
    out: Path,
    dataset: str,
    columns_of: list[tuple[str, str]],
    stats: dict[str, dict[str, str]],
    level: str,
) -> None:
    """`<dataset>.csv`, `.md` and `.tex`: one row per statistic, one column per
    (header, graph) of `columns_of`. A dataset computed in Python has no rows of
    PATH_STATISTICS."""
    computed = is_computed(dataset)
    rows = [s for s in STATISTICS if not (computed and s[0] in PATH_STATISTICS)]
    headers = [header for header, _ in columns_of]
    graphs = [graph for _, graph in columns_of]
    columns = [stats.get(g.lower()) for g in graphs]
    name = display_name(dataset)

    with (out / f"{dataset}.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["statistic", *headers])
        writer.writerow(["graph", *graphs])
        for key, label, _ in rows:
            writer.writerow([label, *("" if c is None else c[key] for c in columns)])

    source = (
        "computed by `scripts/cytoscape_tables.py` with the definitions of "
        "Cytoscape's NetworkAnalyzer for directed graphs"
        if computed
        else "computed by Cytoscape's NetworkAnalyzer on the directed graphs"
    )
    description = (
        f"Network statistics of {dataset} and its copies, {source}. KBgen and NAME "
        f"(this generator) are the copies at rule level {level}"
        + (", PyGraft's copy is made without rules" if "PyGraft" in headers else "")
        + ". "
        + (f"{COMPUTED_NOTE} " if computed else "")
        + "Generated by `scripts/cytoscape_tables.py`; - marks a graph the session "
        "has no statistics for."
    )
    md = [
        f"# {name}",
        "",
        description,
        "",
        "| Statistic | " + " | ".join(headers) + " |",
        "|---|" + "---:|" * len(headers),
    ]
    for key, label, fmt in rows:
        cells = [format_value(None if c is None else c[key], fmt) for c in columns]
        md.append(f"| {label} | " + " | ".join(cells) + " |")
    (out / f"{dataset}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    level_text = LEVEL_TEXT.get(level, f"at rule level {level}")
    caption_name = name.replace("_", "\\_")
    note = f" {COMPUTED_NOTE}" if computed else ""
    tex = [
        "% Generated by scripts/cytoscape_tables.py",
        "\\begin{table}[H]",
        "\\centering",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\renewcommand{\\arraystretch}{0.8}",
        f"\\caption{{Graph statistics of {caption_name} and its "
        f"synthetic copies {level_text}.{note}}}",
        f"\\label{{tab:cytoscape_{dataset}}}",
        "\\begin{tabular}{l||" + "||".join("c" * len(headers)) + "}",
        "\\hline",
        "Statistic & " + " & ".join(headers) + " \\\\",
        "\\hline\\hline",
    ]
    for key, label, fmt in rows:
        cells = [
            format_value(None if c is None else c[key], fmt, latex=True)
            for c in columns
        ]
        tex.append(f"{label} & " + " & ".join(cells) + " \\\\")
    tex += ["\\hline", "\\end{tabular}", "\\end{table}"]
    (out / f"{dataset}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--session", type=Path, default=DEFAULT_SESSION, help="Cytoscape session file"
    )
    parser.add_argument(
        "--root", type=Path, default=Path("exp_datasets"), help="folder of the datasets"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("exp_analysis/cytoscape")
    )
    parser.add_argument(
        "--level", default="r100", help="rule level of kbgen's and our copies"
    )
    parser.add_argument(
        "--no-pygraft",
        action="store_true",
        help="leave the PyGraft column out of each dataset's tables",
    )
    parser.add_argument(
        "--check-python",
        action="store_true",
        help="compare the statistics computed in Python with NetworkAnalyzer's for "
        "every graph of the session",
    )
    args = parser.parse_args()

    datasets = find_datasets(args.root)
    # Per dataset, (header, graph) of every method with a copy; all.csv has them.
    graphs_of = {
        d: [
            (h, graph)
            for h, suffix in METHODS
            if graph_file(args.root, graph := f"{d}{suffix.format(level=args.level)}")
            .exists()
        ]
        for d in datasets
    }
    shown = {h for h, _ in METHODS if not (args.no_pygraft and h == "PyGraft")}
    wanted = {
        g.lower(): g
        for d, graphs in graphs_of.items()
        if not is_computed(d)
        for _, g in graphs
    }

    stats: dict[str, dict[str, str]] = {}
    titles: dict[str, str] = {}
    for title, values in read_statistics(args.session):
        name = graph_name(title)
        if name not in wanted:
            continue
        if name in stats:
            if any(stats[name][k] != values[k] for k, _, _ in STATISTICS):
                sys.exit(
                    f"Two networks of {wanted[name]} have different statistics: "
                    f"{titles[name]!r} and {title!r}"
                )
            continue
        stats[name], titles[name] = values, title

    errors: list[str] = []
    for name, values in stats.items():
        path = graph_file(args.root, wanted[name])
        if args.check_python:
            differ = compare_statistics(values, compute_statistics(read_triples(path)))
            errors += [f"{wanted[name]}: {d}" for d in differ]
            continue
        entities, triples = count_graph(path)
        nodes, edges = int(values["nodeCount"]), int(values["edgeCount"])
        if (nodes, edges) != (entities, triples):
            errors.append(
                f"{wanted[name]}: network {titles[name]!r} has {nodes} nodes and "
                f"{edges} edges, {path} has {entities} entities and {triples} triples"
            )
    if errors:
        sys.exit("Networks that don't match their file:\n  " + "\n  ".join(errors))
    if args.check_python:
        print(f"The Python statistics of {len(stats)} graphs match NetworkAnalyzer's")

    computed = [g for d, gs in graphs_of.items() if is_computed(d) for _, g in gs]
    for graph in computed:
        print(f"Computing the statistics of {graph}", flush=True)
        stats[graph.lower()] = compute_statistics(
            read_triples(graph_file(args.root, graph))
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    with (args.output_dir / "all.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["dataset", "method", "graph", "source", "network_title"]
            + [k for k, _, _ in STATISTICS]
        )
        for dataset, graphs in graphs_of.items():
            source = "python" if is_computed(dataset) else "cytoscape"
            for header, graph in graphs:
                found = stats.get(graph.lower())
                if found is None:
                    if header in shown:
                        missing.append(graph)
                    continue
                writer.writerow(
                    [dataset, header, graph, source, titles.get(graph.lower(), "")]
                    + [found.get(k, "") for k, _, _ in STATISTICS]
                )
            columns_of = [(h, g) for h, g in graphs if h in shown]
            write_tables(args.output_dir, dataset, columns_of, stats, args.level)

    print(f"Wrote {len(datasets)} tables of {len(stats)} graphs to {args.output_dir}/")
    if missing:
        print(f"No statistics in {args.session} for {len(missing)} graphs:")
        for graph in missing:
            print(f"  {graph}")
        sys.exit(1)


if __name__ == "__main__":
    main()
