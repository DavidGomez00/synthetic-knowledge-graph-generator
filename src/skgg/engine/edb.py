"""Builds the Extensional Database (EDB): a set of ground triples over extensional
predicates (those never inferred by a rule head) that satisfy both the source
graph's predicate profiles and the rule bodies that will later drive IDB generation.

`generate_extensional_predicates` first places each directed extensional relation's
self-loops (`place_self_loops`), as many as the source has, and no later step makes
another one. It then iterates three strategies per predicate, in order, until every
extensional predicate's target frequency is reached ("closed"):
  1. `check_direct_matches`: deterministic matches forced by profile counts.
  2. `check_triples_from_rule`: bindings satisfying a rule's extensional body,
     constructed directly from the profiles (`generator.sample_groundings`) so a
     chosen triple never violates another predicate's remaining domain/range budget.
  3. `insert_random_triples`: random assignment for whatever isn't pinned down
     by the first two steps, with subjects and objects drawn weighted by their
     remaining budget, still respecting the Gale-Ryser/Havel-Hakimi
     solvability check (`generator.is_assignment_solvable`).

A relation whose symmetric rule is kept is written as undirected edges, both
directions at once. Direct matches skip it, rule groundings insert both directions,
and once every rule is grounded `generate_undirected_relations` samples the budget
left. If another rule also derives the relation, the EDB leaves it empty and
`sample_derived_undirected_relations` samples the budget left after completion.
"""

import heapq
import logging
import random
from collections import Counter
from collections.abc import Collection, Iterator

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    TripleBuffer,
    count_producible_heads,
    get_atom_bindings,
    get_domain,
    get_frequency,
    get_reflexivity,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import (
    Atom,
    HornRule,
    get_extensional_dependencies,
    get_intensional_preds,
)
from skgg.core.utils import format_triple, short_term
from skgg.engine.generator import (
    decrement_counts,
    is_assignment_solvable,
    sample_groundings,
    update_closed_preds,
)
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)

# Draws of an object set that `insert_random_triples` tries before giving up.
MAX_RANDOM_DRAWS = 1000


# ---------------------------------------------------------------------------
# Self-loops, before any other triple.
# ---------------------------------------------------------------------------
def place_self_loops(
    client: SPARQLWrapper,
    edb_uri: str,
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
) -> int:
    """Buffers the self-loops of each relation of `profiles`, as many triples
    `(e, p, e)` as its `PredicateProfile.reflexivity`, before any other triple is
    generated. No later step makes a self-loop, so each relation ends with the
    source's count.

    A self-loop spends one domain count, one range count and one unit of frequency.
    An entity that can only reach its counts with a self-loop (it needs every object,
    or every subject, itself included) gets one first, with no check, since every
    completion of the profile has it. The others are drawn uniformly from the
    entities in both the domain and the range, as `_undirected_edges` does for
    relations generated as undirected edges, and each of those self-loops must pass
    `is_assignment_solvable`.

    Args:
        client: Wrapper for SPARQL queries.
        edb_uri: The URI where the EDB triples will be inserted.
        profiles: The directed extensional relations; mutated.
        term_mapping: Mapping of terms to their string representations.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        buffer: Shared buffer that accumulates triples across calls.

    Returns:
        The number of buffered triples.
    """
    placed: dict[str, int] = {}

    def loops() -> Iterator[str]:
        """Yields the self-loops of each relation, spending its profile."""
        for predicate in sorted(profiles):
            profile = profiles[predicate]
            if profile.reflexivity <= 0:
                continue
            candidates = list(profile.domain.keys() & profile.range.keys())
            random.shuffle(candidates)
            placed[predicate] = 0
            while placed[predicate] < profile.reflexivity and candidates:
                forced = next(
                    (
                        i
                        for i, entity in enumerate(candidates)
                        if profile.domain[entity] >= len(profile.range)
                        or profile.range[entity] >= len(profile.domain)
                    ),
                    None,
                )
                entity = candidates.pop(
                    len(candidates) - 1 if forced is None else forced
                )
                if forced is None and not is_assignment_solvable(
                    profile, entity, entity
                ):
                    continue
                decrement_counts(profile.domain, entity)
                decrement_counts(profile.range, entity)
                profile.frequency -= 1
                placed[predicate] += 1
                yield format_triple(entity, predicate, entity, term_mapping)

    count = buffer.add(loops())
    buffer.flush_if_full(client=client, graph_uri=edb_uri, chunk_size=chunk_size)

    if placed:
        logger.info(
            "[Self-loops] Placed %d self-loops (placed/target): %s",
            count,
            ", ".join(
                f"{short_term(p)} {n}/{profiles[p].reflexivity}"
                for p, n in sorted(placed.items(), key=lambda item: short_term(item[0]))
            ),
        )
    for predicate, n in placed.items():
        if n < profiles[predicate].reflexivity:
            logger.warning(
                "[Self-loops] %s: placed %d of %d self-loops.",
                short_term(predicate),
                n,
                profiles[predicate].reflexivity,
            )
    return count


