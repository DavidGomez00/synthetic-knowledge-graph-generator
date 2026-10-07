"""SPARQL query construction and execution against the graph database.

Every read (SELECT/ASK) or write (INSERT/CLEAR/COPY) against Virtuoso or GraphDB
goes through this module. Higher-level modules (engine/*.py) build on top of these
functions rather than talking to `SPARQLWrapper` directly. See BACKLOG.md for known
overlap/consolidation TODOs in this file.
"""

import itertools
import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO, cast

import requests
from requests.auth import HTTPDigestAuth
from SPARQLWrapper import JSON, POST, URLENCODED, SPARQLWrapper
from yarl import URL

from skgg.core.rules import Atom, HornRule, RuleSignature
from skgg.core.shapes import SparqlConstraint
from skgg.core.triples import read_tsv, tsv_triple_to_nt
from skgg.core.utils import format_term, format_triple

logger = logging.getLogger(__name__)

SparqlBinding = dict[str, dict[str, str]]


# ---------------------------------------------------------------------------
# Helper functions.
# ---------------------------------------------------------------------------
def _chunk_iter(iterable: Iterable[str], size: int) -> Iterable[tuple[str, ...]]:
    """Yields successive chunks of a given size from an iterable."""
    iterator = iter(iterable)
    while chunk := tuple(itertools.islice(iterator, size)):
        yield chunk


def _get_update_client(client: SPARQLWrapper) -> SPARQLWrapper:
    """Returns a SPARQLWrapper pointing to the write endpoint for updates."""

    if "/repositories/" in client.endpoint and not client.endpoint.endswith(
        "/statements"
    ):
        update_client = SPARQLWrapper(f"{client.endpoint.rstrip('/')}/statements")
        update_client.http_auth = client.http_auth
        update_client.user = getattr(client, "user", None)
        update_client.passwd = getattr(client, "passwd", None)
        return update_client

    return client


def _rest_auth(client: SPARQLWrapper) -> HTTPDigestAuth | tuple[str, str] | None:
    """Returns `requests` credentials matching the client's: digest auth when
    the client uses DIGEST, basic auth otherwise, or None without credentials.
    Used by the calls that bypass SPARQL for the store's REST endpoints."""
    user, passwd = getattr(client, "user", None), getattr(client, "passwd", None)
    if not (user and passwd):
        return None
    if getattr(client, "http_auth", None) == "DIGEST":
        return HTTPDigestAuth(user, passwd)
    return (user, passwd)


def _execute_update_query(client: SPARQLWrapper, query: str) -> None:
    """Executes a SPARQL UPDATE query (INSERT/CLEAR/COPY) against the update endpoint.

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        query: The SPARQL UPDATE query to execute.

    Raises:
        Exception: If the store rejects the update.
    """
    update_client = _get_update_client(client)
    update_client.setMethod("POST")
    update_client.setRequestMethod(URLENCODED)
    update_client.setQuery(query)

    if hasattr(update_client, "parameters"):
        if "query" in update_client.parameters:
            del update_client.parameters["query"]
        update_client.parameters["update"] = query

    try:
        update_client.query()
    except Exception:
        logger.exception("SPARQL UPDATE failed:\n%s", query)
        raise


# ---------------------------------------------------------------------------
# Query execution.
# ---------------------------------------------------------------------------
def execute_select_query(client: SPARQLWrapper, query: str) -> list[SparqlBinding]:
    """Executes a SELECT query and returns the bindings.

    Uses POST (URL-encoded) rather than GET so the query text travels in the
    request body instead of the URL. Queries built with large VALUES clauses
    (see `get_existing_triples`) can easily exceed a GET request's max URL/header
    size and get rejected by the store's HTTP server (e.g. GraphDB's "Request
    header is too large").
    """
    # TODO (optim): Maybe we want this as an iterator
    client.setMethod(POST)
    client.setReturnFormat(JSON)
    client.setQuery(query)

    try:
        response = client.queryAndConvert()

        if isinstance(response, dict) and "results" in response:
            raw_bindings = response["results"].get("bindings", [])
            return cast(list[SparqlBinding], raw_bindings)

        raise ValueError("Failed to retrieve bindings from query results.")

    except Exception:
        logger.error("SPARQL execution failed for query:\n%s", query)
        raise


def execute_insert_query(client: SPARQLWrapper, query: str) -> None:
    """Execute an INSERT query."""
    _execute_update_query(client, query)


