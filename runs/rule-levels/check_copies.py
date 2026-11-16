"""Checks the rule-level copies of this generator (ours) and kbgen, and plans what
reproduce.sh still has to make.

For every dataset and level of `exp_datasets/rule_levels.tsv`:

- The rules this generator uses at the level are computed with
  `skgg.cli.rule_levels.dataset_levels`, as `cli/main.py` selects them (the complete
  rules left after cycle removal plus every partial rule).
- A level whose rules are those of another level (`ours_same_as`) is not generated:
  `same-as:<level>`, or `no-rules` when it uses no rule at all.
- Our copy `<d>_ours_<L>/<d>_ours_<L>.tsv` is valid if its `_used.csv` holds exactly
  those rule IDs, and `ours/times.tsv` has a row for it with its triple count and an
  existing log.
- kbgen's copy `<d>_kbgen_<L>/<d>_kbgen_<L>.tsv` is valid if the rules file kbgen
  learned M3 from (`<work>/rule-levels/<d>-<L>/<d>-<L>-AmieRules.txt`), mapped back to
  rule IDs, holds exactly those rule IDs, and its `stats.json` has the copy's triple
  count.

`--import-legacy DIR` first copies the record (stats, timings, logs, rules file) of
kbgen levels that are valid in another work folder, such as an earlier run's, into
`--work`, so that the summary of this run includes them, together with the
dataset's shared steps (tensor, M1, M2), so that the levels this run makes for that
dataset use the same M2 as the imported ones.

Writes `status.tsv`, and exits with status 1 if a copy that should exist is missing
or invalid. `--plan ours` or `--plan kbgen` prints instead, one line per group, the
levels and datasets left to make with that method: `<levels>\t<datasets>`.

    python runs/rule-levels/check_copies.py
    python runs/rule-levels/check_copies.py --plan kbgen --datasets french1_N frenchN_M
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

from skgg.cli.rule_levels import DEFAULT_SHARES, dataset_levels

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEVELS = ["std1", "r25", "r50", "r75", "r100"]
# The namespace kbgen's driver puts the bare terms of the rules under.
KBGEN_NAMESPACE = "http://example.org/"
LEGACY_FILES = ("stats.json", "timings.json", "pipeline.log", "kbgen.log")
COLUMNS = [
    "dataset",
    "level",
    "pca_threshold",
    "std_threshold",
    "rules_kept",
    "rules_used",
    "ours",
    "ours_triples",
    "ours_seconds",
    "ours_derived",
    "ours_partial_heads",
    "kbgen",
    "kbgen_given",
    "kbgen_parsed",
    "kbgen_triples",
    "kbgen_seconds",
    "kbgen_rule_facts",
]
KBGEN_COLUMNS = [
    "kbgen",
    "kbgen_given",
    "kbgen_parsed",
    "kbgen_triples",
    "kbgen_seconds",
    "kbgen_rule_facts",
]
OURS_COLUMNS = [
    "ours",
    "ours_triples",
    "ours_seconds",
    "ours_derived",
    "ours_partial_heads",
]
# Lines of our generator's log: the triples each completion added, and the head
# triples the partial rules placed in the EDB (logged since commit 226bb8c).
COMPLETION_LINE = re.compile(r"\] \+(\d+) triples in \d+ passes")
PARTIAL_HEADS_LINE = re.compile(
    r"\[Partial\] Head triples placed by the groundings of partial rules: (\d+)\."
)
# kbgen's level steps, whose sum is a level's time (as total_seconds of its summary).
KBGEN_STEPS = (
    "tsv_to_nt",
    "csv_to_amie",
    "load_tensor",
    "learn_M1",
    "learn_M2",
    "learn_M3",
    "synthesize",
    "write_output",
)

logger = logging.getLogger("check_copies")


def load_format_change() -> ModuleType:
    """scripts/format_change.py, which kbgen's driver converts the rules' terms with."""
    path = REPO / "scripts" / "format_change.py"
    spec = importlib.util.spec_from_file_location("format_change", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def read_ids(used: Path) -> set[str]:
    with used.open(encoding="utf-8", newline="") as f:
        return {row["rule_id"].strip() for row in csv.DictReader(f)}


class RuleTexts:
    """Maps the rules of a dataset's CSV, written as kbgen's driver writes them in
    its AMIE rules file, back to their rule_id."""

    def __init__(self, rules_csv: Path, fc: ModuleType) -> None:
        mapping = fc.build_term_mapping(KBGEN_NAMESPACE)
        self.ids: dict[str, str] = {}
        # Rules with std confidence below 1, which our generator uses as partial rules.
        self.partial: set[str] = set()
        with rules_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if float(row["std_confidence"]) < 1:
                    self.partial.add(row["rule_id"].strip())
                tokens = [
                    tok
                    if tok.startswith("?") or tok == "=>"
                    else fc.tsv_term_to_nt(tok, mapping)[1:-1]
                    for tok in row["rule"].split()
                ]
                self.ids["  ".join(tokens)] = row["rule_id"].strip()

    def given(self, amie_rules: Path) -> set[str]:
        """The rule IDs of an AMIE rules file written by kbgen's driver."""
        ids = set()
        lines = amie_rules.read_text(encoding="utf-8").splitlines()[1:]
        for line in lines:
            text = line.split("\t")[0]
            if text not in self.ids:
                raise ValueError(f"{amie_rules}: rule not in the CSV: {text}")
            ids.add(self.ids[text])
        return ids


