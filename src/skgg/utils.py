"""Cross-cutting helpers: logging setup, SPARQL client construction, and mapping
RDF terms (short names) to their fully-qualified namespace URIs.
"""

import logging
from pathlib import Path

from SPARQLWrapper import BASIC, DIGEST, SPARQLWrapper

from skgg.config import RunConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
def resolve_config_path(name: str) -> Path:
    """Resolves a bare config filename against `configurations/`; a value that
    already contains a path separator (e.g. "configurations/mario.json" or an
    absolute path) is used as given. Shared by `cli/main.py` and
    `cli/upload.py`'s `-f`/`--config_file` argument."""
    path = Path(name)
    return path if "/" in name else Path("configurations") / path


def short_term(term: str) -> str:
    """Shortens a URI term (bare or in brackets) to its last path/fragment segment,
    e.g. `<http://FrenchRoyalty.org/child>` -> `child`. Variables and other terms are
    returned unchanged."""
    bare = term[1:-1] if term.startswith("<") and term.endswith(">") else term
    if not bare.startswith("http"):
        return term
    return bare.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(level: int | str = logging.INFO) -> None:
    """Configures the root logger to output to the console.

    Args:
        level: The logging level to set. Accepts standard logging integers
               (e.g., logging.DEBUG) or strings (e.g., "INFO", "DEBUG").
    """
    if isinstance(level, str):
        level = level.upper()

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-8s | %(levelname)-6s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    # Force urllib3 and its connectionpool child to be quiet
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    # same to matplotlib
    logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
    logging.getLogger("matplotlib.pyplot").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
def create_sparql_client(config: RunConfig) -> SPARQLWrapper:
    """Instantiates a SPARQLWrapper client."""
    endpoint_url = config.data.get_full_sparql_url()
    client = SPARQLWrapper(endpoint_url)

    auth_type = config.db_config.auth_type.upper()
    user = config.db_config.user
    password = config.db_config.password

    if auth_type == "DIGEST" and user and password:
        client.setHTTPAuth(DIGEST)
        client.setCredentials(user, password)
    elif auth_type == "BASIC" and user and password:
        client.setHTTPAuth(BASIC)
        client.setCredentials(user, password)

    return client


# ---------------------------------------------------------------------------
# Term mappings.
# ---------------------------------------------------------------------------
DEFAULT_PREFIXES: dict[str, str] = {
    "type": "http://www.w3.org/2000/01/rdf-schema#",
}


def format_term(
    term: str,
    term_mapping: dict[str, str] | None = None,
) -> str:
    """Ensures a term is wrapped in one set of brackets with the correct namespace."""
    if (term.startswith("<") and term.endswith(">")) or term.startswith("?"):
        return term

    if term.startswith("http"):
        return f"<{term}>"

    if term_mapping is not None:
        namespace = term_mapping.get(term, term_mapping.get("default"))
        if namespace is not None:
            return f"<{namespace}{term}>"

    message = "Default namespace not defined, aborting."
    raise ValueError(f"Error parsing term {term}: {message}")


def format_triple(
    subject: str,
    predicate: str,
    obj: str,
    term_mapping: dict[str, str],
) -> str:
    """Returns triple is in SPARQL format with the correct namespace and a final '.'."""
    subject_str = format_term(subject, term_mapping)
    predicate_str = format_term(predicate, term_mapping)
    object_str = format_term(obj, term_mapping)

    return f"{subject_str} {predicate_str} {object_str} ."


def build_term_mapping(
    term_namespaces: dict[str, str], default_namespace: str
) -> dict[str, str]:
    """Builds the bare-term -> namespace-URI mapping used by `format_term`/
    `format_triple`, from `DEFAULT_PREFIXES` overridden by the experiment's
    own `graph.term_namespaces`, plus a "default" fallback namespace.
    """
    term_mapping = DEFAULT_PREFIXES.copy()
    term_mapping.update(term_namespaces)
    term_mapping["default"] = default_namespace
    return term_mapping