def from_binding_row(term: str, binding_row: SparqlBinding) -> tuple[str, str]:
    """Safely extracts a term from a single binding row."""
    if term.startswith("?"):
        var_name = term.lstrip("?")
        val = format_term(binding_row.get(var_name, {}).get("value", var_name))
        v_type = binding_row.get(var_name, {}).get("type", "uri")
        return val, v_type
    return term, "uri"


# ---------------------------------------------------------------------------
# SPARQL query generation.
# ---------------------------------------------------------------------------
def build_rule_query(rule: RuleSignature, graph_uri: str) -> str:
    """Creates a query for the rule signature."""

    # Get the variables from the atomns with extensional predicates
    variables = rule.get_body_variables()
    proj = " ".join(sorted(list(variables)))

    patterns_str = "\n      ".join([f"{atom} ." for atom in sorted(rule.body)])

    unique_values_str = ""
    if len(rule.get_variables()) > 1:
        expressions = [
            f"{v1} != {v2}"
            for v1, v2 in itertools.combinations(sorted(set(rule.get_variables())), 2)
        ]
        unique_values_str = f"FILTER ({' && '.join(expressions)})"

    source_str = f"FROM <{graph_uri}>"

    query = f"""
    SELECT DISTINCT ?rule_id {proj}
    {source_str}
    WHERE {{
      BIND ("{rule.rule_id}" AS ?rule_id)
      {patterns_str}
      {unique_values_str}
    }}
    """
    return query


# A PREFIX or BASE declaration at the head of a shape's `sh:select`.
_QUERY_PROLOGUE = re.compile(
    r"\s*(?:PREFIX\s+[^\s:]*:\s*<[^>]*>|BASE\s*<[^>]*>)", re.IGNORECASE
)

# `$this` filter for each `SparqlConstraint.targets` kind, given the target IRI.
_TARGET_FILTERS = {
    "subjectsOf": "EXISTS {{ $this <{}> [] }}",
    "objectsOf": "EXISTS {{ [] <{}> $this }}",
    "class": "EXISTS {{ $this <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>/"
    "<http://www.w3.org/2000/01/rdf-schema#subClassOf>* <{}> }}",
    "node": "sameTerm($this, <{}>)",
}


def build_shape_query(constraint: SparqlConstraint, graph_uri: str) -> str:
    """Creates a query returning the violations of a SHACL-SPARQL constraint in
    a named graph.

    The constraint's `sh:select` becomes a subquery over `FROM <graph_uri>`,
    with `$this` left unbound (rather than pre-bound per focus node, as SHACL
    does) and then kept only when it is one of the shape's targets. PREFIX/BASE
    lines at the head of the `sh:select` move above the wrapper, since a
    subquery can't hold them.
    """
    select = constraint.select
    prologue = []
    while match := _QUERY_PROLOGUE.match(select):
        prologue.append(match[0].strip())
        select = select[match.end() :]

    prefixes = [
        f"PREFIX {prefix}: <{namespace}>"
        for prefix, namespace in sorted(constraint.prefixes.items())
    ]
    targets = " || ".join(
        _TARGET_FILTERS[kind].format(iri) for kind, iri in constraint.targets
    )
    header = "\n".join([*prefixes, *prologue])
    return f"""{header}
SELECT *
FROM <{graph_uri}>
WHERE {{
  {{
    {select.strip()}
  }}
  FILTER ({targets})
}}
"""


# ---------------------------------------------------------------------------
# Write to database.
# ---------------------------------------------------------------------------
def insert_triples_sparql(
    client: SPARQLWrapper,
    graph_uri: str,
    triple_stream: Iterable[str],
    chunk_size: int,
) -> int:
    """Inserts triples into Virtuoso using SPARQL in batches.

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        graph_uri: The URI of the target named graph.
        triple_stream: An iterable yielding individual SPARQL triple strings.
        chunk_size: Maximum number of triples to insert per SPARQL query.

    Returns:
        The number of new triples added to the graph. Relies on the upstream generator
        to strictly yield novel triples.
    """
    total_inserted = 0

    for chunk in _chunk_iter(triple_stream, chunk_size):
        if unique_chunk := set(chunk):
            triples_payload = "\n".join(unique_chunk)

            query = f"""
            INSERT DATA {{
            GRAPH <{graph_uri}> {{
                {triples_payload}
            }}
            }}"""

            execute_insert_query(client, query)
            total_inserted += len(unique_chunk)

    return total_inserted