# ---------------------------------------------------------------------------
# Step 1: Direct matching of triples.
# ---------------------------------------------------------------------------
def check_direct_matches(
    client: SPARQLWrapper,
    edb_uri: str,
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
    checked: dict[str, int],
) -> int:
    """Retrieves the triples that must be added to the EDB from a set of rules and
    profiles and buffers them for insertion into the EDB (see `TripleBuffer`) —
    deciding them never requires a DB read, so they don't need to land
    immediately.

    `checked` holds each predicate's remaining frequency at the last check that
    found nothing. A predicate's matches depend only on its own domain and range,
    and every triple spent lowers its frequency, so a predicate still at that
    frequency is skipped. Pass the same dict to every call of a run.

    Returns the number of triples buffered for the EDB."""

    def direct_triples() -> Iterator[str]:
        """Yields triples that must be added to the EDB."""
        while True:
            progress_made = False

            for predicate, profile in profiles.items():
                if profile.closed or checked.get(predicate) == profile.frequency:
                    continue
                remaining = profile.frequency

                # Check for direct matches on domain
                for subject, frequency in list(profile.domain.items()):
                    if frequency == len(profile.range) - (subject in profile.range):
                        for obj in list(profile.range.keys() - {subject}):
                            yield format_triple(subject, predicate, obj, term_mapping)
                            decrement_counts(profile.range, obj)
                            profile.frequency -= 1
                        del profile.domain[subject]

                # Check for direct matches on range
                for obj, frequency in list(profile.range.items()):
                    if frequency == len(profile.domain) - (obj in profile.domain):
                        for subject in list(profile.domain.keys() - {obj}):
                            yield format_triple(subject, predicate, obj, term_mapping)
                            decrement_counts(profile.domain, subject)
                            profile.frequency -= 1
                        del profile.range[obj]

                if profile.frequency == remaining:
                    checked[predicate] = remaining
                else:
                    progress_made = True

            if not progress_made:
                break

    count = buffer.add(direct_triples())
    buffer.flush_if_full(client=client, graph_uri=edb_uri, chunk_size=chunk_size)
    return count


