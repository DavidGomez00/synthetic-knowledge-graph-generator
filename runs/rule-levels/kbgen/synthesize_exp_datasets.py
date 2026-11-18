#!/usr/bin/env python3
"""Synthesizes a kbgen (model M3) copy of every benchmark dataset.

For every folder `D` under the root that has `D/D.unified.tsv` (the graph) and
`D/D.csv` (its AMIE rules, as written by the generator's `mine_rules/run_amie.py`),
and that is not a copy (`D_ours`, `D_kbgen`, or a level copy such as `D_kbgen_r25`),
this runs the original kbgen
pipeline in `work/D/`:

    1. D.unified.tsv -> D.nt            (format_change.py, namespace http://example.org/)
    2. D.csv         -> D-AmieRules.txt (AMIE text format, relations as full IRIs)
    3. load_tensor.py D.nt              -> D.npz
    4. learn_model.py D.npz -m M1 / M2 / M3 -r D-AmieRules.txt -> D-M1/2/3.pkl
    5. synthesize.py D-M3.pkl D.kbgenm3.nt -s SIZE            -> D.kbgenm3.nt/.tsv
    6. D.kbgenm3.tsv -> <root>/D_kbgen/D_kbgen.tsv  (Entity_N <TAB> relation name <TAB> Entity_M,
                                                     without kbgen's schema triples)
    7. work/D/stats.json, merged into results/summary.csv and results/relation_counts.csv;
       entity degree skew of the kept and synthetic graphs in results/degree_stats.csv

Each step is skipped when its output exists (unless --force), so a batch can be
resumed. kbgen's scripts run as subprocesses with `work/D` as working directory,
so their log files stay per dataset; their output goes to `work/D/pipeline.log`.

With --levels, the copies are made at rule levels instead: for every dataset and
level L of the generator's `exp_datasets/rule_levels.tsv` (written by
`python -m skgg.cli.rule_levels`), M3 is learned only from the rules with
`pca_confidence >= pca_threshold` of that row (none for r0; for a row with a
`std_threshold`, such as std1, the rules with `std_confidence >= std_threshold`), in
`work/rule-levels/D-L/`, and the copy goes to
`<level root>/D_kbgen_L/D_kbgen_L.tsv` (default level root: `<root>`, so the
copy sits next to `D` and `D_kbgen`). The N-Triples file, tensor, M1 and M2 don't depend on the
rules: they are made once in `work/D/` and linked into each level's folder, so
every level, and the existing `D_kbgen` copy, uses the same M2. A row whose
`kbgen_same_as` is set has the same rules as the level it names and is skipped,
and so is r100, which is the existing `D_kbgen` copy.

With --rules-from-ours, kbgen is given at each level exactly the rules this
project's generator used for that level, the rule_ids of
`<level root>/D_ours_L/D_ours_L_used.csv` (none for a level that keeps no rule), so
that both generators see the same rules. Every level is then made, r100 included
(as `D_kbgen_r100`), the used file must have the row's `rules_ours` rules, and a row
whose `ours_same_as` is set is skipped, as the generator skips it. --force redoes the level
steps only, never `work/D/`. The summary goes to `<results dir>/rule-levels/`.
The generator's `_used.csv` files are read from `<root>` whatever `--level-root` is,
or from `--used-rules-dir` (`<dir>/D_ours_L_used.csv`) for rules selected without
making the generator's copy (runs/rule-levels-entities/select_rules.py).

By default kbgen synthesizes as many entities as the subjects of the graph it loads
(its `n_entities`). With --entities source, synthesize.py gets `-ne N`, N being the
unique entities of the distinct triples of D.unified.tsv, the number PyGraft is given
as num_entities. The summary records both (`entities_mode`, `desired_entities`).

Every copy keeps the logs and records of the run that made it, next to it:
`<copy>.log`, `<copy>.base.log`, `<copy>.stats.json` and `<copy>.rules.txt`
(archive_logs). --archive-only copies them for the runs of --work-dir already made.

    ~/.pyenv/versions/NeSy/bin/python synthesize_exp_datasets.py --levels r0 r25 r50 r75 --jobs 4

Needs the Python environment that runs kbgen (rdflib, numpy, scipy, tqdm,
matplotlib), e.g.:

    ~/.pyenv/versions/NeSy/bin/python synthesize_exp_datasets.py --datasets french
    ~/.pyenv/versions/NeSy/bin/python synthesize_exp_datasets.py --jobs 4
    ~/.pyenv/versions/NeSy/bin/python synthesize_exp_datasets.py --summary-only
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import numpy as np

KBGEN_DIR = Path(__file__).resolve().parent
# This copy sits in runs/rule-levels/kbgen/ of the generator's repository.
DEFAULT_ROOT = KBGEN_DIR.parents[2] / "exp_datasets"
NAMESPACE = "http://example.org/"
SYNTH_PREFIX = "http://dws.uni-mannheim.de/synthesized/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
# Copies of a dataset D: D_ours, D_kbgen, and their level copies such as D_kbgen_r25.
COPY_NAME = re.compile(r"_(ours|kbgen)(_r\d+|_std1)?$")
AMIE_HEADER = "\t".join([
    "Rule", "Head Coverage", "Std Confidence", "PCA Confidence", "Positive Examples",
    "Body size", "PCA Body size", "Functional variable",
])

logger = logging.getLogger("synthesize_exp_datasets")


def load_format_change(path: Path):
    spec = importlib.util.spec_from_file_location("format_change", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["format_change"] = module
    spec.loader.exec_module(module)
    return module


class Dataset:
    """A dataset's files, at all its rules (level None) or at one rule level, whose
    copy only keeps the rules with std confidence >= std_threshold if that is set,
    else those with PCA confidence >= threshold (no rule if None)."""

    def __init__(self, root: Path, name: str, work_root: Path, level: str | None = None,
                 threshold: Decimal | None = None, level_root: Path | None = None,
                 std_threshold: Decimal | None = None, rule_ids: set[str] | None = None,
                 rules_from: Path | None = None):
        self.name = name
        self.level = level
        self.threshold = threshold
        self.std_threshold = std_threshold
        # With --rules-from-ours: the rule_ids given to kbgen, read from `rules_from`.
        self.rule_ids = rule_ids
        self.rules_from = rules_from
        self.tag = name if level is None else f"{name}-{level}"
        self.folder = root / name
        self.triples = self.folder / f"{name}.unified.tsv"
        self.rules_csv = self.folder / f"{name}.csv"
        # The steps that don't depend on the rules always run in work/D.
        self.base_work = work_root / name
        self.nt = self.base_work / f"{name}.nt"
        self.base_npz = self.base_work / f"{name}.npz"
        self.base_models = {m: self.base_work / f"{name}-{m}.pkl" for m in ("M1", "M2")}
        self.work = self.base_work if level is None else work_root / "rule-levels" / self.tag
        self.amie = self.work / f"{self.tag}-AmieRules.txt"
        self.npz = self.work / f"{self.tag}.npz"
        self.models = {m: self.work / f"{self.tag}-{m}.pkl" for m in ("M1", "M2", "M3")}
        self.synth_nt = self.work / f"{self.tag}.kbgenm3.nt"
        self.synth_tsv = self.work / f"{self.tag}.kbgenm3.tsv"
        out_root = root if level is None else level_root or root
        copy = f"{name}_kbgen" if level is None else f"{name}_kbgen_{level}"
        self.output = out_root / copy / f"{copy}.tsv"
        self.stats = self.work / "stats.json"
        self.timings = self.work / "timings.json"
        self.log = self.work / "pipeline.log"


def find_datasets(root: Path, work_root: Path, names: list[str] | None) -> list[Dataset]:
    found = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if COPY_NAME.search(folder.name):
            continue
        ds = Dataset(root, folder.name, work_root)
        if ds.triples.is_file() and ds.rules_csv.is_file():
            found[ds.name] = ds
        else:
            logger.warning("Skipping %s: no %s or %s", folder.name, ds.triples.name, ds.rules_csv.name)
    if names:
        unknown = sorted(set(names) - set(found))
        if unknown:
            raise SystemExit(f"Not datasets under {root}: {', '.join(unknown)}")
        found = {n: d for n, d in found.items() if n in names}
    # smallest first, so quick datasets finish (and fail) early
    return sorted(found.values(), key=lambda d: d.triples.stat().st_size)


def ours_level_datasets(levels_file: Path, datasets: list[Dataset], levels: list[str], root: Path,
                        work_root: Path, level_root: Path | None,
                        used_rules_dir: Path | None = None) -> list[Dataset]:
    """The level copies to make with --rules-from-ours: each dataset at each level of
    `levels`, with the rules this project's generator used at that level, read from
    `<used_rules_dir>/D_ours_L_used.csv` if `used_rules_dir` is set."""
    with levels_file.open(encoding="utf-8", newline="") as f:
        rows = {(r["dataset"], r["level"]): r for r in csv.DictReader(f, delimiter="\t")}
    todo = []
    for ds in datasets:
        with ds.rules_csv.open(encoding="utf-8", newline="") as f:
            known = {r["rule_id"].strip() for r in csv.DictReader(f)}
        for level in levels:
            row = rows.get((ds.name, level))
            if row is None:
                raise SystemExit(f"{levels_file} has no level {level} for {ds.name}")
            tag = f"{ds.name}-{level}"
            if row["ours_same_as"]:
                logger.info("[%s] skipped: the generator uses the rules of %s", tag, row["ours_same_as"])
                continue
            copy = f"{ds.name}_ours_{level}"
            # The generator's copies stay next to their dataset, whatever the level root.
            used = (used_rules_dir or root / copy) / f"{copy}_used.csv"
            if row["rules_kept"] == "0":
                ids, used = set(), None
            elif not used.is_file():
                raise SystemExit(f"{tag}: no {used}; make the generator's copy first")
            else:
                with used.open(encoding="utf-8", newline="") as f:
                    ids = {r["rule_id"].strip() for r in csv.DictReader(f)}
            if len(ids) != int(row["rules_ours"]):
                raise SystemExit(f"{tag}: {used} has {len(ids)} rules, {levels_file} says "
                                 f"rules_ours {row['rules_ours']}")
            if ids - known:
                raise SystemExit(f"{tag}: {used} has rules not in {ds.rules_csv}: {sorted(ids - known)}")
            todo.append(Dataset(root, ds.name, work_root, level, None, level_root,
                                rule_ids=ids, rules_from=used))
    return todo


def level_datasets(levels_file: Path, datasets: list[Dataset], levels: list[str], root: Path,
                   work_root: Path, level_root: Path | None) -> list[Dataset]:
    """The level copies to make: each dataset at each level of `levels`, in the order
    of `datasets`, leaving out a level with the rules of another (kbgen_same_as) and a
    level that keeps every rule, which is the existing D_kbgen copy."""
    with levels_file.open(encoding="utf-8", newline="") as f:
        rows = {(r["dataset"], r["level"]): r for r in csv.DictReader(f, delimiter="\t")}
    unknown = sorted(set(levels) - {level for _, level in rows})
    if unknown:
        raise SystemExit(f"Levels not in {levels_file}: {', '.join(unknown)}")
    todo = []
    for ds in datasets:
        with ds.rules_csv.open(encoding="utf-8", newline="") as f:
            rules = list(csv.DictReader(f))
        pca = [Decimal(r["pca_confidence"]) for r in rules]
        std = [Decimal(r["std_confidence"]) for r in rules]
        for level in levels:
            row = rows.get((ds.name, level))
            if row is None:
                raise SystemExit(f"{levels_file} has no level {level} for {ds.name}")
            tag = f"{ds.name}-{level}"
            if row["kbgen_same_as"]:
                logger.info("[%s] skipped: same rules as %s", tag, row["kbgen_same_as"])
                continue
            threshold = Decimal(row["pca_threshold"]) if row["pca_threshold"] else None
            std_threshold = Decimal(row["std_threshold"]) if row.get("std_threshold") else None
            if std_threshold is not None:
                kept = sum(value >= std_threshold for value in std)
                condition = f"std >= {std_threshold}"
            else:
                kept = 0 if threshold is None else sum(value >= threshold for value in pca)
                condition = f"PCA >= {threshold}"
            if kept != int(row["rules_kept"]):
                raise SystemExit(f"{tag}: {levels_file} says {row['rules_kept']} rules, "
                                 f"{ds.rules_csv} has {kept} at {condition}; rerun skgg.cli.rule_levels")
            if kept == len(pca) and kept:
                logger.info("[%s] skipped: every rule, the copy is %s_kbgen", tag, ds.name)
                continue
            todo.append(Dataset(root, ds.name, work_root, level, threshold, level_root, std_threshold))
    return todo


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, ds: Dataset, fc, python: str, size: float, force: bool, entities: str = "kept"):
        self.ds, self.fc, self.python, self.size, self.force = ds, fc, python, size, force
        # "kept": kbgen's own entity count (the subjects of the graph it loads);
        # "source": synthesize.py -ne with the unique entities of D.unified.tsv.
        self.entities = entities
        self._source_entities: int | None = None
        self.term_mapping = fc.build_term_mapping(NAMESPACE)
        ds.base_work.mkdir(parents=True, exist_ok=True)
        ds.work.mkdir(parents=True, exist_ok=True)
        self.times = json.loads(ds.timings.read_text()) if ds.timings.is_file() and not force else {}

    def note(self, msg: str) -> None:
        logger.info("[%s] %s", self.ds.tag, msg)
        with self.ds.log.open("a", encoding="utf-8") as f:
            f.write(f"### {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def step(self, key: str, outputs: list[Path], action) -> None:
        if not self.force and all(p.is_file() for p in outputs):
            self.note(f"{key}: skipped, output exists")
            return
        self.note(f"{key}: start")
        start = time.time()
        try:
            action()
        except BaseException:
            for p in outputs:
                p.unlink(missing_ok=True)
            raise
        missing = [p.name for p in outputs if not p.is_file()]
        if missing:
            raise RuntimeError(f"{key} did not write {', '.join(missing)}")
        self.times[key] = round(time.time() - start, 1)
        self.ds.timings.write_text(json.dumps(self.times, indent=2))
        self.note(f"{key}: done in {self.times[key]} s")

    def kbgen(self, script: str, *args: str, cwd: Path | None = None) -> None:
        cmd = [self.python, str(KBGEN_DIR / script), *args]
        with self.ds.log.open("a", encoding="utf-8") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            subprocess.run(cmd, cwd=cwd or self.ds.work, stdout=log, stderr=subprocess.STDOUT, check=True,
                           env={**os.environ, "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"})

    def source_entities(self) -> int:
        """The unique entities (subjects and objects) of the distinct triples of
        D.unified.tsv, the number PyGraft was given as num_entities."""
        if self._source_entities is None:
            seen, entities = set(), set()
            for _, t in self.fc.read_tsv(self.ds.triples):
                if t is None or t in seen:
                    continue
                seen.add(t)
                entities.update((t[0], t[2]))
            self._source_entities = len(entities)
        return self._source_entities

    def synthesize_args(self) -> list[str]:
        args = [self.ds.models["M3"].name, self.ds.synth_nt.name, "-s", str(self.size)]
        if self.entities == "source":
            args += ["-ne", str(self.source_entities())]
        return args

    def link_base(self) -> None:
        """Links work/D's tensor and M2 into the level's folder under the level's names,
        which learn_model.py derives the M3 file name from."""
        ds = self.ds
        for link, target in ((ds.npz, ds.base_npz), (ds.models["M2"], ds.base_models["M2"])):
            link.unlink(missing_ok=True)
            link.symlink_to(os.path.relpath(target, link.parent))

    def keeps_rule(self, row: dict[str, str]) -> bool:
        """Whether the dataset's level keeps a row of the rules CSV."""
        if self.ds.level is None:
            return True
        if self.ds.rule_ids is not None:
            return row["rule_id"].strip() in self.ds.rule_ids
        if self.ds.std_threshold is not None:
            return Decimal(row["std_confidence"]) >= self.ds.std_threshold
        return self.ds.threshold is not None and Decimal(row["pca_confidence"]) >= self.ds.threshold

    def convert_triples(self) -> None:
        written, duplicates, invalid = self.fc.convert(self.ds.triples, self.ds.nt, NAMESPACE)
        self.note(f"tsv->nt: {written} triples, {duplicates} duplicates, {invalid} invalid lines")

    def convert_rules(self) -> None:
        lines = [AMIE_HEADER]
        total = 0
        with self.ds.rules_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                total += 1
                if not self.keeps_rule(row):
                    continue
                tokens = []
                for tok in row["rule"].split():
                    if tok.startswith("?") or tok == "=>":
                        tokens.append(tok)
                    else:  # a relation: bare IRI, as in AMIE's own output
                        tokens.append(self.fc.tsv_term_to_nt(tok, self.term_mapping)[1:-1])
                lines.append("\t".join([
                    "  ".join(tokens), row["head_coverage"], row["std_confidence"], row["pca_confidence"],
                    row["positive_examples"], row["body_size"], row["pca_body_size"],
                    row["functional_variable"],
                ]))
        self.ds.amie.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.note(f"csv->amie: {len(lines) - 1} of {total} rules")

    def relation_names(self, model) -> dict[str, str]:
        from util import URIRelation
        return {
            str(URIRelation(i).uri): self.fc.nt_term_to_tsv(f"<{uri}>", self.term_mapping)
            for uri, i in model.rel_dict.items()
        }

    def write_output(self) -> None:
        with self.ds.models["M3"].open("rb") as f:
            model = pickle.load(f)
        names = self.relation_names(model)
        schema = written = 0
        self.ds.output.parent.mkdir(parents=True, exist_ok=True)
        with self.ds.synth_tsv.open(encoding="utf-8") as src, self.ds.output.open("w", encoding="utf-8") as out:
            for line in src:
                s, p, o = line.rstrip("\n").split("\t")
                if p == RDF_TYPE:
                    schema += 1
                    continue
                row = (s.removeprefix(SYNTH_PREFIX), names[p], o.removeprefix(SYNTH_PREFIX))
                if any("http" in t or "/" in t for t in (row[0], row[2])):
                    raise RuntimeError(f"unmapped term in synthetic triple: {line.strip()}")
                out.write("\t".join(row) + "\n")
                written += 1
        self.note(f"output: {written} facts written, {schema} schema triples dropped")

    def run_base(self) -> None:
        """The steps that don't depend on the rules, in work/D."""
        ds = self.ds
        self.step("tsv_to_nt", [ds.nt], self.convert_triples)
        self.step("load_tensor", [ds.base_npz],
                  lambda: self.kbgen("load_tensor.py", ds.nt.name, cwd=ds.base_work))
        self.step("learn_M1", [ds.base_models["M1"]],
                  lambda: self.kbgen("learn_model.py", ds.base_npz.name, "-m", "M1", cwd=ds.base_work))
        self.step("learn_M2", [ds.base_models["M2"]],
                  lambda: self.kbgen("learn_model.py", ds.base_npz.name, "-m", "M2", cwd=ds.base_work))

    def run(self) -> None:
        """Every step. A level's run expects run_base to have run for its dataset, so
        that levels of one dataset never write work/D at the same time."""
        ds = self.ds
        if ds.level is None:
            self.run_base()
        else:
            self.step("link_base", [ds.npz, ds.models["M2"]], self.link_base)
        self.step("csv_to_amie", [ds.amie], self.convert_rules)
        self.step("learn_M3", [ds.models["M3"]],
                  lambda: self.kbgen("learn_model.py", ds.npz.name, "-m", "M3", "-r", ds.amie.name))
        self.step("synthesize", [ds.synth_nt, ds.synth_tsv],
                  lambda: self.kbgen("synthesize.py", *self.synthesize_args()))
        self.step("write_output", [ds.output], self.write_output)
        self.step("stats", [ds.stats], self.write_stats)
        archive_logs(ds.work, ds.base_work, json.loads(ds.stats.read_text()))

    # -----------------------------------------------------------------------
    def write_stats(self) -> None:
        ds, fc = self.ds, self.fc

        inp, inp_ents = Counter(), set()
        seen = set()
        for _, t in fc.read_tsv(ds.triples):
            if t is None or t in seen:
                continue
            seen.add(t)
            inp[t[1]] += 1
            inp_ents.update((t[0], t[2]))
        seen = None

        npz = np.load(ds.npz, allow_pickle=True)
        rel_dict = npz["relations_dict"].item()
        data = npz["data"]
        kept = Counter()
        kept_ents = set()
        for uri, i in rel_dict.items():
            m = data[i]
            kept[fc.nt_term_to_tsv(f"<{uri}>", self.term_mapping)] = int(m.nnz)
            kept_ents.update(m.row.tolist())
            kept_ents.update(m.col.tolist())
        model_entities = int(npz["types"].item().shape[0])

        syn, syn_ents = Counter(), set()
        with ds.output.open(encoding="utf-8") as f:
            for line in f:
                s, p, o = line.rstrip("\n").split("\t")
                syn[p] += 1
                syn_ents.update((s, o))

        with ds.models["M3"].open("rb") as f:
            model = pickle.load(f)
        with ds.rules_csv.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        csv_rules = len(rows)
        counts_file = ds.synth_nt.with_name(ds.synth_nt.name.replace(".nt", ".counts.json"))
        counts = json.loads(counts_file.read_text()) if counts_file.is_file() else {}

        n_in, n_kept, n_syn = sum(inp.values()), sum(kept.values()), sum(syn.values())
        relations = sorted(set(inp) | set(kept) | set(syn))
        tvd = 0.5 * sum(abs(kept[r] / n_kept - syn[r] / n_syn) for r in relations) if n_kept and n_syn else None
        stats = {
            "dataset": ds.tag,
            "level": ds.level,
            "pca_threshold": None if ds.threshold is None else str(ds.threshold),
            "std_threshold": None if ds.std_threshold is None else str(ds.std_threshold),
            "rules_from": None if ds.rules_from is None else str(ds.rules_from),
            "given_rules": sum(1 for row in rows if self.keeps_rule(row)),
            "given_rule_ids": [row["rule_id"].strip() for row in rows if self.keeps_rule(row)],
            "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            "output": str(ds.output),
            "input_triples": n_in,
            "input_entities": len(inp_ents),
            "input_relations": len(inp),
            "kept_triples": n_kept,
            "dropped_share": round(1 - n_kept / n_in, 4),
            "kept_entities": len(kept_ents),
            "model_entities": model_entities,
            "entities_mode": self.entities,
            "desired_entities": self.source_entities() if self.entities == "source" else model_entities,
            "kept_relations": sum(1 for r in kept if kept[r]),
            "synthetic_triples": n_syn,
            "synthetic_vs_kept": round(n_syn / n_kept, 4) if n_kept else None,
            "synthetic_entities": len(syn_ents),
            "synthetic_relations": len(syn),
            "csv_rules": csv_rules,
            "parsed_rules": len(model.rules.rules),
            # Facts added by a rule firing during synthesis (synthesize.py's counts file);
            # the other facts of the copy were sampled from the model's distributions.
            "rule_facts": counts.get("count_rule_facts"),
            "model_counts": counts,
            "relations_lost_in_load": sorted(r for r in inp if not kept[r]),
            "relations_lost_in_synthesis": sorted(r for r in kept if kept[r] and not syn[r]),
            "tvd_kept_vs_synthetic": round(tvd, 4) if tvd is not None else None,
            "seconds": dict(self.times),
            "relations": {r: {"input": inp[r], "kept": kept[r], "synthetic": syn[r]} for r in relations},
        }
        ds.stats.write_text(json.dumps(stats, indent=2))
        self.note(f"stats: {n_in} input, {n_kept} kept, {n_syn} synthetic triples; "
                  f"{stats['parsed_rules']}/{stats['given_rules']} rules parsed")