def delete_triples_sparql(
    client: SPARQLWrapper, graph_uri: str, triples: Iterable[str]
) -> None:
    """Deletes the given triples (formatted as by `format_triple`) from a graph."""
    if payload := "\n".join(triples):
        _execute_update_query(
            client, f"DELETE DATA {{ GRAPH <{graph_uri}> {{ {payload} }} }}"
        )


class TripleBuffer:
    """Accumulates triples that were decided without needing a DB read (see
    `engine/edb.py`'s direct-match/random-assignment steps), so they can be
    inserted in fewer, larger batches instead of one `INSERT DATA` round
    trip per call. Call `flush()` before any query that must see their
    effect, and once more before relying on the target graph being complete.
    """

    def __init__(self) -> None:
        self._triples: list[str] = []

    def __len__(self) -> int:
        return len(self._triples)

    def add(self, triples: Iterable[str]) -> int:
        """Buffers triples in memory. Returns how many were added."""
        added = 0
        for triple in triples:
            self._triples.append(triple)
            added += 1
        return added

    def flush(self, client: SPARQLWrapper, graph_uri: str, chunk_size: int) -> int:
        """Inserts every buffered triple into `graph_uri` and clears the buffer."""
        if not self._triples:
            return 0
        count = insert_triples_sparql(
            client=client,
            graph_uri=graph_uri,
            triple_stream=iter(self._triples),
            chunk_size=chunk_size,
        )
        self._triples.clear()
        return count

    def flush_if_full(
        self, client: SPARQLWrapper, graph_uri: str, chunk_size: int
    ) -> int:
        """Flushes if the buffer holds at least `chunk_size` triples, else no-ops.

        Returns the number of triples inserted (0 if it didn't flush).
        """
        if len(self._triples) < chunk_size:
            return 0
        return self.flush(client=client, graph_uri=graph_uri, chunk_size=chunk_size)


def insert_triples_bulk(
    client: SPARQLWrapper,
    graph_uri: str,
    triples: Iterator[str],
    chunk_size: int = 10000,
) -> int:
    """Bulk-inserts N-Triples into a named graph via the store's native bulk-load
    endpoint, bypassing SPARQL Update parsing entirely.

    `INSERT DATA` queries force the store to parse a full SPARQL Update grammar for
    every triple, which does not scale to the hundreds of thousands of triples used to
    initialize a graph (see `initialize_graph`).
    Posting raw N-Triples payloads straight to the store's Graph Store / bulk-statements
    REST endpoint is dramatically faster and is the scalable path for that use case.
    Dispatches to the right protocol based on the client's endpoint:

    - GraphDB (RDF4J): streams batches to the repository's `/statements` endpoint (the
      RDF4J "add statements" REST API) with `context=<graph_uri>` so each batch lands
      directly in the target named graph.
    - Virtuoso: streams batches to `sparql-graph-crud-auth` (the SPARQL 1.1 Graph Store
      HTTP Protocol endpoint).

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        graph_uri: Target named graph URI.
        triples: Iterator yielding N-Triple formatted strings.
        chunk_size: Number of triples to send per HTTP POST request.

    Returns:
        The number of triples sent to the store.

    Raises:
        requests.HTTPError: If the store rejects a batch.
    """
    is_graphdb = "/repositories/" in client.endpoint

    if is_graphdb:
        url = _get_update_client(client).endpoint
        params = {"context": f"<{graph_uri}>"}
    else:
        url = str(URL(client.endpoint).with_name("sparql-graph-crud-auth"))
        params = {"graph-uri": graph_uri}

    headers = {"Content-Type": "application/n-triples"}

    total_inserted = 0

    with requests.Session() as session:
        session.auth = _rest_auth(client)

        for batch in _chunk_iter(triples, chunk_size):
            payload = "\n".join(batch) + "\n"
            response = session.post(url, params=params, headers=headers, data=payload)
            response.raise_for_status()
            total_inserted += len(batch)

    return total_inserted


NT_CONTENT_TYPES = ("application/n-triples", "text/plain")
NT_ACCEPT = "application/n-triples, text/plain;q=0.9"


def _write_nt_response(response: requests.Response, f: BinaryIO) -> int:
    """Streams an N-Triples response into `f`, one triple per line, skipping
    blank and comment lines. Terms are separated by single spaces whatever the
    store used (Virtuoso uses tabs). Returns the number of triples written.

    Raises:
        requests.HTTPError: If the store rejected the request.
        ValueError: If the store answered in a format other than N-Triples.
    """
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    if content_type not in NT_CONTENT_TYPES:
        raise ValueError(
            f"Expected N-Triples from {response.url}, got '{content_type}'."
        )
    written = 0
    for line in response.iter_lines(chunk_size=1 << 16):
        stripped = line.strip()
        if stripped and not stripped.startswith(b"#"):
            # Subjects and predicates hold no whitespace; a literal object may.
            subject, predicate, rest = stripped.split(None, 2)
            obj = rest.removesuffix(b".").rstrip()
            f.write(b" ".join((subject, predicate, obj)) + b" .\n")
            written += 1
    return written


