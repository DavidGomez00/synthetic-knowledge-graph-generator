"""Configuration classes for the experiment and preprocessing settings."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

from typing_extensions import Self
from yarl import URL

T = TypeVar("T")


@dataclass
class DataConfig:
    """Configuration for input and output directories."""

    input_dir: Path = Path("data/")

    # Base URL of the database instance
    database_url: URL = URL("http://localhost:8890/")

    # Path/suffix for SPARQL endpoint
    sparql_endpoint: str = "sparql"

    def get_full_sparql_url(self) -> str:
        """Returns the full SPARQL endpoint for SPARQLWrapper."""
        return str(self.database_url / self.sparql_endpoint)

    def __post_init__(self) -> None:
        """Validate input and create output directories."""
        self.input_dir = Path(self.input_dir)
        self.database_url = URL(self.database_url)

        self._validate_path(self.input_dir, "input_dir")

    @staticmethod
    def _validate_path(path: Path, field_name: str) -> None:
        """Check whether a path is valid."""
        if not path.exists():
            raise FileNotFoundError(
                f"Configuration Error: The {field_name} does not exist at {path}"
            )
        if not path.is_dir():
            raise NotADirectoryError(
                f"Configuration Error: {field_name} at {path} is not a directory."
            )


@dataclass(frozen=True)
class DatabaseAuthConfig:
    """Database authentication and batching settings."""

    user: str | None = "dba"
    password: str | None = "dba"

    # Supported: "DIGEST" (Virtuoso default), "BASIC" (GraphDB default), or "NONE"
    auth_type: Literal["DIGEST", "BASIC", "NONE"] = "DIGEST"
    chunk_size: int = 5000


@dataclass(frozen=True)
class GraphConfig:
    """Knowledge Graph settings: file locations, and the named-graph URIs that key
    each stage of the pipeline (see "Graph layout" in docs/architecture.md).

    Graph URIs are not set in the config. They are derived from `root_uri`, the
    namespace without its trailing `/` or `#` (e.g. `http://FrenchRoyalty.org`),
    and for a pipeline run from the label of its rule filter
    (`core.rules.rule_filter_label`, e.g. `pca=0.9` or `std=1`):

        <root>/base                  the uploaded source graph (`base_uri`)
        <root>/skgg/<filter>/edb     the generated EDB (`edb_uri`)
        <root>/skgg/<filter>         the EDB plus what completion derives from it
                                     (`synthetic_uri`)
        <root>/skgg/<filter>/filled  a copy of the synthetic graph plus the
                                     triples that the optional fill step adds
                                     (`--fill` in `cli/main.py`, `filled_uri`)

    Configs with the same namespace share these graphs.

    Attributes:
        name: Human-readable name for the graph/experiment.
        triple_file: Filename (relative to `data.input_dir`) of the base graph, an
            .nt or .tsv file, consumed by `cli/graph.py upload`.
        namespace: Default namespace URI used to resolve unprefixed terms, and
            the root of the graph URIs.
        term_namespaces: Optional bare-term -> namespace-URI overrides, merged
            over `utils.DEFAULT_PREFIXES` and under the `namespace` default
            when resolving unprefixed terms (see `utils.build_term_mapping`).
            Only needed when a term's namespace differs from `namespace`.
    """

    name: str
    triple_file: str
    namespace: str
    term_namespaces: dict[str, str] = field(default_factory=dict)

    @property
    def root_uri(self) -> str:
        """The namespace without its trailing `/` or `#`, which every graph URI of
        the dataset starts with."""
        return self.namespace.rstrip("/#")

    @property
    def base_uri(self) -> str:
        """Named-graph URI of the uploaded source graph."""
        return f"{self.root_uri}/base"

    @property
    def skgg_uri(self) -> str:
        """Prefix of the pipeline's graph URIs. No graph is stored at it."""
        return f"{self.root_uri}/skgg"

    def synthetic_uri(self, rule_filter: str) -> str:
        """Named-graph URI of the synthetic graph (EDB + completion) of a run whose
        rule filter is labeled `rule_filter`."""
        return f"{self.skgg_uri}/{rule_filter}"

    def edb_uri(self, rule_filter: str) -> str:
        """Named-graph URI of the EDB of a run whose rule filter is labeled
        `rule_filter`."""
        return f"{self.synthetic_uri(rule_filter)}/edb"

    def filled_uri(self, rule_filter: str) -> str:
        """Named-graph URI of the filled copy of the synthetic graph of a run whose
        rule filter is labeled `rule_filter`."""
        return f"{self.synthetic_uri(rule_filter)}/filled"


@dataclass(frozen=True)
class RulesConfig:
    """Settings for loading and filtering the Horn rule set.

    Attributes:
        rules_file: Filename (relative to `data.input_dir`) of the rules CSV,
            parsed by `core.rules.parse_rule_set`.
        pca_threshold: Minimum PCA confidence a rule must have to be kept, or
            None (the default) to keep the rules with std confidence >=
            `core.rules.DEFAULT_STD_THRESHOLD` (1) instead.
            `core.rules.parse_rule_set` drops every rule below the threshold
            or missing the confidence it filters on, so only the remaining
            rules flow into EDB generation and completion. Overridable per run
            with `--pca-conf` on the CLI.
    """

    rules_file: str
    pca_threshold: float | None = None


@dataclass(frozen=True)
class LoggingConfig:
    """Logging settings for an experiment run.

    Attributes:
        level: Root logger level, as an int (e.g. `logging.DEBUG`) or a level
            name string (e.g. `"DEBUG"`). Passed to `utils.setup_logging`.
    """

    level: int | str = logging.INFO


@dataclass
class RunConfig:
    """Master configuration object for the experiment run."""

    graph: GraphConfig
    rules: RulesConfig
    db_config: DatabaseAuthConfig = field(default_factory=DatabaseAuthConfig)
    data: DataConfig = field(default_factory=DataConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_json(cls, json_path: Path | str) -> Self:
        """Loads a RunConfig from a JSON file."""

        def load_section(section_cls: type[T], key: str, required: bool = False) -> T:
            """Builds a config dataclass from a JSON section.

            Args:
                section_cls: The dataclass to construct from the section.
                key: The JSON key to fetch the section from.
                required: If True, raises a KeyError if the section is missing.

            Raises:
                KeyError: If a required section is missing from the JSON.
                ValueError: If the section exists but is not a JSON object
                    (mapping), or its fields don't match `section_cls`'s
                    constructor (missing or unexpected fields).
            """
            if key not in data:
                if required:
                    raise KeyError(
                        f"Configuration Error: Missing mandatory section {key}"
                    )
                section = {}
            else:
                section = data[key]
                if not isinstance(section, dict):
                    raise ValueError(
                        f"Configuration Error: Expected '{key}' to be a mapping, "
                        f"got {type(section).__name__}"
                    )

            try:
                return section_cls(**section)
            except TypeError as e:
                raise ValueError(
                    f"Configuration Error: Invalid '{key}' section: {e}"
                ) from e

        # Read JSON contents
        json_path = Path(json_path)

        with open(json_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        db_config = load_section(DatabaseAuthConfig, "db_config", required=True)
        graph_config = load_section(GraphConfig, "graph", required=True)
        rules_config = load_section(RulesConfig, "rules", required=True)
        data_config = load_section(DataConfig, "data", required=True)
        logging_config = load_section(LoggingConfig, "logging")

        return cls(
            data=data_config,
            graph=graph_config,
            rules=rules_config,
            logging=logging_config,
            db_config=db_config,
        )
