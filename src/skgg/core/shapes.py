"""SHACL-SPARQL shapes: reads the `sh:sparql` constraints of a `<dataset>.shapes.ttl`
file, which `core/queries.build_shape_query` turns into queries against a named
graph (see `cli/validate.py`).

Only the shapes file is parsed with rdflib; the data graph stays in the store.
Supported: `sh:sparql` constraints with an `sh:select` query, on shapes targeted by
`sh:targetSubjectsOf`, `sh:targetObjectsOf`, `sh:targetClass` or `sh:targetNode`.
Every other constraint type is skipped with a warning.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import SH
from rdflib.term import Node

logger = logging.getLogger(__name__)

# Target predicate -> the `SparqlConstraint.targets` kind it becomes.
TARGET_KINDS = {
    SH.targetSubjectsOf: "subjectsOf",
    SH.targetObjectsOf: "objectsOf",
    SH.targetClass: "class",
    SH.targetNode: "node",
}

# Pre-bound SHACL-SPARQL variables that a query against the data graph alone
# can't provide.
UNSUPPORTED_VARIABLES = re.compile(r"[?$](shapesGraph|currentShape|PATH)\b")

# A SPARQL result binding, as returned by `queries.execute_select_query`.
Binding = dict[str, dict[str, str]]


@dataclass(frozen=True)
class SparqlConstraint:
    """One `sh:sparql` constraint of a shape.

    Attributes:
        shape: The shape's IRI (or blank node ID).
        message: The constraint's `sh:message`, or the shape's IRI without one.
        select: The `sh:select` query text, as written in the shapes file.
        targets: `(kind, iri)` pairs, kind being one of `TARGET_KINDS`' values,
            e.g. `("subjectsOf", "http://FrenchRoyalty.org/father")`.
        prefixes: Prefix -> namespace for the query: the file's `@prefix`
            declarations plus the constraint's `sh:prefixes`/`sh:declare`.
    """

    shape: str
    message: str
    select: str
    targets: tuple[tuple[str, str], ...]
    prefixes: dict[str, str] = field(default_factory=dict, compare=False)


def _is_true(graph: Graph, node: Node) -> bool:
    """Whether `node` has `sh:deactivated true`."""
    value = graph.value(node, SH.deactivated)
    return isinstance(value, Literal) and value.toPython() is True


def _declared_prefixes(graph: Graph, constraint: Node) -> dict[str, str]:
    """The `sh:prefix`/`sh:namespace` pairs declared through the constraint's
    `sh:prefixes` (standard SHACL-SPARQL, as opposed to Turtle `@prefix`)."""
    prefixes: dict[str, str] = {}
    for holder in graph.objects(constraint, SH.prefixes):
        for declaration in graph.objects(holder, SH.declare):
            prefix = graph.value(declaration, SH.prefix)
            namespace = graph.value(declaration, SH.namespace)
            if prefix is not None and namespace is not None:
                prefixes[str(prefix)] = str(namespace)
    return prefixes


def load_shapes(shapes_file: Path | str) -> list[SparqlConstraint]:
    """Reads every supported `sh:sparql` constraint of a Turtle shapes file.

    A shape or constraint is skipped, with a warning, when it is deactivated,
    has no target, has no `sh:select`, uses `$shapesGraph`/`$currentShape`/
    `$PATH`, or (for a declared `sh:NodeShape`/`sh:PropertyShape`) has no
    `sh:sparql` constraint at all.
    """
    graph = Graph(bind_namespaces="none")
    graph.parse(shapes_file, format="turtle")
    file_prefixes = {prefix: str(namespace) for prefix, namespace in graph.namespaces()}

    declared = set(graph.subjects(predicate=None, object=SH.NodeShape)) | set(
        graph.subjects(predicate=None, object=SH.PropertyShape)
    )
    for shape in sorted(declared - set(graph.subjects(SH.sparql)), key=str):
        logger.warning("Skipping shape <%s>: it has no sh:sparql constraint.", shape)

    constraints: list[SparqlConstraint] = []
    for shape in sorted(set(graph.subjects(SH.sparql)), key=str):
        if _is_true(graph, shape):
            logger.info("Skipping shape <%s>: deactivated.", shape)
            continue
        targets = tuple(
            sorted(
                (kind, str(target))
                for predicate, kind in TARGET_KINDS.items()
                for target in graph.objects(shape, predicate)
                if isinstance(target, URIRef)
            )
        )
        if not targets:
            logger.warning("Skipping shape <%s>: it has no supported target.", shape)
            continue

        for constraint in graph.objects(shape, SH.sparql):
            select = graph.value(constraint, SH.select)
            if select is None:
                logger.warning("Skipping a constraint of <%s>: no sh:select.", shape)
                continue
            if _is_true(graph, constraint):
                logger.info("Skipping a constraint of <%s>: deactivated.", shape)
                continue
            if match := UNSUPPORTED_VARIABLES.search(str(select)):
                logger.warning(
                    "Skipping a constraint of <%s>: %s is not supported.",
                    shape,
                    match[0],
                )
                continue
            message = graph.value(constraint, SH.message)
            constraints.append(
                SparqlConstraint(
                    shape=str(shape),
                    message=str(message) if message is not None else str(shape),
                    select=str(select),
                    targets=targets,
                    prefixes=file_prefixes | _declared_prefixes(graph, constraint),
                )
            )

    logger.info("Read %d SPARQL constraints from %s.", len(constraints), shapes_file)
    return constraints


def triples_in_row(
    row: Binding,
) -> list[tuple[dict[str, str], dict[str, str] | None, dict[str, str] | None]]:
    """The `(this, path, value)` bindings a result row names: `?this ?path ?value`,
    then `?this2 ?path2 ?value2`, and so on until a `?thisN` is unbound. `path`
    and `value` are None when the query leaves them unbound."""
    groups = []
    for n in range(1, len(row) + 2):
        suffix = "" if n == 1 else str(n)
        this = row.get(f"this{suffix}")
        if this is None:
            break
        groups.append((this, row.get(f"path{suffix}"), row.get(f"value{suffix}")))
    return groups