def export_graph_nt(
    client: SPARQLWrapper, graph_uri: str, nt_path: Path, page_size: int = 5000
) -> int:
    """Writes a named graph to an N-Triples file, the read counterpart of
    `insert_triples_bulk`. Dispatches on the client's endpoint:

    - GraphDB (RDF4J): one `GET` on the repository's `/statements` endpoint
      with `context=<graph_uri>` and `infer=false` (explicit statements only),
      which returns the whole graph.
    - Virtuoso: its Graph Store endpoint (`sparql-graph-crud-auth`) stops at
      `ResultSetMaxRows` triples, so the graph is read with CONSTRUCT queries
      of `page_size` triples each, sorted in a sub-select so that deep
      offsets stay valid, until a page comes back short.

    A store cap below `page_size` also ends the Virtuoso loop early, so
    callers should compare the result with `get_triple_count`.

    Args:
        client: An instantiated and configured SPARQLWrapper client.
        graph_uri: The named graph to export.
        nt_path: The .nt file to write. Its parent folder must exist.
        page_size: Triples per CONSTRUCT query (Virtuoso only).

    Returns:
        The number of triples written.

    Raises:
        requests.HTTPError: If the store rejects a request.
        ValueError: If the store answers in a format other than N-Triples.
    """
    headers = {"Accept": NT_ACCEPT}

    with requests.Session() as session, nt_path.open("wb") as f:
        session.auth = _rest_auth(client)

        if "/repositories/" in client.endpoint:
            params = {"context": f"<{graph_uri}>", "infer": "false"}
            url = _get_update_client(client).endpoint
            with session.get(url, params=params, headers=headers, stream=True) as r:
                return _write_nt_response(r, f)

        written = 0
        while True:
            query = f"""
            CONSTRUCT {{ ?s ?p ?o }}
            WHERE {{
              {{
                SELECT ?s ?p ?o
                WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}
                ORDER BY ?s ?p ?o
              }}
            }}
            LIMIT {page_size} OFFSET {written}"""
            with session.post(
                client.endpoint, data={"query": query}, headers=headers, stream=True
            ) as r:
                page = _write_nt_response(r, f)
            written += page
            logger.debug("Exported %d triples from <%s>.", written, graph_uri)
            if page < page_size:
                return written


def insert_graph(
    client: SPARQLWrapper,
    graph_uri: str,
    chunk_size: int,
    triple_file: Path | str,
    term_mapping: dict[str, str] | None = None,
) -> None:
    """Overwrites a graph with contents from an .nt or .tsv file.

    Uses `insert_triples_bulk`'s bulk-load REST endpoint (rather than SPARQL
    `INSERT DATA`) since a base graph loaded from disk can easily reach the
    hundreds of thousands of triples that `INSERT DATA` doesn't scale to.

    Args:
        client: The SPARQL wrapper client used to execute queries.
        graph_uri: The URI of the named graph to overwrite.
        chunk_size: The number of triples to insert per batch.
        triple_file: The local file path to the .nt or .tsv file.
        term_mapping: Mapping of bare terms to their namespace URIs (see
            `utils.format_term`). Required when `triple_file` is a .tsv file,
            whose subject/predicate/object columns are unqualified terms, turned
            into IRIs by `triples.tsv_term_to_nt`; ignored for .nt files, which
            are already fully qualified.

    Raises:
        ValueError: If `triple_file` does not point to an existing file, is a
            .tsv file and no `term_mapping` is given, or has a .tsv line without
            three non-empty fields.
    """

    triple_file = Path(triple_file)
    if not triple_file.is_file():
        raise ValueError("Invalid input file: %s", triple_file)

    is_tsv = triple_file.suffix.lower() == ".tsv"
    if is_tsv and term_mapping is None:
        raise ValueError("A term_mapping is required to load a .tsv file.")

    clear_graph(client, graph_uri)

    def _nt_stream(file_path: Path) -> Iterator[str]:
        """Streams triples locally from an .nt file, already fully qualified."""
        with file_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    yield stripped

    def _tsv_stream(file_path: Path, mapping: dict[str, str]) -> Iterator[str]:
        """Streams triples from a tab-separated (subject, predicate, object)
        file, resolving each bare term to a full URI via `mapping`."""
        for number, triple in read_tsv(file_path):
            if triple is None:
                raise ValueError(
                    f"{file_path}:{number}: expected 3 non-empty tab-separated fields."
                )
            yield tsv_triple_to_nt(triple, mapping)

    iterator = (
        _tsv_stream(triple_file, cast(dict[str, str], term_mapping))
        if is_tsv
        else _nt_stream(triple_file)
    )

    n = insert_triples_bulk(client, graph_uri, iterator, chunk_size)
    logger.debug(
        "Inserted %d triples to <%s> from '%s'.", n, graph_uri, triple_file.name
    )


