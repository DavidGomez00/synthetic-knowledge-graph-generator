"""Generates a synthetic copy of every benchmark dataset.

For every folder directly under the root that has `<folder>.unified.tsv` and
`<folder>.csv`, creates a temporary GraphDB repository, uploads the graph, runs the
pipeline of `cli/main.py` on it at the default rule filter (std confidence 1, without
the fill step), and saves the synthetic graph to
`<root>/<folder>_ours/<folder>_ours.tsv`. The repository is deleted afterwards, and
the output of the three steps goes to `<log dir>/<folder>.log`, at INFO level
unless `--log-level` says otherwise.

Datasets run one at a time, smallest first. A dataset that already has its .tsv file
is skipped unless `--force` is given, so an interrupted batch can be resumed. A
dataset that fails doesn't stop the others, and the script exits with status 1 once
every dataset is done.

    python -m skgg.cli.generate_datasets
    python -m skgg.cli.generate_datasets --datasets french WN18RR --force

`--skip-reflexivity` runs `cli/main.py` with the same flag, generating as before the
self-loop fix. Its copies go to the same `<folder>_ours/<folder>_ours.tsv`, so
existing copies are only replaced with `--force`.

With `--levels`, the copies are made at rule levels of `<root>/rule_levels.tsv`
(`--rule-levels`, written by `cli/rule_levels.py`) instead: for each dataset and
level L, `cli/main.py` keeps the rules with PCA confidence >= the row's
`pca_threshold` (`--pca-conf`; none at all for r0, through a rules file with no
rule; for std1, which has no PCA threshold, the rules with std confidence 1, as
without `--levels`), and the copy goes to
`<root>/<folder>_ours_L/<folder>_ours_L.tsv`, with the rules the run used next to it
as `<folder>_ours_L_used.csv`. A row with
`ours_same_as` is skipped. Each level has its own repository
(`skgg_ours_<folder>_L`) and its own copy of the rules file, so several invocations
can make levels of the same dataset at once. See docs/rule-levels.md.

    python -m skgg.cli.generate_datasets --levels r0 std1 r25 r50 r75 r100 \
        --datasets french french1_1 french1_N frenchN_1 frenchN_M

`--completeness pca` makes the copies with the PCA setting of `cli/main.py`, where
the rules with PCA confidence 1 are complete instead of those with std confidence 1
(without `--levels`, the rules with PCA confidence 1 are the ones kept). Its copies go
under `--output-root`, default `exp_datasets_pca`, as
`<output root>/<folder>_ours[_L]/<folder>_ours[_L].tsv`, so the std copies next to
the datasets are left alone. With `--levels` it reads `<output root>/rule_levels.tsv`,
written by `cli/rule_levels.py --completeness pca`, whose first level is `pca1`
instead of `std1`, and refuses a levels file of the other setting. Its repositories
end in `_pca` and its logs go to `logs/ours_pca`, so both settings can run at once.

    python -m skgg.cli.rule_levels --completeness pca
    python -m skgg.cli.generate_datasets --completeness pca \
        --levels pca1 r25 r50 r75 r100 --datasets french
"""

import argparse
import csv
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from skgg.core.config import GraphConfig
from skgg.core.rules import COMPLETENESS, Completeness, rule_filter_label
from skgg.core.utils import setup_logging

logger = logging.getLogger(__name__)

OUTPUT_SUFFIX = "_ours"
# Copies of a dataset D, which are not datasets themselves: D_ours, D_kbgen,
# D_pygraft and the rule-level copies such as D_ours_r25.
COPY_NAME = re.compile(r"_(ours|kbgen|pygraft)(_r\d+|_std1|_pca1)?$")
REPOSITORY_PREFIX = "skgg_ours_"
# Default folder of the PCA-setting copies (`--completeness pca`).
PCA_ROOT = Path("exp_datasets_pca")
CHUNK_SIZE = 5000

