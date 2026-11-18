"""Writes one table per dataset with the network statistics that Cytoscape's
NetworkAnalyzer computed for the dataset and its copies.

The statistics are read from a saved Cytoscape session (`--session`, a `.cys`
zip file). NetworkAnalyzer keeps the results of each analysed network as JSON in
the `statistics` column of the network's hidden table, `tables/<SUID>-<name>/
HIDDEN-org.cytoscape.model.CyNetwork-<SUID>+hidden+network.cytable`, with the
network's title (`french1_1.unified.tsv (directed)`) in `networkTitle`.

Each dataset under `--root` (default `exp_datasets`) with a `<folder>.unified.tsv`,
except the YAGO3-10 datasets, gets four columns: the original, PyGraft's copy
(`<folder>_pygraft`), kbgen's and our copy at rule level `--level` (default
`r100`, `<folder>_kbgen_<level>` and `<folder>_ours_<level>`). A network is
matched to a graph by its title, without `.tsv`, `.unified`, Cytoscape's `_1`
suffix of a network imported twice, and case. Its node and edge counts must equal
the distinct entities and triples of the graph's file, so a network loaded from
the wrong file is refused.

Writes to `--output-dir` (default `exp_analysis/cytoscape`) `<dataset>.csv`
(unrounded), `.md` and `.tex`, one row per statistic, and `all.csv`, one row per
graph. A graph without statistics gets `-` in its column, is listed at the end,
and makes the script exit with status 1.

Usage:
    python scripts/cytoscape_tables.py
    python scripts/cytoscape_tables.py --session path/to/session.cys --level r25
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
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
# Column header and the suffix of the graph's folder; NAME is this generator, as
# in `results_table.py` and `characterize_datasets.py`.
METHODS = (
    ("Original", ""),
    ("PyGraft", "_pygraft"),
    ("KBgen", "_kbgen_{level}"),
    ("NAME", "_ours_{level}"),
)
# Datasets in the order of the result tables: family, then variant.
FAMILY_ORDER = ("french", "family", "wn18rr", "sgkg")
VARIANT_ORDER = ("", "1_1", "1_n", "n_1", "n_m")


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


def count_graph(path: Path) -> tuple[int, int]:
    """Distinct entities and distinct triples of a tab-separated triples file."""
    triples = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                triples.add(tuple(parts))
    entities = {term for h, _, t in triples for term in (h, t)}
    return len(entities), len(triples)


def dataset_key(name: str) -> tuple[int, int, str]:
    lower = name.lower()
    family = next(
        (i for i, f in enumerate(FAMILY_ORDER) if lower.startswith(f)),
        len(FAMILY_ORDER),
    )
    rest = lower[len(FAMILY_ORDER[family]) :] if family < len(FAMILY_ORDER) else lower
    variant = VARIANT_ORDER.index(rest) if rest in VARIANT_ORDER else len(VARIANT_ORDER)
    return family, variant, lower


def find_datasets(root: Path) -> list[str]:
    names = [
        p.name
        for p in root.iterdir()
        if (p / f"{p.name}.unified.tsv").exists()
        and not p.name.startswith("YAGO3-10")
    ]
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
    graphs: list[str],
    stats: dict[str, dict[str, str]],
    level: str,
) -> None:
    headers = [header for header, _ in METHODS]
    columns = [stats.get(g.lower()) for g in graphs]

    with (out / f"{dataset}.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["statistic", *headers])
        writer.writerow(["graph", *graphs])
        for key, label, _ in STATISTICS:
            writer.writerow([label, *("" if c is None else c[key] for c in columns)])

    description = (
        f"Network statistics of {dataset} and its copies, computed by Cytoscape's "
        "NetworkAnalyzer on the directed graphs. KBgen and NAME (this generator) are "
        f"the copies at rule level {level}, PyGraft's copy is made without rules. "
        "Generated by `scripts/cytoscape_tables.py`; - marks a graph the session has "
        "no statistics for."
    )
    md = [
        f"# {dataset}",
        "",
        description,
        "",
        "| Statistic | " + " | ".join(headers) + " |",
        "|---|" + "---:|" * len(headers),
    ]
    for key, label, fmt in STATISTICS:
        cells = [format_value(None if c is None else c[key], fmt) for c in columns]
        md.append(f"| {label} | " + " | ".join(cells) + " |")
    (out / f"{dataset}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    caption = dataset.replace("_", "\\_")
    tex = [
        "% Generated by scripts/cytoscape_tables.py",
        "\\begin{table}[t]",
        "\\centering",
        f"\\caption{{Network statistics of {caption} and its copies (Cytoscape "
        f"NetworkAnalyzer, directed; KBgen and NAME at rule level {level}).}}",
        f"\\label{{tab:cytoscape_{dataset}}}",
        "\\begin{tabular}{l" + "r" * len(headers) + "}",
        "\\toprule",
        "Statistic & " + " & ".join(headers) + " \\\\",
        "\\midrule",
    ]
    for key, label, fmt in STATISTICS:
        cells = [
            format_value(None if c is None else c[key], fmt, latex=True)
            for c in columns
        ]
        tex.append(f"{label} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
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
    args = parser.parse_args()

    datasets = find_datasets(args.root)
    graphs_of = {
        d: [f"{d}{suffix.format(level=args.level)}" for _, suffix in METHODS]
        for d in datasets
    }
    wanted = {g.lower(): g for graphs in graphs_of.values() for g in graphs}

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
        if not path.exists():
            errors.append(f"{wanted[name]}: no file {path}")
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

    args.output_dir.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    with (args.output_dir / "all.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["dataset", "method", "graph", "network_title"]
            + [k for k, _, _ in STATISTICS]
        )
        for dataset, graphs in graphs_of.items():
            for (header, _), graph in zip(METHODS, graphs, strict=True):
                found = stats.get(graph.lower())
                if found is None:
                    missing.append(graph)
                    continue
                writer.writerow(
                    [dataset, header, graph, titles[graph.lower()]]
                    + [found[k] for k, _, _ in STATISTICS]
                )
            write_tables(args.output_dir, dataset, graphs, stats, args.level)

    print(f"Wrote {len(datasets)} tables of {len(stats)} graphs to {args.output_dir}/")
    if missing:
        print(f"No statistics in {args.session} for {len(missing)} graphs:")
        for graph in missing:
            print(f"  {graph}")
        sys.exit(1)


if __name__ == "__main__":
    main()