# ---------------------------------------------------------------------------
# Step 2: Extract ext. predicates from rule bodies
# ---------------------------------------------------------------------------
def check_triples_from_rule(
    rule: HornRule,
    intensional_preds: set[str],
    profiles: dict[str, PredicateProfile],
    client: SPARQLWrapper,
    term_mapping: dict[str, str],
    edb_uri: str,
    chunk_size: int,
    undirected_preds: Collection[str] = (),
    skip_reflexivity: bool = False,
) -> int:
    """Builds groundings of a rule's extensional body and inserts them into the EDB.

    Args:
        rule: The HornRule being evaluated.
        intensional_preds: Set of intensional predicate strings.
        profiles: Global predicate profiles tracking domains and ranges.
        client: Wrapper for SPARQL queries.
        term_mapping: Mapping of terms to their string representations.
        edb_uri: The URI of the target EDB.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        undirected_preds: Predicates whose triples are inserted in both directions.
        skip_reflexivity: Use the grounding logic before the self-loop fix (see
            `generator.sample_grounding_groups`).

    Returns:
        The number of successfully inserted triples.
    """

    closed_preds = {p for p, profile in profiles.items() if profile.closed}
    excluded_preds = intensional_preds | closed_preds
    # A predicate with no domain or range left can't be grounded, even if it has not
    # been flagged as closed yet.
    exhausted_preds = {
        p for p, profile in profiles.items() if not profile.domain or not profile.range
    }
    candidate_atoms = [
        a for a in rule.body if a.predicate not in excluded_preds | exhausted_preds
    ]
    if unprofiled := {a.predicate for a in candidate_atoms} - profiles.keys():
        logger.warning(
            "Checking triples from rule %s: No profile for %s (absent from the source "
            "graph or a predicate format mismatch); ignoring those atoms.",
            rule.rule_id,
            sorted(unprofiled),
        )
    body_atoms = [a for a in candidate_atoms if a.predicate in profiles]
    if not body_atoms:
        return 0

    # Existing triples (also those of closed predicates, e.g. created for another
    # rule) may already yield some of the heads this rule needs.
    producible_heads = count_producible_heads(
        client, rule, rule.get_extensional_body(intensional_preds), edb_uri
    )
    missing_heads = int(rule.support - producible_heads)
    if missing_heads <= 0:
        return 0

    logger.debug("Generating predicates from rule %s: %s", rule.rule_id, body_atoms)

    triples = sample_groundings(
        client=client,
        target_uri=edb_uri,
        atoms=body_atoms,
        head_vars=rule.get_head_variables(),
        profiles=profiles,
        missing_heads=missing_heads,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
        undirected=undirected_preds,
        skip_reflexivity=skip_reflexivity,
    )

    return insert_triples_sparql(
        client=client,
        graph_uri=edb_uri,
        triple_stream=iter(triples),
        chunk_size=chunk_size,
    )


# ---------------------------------------------------------------------------
# Step 3: Produce triples from a random subject.
# ---------------------------------------------------------------------------
def _weighted_sample(
    weights: dict[str, int], k: int, exclude: str | None = None
) -> list[str]:
    """Draws `k` distinct keys of `weights` other than `exclude` without
    replacement, each with probability proportional to its (positive) weight
    (Efraimidis-Spirakis).

    The keys of the draw are computed in one list, in the order of `weights`, which
    takes the same random numbers as a key function would and is faster."""
    rand = random.random
    if exclude in weights:
        keys = [key for key in weights if key != exclude]
        scores = [rand() ** (1 / w) for key, w in weights.items() if key != exclude]
    else:
        keys = list(weights)
        scores = [rand() ** (1 / w) for w in weights.values()]
    return [
        keys[i] for i in heapq.nlargest(k, range(len(keys)), key=scores.__getitem__)
    ]