# No inference: the pipeline reads explicit triples only.
REPOSITORY_CONFIG = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix rep: <http://www.openrdf.org/config/repository#>.
@prefix sr: <http://www.openrdf.org/config/repository/sail#>.
@prefix sail: <http://www.openrdf.org/config/sail#>.
@prefix graphdb: <http://www.ontotext.com/config/graphdb#>.
[] a rep:Repository ;
   rep:repositoryID "{repository}" ;
   rdfs:label "Temporary repository of cli/generate_datasets.py" ;
   rep:repositoryImpl [
     rep:repositoryType "graphdb:SailRepository" ;
     sr:sailImpl [
       sail:sailType "graphdb:Sail" ;
       graphdb:ruleset "empty" ;
       graphdb:disable-sameAs "true"
     ]
   ] .
"""


@dataclass(frozen=True)
class GraphDBStore:
    """Connection to a GraphDB instance, for managing its repositories."""

    url: str
    user: str
    password: str

    def _endpoint(self, path: str = "") -> str:
        return f"{self.url.rstrip('/')}/rest/repositories{path}"

    def check(self) -> None:
        """Raises `requests.RequestException` if the store can't be reached."""
        response = requests.get(
            self._endpoint(), auth=(self.user, self.password), timeout=30
        )
        response.raise_for_status()

    def delete_repository(self, repository: str) -> None:
        """Deletes a repository, if it exists."""
        response = requests.delete(
            self._endpoint(f"/{repository}"),
            auth=(self.user, self.password),
            timeout=600,
        )
        if response.status_code != 404:
            response.raise_for_status()

    def create_repository(self, repository: str) -> None:
        """Creates an empty repository, replacing one left by an interrupted run."""
        self.delete_repository(repository)
        config = REPOSITORY_CONFIG.replace("{repository}", repository)
        response = requests.post(
            self._endpoint(),
            files={"config": ("config.ttl", config, "text/turtle")},
            auth=(self.user, self.password),
            timeout=120,
        )
        response.raise_for_status()


def triple_file(folder: Path) -> Path:
    return folder / f"{folder.name}.unified.tsv"


def rules_file(folder: Path) -> Path:
    return folder / f"{folder.name}.csv"


@dataclass(frozen=True)
class RuleLevel:
    """A rule level of `rule_levels.tsv` for one dataset: its name (e.g. `r25`),
    the PCA threshold its rules need (None for no PCA threshold: the default std
    confidence 1 filter for std1, or no rule at all if `no_rules`), and the level
    whose copy stands in for it (`ours_same_as`), if any."""

    name: str
    threshold: str | None
    same_as: str
    no_rules: bool = False