# ---------------------------------------------------------------------------
# Logs kept with each copy
# ---------------------------------------------------------------------------
def archive_logs(work: Path, base_work: Path, stats: dict) -> bool:
    """Copies the logs and records of the run that made a copy next to it, so the copy
    keeps them wherever --work-dir is: `<copy>.log` (the level's pipeline.log: kbgen's
    commands and output), `<copy>.base.log` (the dataset's pipeline.log: tensor, M1 and
    M2, when the copy is a level), `<copy>.stats.json` (stats.json, with the rule IDs
    given, the model's counters and the seconds of each step) and `<copy>.rules.txt`
    (the rules as kbgen read them). Skips a copy that no longer exists."""
    output = Path(stats["output"]) if "output" in stats else None
    if output is None or not output.is_file():
        logger.warning("[%s] no copy at %s, logs not archived", stats.get("dataset"), output)
        return False
    stem = output.with_suffix("")
    files = {work / "pipeline.log": stem.with_suffix(".log"),
             work / "stats.json": stem.with_suffix(".stats.json")}
    if base_work != work:
        files[base_work / "pipeline.log"] = stem.with_suffix(".base.log")
    rules = sorted(work.glob("*-AmieRules.txt"))
    if rules:
        files[rules[0]] = stem.with_suffix(".rules.txt")
    for source, target in files.items():
        if source.is_file():
            shutil.copyfile(source, target)
    return True