def insert_random_triples(
    client: SPARQLWrapper,
    edb_uri: str,
    profile: PredicateProfile,
    predicate: str,
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
    skip_reflexivity: bool = False,
) -> int:
    """Generates random triples from a single predicate's profile and buffers them
    for insertion (see `TripleBuffer`) — the random assignment never requires a DB
    read, so the triples don't need to land immediately.

    The subject is drawn with probability proportional to its remaining domain
    count and gets all of its triples at once. Its objects are drawn without
    replacement from the range without the subject itself (self-loops are placed
    first, by `place_self_loops`), with probability proportional to their remaining
    range count, until a set passes the solvability check. The check is the one of
    `generator.is_assignment_solvable` for a relation without self-loops. Serving
    large demands while the most partners remain means fewer sets fail that check.

    With `skip_reflexivity` (the generator before the self-loop fix), the subject
    can be drawn as its own object, and the check is the plain one for a relation
    that may have self-loops. Both versions check a draw in one pass over the
    range.

    Args:
        client: Wrapper for SPARQL queries.
        edb_uri: The URI where the EDB triples will be inserted.
        profile: The predicate profile tracking available domains and ranges.
        predicate: The predicate string for the triples.
        term_mapping: Mapping of terms to their string representations.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        buffer: Shared buffer that accumulates triples across calls.
        skip_reflexivity: Allow self-loops, as before the self-loop fix.

    Returns:
        The number of buffered triples.

    Raises:
        ValueError: If there are not enough available objects to satisfy the domain,
            or no object set passes the solvability check in `MAX_RANDOM_DRAWS`
            draws.
    """

    available_subjects = list(profile.domain.keys())
    if not available_subjects:
        logger.warning("Empty domain for predicate %s.", predicate)
        return 0

    subject = random.choices(available_subjects, weights=list(profile.domain.values()))[
        0
    ]
    required_count = profile.domain[subject]

    # Without `skip_reflexivity`, no entity can be its own partner:
    # `place_self_loops` placed the self-loops.
    loop_free = not skip_reflexivity
    excluded = subject if loop_free else None
    available_objects = len(profile.range) - (loop_free and subject in profile.range)
    if available_objects < required_count:
        raise ValueError(
            f"Error assigning triples for {predicate}. "
            f"{subject} must be in {required_count} triples. "
            f"There are only {available_objects} different objects available."
        )

    # The other subjects keep their counts. Each needs that many objects, other than
    # itself if loop-free, so one that is also an object needs one more. The same
    # holds for the objects and their subjects. The range doesn't change between
    # draws, so the objects' needs are counted once, and a draw only adjusts those
    # of its chosen objects. Set operations and `map` keep these passes in C.
    domain, range_ = profile.domain, profile.range
    sim_domain_len = len(domain) - 1
    # The entities other than the subject that are in both, which need one more.
    bonus = domain.keys() & range_.keys() if loop_free else set()
    bonus.discard(subject)
    # The largest count among the other subjects: the largest one, unless that is
    # the subject's alone.
    top = max(domain.values())
    if domain[subject] == top and list(domain.values()).count(top) == 1:
        top = max(filter(top.__gt__, domain.values()), default=0)
    max_need = max(top, max(map(domain.__getitem__, bonus), default=-1) + 1)
    need_counts = Counter(range_.values())
    for count, n in Counter(map(range_.__getitem__, bonus)).items():
        need_counts[count] -= n
        need_counts[count + 1] += n
    needs_desc = sorted((v for v, n in need_counts.items() if n > 0), reverse=True)
    chosen_objects: list[str] = []

    for draw in range(1, MAX_RANDOM_DRAWS + 1):
        chosen_objects = _weighted_sample(range_, required_count, exclude=excluded)
        chosen_set = set(chosen_objects)

        # Each chosen object loses a count, and leaves the range with its last one.
        # Then the objects left, and the largest need among them.
        shift: Counter[int] = Counter()
        dropped = 0
        for obj in chosen_set:
            need = range_[obj] + (obj in bonus)
            shift[need] -= 1
            if range_[obj] > 1:
                shift[need - 1] += 1
            else:
                dropped += 1
        sim_range_len = len(range_) - dropped
        max_range = next((v for v in needs_desc if need_counts[v] + shift[v] > 0), 0)
        max_range = max(
            (v for v, n in shift.items() if n > 0 and v > max_range), default=max_range
        )

        # A subject stops being an object when this one takes its last range count,
        # which lowers its need. Only worth computing when `max_need` doesn't fit.
        max_domain = max_need
        if loop_free and max_need > sim_range_len:
            max_domain = max(
                (
                    count + (k in range_ and not (range_[k] == 1 and k in chosen_set))
                    for k, count in domain.items()
                    if k != subject
                ),
                default=0,
            )

        if max_domain <= sim_range_len and max_range <= sim_domain_len:
            if draw > 1:
                logger.debug("Rejected %d object sets for %s.", draw - 1, subject)
            break
    else:
        raise ValueError(
            f"Error assigning triples for {predicate}. No set of {required_count} "
            f"objects for {subject} passed the solvability check in "
            f"{MAX_RANDOM_DRAWS} draws."
        )

    def random_matches() -> Iterator[str]:
        """Yield random triples generated for a predicate using its profile."""

        profile.frequency -= required_count
        del profile.domain[subject]

        for obj in chosen_objects:
            decrement_counts(profile.range, obj)
            yield format_triple(subject, predicate, obj, term_mapping)

    count = buffer.add(random_matches())
    buffer.flush_if_full(client=client, graph_uri=edb_uri, chunk_size=chunk_size)
    return count


