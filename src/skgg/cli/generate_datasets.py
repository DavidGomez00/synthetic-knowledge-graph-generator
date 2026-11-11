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
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from skgg.core.config import GraphConfig
from skgg.core.rules import rule_filter_label
from skgg.core.utils import setup_logging

logger = logging.getLogger(__name__)

OUTPUT_SUFFIX = "_ours"
REPOSITORY_PREFIX = "skgg_ours_"
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


def output_file(folder: Path) -> Path:
    name = f"{folder.name}{OUTPUT_SUFFIX}"
    return folder.parent / name / f"{name}.tsv"


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
        if folder.name.endswith(OUTPUT_SUFFIX):
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
) -> None:
    """Writes the run config that the pipeline's scripts read."""
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
        "rules": {"rules_file": rules_file(folder).name},
        "logging": {"level": log_level},
    }
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def generate_dataset(
    folder: Path,
    log_path: Path,
    store: GraphDBStore,
    log_level: str,
    skip_reflexivity: bool = False,
) -> None:
    """Runs upload, pipeline and download for one dataset, each as its own process,
    so a dataset's memory is freed and its failure stays its own.

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
    synthetic_uri = graph.synthetic_uri(rule_filter_label(None))
    repository = f"{REPOSITORY_PREFIX}{folder.name}"

    store.create_repository(repository)
    try:
        with (
            tempfile.TemporaryDirectory() as tmp,
            log_path.open("w", encoding="utf-8") as log,
        ):
            config = Path(tmp) / "config.json"
            _write_config(config, folder, graph, store, repository, log_level)
            download_stem = Path(tmp) / "graph"
            for step in (
                ["skgg.cli.graph", "upload", "-f", str(config)],
                [
                    "skgg.cli.main",
                    "-f",
                    str(config),
                    *(["--skip-reflexivity"] if skip_reflexivity else []),
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
            output = output_file(folder)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(download_stem.with_suffix(".tsv"), output)
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
) -> list[str]:
    """Generates the datasets under `root`.

    Returns:
        The names of the datasets that failed.
    """
    datasets = find_datasets(root, names)
    store.check()
    log_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for number, folder in enumerate(datasets, start=1):
        output = output_file(folder)
        if output.exists() and not force:
            logger.info(
                "[%d/%d] %s: %s exists, skipping.",
                number,
                len(datasets),
                folder.name,
                output.name,
            )
            continue

        logger.info("[%d/%d] %s: generating.", number, len(datasets), folder.name)
        log_path = log_dir / f"{folder.name}.log"
        start = time.monotonic()
        try:
            generate_dataset(folder, log_path, store, log_level, skip_reflexivity)
        except (subprocess.CalledProcessError, requests.RequestException) as error:
            logger.error(
                "%s: failed after %.0f s (%s). See %s.",
                folder.name,
                time.monotonic() - start,
                error,
                log_path,
            )
            failed.append(folder.name)
            continue
        logger.info(
            "%s: %d triples (source %d) in %.0f s.",
            folder.name,
            _count_lines(output),
            _count_lines(triple_file(folder)),
            time.monotonic() - start,
        )

    logger.info("%d of %d datasets ok.", len(datasets) - len(failed), len(datasets))
    if failed:
        logger.error("Failed datasets: %s", ", ".join(failed))
    return failed


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
        default=Path("logs/ours"),
        help="Folder for each dataset's log (default: %(default)s).",
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
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level="INFO")
    failures = generate_all(
        args.root,
        args.datasets,
        args.force,
        args.log_dir,
        GraphDBStore(args.database_url, args.user, args.password),
        args.log_level,
        args.skip_reflexivity,
    )
    sys.exit(1 if failures else 0)