def clear_graph(client: SPARQLWrapper, graph_uri: str) -> None:
    """Removes all triples from a specified named graph.

    Args:
        client: The SPARQL wrapper client used to execute queries.
        graph_uri: The URI of the named graph to clear.

    Raises:
        Exception: If the SPARQL CLEAR operation fails.
    """
    _execute_update_query(client, f"CLEAR SILENT GRAPH <{graph_uri}>")


def copy_graph(
    client: SPARQLWrapper, source_graph_uri: str, target_graph_uri: str
) -> None:
    """Copy all contents from a graph to another.

    Args:
        client: The SPARQL wrapper client used to execute queries.
        source_graph_uri: The URI of the named graph to copy from.
        target_graph_uri: The URI of the named graph to copy to.

    Raises:
        Exception: If the SPARQL COPY operation fails.
    """
    query = f"COPY GRAPH <{source_graph_uri}> TO GRAPH <{target_graph_uri}>"
    _execute_update_query(client, query)
    logger.debug(
        "Successfully copied <%s> to <%s>.", source_graph_uri, target_graph_uri
    )


def initialize_graph(
    client: SPARQLWrapper,
    source: str | None,
    new_graph_uri: str,
    chunk_size: int,
    term_mapping: dict[str, str] | None = None,
) -> None:
    """Overwrites the new graph URI's content with the source's content.

    Args:
        client: Wrapper for SPARQL queries.
        source: A .nt/.tsv file path or a Graph URI. If None, the graph is only
            cleared.
        new_graph_uri: URI where the source content will be written.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        term_mapping: Mapping of bare terms to their namespace URIs (see
            `utils.format_term`). Required when `source` is a .tsv file;
            ignored for .nt files and Graph URIs.

    Raises:
        ValueError: If the source format is not valid (neither a .nt/.tsv file
            nor a URI).
    """

    if source is None:
        clear_graph(client, new_graph_uri)
        logger.debug("Initializated empty graph at <%s>.", new_graph_uri)
        return

    clean_source = str(source).strip("<>")
    clean_target = new_graph_uri.strip("<>")
    is_triple_file = clean_source.endswith((".nt", ".tsv"))
    is_uri = clean_source.startswith(("http:", "https:"))

    if not (is_triple_file or is_uri):
        raise ValueError(
            f"Invalid source '{source}'. Expected a .nt/.tsv file or a URI."
        )

    if is_uri and clean_source == clean_target:
        return

    clear_graph(client, new_graph_uri)

    if is_triple_file:
        insert_graph(
            client=client,
            graph_uri=new_graph_uri,
            triple_file=source,
            chunk_size=chunk_size,
            term_mapping=term_mapping,
        )

    else:
        copy_graph(
            client=client,
            source_graph_uri=clean_source,
            target_graph_uri=new_graph_uri,
        )


# ---------------------------------------------------------------------------
# Query metrics.
# ---------------------------------------------------------------------------
def get_predicate_frequencies(client: SPARQLWrapper, graph_uri: str) -> dict[str, int]:
    """Retrieves all unique predicates in the graph and the frequency of each one."""

    predicate_frequencies: dict[str, int] = {}
    query = f"""
        SELECT ?predicate (COUNT(*) AS ?frequency)
        WHERE {{
          GRAPH <{graph_uri}> {{
            ?s ?predicate ?o .
          }}
        }}
        GROUP BY ?predicate
        """

    results = execute_select_query(client, query)
    if not results:
        return predicate_frequencies

    for row in results:
        predicate = row["predicate"]["value"]
        frequency = int(row["frequency"]["value"])
        predicate_frequencies[predicate] = frequency

    return predicate_frequencies