def archive_all(work_root: Path, levels: bool) -> None:
    """archive_logs for every run with a stats.json under `work_root`."""
    folders = sorted((work_root / "rule-levels" if levels else work_root).glob("*/stats.json"))
    done = 0
    for path in folders:
        stats = json.loads(path.read_text())
        base = work_root / stats["dataset"].rsplit("-", 1)[0] if levels else path.parent
        done += archive_logs(path.parent, base, stats)
    logger.info("Archived the logs of %d of %d copies", done, len(folders))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
STEP_KEYS = ["tsv_to_nt", "csv_to_amie", "load_tensor", "learn_M1", "learn_M2", "learn_M3", "synthesize",
             "write_output"]


def write_summary(work_root: Path, results_dir: Path, root: Path, levels: bool = False) -> None:
    """Merges the stats.json of every dataset folder of `work_root` (with `levels`,
    of `work_root`/rule-levels, into `results_dir`/rule-levels)."""
    if levels:
        work_root, results_dir = work_root / "rule-levels", results_dir / "rule-levels"
    stats, folders = [], []
    for path in sorted(work_root.glob("*/stats.json")):
        stats.append(json.loads(path.read_text()))
        folders.append(path.parent)
    if not stats:
        logger.warning("No stats.json under %s", work_root)
        return
    results_dir.mkdir(parents=True, exist_ok=True)
    level_columns = ["level", "pca_threshold", "std_threshold", "rules_from", "given_rules",
                     "given_rule_ids"] if levels else []
    columns = ["dataset", *level_columns, "input_triples", "kept_triples", "synthetic_triples", "dropped_share",
               "synthetic_vs_kept", "input_entities", "kept_entities", "model_entities", "entities_mode", "desired_entities",
               "synthetic_entities",
               "input_relations", "kept_relations", "synthetic_relations", "csv_rules", "parsed_rules",
               "rule_facts", "tvd_kept_vs_synthetic", "relations_lost_in_load", "relations_lost_in_synthesis",
               "total_seconds", *[f"seconds_{k}" for k in STEP_KEYS], "finished"]
    with (results_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for s in stats:
            row = {k: s.get(k) for k in columns}
            row["relations_lost_in_load"] = ";".join(s["relations_lost_in_load"])
            row["relations_lost_in_synthesis"] = ";".join(s["relations_lost_in_synthesis"])
            if levels:
                row["given_rule_ids"] = ";".join(s.get("given_rule_ids", []))
            row["total_seconds"] = round(sum(s["seconds"].get(k, 0) for k in STEP_KEYS), 1)
            for k in STEP_KEYS:
                row[f"seconds_{k}"] = s["seconds"].get(k)
            w.writerow(row)
    with (results_dir / "relation_counts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dataset", "relation", "input", "kept", "synthetic", "kept_share", "synthetic_share"])
        for s in stats:
            for r, c in s["relations"].items():
                kept_share = c["kept"] / s["kept_triples"] if s["kept_triples"] else 0
                syn_share = c["synthetic"] / s["synthetic_triples"] if s["synthetic_triples"] else 0
                w.writerow([s["dataset"], r, c["input"], c["kept"], c["synthetic"],
                            round(kept_share, 5), round(syn_share, 5)])
    logger.info("Wrote %s and %s (%d datasets)", results_dir / "summary.csv",
                results_dir / "relation_counts.csv", len(stats))

    with (results_dir / "degree_stats.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dataset",
                     "kept_max_degree", "kept_top1pct_degree_share", "kept_median_degree",
                     "synthetic_max_degree", "synthetic_top1pct_degree_share", "synthetic_median_degree"])
        for s, folder in zip(stats, folders):
            name = s["dataset"]
            output = Path(s["output"]) if "output" in s else root / f"{name}_kbgen" / f"{name}_kbgen.tsv"
            w.writerow([name, *degree_stats(folder / f"{name}.npz", output)])
    logger.info("Wrote %s", results_dir / "degree_stats.csv")


