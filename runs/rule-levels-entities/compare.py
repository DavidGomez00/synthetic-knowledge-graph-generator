#!/usr/bin/env python3
"""Compares kbgen's level copies made with the source's entity count (synthesize.py -ne,
exp_datasets_kbgen_entities/) with the earlier ones (exp_datasets/), and estimates the
synthesis time of the levels not made yet.

    python runs/rule-levels-entities/compare.py                 # every level in the new summary
    python runs/rule-levels-entities/compare.py --jobs 6 8      # ETA with 6 and 8 processes

Standard library only. Degrees are counted on the distinct triples of each file, a
triple adding one to its subject and one to its object.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import statistics
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OLD_SUMMARY = REPO / "runs/rule-levels/kbgen-results/rule-levels/summary.csv"
NEW_SUMMARY = REPO / "runs/rule-levels-entities/kbgen-results/rule-levels/summary.csv"
LEVEL_ORDER = ["std1", "pca1", "r25", "r50", "r75", "r100"]


def graph_stats(path: Path) -> dict[str, float]:
    triples = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                triples.add(tuple(parts))
    degree: Counter[str] = Counter()
    for s, _, o in triples:
        degree[s] += 1
        degree[o] += 1
    values = sorted(degree.values(), reverse=True)
    top = max(1, len(values) // 100)
    return {
        "entities": len(values),
        "triples": len(triples),
        "relations": len({p for _, p, _ in triples}),
        "mean_degree": sum(values) / len(values),
        "median_degree": statistics.median(values),
        "max_degree": values[0],
        "top1_share": sum(values[:top]) / sum(values),
    }


def read_summary(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return {(r["dataset"].rsplit("-", 1)[0], r["level"]): r for r in csv.DictReader(f)}


def key(item: tuple[str, str]) -> tuple[str, int]:
    return item[0].lower(), LEVEL_ORDER.index(item[1])


def compare(old: dict, new: dict) -> list[tuple[str, str, float, float]]:
    """Prints the tables and returns (dataset, level, old seconds, new seconds)."""
    sources: dict[str, dict[str, float]] = {}
    print("| Copy | Desired before | Desired now | Entities source / before / now | "
          "Triples kept / before / now | Relations source / now | Rule facts before / now | "
          "Sampled facts before / now | "
          "Max degree source / before / now | Median degree source / before / now | "
          "Top 1% share source / before / now | Synthesis s before / now |")
    print("|---|---:|---:|---|---|---|---|---|---|---|---|---|")
    times = []
    for d, level in sorted(new, key=key):
        n, o = new[(d, level)], old.get((d, level))
        if o is None:
            continue
        if d not in sources:
            sources[d] = graph_stats(REPO / "exp_datasets" / d / f"{d}.unified.tsv")
        src = sources[d]
        copy = f"{d}_kbgen_{level}"
        before = graph_stats(REPO / "exp_datasets" / copy / f"{copy}.tsv")
        now = graph_stats(REPO / "exp_datasets_kbgen_entities" / copy / f"{copy}.tsv")
        t_old, t_new = float(o["seconds_synthesize"]), float(n["seconds_synthesize"])
        times.append((d, level, t_old, t_new))
        print(f"| {copy} | {o['model_entities']} | {n['desired_entities']} "
              f"| {src['entities']} / {before['entities']} / {now['entities']} "
              f"| {n['kept_triples']} / {before['triples']} / {now['triples']} "
              f"| {src['relations']} / {now['relations']} "
              f"| {o['rule_facts']} / {n['rule_facts']} "
              f"| {int(o['synthetic_triples']) - int(o['rule_facts'])} / "
              f"{int(n['synthetic_triples']) - int(n['rule_facts'])} "
              f"| {src['max_degree']} / {before['max_degree']} / {now['max_degree']} "
              f"| {src['median_degree']:g} / {before['median_degree']:g} / {now['median_degree']:g} "
              f"| {src['top1_share']:.3f} / {before['top1_share']:.3f} / {now['top1_share']:.3f} "
              f"| {t_old:.0f} / {t_new:.0f} |")
    return times


def lpt(durations: list[float], jobs: int) -> float:
    """Wall time of `durations` run longest first on `jobs` processes."""
    loads = [0.0] * jobs
    heapq.heapify(loads)
    for t in sorted(durations, reverse=True):
        heapq.heappush(loads, heapq.heappop(loads) + t)
    return max(loads)


def hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def eta(old: dict, new: dict, times: list, jobs_list: list[int]) -> None:
    # Ratios of the levels long enough to time reliably.
    ratios = [t_new / t_old for _, _, t_old, t_new in times if t_old >= 30]
    ratio = statistics.median(ratios) if ratios else 1.0
    print(f"\nSynthesis time now / before, levels of at least 30 s: median {ratio:.2f} "
          f"(min {min(ratios):.2f}, max {max(ratios):.2f}, {len(ratios)} levels)\n")
    todo: dict[str, list[float]] = {}
    for (d, level), o in old.items():
        if (d, level) in new:
            continue
        todo.setdefault(d, []).append(float(o["seconds_synthesize"]) * ratio)
    print("| Dataset | Levels | Synthesis, all levels | Longest level |")
    print("|---|---:|---:|---:|")
    for d in sorted(todo, key=lambda d: -sum(todo[d])):
        print(f"| {d} | {len(todo[d])} | {hours(sum(todo[d]))} | {hours(max(todo[d]))} |")
    everything = [t for ts in todo.values() for t in ts]
    print(f"\nTotal: {len(everything)} levels, {hours(sum(everything))} of synthesis; "
          f"longest level {hours(max(everything))}.")
    for jobs in jobs_list:
        print(f"Wall time with {jobs} processes, longest level first: {hours(lpt(everything, jobs))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, nargs="+", default=[6, 8])
    args = parser.parse_args()
    old, new = read_summary(OLD_SUMMARY), read_summary(NEW_SUMMARY)
    times = compare(old, new)
    eta(old, new, times, args.jobs)


if __name__ == "__main__":
    main()