def get_domain(client: SPARQLWrapper, graph_uri: str, predicate: str) -> dict[str, int]:
    """Retrieves the distribution of subjects for a predicate in a graph."""

    domain: dict[str, int] = {}

    query = f"""
    SELECT ?subject (COUNT(*) AS ?count)
    WHERE {{
      GRAPH <{graph_uri}> {{
        ?subject {predicate} ?o
      }}
    }}
    GROUP BY ?subject
    """

    if results := execute_select_query(client, query):
        for row in results:
            subject = f"<{row['subject']['value']}>"
            frequency = int(row["count"]["value"])
            domain[subject] = frequency

        return domain

    logger.warning("Retrieved None for the domain of predicate %s.", predicate)
    return domain


def get_range(client: SPARQLWrapper, graph_uri: str, predicate: str) -> dict[str, int]:
    """Retrieves the distribution of objects for a predicate in a graph."""

    p_range: dict[str, int] = {}

    query = f"""
    SELECT ?obj (COUNT(*) AS ?count)
    WHERE {{
      GRAPH <{graph_uri}> {{
        ?s {predicate} ?obj
      }}
    }}
    GROUP BY ?obj
    """

    if results := execute_select_query(client, query):
        for row in results:
            obj = f"<{row['obj']['value']}>"
            frequency = int(row["count"]["value"])
            p_range[obj] = frequency
        return p_range

    logger.warning("Retrieved None for the domain of predicate %s.", predicate)
    return p_range


def get_reflexivity(client: SPARQLWrapper, graph_uri: str, predicate: str) -> int:
    """Retrieves how many triples with this predicate are reflexive (obj == subj)."""

    query = f"""
    SELECT (COUNT(*) AS ?c)
    WHERE {{
      GRAPH <{graph_uri}> {{
        ?s {predicate} ?s
      }}
    }}"""

    if results := execute_select_query(client, query):
        return int(results[0]["c"]["value"])
    return 0


def get_support(client: SPARQLWrapper, rule: HornRule, graph_uri: str) -> int:
    """Returns the support for the rule in the graph."""

    patterns = "\n          ".join(
        [f"{atom} ." for atom in rule.body] + [f"{rule.head} ."]
    )
    proj = " ".join(sorted(rule.get_head_variables()))

    query = f"""
    SELECT (COUNT(*) AS ?supp) WHERE {{
      SELECT DISTINCT {proj} WHERE {{
        GRAPH <{graph_uri}> {{
          {patterns}
        }}
      }}
    }}"""

    if results := execute_select_query(client, query):
        return int(results[0]["supp"]["value"])

    logger.warning("Retrieved None for %s support in %s.", rule.rule_id, graph_uri)
    return 0


def _bind_atom(atom: Atom, subject: str, obj: str) -> dict[str, str] | None:
    """Returns the variable bindings that make `atom` match the triple
    `(subject atom.predicate obj)`, or None if it can't (a constant differs, or the
    same variable would need two values)."""
    binding: dict[str, str] = {}
    for term, value in ((atom.subject, subject), (atom.obj, obj)):
        if not term.startswith("?"):
            if term != value:
                return None
        elif binding.setdefault(term, value) != value:
            return None
    return binding


def _values_clause(binding: dict[str, str]) -> str:
    """Formats a binding as a one-row VALUES clause (empty for no variables)."""
    if not binding:
        return ""
    names = " ".join(binding)
    values = " ".join(binding.values())
    return f"VALUES ({names}) {{ ({values}) }}"


def has_solution(client: SPARQLWrapper, query: str) -> bool:
    """Returns whether a query, e.g. one built by `build_head_impact_query`, has a
    solution."""
    return bool(execute_select_query(client, query))


def build_head_impact_query(
    rule: HornRule, subject: str, obj: str, graph_uri: str
) -> str | None:
    """Builds a query with a solution iff adding the head triple
    `(subject head-predicate obj)` would raise the rule's support:
    some body grounding has those head values. The triple is new,
    so that head binding is not in the support yet.

    Returns None if the head atom can't match the triple.
    """
    if (binding := _bind_atom(rule.head, subject, obj)) is None:
        return None

    patterns = "\n          ".join(f"{atom} ." for atom in sorted(rule.body))
    return f"""
    SELECT * WHERE {{
      {_values_clause(binding)}
      GRAPH <{graph_uri}> {{
          {patterns}
      }}
    }} LIMIT 1"""