# ---------------------------------------------------------------------------
# Symmetric relations: undirected edges.
# ---------------------------------------------------------------------------
def get_undirected_rule_ids(
    rules: dict[str, HornRule], profiles: dict[str, PredicateProfile]
) -> set[str]:
    """Returns the IDs of the symmetric rules whose relation can be generated as
    undirected edges: every entity has the same domain and range count."""
    return {
        r_id
        for r_id, rule in rules.items()
        if rule.is_symmetric()
        and profiles[rule.head.predicate].domain == profiles[rule.head.predicate].range
    }


def _edge_key(a: str, b: str) -> tuple[str, str]:
    """Returns the key of the undirected edge between two entities."""
    return (a, b) if a < b else (b, a)


def _undirected_edges(
    degrees: dict[str, int], reflexive: int, existing: set[tuple[str, str]]
) -> tuple[list[str], list[tuple[str, str]]]:
    """Draws a random simple undirected graph with the given degrees by stub
    matching: each entity gets one stub per degree, and shuffled stubs are paired.

    A self-loop, or a pair that is already an edge, is repaired by swapping it with
    a random new edge `(c, d)` into `(a, c)` and `(b, d)`. Swaps only touch new edges,
    so `existing` stays as it is. A pair that `MAX_RANDOM_DRAWS` swaps can't repair
    is dropped, and so is an odd stub left over, so the graph can end short of
    `degrees`.

    Args:
        degrees: Degree still to assign per entity.
        reflexive: Self-loops to place, one per entity, each spending one degree.
        existing: Keys (`_edge_key`) of the edges already in the graph.

    Returns:
        The entities with a new self-loop, and the new edges.
    """
    remaining = dict(degrees)
    candidates = [e for e in remaining if _edge_key(e, e) not in existing]
    loops = random.sample(candidates, min(reflexive, len(candidates)))
    for entity in loops:
        remaining[entity] -= 1

    stubs = [entity for entity, count in remaining.items() for _ in range(count)]
    random.shuffle(stubs)

    placed = set(existing)
    edges: list[tuple[str, str]] = []
    clashes: list[tuple[str, str]] = []
    for a, b in zip(stubs[::2], stubs[1::2], strict=False):
        if a == b or _edge_key(a, b) in placed:
            clashes.append((a, b))
        else:
            placed.add(_edge_key(a, b))
            edges.append((a, b))

    for a, b in clashes:
        for _ in range(MAX_RANDOM_DRAWS if edges else 0):
            index = random.randrange(len(edges))
            c, d = edges[index]
            if random.random() < 0.5:
                c, d = d, c
            first, second = _edge_key(a, c), _edge_key(b, d)
            if a == c or b == d or first == second or {first, second} & placed:
                continue
            placed.remove(_edge_key(c, d))
            placed.update((first, second))
            edges[index] = (a, c)
            edges.append((b, d))
            break

    return loops, edges