def degree_stats(npz_path: Path, output: Path) -> list:
    """Degree skew of the kept graph (kbgen's tensor) and of the synthetic copy.

    The degree of an entity is the number of triples it appears in, as subject or object.
    Returns, for each graph: max degree, share of all degree held by the top 1% of
    entities, and median degree.
    """
    def summarize(deg: Counter) -> list:
        v = np.sort(np.fromiter(deg.values(), dtype=np.int64))[::-1]
        top = max(1, round(len(v) * 0.01))
        return [int(v[0]), round(float(v[:top].sum() / v.sum()), 4), float(np.median(v))]

    kept = Counter()
    for m in np.load(npz_path, allow_pickle=True)["data"]:
        kept.update(m.row.tolist())
        kept.update(m.col.tolist())
    syn = Counter()
    with output.open(encoding="utf-8") as f:
        for line in f:
            s, _, o = line.rstrip("\n").split("\t")
            syn[s] += 1
            syn[o] += 1
    return summarize(kept) + summarize(syn)


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="folder with one subfolder per dataset")
    p.add_argument("--datasets", nargs="+", default=None, help="dataset folder names (default: all)")
    p.add_argument("--work-dir", type=Path, default=KBGEN_DIR / "work", help="intermediate files")
    p.add_argument("--results-dir", type=Path, default=KBGEN_DIR / "results", help="summary CSVs")
    p.add_argument("--format-change", type=Path, default=None,
                   help="path to format_change.py (default: <root>/../scripts/format_change.py)")
    p.add_argument("--python", default=sys.executable, help="interpreter that runs kbgen's scripts")
    p.add_argument("--jobs", type=int, default=1, help="datasets processed in parallel")
    p.add_argument("--size", type=float, default=1.0, help="synthesize.py -s (fraction of the original facts)")
    p.add_argument("--force", action="store_true", help="redo every step (with --levels, every level step)")
    p.add_argument("--summary-only", action="store_true", help="only rebuild the summary CSVs")
    p.add_argument("--archive-only", action="store_true",
                   help="only copy each run's logs and records next to its copy")
    p.add_argument("--levels", nargs="+", default=None,
                   help="make the copies at these rule levels of --rule-levels, e.g. r0 r25 r50 r75 "
                        "(default: the copies with all rules, <root>/D_kbgen)")
    p.add_argument("--rule-levels", type=Path, default=None,
                   help="rule levels file of skgg.cli.rule_levels (default: <root>/rule_levels.tsv)")
    p.add_argument("--rules-from-ours", action="store_true",
                   help="with --levels, give kbgen at each level the rules of this project's generator, "
                        "D_ours_L/D_ours_L_used.csv")
    p.add_argument("--entities", choices=["kept", "source"], default="kept",
                   help="entities to synthesize: kbgen's own count, the subjects of the graph it loads "
                        "(kept, the default), or the unique entities of D.unified.tsv, passed as "
                        "synthesize.py -ne (source)")
    p.add_argument("--used-rules-dir", type=Path, default=None,
                   help="with --rules-from-ours, read D_ours_L_used.csv from this folder "
                        "(default: <root>/D_ours_L/)")
    p.add_argument("--level-root", type=Path, default=None,
                   help="folder of the level copies D_kbgen_L (default: <root>)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.path.insert(0, str(KBGEN_DIR))
    levels = args.levels is not None

    if args.archive_only:
        archive_all(args.work_dir, levels)
        return 0
    if args.summary_only:
        write_summary(args.work_dir, args.results_dir, args.root, levels)
        return 0

    fc = load_format_change(args.format_change or args.root.parent / "scripts" / "format_change.py")
    datasets = find_datasets(args.root, args.work_dir, args.datasets)
    logger.info("Datasets: %s", ", ".join(d.name for d in datasets))

    failed = []

    def process(ds: Dataset, base_only: bool = False) -> None:
        try:
            runner = Runner(ds, fc, args.python, args.size, args.force and not base_only, args.entities)
            runner.run_base() if base_only else runner.run()
            logger.info("[%s] finished%s", ds.tag, " the steps shared by its levels" if base_only else "")
        except Exception:
            failed.append(ds.tag)
            logger.error("[%s] FAILED (see %s)\n%s", ds.tag, ds.log, traceback.format_exc())

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        if levels:
            # Every level of a dataset reads work/D, so it is made first, once.
            list(pool.map(lambda ds: process(ds, base_only=True), datasets))
            ready = [ds for ds in datasets if ds.tag not in failed]
            levels_file = args.rule_levels or args.root / "rule_levels.tsv"
            if args.rules_from_ours:
                todo = ours_level_datasets(levels_file, ready, args.levels, args.root, args.work_dir,
                                           args.level_root, args.used_rules_dir)
            else:
                todo = level_datasets(levels_file, ready, args.levels, args.root, args.work_dir,
                                      args.level_root)
            logger.info("Levels: %s", ", ".join(ds.tag for ds in todo))
            list(pool.map(process, todo))
        else:
            list(pool.map(process, datasets))

    write_summary(args.work_dir, args.results_dir, args.root, levels)
    if failed:
        logger.error("Failed datasets: %s", ", ".join(sorted(failed)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