def read_times(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """The last row of `ours/times.tsv` per dataset and level."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {
            (r["dataset"], r["level"]): r for r in csv.DictReader(f, delimiter="\t")
        }


def kbgen_seconds(stats: dict) -> float:
    return round(sum(stats["seconds"].get(k, 0) for k in KBGEN_STEPS), 1)


def check_ours(
    root: Path, d: str, level: str, expected: set[str], times: dict, partial: bool
) -> tuple[str, str, str, str, str]:
    """Status, triples, seconds, triples derived by completion and head triples
    placed by partial rules of our copy. `partial`: the level uses partial rules,
    so the log must give their head triples."""
    copy = root / f"{d}_ours_{level}" / f"{d}_ours_{level}.tsv"
    used = copy.with_name(f"{copy.stem}_used.csv")
    if not copy.is_file():
        return "missing", "", "", "", ""
    triples = str(count_lines(copy))
    if not used.is_file():
        return "invalid:no _used.csv", triples, "", "", ""
    ids = read_ids(used)
    if ids != expected:
        reason = f"used rules differ ({len(ids)} vs {len(expected)} expected)"
        return f"invalid:{reason}", triples, "", "", ""
    row = times.get((d, level))
    if row is None:
        return "invalid:no times row", triples, "", "", ""
    if row["triples"] != triples:
        return f"invalid:times row has {row['triples']} triples", triples, "", "", ""
    log = REPO / row["log"]
    if not log.is_file():
        return f"invalid:no log {row['log']}", triples, row["seconds"], "", ""
    text = log.read_text(encoding="utf-8", errors="replace")
    derived = str(sum(int(n) for n in COMPLETION_LINE.findall(text)))
    heads = PARTIAL_HEADS_LINE.findall(text)
    if partial and not heads:
        return (
            "invalid:log lacks the partial head count",
            triples,
            row["seconds"],
            derived,
            "",
        )
    return "valid", triples, row["seconds"], derived, heads[-1] if heads else "0"


def check_kbgen(
    root: Path, d: str, level: str, expected: set[str], folder: Path, texts: RuleTexts
) -> tuple[str, str, str, str, str]:
    """Status, given rules, parsed rules, triples, seconds and facts added by rules
    of a kbgen copy whose record is in `folder` (`<work>/rule-levels/<d>-<L>`)."""
    copy = root / f"{d}_kbgen_{level}" / f"{d}_kbgen_{level}.tsv"
    if not copy.is_file():
        return "missing", "", "", "", "", ""
    triples = str(count_lines(copy))
    stats_file = folder / "stats.json"
    amie = folder / f"{d}-{level}-AmieRules.txt"
    if not stats_file.is_file() or not amie.is_file():
        return "invalid:no run record", "", "", triples, "", ""
    stats = json.loads(stats_file.read_text())
    given = texts.given(amie)
    parsed = str(stats.get("parsed_rules", ""))
    seconds = str(kbgen_seconds(stats))
    rule_facts = "" if stats.get("rule_facts") is None else str(stats["rule_facts"])
    if given != expected:
        return (
            f"invalid:given rules differ ({len(given)} vs {len(expected)} expected)",
            str(len(given)),
            parsed,
            triples,
            seconds,
            rule_facts,
        )
    if "given_rule_ids" in stats and set(stats["given_rule_ids"]) != expected:
        return (
            "invalid:stats given_rule_ids differ",
            str(len(given)),
            parsed,
            triples,
            seconds,
            rule_facts,
        )
    if str(stats["synthetic_triples"]) != triples:
        return (
            f"invalid:stats has {stats['synthetic_triples']} triples",
            str(len(given)),
            parsed,
            triples,
            seconds,
            rule_facts,
        )
    if not rule_facts:
        return (
            "invalid:no count of rule facts",
            str(len(given)),
            parsed,
            triples,
            seconds,
            "",
        )
    return "valid", str(len(given)), parsed, triples, seconds, rule_facts


def import_legacy(legacy: Path, work: Path, d: str, level: str) -> None:
    """Copies the record of one kbgen level, and its dataset's shared steps, from
    the work folder `legacy` into `work`."""
    src = legacy / "rule-levels" / f"{d}-{level}"
    dst = work / "rule-levels" / f"{d}-{level}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in (*LEGACY_FILES, f"{d}-{level}-AmieRules.txt"):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
    # The dataset's shared steps (tensor, M1, M2): the imported level was synthesized
    # from this M2, and the levels made in this run must use the same one.
    if not (work / d).exists():
        shutil.copytree(legacy / d, work / d, symlinks=True)
        logger.info("%s: imported kbgen's shared steps from %s", d, legacy / d)
    link_shared(work, d, level)
    logger.info("%s %s: imported kbgen's record from %s", d, level, src)


def link_shared(work: Path, d: str, level: str) -> None:
    """Links the dataset's tensor and M2 into a level's folder, as the driver's
    `link_base` does; its summary reads the level's tensor."""
    folder = work / "rule-levels" / f"{d}-{level}"
    for name, target in (
        (f"{d}-{level}.npz", f"{d}.npz"),
        (f"{d}-{level}-M2.pkl", f"{d}-M2.pkl"),
    ):
        link = folder / name
        if not link.exists() and (work / d / target).is_file():
            link.unlink(missing_ok=True)
            link.symlink_to(Path("..", "..", d, target))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=REPO / "exp_datasets")
    p.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        help="default: every dataset of rule_levels.tsv",
    )
    p.add_argument("--levels", nargs="+", default=LEVELS)
    p.add_argument("--times", type=Path, default=HERE / "ours" / "times.tsv")
    p.add_argument(
        "--work",
        type=Path,
        default=HERE / "kbgen-work",
        help="kbgen's work folder of this run",
    )
    p.add_argument(
        "--import-legacy",
        type=Path,
        default=None,
        metavar="DIR",
        help="kbgen work folder of an earlier run to import valid levels from",
    )
    p.add_argument("-o", "--output", type=Path, default=HERE / "status.tsv")
    p.add_argument("--plan", choices=["ours", "kbgen"], default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("skgg").setLevel(logging.WARNING)

    with (args.root / "rule_levels.tsv").open(encoding="utf-8", newline="") as f:
        table = {
            (r["dataset"], r["level"]): r for r in csv.DictReader(f, delimiter="\t")
        }
    datasets = args.datasets or list(dict.fromkeys(d for d, _ in table))
    fc = load_format_change()
    times = read_times(args.times)

    rows = []
    for d in datasets:
        levels = {
            lv.name: lv for lv in dataset_levels(args.root / d, list(DEFAULT_SHARES))[1]
        }
        texts = RuleTexts(args.root / d / f"{d}.csv", fc)
        for level in args.levels:
            row = table[(d, level)]
            expected = levels[level].ours
            if len(expected) != int(row["rules_ours"]):
                raise SystemExit(
                    f"{d} {level}: rule_levels.tsv says {row['rules_ours']} rules, "
                    f"the computation gives {len(expected)}; run skgg.cli.rule_levels"
                )
            out = {
                "dataset": d,
                "level": level,
                "pca_threshold": row["pca_threshold"],
                "std_threshold": row["std_threshold"],
                "rules_kept": row["rules_kept"],
                "rules_used": len(expected),
            }
            if row["ours_same_as"]:
                stand_in = (
                    "no-rules" if not expected else f"same-as:{row['ours_same_as']}"
                )
                out.update(ours=stand_in, kbgen=stand_in)
                rows.append(out)
                continue
            ours = check_ours(
                args.root, d, level, expected, times, bool(expected & texts.partial)
            )
            folder = args.work / "rule-levels" / f"{d}-{level}"
            kbgen = check_kbgen(args.root, d, level, expected, folder, texts)
            if kbgen[0] != "valid" and args.import_legacy is not None:
                legacy = args.import_legacy / "rule-levels" / f"{d}-{level}"
                legacy_status = check_kbgen(
                    args.root, d, level, expected, legacy, texts
                )[0]
                if legacy_status == "valid":
                    import_legacy(args.import_legacy, args.work, d, level)
                    kbgen = check_kbgen(args.root, d, level, expected, folder, texts)
                elif kbgen[0] != "missing" and legacy_status.startswith("invalid:"):
                    kbgen = (f"{kbgen[0]}; earlier run {legacy_status[8:]}", *kbgen[1:])
            if kbgen[0] == "valid":
                link_shared(args.work, d, level)
            out.update(dict(zip(OURS_COLUMNS, ours, strict=True)))
            out.update(dict(zip(KBGEN_COLUMNS, kbgen, strict=True)))
            rows.append(out)
            logger.info("%-13s %-5s ours: %-40s kbgen: %s", d, level, ours[0], kbgen[0])

    if args.plan:
        groups: dict[tuple[str, ...], list[str]] = {}
        for d in datasets:
            todo = tuple(
                r["level"]
                for r in rows
                if r["dataset"] == d
                and r[args.plan] != "valid"
                and not r[args.plan].startswith(("no-rules", "same-as"))
            )
            if todo:
                groups.setdefault(todo, []).append(d)
        for todo, names in groups.items():
            print(" ".join(todo) + "\t" + " ".join(names))
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    bad = [
        r
        for r in rows
        for m in ("ours", "kbgen")
        if not r[m].startswith(("valid", "no-rules", "same-as"))
    ]
    logger.info(
        "Wrote %s: %d levels, %d copies missing or invalid",
        args.output,
        len(rows),
        len(bad),
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