def _sample_undirected(
    client: SPARQLWrapper,
    predicate: str,
    profile: PredicateProfile,
    degrees: dict[str, int],
    reflexive: int,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> int:
    """Samples undirected edges for `degrees`, around the edges of `predicate`
    already in the graph, inserts both directions of each one and closes the
    profile, so the fill step leaves the relation alone.

    Returns:
        The number of triples inserted.
    """
    frequency = get_frequency(client, predicate, graph_uri)
    existing = {
        _edge_key(row["?s"], row["?o"])
        for row in get_atom_bindings(
            client, [Atom("?s", predicate, "?o")], ["?s", "?o"], graph_uri, frequency
        )
    }
    loops, edges = _undirected_edges(degrees, reflexive, existing)

    def triples() -> Iterator[str]:
        """Yields each self-loop once and each edge in both directions."""
        for entity in loops:
            yield format_triple(entity, predicate, entity, term_mapping)
        for a, b in edges:
            yield format_triple(a, predicate, b, term_mapping)
            yield format_triple(b, predicate, a, term_mapping)

    inserted = insert_triples_sparql(
        client=client,
        graph_uri=graph_uri,
        triple_stream=triples(),
        chunk_size=chunk_size,
    )
    profile.closed = True

    budget = sum(degrees.values())
    logger.info(
        "[Symmetric] %s: sampled %d of the %d triples left (%d edges, %d self-loops).",
        short_term(predicate),
        inserted,
        budget,
        len(edges),
        len(loops),
    )
    if inserted < budget:
        logger.warning(
            "[Symmetric] %s ends %d triples short: no valid pairing left for them.",
            short_term(predicate),
            budget - inserted,
        )
    return inserted


def generate_undirected_relations(
    client: SPARQLWrapper,
    predicates: Collection[str],
    profiles: dict[str, PredicateProfile],
    edb_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> None:
    """Samples, in the EDB, the budget that rule groundings left of each open
    relation of `predicates`, which only its symmetric rule derives."""
    for predicate in sorted(predicates):
        profile = profiles[predicate]
        if profile.closed:
            continue
        profile.frequency -= _sample_undirected(
            client,
            predicate,
            profile,
            profile.domain,
            profile.reflexivity,
            edb_uri,
            term_mapping,
            chunk_size,
        )
        profile.domain.clear()
        profile.range.clear()


def sample_derived_undirected_relations(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    profiles: dict[str, PredicateProfile],
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> int:
    """Samples what completion left short of each open symmetric relation that
    another rule also derives, in the completed graph `graph_uri`. An entity already
    at or past its source degree gets no new edge.

    The profiles of these relations are unspent: direct matches skip them and rule
    groundings leave out intensional predicates.

    Returns:
        The number of triples inserted.
    """
    symmetric_preds = {r.head.predicate for r in rules.values() if r.is_symmetric()}
    inserted = 0
    for predicate in sorted(symmetric_preds & get_intensional_preds(rules)):
        profile = profiles[predicate]
        if profile.closed:
            continue
        current = get_domain(client, graph_uri, predicate)
        degrees = {
            entity: count - current.get(entity, 0)
            for entity, count in profile.domain.items()
            if count > current.get(entity, 0)
        }
        reflexive = profile.reflexivity - get_reflexivity(client, graph_uri, predicate)
        inserted += _sample_undirected(
            client,
            predicate,
            profile,
            degrees,
            max(reflexive, 0),
            graph_uri,
            term_mapping,
            chunk_size,
        )
    return inserted


# ---------------------------------------------------------------------------
# Generate extensional predicates.
# ---------------------------------------------------------------------------
def _format_relations(
    preds: set[str],
    profiles: dict[str, PredicateProfile],
    targets: dict[str, int],
    intensional_preds: set[str],
) -> str:
    """Formats each predicate of `preds` as `name current/target (status)`, where
    current is how many triples were generated so far (its target minus its
    remaining `PredicateProfile.frequency`). Intensional predicates are marked."""

    def relation(pred: str) -> str:
        profile = profiles[pred]
        status = "closed" if profile.closed else "open"
        if pred in intensional_preds:
            status += ", intensional"
        current = targets[pred] - profile.frequency
        return f"{short_term(pred)} {current}/{targets[pred]} ({status})"

    return ", ".join(relation(p) for p in sorted(preds, key=short_term))


def generate_extensional_predicates(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    edb_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile],
    skip_reflexivity: bool = False,
) -> None:
    """Generates an EDB from a set of rules and predicate profiles.

    With `skip_reflexivity`, the EDB is generated as before the self-loop fix
    (`35a1b81`): no `place_self_loops`, and rule groundings and random triples may
    make self-loops by chance, so a relation's self-loops don't match the source's.
    Relations generated as undirected edges place theirs either way.

    Args:
        client: Wrapper for SPARQL queries.
        rules: Mapping of rule IDs to HornRule objects.
        term_mapping: Mapping of terms to their string representations.
        edb_uri: The URI where the Extensional Database (EDB) will be generated.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        profiles: The metrics for each predicate in the original graph.
        skip_reflexivity: Generate as before the self-loop fix (see above).
    """
    # Instantiate a new graph
    initialize_graph(
        client=client,
        source=None,
        new_graph_uri=edb_uri,
        chunk_size=chunk_size,
    )

    # Buffers triples decided by direct-match/random assignment (no DB read
    # needed to produce them), so they're inserted in fewer, larger batches
    # instead of one round trip per call. Flushed before any step that needs
    # to read `edb_uri`, and unconditionally before this function returns.
    buffer = TripleBuffer()

    # Target frequency per predicate, copied before the steps below spend
    # `PredicateProfile.frequency` as a remaining budget.
    targets = {p: pr.frequency for p, pr in profiles.items()}
    intensional_preds = get_intensional_preds(rules)
    symmetric_preds = {r.head.predicate for r in rules.values() if r.is_symmetric()}
    # Relations that only their symmetric rule derives.
    undirected_preds = symmetric_preds - intensional_preds
    # A direct match would write only one direction of a symmetric relation.
    directed_profiles = {
        p: pr for p, pr in profiles.items() if p not in symmetric_preds
    }
    direct_checked: dict[str, int] = {}

    # Self-loops first: no later step makes one. Relations generated as undirected
    # edges place theirs when they are sampled.
    if skip_reflexivity:
        logger.info("[Self-loops] Skipped (--skip-reflexivity): left to chance.")
    else:
        place_self_loops(
            client=client,
            edb_uri=edb_uri,
            profiles={
                p: pr
                for p, pr in directed_profiles.items()
                if p not in intensional_preds
            },
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
        )
    before_warm_up = {p: pr.frequency for p, pr in profiles.items()}

    # Warm-up: Direct matches for any predicate untill no new triples.
    logger.info("Start warm-up.")

    while True:
        if count := check_direct_matches(
            client=client,
            edb_uri=edb_uri,
            profiles=directed_profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
            checked=direct_checked,
        ):
            logger.info("[Warm-up] Added %d direct matches to EDB.", count)
        else:
            break

    update_closed_preds(profiles=profiles)

    # Direct matches cover every predicate, so intensional ones may get triples too.
    if warm_preds := {
        p for p, pr in profiles.items() if pr.frequency < before_warm_up[p]
    }:
        logger.info(
            "[Warm-up] Relations with direct matches (%d, added/target): %s",
            len(warm_preds),
            _format_relations(warm_preds, profiles, targets, intensional_preds),
        )
    else:
        logger.info("[Warm-up] No direct matches.")

    extensional_dependency = get_extensional_dependencies(rules)
    extensional_profiles = {
        pred: profiles[pred] for pred in (profiles.keys() - intensional_preds)
    }
    if not extensional_profiles:
        logger.warning("Retrieved 0 extensional predicates, EDB will be empty.")

    checked_rules: set[str] = set()
    step = 0

    def _end_edb() -> bool:
        """Updates closed predicates and logs progress. Returns True if all extensional
        profiles are closed."""

        if update_closed_preds(profiles):
            logger.info(
                "[Step %d] Closed predicates [%d/%d].",
                step,
                sum(1 for pr in profiles.values() if pr.closed),
                len(profiles),
            )

        return all(pr.closed for pr in extensional_profiles.values())

    logger.info("Creating EDB")
    open_ext_preds = {p for p, pr in extensional_profiles.items() if not pr.closed}
    logger.info(
        "Extensional relations to generate (%d/%d, current/target): %s",
        len(open_ext_preds),
        len(extensional_profiles),
        _format_relations(open_ext_preds, profiles, targets, intensional_preds)
        or "none",
    )
    logger.info(
        "Closed predicates [%d/%d].",
        sum(1 for pr in profiles.values() if pr.closed),
        len(profiles),
    )

    while not _end_edb():
        step += 1
        progress = False

        # Step 1: Check direct matches
        if direct_count := check_direct_matches(
            client=client,
            edb_uri=edb_uri,
            profiles=directed_profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
            checked=direct_checked,
        ):
            progress = True
            logger.debug("[Step %d] Added %d triples directly.", step, direct_count)

            if _end_edb():
                break

        # Step 2: Check rule bodies
        if not progress and (len(checked_rules) < len(rules)):
            # check_triples_from_rule queries edb_uri, so it needs every triple to
            # already be visible in the DB, not just buffered.
            buffer.flush(client=client, graph_uri=edb_uri, chunk_size=chunk_size)

            for r_id, r in rules.items():
                if (
                    r_id in checked_rules
                    or extensional_dependency[r_id] - checked_rules
                ):
                    continue

                producible_heads = count_producible_heads(
                    client, r, r.get_extensional_body(intensional_preds), edb_uri
                )
                if producible_heads >= r.support:
                    checked_rules.add(r_id)
                    r.closed = True
                    continue

                if len(r.get_extensional_body(intensional_preds)) <= 1:
                    # We can just use random assignments
                    checked_rules.add(r_id)
                    continue

                # Open rule with 2 or more ext. predicates and no ext. dependencies. get
                # triples by sampling groundings.
                r_count = check_triples_from_rule(
                    rule=r,
                    intensional_preds=intensional_preds,
                    profiles=profiles,
                    client=client,
                    term_mapping=term_mapping,
                    edb_uri=edb_uri,
                    chunk_size=chunk_size,
                    undirected_preds=undirected_preds,
                    skip_reflexivity=skip_reflexivity,
                )
                checked_rules.add(r_id)

                if r_count:
                    progress = True
                    logger.debug(
                        "[Step %d] Added %d triples using rule %s's body.",
                        step,
                        r_count,
                        r_id,
                    )
                    break

        if _end_edb():
            break

        # Step 3: Assign randomly
        if not progress:
            # Symmetric relations wait for every rule's groundings to spend their
            # budget first.
            if len(checked_rules) >= len(rules) and any(
                not profiles[p].closed for p in undirected_preds
            ):
                buffer.flush(client=client, graph_uri=edb_uri, chunk_size=chunk_size)
                generate_undirected_relations(
                    client=client,
                    predicates=undirected_preds,
                    profiles=profiles,
                    edb_uri=edb_uri,
                    term_mapping=term_mapping,
                    chunk_size=chunk_size,
                )
                continue

            open_preds = [
                p
                for p, pr in extensional_profiles.items()
                if not pr.closed and p not in undirected_preds
            ]
            if not open_preds:
                continue
            predicate = random.choice(open_preds)
            profile = profiles[predicate]

            ran_count = insert_random_triples(
                client=client,
                edb_uri=edb_uri,
                profile=profile,
                predicate=predicate,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
                buffer=buffer,
                skip_reflexivity=skip_reflexivity,
            )
            if ran_count:
                logger.debug(
                    "[Step %d] Added %d triples by random assignment.", step, ran_count
                )

            if _end_edb():
                break

    # Guarantee every buffered triple lands before this graph is considered
    # complete by callers (e.g. get_triple_count, complete_graph).
    buffer.flush(client=client, graph_uri=edb_uri, chunk_size=chunk_size)