def _supported_patterns(rule: HornRule) -> list[str]:
    """Returns the patterns of a body grounding plus the head, with the non-head
    variables renamed (`?x` -> `?x_old`). Inside `FILTER NOT EXISTS`, they match
    when the head binding of the enclosing solution is already in the rule's
    support (see `get_support`)."""
    head_vars = rule.get_head_variables()

    def rename(term: str) -> str:
        return f"{term}_old" if term.startswith("?") and term not in head_vars else term

    body = [
        f"{rename(a.subject)} {a.predicate} {rename(a.obj)} ."
        for a in sorted(rule.body)
    ]
    return [*body, f"{rule.head} ."]


def build_body_impact_query(
    rule: HornRule, atom: Atom, subject: str, obj: str, graph_uri: str
) -> str | None:
    """Builds a query returning the head bindings (head variables, sorted, then
    `?present`) of the new body groundings that adding the triple
    `subject atom.predicate obj` (not yet in the graph) creates, with the triple
    matching `atom`, and whose head binding is not in the rule's support yet (see
    `get_support`). Read the rows with `get_new_head_bindings`.

    `?present` tells whether the head triple is already in the graph: if so, the
    grounding raises the support; if not, it leaves the graph no longer closed
    under the rule. A grounding whose head binding is already supported changes
    neither and is not returned.

    Other body atoms with the same predicate may match the new triple too. Like
    `get_support`, variables may take equal values.

    Returns None if `atom` can't match the triple.
    """
    if (binding := _bind_atom(atom, subject, obj)) is None:
        return None

    patterns = []
    for other in sorted(rule.body - {atom}):
        other_binding = (
            _bind_atom(other, subject, obj)
            if other.predicate == atom.predicate
            else None
        )
        if other_binding is None:
            patterns.append(f"{other} .")
        else:
            # The new triple is not in the graph yet, so match it explicitly.
            values = _values_clause(other_binding)
            patterns.append(f"{{ {other} . }} UNION {{ {values} }}")

    proj = " ".join(sorted(rule.get_head_variables()))
    newline = "\n          "
    return f"""
    SELECT DISTINCT {proj} ?present WHERE {{
      {_values_clause(binding)}
      GRAPH <{graph_uri}> {{
          {newline.join(patterns)}
      }}
      FILTER NOT EXISTS {{
        GRAPH <{graph_uri}> {{
          {newline.join(_supported_patterns(rule))}
        }}
      }}
      BIND (EXISTS {{ GRAPH <{graph_uri}> {{ {rule.head} . }} }} AS ?present)
    }}"""


def get_new_head_bindings(
    client: SPARQLWrapper, rule: HornRule, query: str
) -> list[tuple[tuple[str, ...], bool]]:
    """Runs a query built by `build_body_impact_query` and returns its rows as
    (head values in sorted head-variable order, as bracketed terms; whether the
    head triple is present)."""
    names = [v[1:] for v in sorted(rule.get_head_variables())]
    return [
        (
            tuple(format_term(row[name]["value"]) for name in names),
            row["present"]["value"] == "true",
        )
        for row in execute_select_query(client, query)
    ]


def get_unsupported_head_bindings(
    client: SPARQLWrapper,
    rule: HornRule,
    atoms: Iterable[Atom],
    variables: Iterable[str],
    graph_uri: str,
    limit: int,
) -> list[dict[str, str]]:
    """Returns up to `limit` distinct bindings of `variables` that satisfy `atoms`
    and the rule's head, where the head binding is not in the rule's support yet:
    head triples present that no body grounding of the rule explains.

    Values come back as bracketed terms, as in `get_atom_bindings`.
    """
    patterns = "\n          ".join(
        [f"{atom} ." for atom in atoms] + [f"{rule.head} ."]
    )
    supported = "\n          ".join(_supported_patterns(rule))
    proj = " ".join(sorted(set(variables)))

    query = f"""
    SELECT DISTINCT {proj} WHERE {{
      GRAPH <{graph_uri}> {{
          {patterns}
      }}
      FILTER NOT EXISTS {{
        GRAPH <{graph_uri}> {{
          {supported}
        }}
      }}
    }} LIMIT {limit}"""

    return [
        {f"?{name}": format_term(cell["value"]) for name, cell in row.items()}
        for row in execute_select_query(client, query)
    ]