def read_levels(
    path: Path, completeness: Completeness = "std"
) -> dict[str, dict[str, RuleLevel]]:
    """Reads `rule_levels.tsv` into {dataset: {level: RuleLevel}}.

    Raises:
        ValueError: If the file was written for another setting than
            `completeness` (its `completeness` column, empty or missing for std).
    """
    levels: dict[str, dict[str, RuleLevel]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            setting = row.get("completeness") or "std"
            if setting != completeness:
                raise ValueError(
                    f"{path} has the levels of the {setting} setting, not "
                    f"{completeness}: write it with cli/rule_levels.py "
                    f"--completeness {completeness}."
                )
            no_rules = row["rules_kept"] == "0"
            threshold = None if no_rules else row["pca_threshold"] or None
            levels.setdefault(row["dataset"], {})[row["level"]] = RuleLevel(
                row["level"], threshold, row["ours_same_as"], no_rules
            )
    return levels


def output_file(
    folder: Path, level: RuleLevel | None = None, output_root: Path | None = None
) -> Path:
    """`<output_root>/<folder>_ours[_<level>]/<folder>_ours[_<level>].tsv`, with
    the dataset's own parent folder as the default root."""
    name = f"{folder.name}{OUTPUT_SUFFIX}" + (f"_{level.name}" if level else "")
    return (output_root or folder.parent) / name / f"{name}.tsv"


def _count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def find_datasets(root: Path, names: list[str] | None) -> list[Path]:
    """Returns the dataset folders to generate, smallest triple file first.

    Args:
        root: Folder holding one subfolder per dataset.
        names: Folder names to keep, or None to keep every dataset.

    Raises:
        ValueError: If a name is not a dataset under `root`, or `root` has no dataset.
    """
    datasets = {}
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        if COPY_NAME.search(folder.name):
            continue
        if triple_file(folder).is_file() and rules_file(folder).is_file():
            datasets[folder.name] = folder
        else:
            logger.warning(
                "Skipping %s: needs %s and %s.",
                folder,
                triple_file(folder).name,
                rules_file(folder).name,
            )
    if not datasets:
        raise ValueError(f"No folder under {root} has a triple file and a rules file.")

    if names is not None:
        if unknown := sorted(set(names) - set(datasets)):
            raise ValueError(f"Not datasets under {root}: {', '.join(unknown)}")
        datasets = {name: datasets[name] for name in names}
    return sorted(datasets.values(), key=lambda f: triple_file(f).stat().st_size)


def _write_config(
    path: Path,
    folder: Path,
    graph: GraphConfig,
    store: GraphDBStore,
    repository: str,
    log_level: str,
    rules: Path | None = None,
) -> None:
    """Writes the run config that the pipeline's scripts read. `rules` replaces
    the dataset's rules file."""
    config = {
        "data": {
            "input_dir": str(folder.resolve()),
            "database_url": store.url,
            "sparql_endpoint": f"repositories/{repository}",
        },
        "db_config": {
            "user": store.user,
            "password": store.password,
            "auth_type": "BASIC",
            "chunk_size": CHUNK_SIZE,
        },
        "graph": asdict(graph),
        "rules": {"rules_file": str(rules) if rules else rules_file(folder).name},
        "logging": {"level": log_level},
    }
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def generate_dataset(
    folder: Path,
    log_path: Path,
    store: GraphDBStore,
    log_level: str,
    skip_reflexivity: bool = False,
    level: RuleLevel | None = None,
    completeness: Completeness = "std",
    output_root: Path | None = None,
) -> None:
    """Runs upload, pipeline and download for one dataset, each as its own process,
    so a dataset's memory is freed and its failure stays its own.

    With a `level`, the pipeline keeps the rules of that level (see the module
    docstring), and the rules it used are saved next to the copy. `completeness`
    is the pipeline's setting (`cli/main.py --completeness`), and the copy goes
    under `output_root` (`output_file`).

    The .tsv file only appears in its final place once every step has succeeded.

    Raises:
        subprocess.CalledProcessError: If one of the steps fails.
        requests.RequestException: If the repository can't be created or deleted.
    """
    graph = GraphConfig(
        name=folder.name,
        triple_file=triple_file(folder).name,
        namespace=f"http://skgg.org/{folder.name}/",
    )
    threshold = None if level is None else level.threshold
    synthetic_uri = graph.synthetic_uri(
        rule_filter_label(
            None if threshold is None else float(threshold), completeness
        )
    )
    # Each level and setting has its own repository, so the levels of a dataset can
    # run at once.
    repository = f"{REPOSITORY_PREFIX}{folder.name}"
    if level is not None:
        repository += f"_{level.name}"
    if completeness != "std":
        repository += f"_{completeness}"

    store.create_repository(repository)
    try:
        with (
            tempfile.TemporaryDirectory() as tmp,
            log_path.open("w", encoding="utf-8") as log,
        ):
            config = Path(tmp) / "config.json"
            rules = None
            if level is not None:
                # cli/main.py writes the rules it used next to the rules file, so the
                # run reads a copy in its own folder: runs of other levels of the
                # dataset can't overwrite that file or move it away.
                rules = Path(tmp) / rules_file(folder).name
                if level.no_rules:
                    # A rules file with the header only: no rule at all.
                    with rules_file(folder).open(encoding="utf-8") as f:
                        rules.write_text(f.readline(), encoding="utf-8")
                else:
                    shutil.copy(rules_file(folder), rules)
            _write_config(config, folder, graph, store, repository, log_level, rules)
            download_stem = Path(tmp) / "graph"
            used = (rules or rules_file(folder)).with_name(f"{folder.name}_used.csv")
            # Without a level the file is next to the dataset's rules file; remove
            # an earlier run's file so that only this run's can be copied.
            used.unlink(missing_ok=True)
            for step in (
                ["skgg.cli.graph", "upload", "-f", str(config)],
                [
                    "skgg.cli.main",
                    "-f",
                    str(config),
                    *(["--skip-reflexivity"] if skip_reflexivity else []),
                    *(["--pca-conf", threshold] if threshold is not None else []),
                    *(
                        ["--completeness", completeness]
                        if completeness != "std"
                        else []
                    ),
                ],
                [
                    "skgg.cli.graph",
                    "download",
                    "-f",
                    str(config),
                    "--graph-uri",
                    synthetic_uri,
                    "-o",
                    str(download_stem),
                ],
            ):
                subprocess.run(
                    [sys.executable, "-m", *step],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            output = output_file(folder, level, output_root)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(download_stem.with_suffix(".tsv"), output)
            if level is not None and not level.no_rules:
                if not used.is_file():
                    raise FileNotFoundError(f"{folder.name} {level.name}: no {used}.")
                shutil.copy(used, output.with_name(f"{output.stem}_used.csv"))
    finally:
        store.delete_repository(repository)


def generate_all(
    root: Path,
    names: list[str] | None,
    force: bool,
    log_dir: Path,
    store: GraphDBStore,
    log_level: str = "INFO",
    skip_reflexivity: bool = False,
    levels: list[str] | None = None,
    levels_file: Path | None = None,
    times_file: Path | None = None,
    completeness: Completeness = "std",
    output_root: Path | None = None,
) -> list[str]:
    """Generates the datasets under `root`, or with `levels`, their copies at those
    rule levels of `levels_file` (default `<output_root>/rule_levels.tsv`). With
    `times_file`, appends a row per copy made (see `TIMES_COLUMNS`). The copies go
    under `output_root`, by default `root` in the std setting and `PCA_ROOT` in
    the PCA setting (`completeness`).

    Returns:
        The names of the datasets (or `<dataset> <level>`) that failed.

    Raises:
        ValueError: If a dataset has no row in `levels_file` for a level.
    """
    if output_root is None:
        output_root = PCA_ROOT if completeness == "pca" else root
    datasets = find_datasets(root, names)
    jobs: list[tuple[Path, RuleLevel | None]] = [(folder, None) for folder in datasets]
    if levels is not None:
        table = read_levels(
            levels_file or output_root / "rule_levels.tsv", completeness
        )
        jobs = []
        for folder in datasets:
            for name in levels:
                if name not in table.get(folder.name, {}):
                    raise ValueError(f"No level {name} for {folder.name}.")
                jobs.append((folder, table[folder.name][name]))
    store.check()
    log_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for number, (folder, level) in enumerate(jobs, start=1):
        label = folder.name if level is None else f"{folder.name} {level.name}"
        output = output_file(folder, level, output_root)
        if level is not None and level.same_as:
            logger.info(
                "[%d/%d] %s: same rules as %s (ours_same_as), skipping.",
                number,
                len(jobs),
                label,
                level.same_as,
            )
            continue
        if output.exists() and not force:
            logger.info(
                "[%d/%d] %s: %s exists, skipping.",
                number,
                len(jobs),
                label,
                output.name,
            )
            continue

        logger.info("[%d/%d] %s: generating.", number, len(jobs), label)
        log_path = log_dir / f"{output.stem}.log"
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        start = time.monotonic()
        try:
            generate_dataset(
                folder,
                log_path,
                store,
                log_level,
                skip_reflexivity,
                level,
                completeness,
                output_root,
            )
        except (subprocess.CalledProcessError, requests.RequestException) as error:
            logger.error(
                "%s: failed after %.0f s (%s). See %s.",
                label,
                time.monotonic() - start,
                error,
                log_path,
            )
            failed.append(label)
            continue
        seconds = time.monotonic() - start
        logger.info(
            "%s: %d triples (source %d) in %.0f s.",
            label,
            _count_lines(output),
            _count_lines(triple_file(folder)),
            seconds,
        )
        # The copy keeps the log of the run that made it, wherever --log-dir is.
        copy_log = output.with_suffix(".log")
        if log_path.resolve() != copy_log.resolve():
            shutil.copyfile(log_path, copy_log)
        if times_file is not None:
            used = output.with_name(f"{output.stem}_used.csv")
            _append_time(
                times_file,
                {
                    "dataset": folder.name,
                    "level": "" if level is None else level.name,
                    "completeness": completeness,
                    "rules_used": _count_lines(used) - 1 if used.is_file() else 0,
                    "source_triples": _count_lines(triple_file(folder)),
                    "triples": _count_lines(output),
                    "seconds": round(seconds, 1),
                    "started": started,
                    "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "commit": _git_commit(),
                    "log": str(log_path),
                },
            )

    logger.info("%d of %d copies ok.", len(jobs) - len(failed), len(jobs))
    if failed:
        logger.error("Failed datasets: %s", ", ".join(failed))
    return failed


TIMES_COLUMNS = [
    "dataset",
    "level",
    "completeness",
    "rules_used",
    "source_triples",
    "triples",
    "seconds",
    "started",
    "finished",
    "commit",
    "log",
]


def _append_time(path: Path, row: dict[str, object]) -> None:
    """Appends `row` to the tab-separated `path`, writing the header if it is new.
    An existing file keeps its own columns, so a file written before a column was
    added to `TIMES_COLUMNS` stays aligned; the columns it lacks are left out."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    columns = TIMES_COLUMNS
    if not new:
        with path.open(encoding="utf-8", newline="") as f:
            columns = next(csv.reader(f, delimiter="\t"), TIMES_COLUMNS)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=columns, delimiter="\t", extrasaction="ignore"
        )
        if new:
            writer.writeheader()
        writer.writerow(row)


def _git_commit() -> str:
    """The commit of the working tree, with `+dirty` if `src/` has uncommitted
    changes, or an empty string outside git."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "src"], check=False
        ).returncode
    except (OSError, subprocess.CalledProcessError):
        return ""
    return commit + ("+dirty" if dirty else "")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the pipeline on every <folder>/<folder>.unified.tsv and save "
        "the synthetic graph to <folder>_ours/<folder>_ours.tsv. Needs a running "
        "GraphDB, where it creates and deletes one temporary repository per dataset."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exp_datasets"),
        help="Folder holding one subfolder per dataset (default: %(default)s).",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        help="Only generate these dataset folders (default: all of them).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Generate the datasets that already have a .tsv file again.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Folder for each dataset's log (default: logs/ours, or logs/ours_pca "
        "with --completeness pca).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level of each dataset's log (default: %(default)s). WARNING "
        "leaves out the pipeline's phases and its run summary.",
    )
    parser.add_argument(
        "--database-url",
        default="http://localhost:7200/",
        help="Base URL of GraphDB (default: %(default)s).",
    )
    parser.add_argument(
        "--user", default="admin", help="GraphDB user (default: %(default)s)."
    )
    parser.add_argument(
        "--password",
        default="rootpassword",
        help="GraphDB password (default: the one in configurations/).",
    )
    parser.add_argument(
        "--skip-reflexivity",
        action="store_true",
        help="Pass --skip-reflexivity to cli/main.py: generate as before the "
        "self-loop fix.",
    )
    parser.add_argument(
        "--levels",
        nargs="+",
        default=None,
        help="Make the copies at these rule levels, e.g. r0 r25 r50 r75 r100, "
        "into <folder>_ours_<level>/ (default: the copies with the default rule "
        "filter, <folder>_ours/).",
    )
    parser.add_argument(
        "--rule-levels",
        type=Path,
        default=None,
        help="Rule levels file of cli/rule_levels.py (default: "
        "<output root>/rule_levels.tsv).",
    )
    parser.add_argument(
        "--completeness",
        choices=COMPLETENESS,
        default="std",
        help="Setting of cli/main.py: std (complete rules have std confidence 1) or "
        "pca (PCA confidence 1) (default: %(default)s).",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Folder of the copies (default: --root, or "
        f"{PCA_ROOT} with --completeness pca).",
    )
    parser.add_argument(
        "--times",
        type=Path,
        default=None,
        help="Tab-separated file to append a row to per copy made: dataset, level, "
        "rules used, triples, seconds, start and end, commit and log.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level="INFO")
    failures = generate_all(
        args.root,
        args.datasets,
        args.force,
        args.log_dir
        or Path("logs/ours_pca" if args.completeness == "pca" else "logs/ours"),
        GraphDBStore(args.database_url, args.user, args.password),
        args.log_level,
        args.skip_reflexivity,
        args.levels,
        args.rule_levels,
        args.times,
        args.completeness,
        args.output_root,
    )
    sys.exit(1 if failures else 0)
