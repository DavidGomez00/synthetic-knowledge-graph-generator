# ruff: noqa: E501  (the Markdown it writes keeps each paragraph and table row on one line)
"""Writes runs/rule-levels/REPORT.md, the tables of the rule-level copies, from the
records of the run: status.tsv (check_copies.py), ours/times.tsv
(cli/generate_datasets.py --times), kbgen-results/rule-levels/summary.csv (kbgen's
driver), the shared steps' timings.json in kbgen-work/ and machine.txt.

    python runs/rule-levels/make_report.py

Needs only the Python standard library.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SHARED_STEPS = ("tsv_to_nt", "load_tensor", "learn_M1", "learn_M2")


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def duration(seconds: float | str | None) -> str:
    if seconds in (None, ""):
        return ""
    s = round(float(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {s % 3600 // 60} min"


def number(value: str | int | None) -> str:
    return "" if value in (None, "") else f"{int(value):,}"


def stamp(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    status = read_tsv(HERE / "status.tsv")
    times = {
        (r["dataset"], r["level"]): r for r in read_tsv(HERE / "ours" / "times.tsv")
    }
    summary_path = HERE / "kbgen-results" / "rule-levels" / "summary.csv"
    kbgen = {}
    if summary_path.is_file():
        with summary_path.open(encoding="utf-8", newline="") as f:
            kbgen = {r["dataset"]: r for r in csv.DictReader(f)}
    machine = (
        (HERE / "machine.txt").read_text() if (HERE / "machine.txt").is_file() else ""
    )

    out = [
        "# Rule-level copies: results",
        "",
        f"Written by `make_report.py` on {dt.datetime.now():%Y-%m-%d %H:%M} from `status.tsv`, `ours/times.tsv`, "
        "`kbgen-results/rule-levels/summary.csv` and `kbgen-work/<dataset>/timings.json`. README.md says how the copies were made.",
        "",
        "## Machine",
        "",
        "```",
        machine.strip() or "machine.txt missing: run reproduce.sh record",
        "```",
        "",
        "## Copies",
        "",
        "*Filter* is what a rule needs to be kept at the level. *Used* is the rules our generator used, the rules kbgen was "
        "given; *parsed* is those kbgen could apply (a rule whose relation kbgen's tensor lacks is dropped). A time covers one "
        "copy: for ours the upload of the source to GraphDB, `cli/main.py` and the download; for kbgen M3, `synthesize.py` and "
        "writing the copy, without the shared steps of the next table.",
        "",
        "| Dataset | Level | Filter | Rules kept | Used | kbgen parsed | Source triples | Ours triples | Ours time | kbgen triples | kbgen time |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    totals = {"ours": [0, 0.0], "kbgen": [0, 0.0]}
    provenance = []
    for r in status:
        d, level = r["dataset"], r["level"]
        filt = (
            f"std = {r['std_threshold']}"
            if r["std_threshold"]
            else f"PCA ≥ {r['pca_threshold']}"
        )
        if r["ours"].startswith(("no-rules", "same-as")):
            note = (
                "no rule: no copy"
                if r["ours"] == "no-rules"
                else f"same rules as {r['ours'][8:]}: no copy"
            )
            out.append(
                f"| {d} | {level} | {filt} | {r['rules_kept']} | {r['rules_used']} | | | {note} | | | |"
            )
            continue
        t = times.get((d, level), {})
        k = kbgen.get(f"{d}-{level}", {})
        source = t.get("source_triples") or k.get("input_triples")
        out.append(
            f"| {d} | {level} | {filt} | {r['rules_kept']} | {r['rules_used']} | {r['kbgen_parsed']} | {number(source)} | "
            f"{number(r['ours_triples'])} | {duration(r['ours_seconds'])} | {number(r['kbgen_triples'])} | "
            f"{duration(r['kbgen_seconds'])} |"
        )
        for method, seconds, valid in (
            ("ours", r["ours_seconds"], r["ours"]),
            ("kbgen", r["kbgen_seconds"], r["kbgen"]),
        ):
            if valid == "valid" and seconds:
                totals[method][0] += 1
                totals[method][1] += float(seconds)
        stats = HERE / "kbgen-work" / "rule-levels" / f"{d}-{level}" / "stats.json"
        provenance.append(
            f"| {d} | {level} | {t.get('finished', '')[:16]} | {t.get('commit', '')} | {r['ours']} | "
            f"{stamp(stats) if stats.is_file() else ''} | {r['kbgen']} |"
        )

    out += [
        "",
        "## Totals",
        "",
        "Sum of the copies' times; levels of kbgen ran several at a time, so its sum is CPU time, not wall-clock time.",
        "",
        "| Method | Copies | Sum of times |",
        "|---|---:|---:|",
        *(f"| {m} | {n} | {duration(s)} |" for m, (n, s) in totals.items()),
        "",
        "## kbgen's shared steps",
        "",
        "Made once per dataset and shared by its levels: the N-Triples file, the tensor and the models M1 and M2.",
        "",
        "| Dataset | Time | Made |",
        "|---|---:|---|",
    ]
    for d in dict.fromkeys(r["dataset"] for r in status):
        timings = HERE / "kbgen-work" / d / "timings.json"
        if timings.is_file():
            seconds = json.loads(timings.read_text())
            out.append(
                f"| {d} | {duration(sum(seconds.get(k, 0) for k in SHARED_STEPS))} | {stamp(timings)} |"
            )
    out += [
        "",
        "## Provenance",
        "",
        "When each copy was finished, from which commit of this repository (ours; `+dirty`: with uncommitted changes, "
        "listed in README.md), and the verdict of `check_copies.py`.",
        "",
        "| Dataset | Level | Ours finished | Ours commit | Ours check | kbgen finished | kbgen check |",
        "|---|---|---|---|---|---|---|",
        *provenance,
        "",
    ]
    (HERE / "REPORT.md").write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {HERE / 'REPORT.md'}")


if __name__ == "__main__":
    main()