def count_producible_heads(
    client: SPARQLWrapper, rule: HornRule, atoms: Iterable[Atom], graph_uri: str
) -> int:
    """Returns how many different heads the bodies formed by `atoms` can produce.

    This counts the distinct head-variable bindings that satisfy `atoms` in
    `graph_uri`, without requiring the head atom to be present (see `get_support`
    for the support of the rule itself). While the EDB is being built, the head
    predicate is intensional and absent from it, so this is the amount of the rule's
    support that the triples already in the EDB will yield once the head is derived.
    `atoms` is normally the rule's extensional body. Only head variables that occur
    in `atoms` are projected.
    """
    atoms = list(atoms)
    if not atoms:
        return 0

    patterns = "\n          ".join(f"{atom} ." for atom in atoms)
    atom_vars = {t for a in atoms for t in (a.subject, a.obj) if t.startswith("?")}
    proj = " ".join(sorted(rule.get_head_variables() & atom_vars))

    if proj:
        query = f"""
    SELECT (COUNT(*) AS ?heads) WHERE {{
      SELECT DISTINCT {proj} WHERE {{
        GRAPH <{graph_uri}> {{
          {patterns}
        }}
      }}
    }}"""
    else:
        # No head variable to project: the body either holds (one head) or it doesn't.
        query = f"""
    SELECT (COUNT(*) AS ?heads) WHERE {{
      SELECT * WHERE {{
        GRAPH <{graph_uri}> {{
          {patterns}
        }}
      }} LIMIT 1
    }}"""

    if results := execute_select_query(client, query):
        return int(results[0]["heads"]["value"])
    return 0


def get_atom_bindings(
    client: SPARQLWrapper,
    atoms: Iterable[Atom],
    variables: Iterable[str],
    graph_uri: str,
    limit: int,
) -> list[dict[str, str]]:
    """Returns up to `limit` distinct bindings of `variables` that satisfy `atoms`.

    Values come back as bracketed terms (`<uri>`), the same form used by the keys of
    predicate profiles. With no variables to project, the result is `[{}]` if the
    atoms hold in the graph and `[]` otherwise.
    """
    patterns = "\n          ".join(f"{atom} ." for atom in atoms)
    proj = " ".join(sorted(set(variables))) or "*"

    query = f"""
    SELECT DISTINCT {proj}
    FROM <{graph_uri}>
    WHERE {{
      {patterns}
    }} LIMIT {limit if proj != "*" else 1}"""

    rows = execute_select_query(client, query)
    if proj == "*":
        return [{}] if rows else []
    return [
        {f"?{name}": format_term(cell["value"]) for name, cell in row.items()}
        for row in rows
    ]


def get_frequency(client: SPARQLWrapper, predicate: str, graph_uri: str) -> int:
    """Returns the number of times a predicate appears in the graph."""

    query = f"""
    SELECT (COUNT(*) AS ?frequency)
    WHERE {{
      GRAPH <{graph_uri}> {{
        ?s {predicate} ?o .
      }}
    }}"""

    if results := execute_select_query(client, query):
        return int(results[0]["frequency"]["value"])

    logger.warning("Retrieved None for %s frequency in %s.", predicate, graph_uri)
    return 0


def get_triple_count(client: SPARQLWrapper, graph_uri: str) -> int:
    """Returns the total triples in a graph."""

    query = f"""
    SELECT (COUNT(*) AS ?total)
    WHERE {{
      GRAPH <{graph_uri}> {{
        ?s ?p ?o .
      }}
    }}"""

    if results := execute_select_query(client, query):
        total_triples = int(results[0]["total"]["value"])
        if total_triples == 0:
            logger.warning("Retrieved 0 triples from %s.", graph_uri)

        return total_triples
    logger.warning("Retrieved None for total triples in %s.", graph_uri)
    return 0


# ---------------------------------------------------------------------------
# Helpers for IDB/EDB generation.
# ---------------------------------------------------------------------------
def get_existing_triples(
    client: SPARQLWrapper,
    graph_uri: str,
    candidate_triples: Iterable[str],
    term_mapping: dict[str, str],
    chunk_size: int,
) -> set[str]:
    """Return triples from 'candidate_triples' that already exist in 'graph_uri'."""
    existing_triples = set()

    for chunk in _chunk_iter(candidate_triples, chunk_size):
        formatted_values = (f"({triple.strip(' .')})" for triple in chunk)
        values_clause = " ".join(formatted_values)

        query = f"""
            SELECT ?s ?p ?o
            WHERE {{
                GRAPH <{graph_uri}> {{
                    ?s ?p ?o .
                    VALUES (?s ?p ?o) {{ {values_clause} }}
                }}
            }}
        """

        bindings = execute_select_query(client, query)

        existing_triples.update(
            format_triple(
                subject=from_binding_row("?s", binding_row)[0],
                predicate=from_binding_row("?p", binding_row)[0],
                obj=from_binding_row("?o", binding_row)[0],
                term_mapping=term_mapping,
            )
            for binding_row in bindings
        )

    return existing_triples
