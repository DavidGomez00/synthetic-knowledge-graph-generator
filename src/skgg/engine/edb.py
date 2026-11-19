"""Builds the initial facts (F0): a set of ground triples over non-deducible
predicates (those never inferred by a rule head) that satisfy both the source
graph's predicate profiles and the rule bodies that will later drive completion.
The module keeps its earlier name, `edb.py` (from the extensional database of
Datalog, which inspired the split; see "Initial facts (F0) and deduced facts (F*)"
in `docs/concepts.md`).

`generate_non_deducible_predicates` first places each directed non-deducible relation's
self-loops (`place_self_loops`), as many as the source has, and no later step makes
another one. It then iterates three strategies per predicate, in order, until every
non-deducible predicate's target frequency is reached ("closed"):
  1. `check_direct_matches`: deterministic matches forced by profile counts.
  2. `check_triples_from_rule`: bindings satisfying a rule's non-deducible body,
     constructed directly from the profiles (`generator.sample_groundings`) so a
     chosen triple never violates another predicate's remaining domain/range budget.
     A partial rule (`HornRule.is_complete` is False) is grounded with its head,
     `HornRule.grounding_target` times, since completion doesn't apply it, and
     only once every complete rule is grounded.
  3. `insert_random_triples`: random assignment for whatever isn't pinned down
     by the first two steps, with subjects and objects drawn weighted by their
     remaining budget, still respecting the Gale-Ryser/Havel-Hakimi
     solvability check (`generator.is_assignment_solvable`).

A relation whose symmetric rule is kept is written as undirected edges, both
directions at once. Direct matches skip it, rule groundings insert both directions,
and once every rule is grounded `generate_undirected_relations` samples the budget
left. If another rule also derives the relation, initial-fact generation leaves it empty
and `sample_derived_undirected_relations` samples the budget left after completion.
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
    get_support,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import (
    Atom,
    HornRule,
    get_deducible_preds,
    get_non_deducible_dependencies,
    get_undirected_preds,
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
# Existing bindings of a partial rule's closed atoms fetched per missing grounding,
# for its new triples to join with (as `fill.ROWS_PER_HEAD`).
ROWS_PER_GROUNDING = 20


# ---------------------------------------------------------------------------
# Self-loops, before any other triple.
# ---------------------------------------------------------------------------
def place_self_loops(
    client: SPARQLWrapper,
    initial_facts_uri: str,
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
        initial_facts_uri: The URI where the triples of F0 will be inserted.
        profiles: The directed non-deducible relations; mutated.
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
    buffer.flush_if_full(
        client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size
    )

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
    initial_facts_uri: str,
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
    checked: dict[str, int],
) -> int:
    """Retrieves the triples that must be added to F0 from a set of rules and
    profiles and buffers them for insertion into F0 (see `TripleBuffer`) —
    deciding them never requires a DB read, so they don't need to land
    immediately.

    `checked` holds each predicate's remaining frequency at the last check that
    found nothing. A predicate's matches depend only on its own domain and range,
    and every triple spent lowers its frequency, so a predicate still at that
    frequency is skipped. Pass the same dict to every call of a run.

    Returns the number of triples buffered for F0."""

    def direct_triples() -> Iterator[str]:
        """Yields triples that must be added to F0."""
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
    buffer.flush_if_full(
        client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size
    )
    return count


# ---------------------------------------------------------------------------
# Step 2: Extract ext. predicates from rule bodies
# ---------------------------------------------------------------------------
def produced_groundings(
    client: SPARQLWrapper,
    rule: HornRule,
    deducible_preds: set[str],
    initial_facts_uri: str,
) -> int:
    """Returns how much of the rule's `grounding_target` F0 already yields.

    For a complete rule, the distinct heads its non-deducible body produces, which
    completion derives later (`count_producible_heads`). For a partial rule, its
    support in F0, since F0 holds its heads too (`get_support`)."""
    if rule.is_complete:
        return count_producible_heads(
            client,
            rule,
            rule.get_non_deducible_body(deducible_preds),
            initial_facts_uri,
        )
    return get_support(client, rule, initial_facts_uri)


def _vars(atoms: Collection[Atom]) -> set[str]:
    """Returns the variables of `atoms`."""
    return {t for a in atoms for t in (a.subject, a.obj) if t.startswith("?")}


def _spent_preds(profiles: dict[str, PredicateProfile]) -> set[str]:
    """Returns the predicates with no budget left: closed, or with no domain or
    range left, which can't be grounded even if not flagged as closed yet."""
    return {
        p
        for p, profile in profiles.items()
        if profile.closed or not profile.domain or not profile.range
    }


def _join_rows(
    client: SPARQLWrapper,
    rule: HornRule,
    atoms: list[Atom],
    initial_facts_uri: str,
    limit: int,
) -> list[dict[str, str]] | None:
    """Returns the bindings already in F0 of the body atoms of a partial rule that
    are not in `atoms` (their predicate has no budget left), for the new triples to
    join with: up to `limit` rows over the variables they share with `atoms` and
    the head. None if every body atom is in `atoms`; an empty list if those atoms
    have no fact to join."""
    spent = sorted(rule.body - set(atoms))
    if not spent:
        return None
    return get_atom_bindings(
        client,
        spent,
        _vars(spent) & (_vars(atoms) | rule.get_head_variables()),
        initial_facts_uri,
        limit,
    )


def check_triples_from_rule(
    rule: HornRule,
    deducible_preds: set[str],
    profiles: dict[str, PredicateProfile],
    client: SPARQLWrapper,
    term_mapping: dict[str, str],
    initial_facts_uri: str,
    chunk_size: int,
    undirected_preds: Collection[str] = (),
    skip_reflexivity: bool = False,
    head_triples: list[int] | None = None,
) -> int:
    """Builds groundings of the rule's initial-fact atoms
    (`HornRule.get_initial_fact_atoms`) up to its `grounding_target` and inserts them
    into F0.

    For a complete rule, these are the atoms of its non-deducible body whose
    predicates are still open; completion derives the head later. For a partial
    rule, they are its body and its head, so each grounding adds the head with its
    body. A partial rule whose head predicate has no budget left gets no such
    grounding. Its body atoms whose predicate has no budget left are not generated:
    the new triples join existing facts of F0 of those atoms instead
    (`fixed_bindings`), and if there are none the rule gets nothing.

    Args:
        rule: The HornRule being evaluated.
        deducible_preds: Set of deducible predicate strings.
        profiles: Global predicate profiles tracking domains and ranges.
        client: Wrapper for SPARQL queries.
        term_mapping: Mapping of terms to their string representations.
        initial_facts_uri: The URI of the initial facts.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        undirected_preds: Predicates whose triples are inserted in both directions.
        skip_reflexivity: Use the grounding logic before the self-loop fix (see
            `generator.sample_grounding_groups`).
        head_triples: If given, its first element is increased by the head triples
            a partial rule's groundings add.

    Returns:
        The number of successfully inserted triples.
    """
    spent_preds = _spent_preds(profiles)
    initial_fact_atoms = rule.get_initial_fact_atoms(deducible_preds)
    if unprofiled := {a.predicate for a in initial_fact_atoms} - profiles.keys():
        logger.warning(
            "Checking triples from rule %s: No profile for %s (absent from the source "
            "graph or a predicate format mismatch); ignoring those atoms.",
            rule.rule_id,
            sorted(unprofiled),
        )
    # Body atoms first, in the order of the body, then the head of a partial rule.
    atoms = [
        a
        for a in (*rule.body, rule.head)
        if a in initial_fact_atoms
        and a.predicate in profiles
        and a.predicate not in spent_preds
    ]
    if not atoms:
        return 0

    inserted = 0
    if not rule.is_complete and rule.head not in atoms:
        logger.debug(
            "Partial rule %s: no budget left for its head; leaving its support to "
            "chance.",
            rule.rule_id,
        )
    # Existing triples (also those of closed predicates, e.g. created for another
    # rule) may already yield some of the groundings this rule needs.
    elif (
        missing_heads := rule.grounding_target
        - produced_groundings(client, rule, deducible_preds, initial_facts_uri)
    ) > 0:
        fixed = None
        if not rule.is_complete:
            fixed = _join_rows(
                client,
                rule,
                atoms,
                initial_facts_uri,
                ROWS_PER_GROUNDING * missing_heads,
            )
        if fixed is not None and not fixed:
            logger.debug(
                "Partial rule %s: no F0 facts to join with; leaving its support to "
                "chance.",
                rule.rule_id,
            )
        else:
            logger.debug("Generating predicates from rule %s: %s", rule.rule_id, atoms)
            triples = sample_groundings(
                client=client,
                target_uri=initial_facts_uri,
                atoms=atoms,
                head_vars=rule.get_head_variables(),
                profiles=profiles,
                missing_heads=missing_heads,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
                fixed_bindings=fixed,
                undirected=undirected_preds,
                skip_reflexivity=skip_reflexivity,
                # A partial rule's head is the last of `atoms` when it is grounded.
                last_atom_triples=None if rule.is_complete else head_triples,
            )
            inserted += insert_triples_sparql(
                client=client,
                graph_uri=initial_facts_uri,
                triple_stream=iter(triples),
                chunk_size=chunk_size,
            )
    return inserted


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
    initial_facts_uri: str,
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
        initial_facts_uri: The URI where the triples of F0 will be inserted.
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
    buffer.flush_if_full(
        client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size
    )
    return count


# ---------------------------------------------------------------------------
# Symmetric relations: undirected edges.
# ---------------------------------------------------------------------------
def get_undirected_rule_ids(
    rules: dict[str, HornRule], profiles: dict[str, PredicateProfile]
) -> set[str]:
    """Returns the IDs of the complete symmetric rules whose relation can be generated
    as undirected edges: every entity has the same domain and range count. A partial
    symmetric rule is grounded like any other partial rule instead, a pair of
    triples per grounding."""
    return {
        r_id
        for r_id, rule in rules.items()
        if rule.is_complete
        and rule.is_symmetric()
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
    initial_facts_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> None:
    """Samples, in F0, the budget that rule groundings left of each open
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
            initial_facts_uri,
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
    groundings leave out deducible predicates.

    Returns:
        The number of triples inserted.
    """
    inserted = 0
    for predicate in sorted(get_undirected_preds(rules) & get_deducible_preds(rules)):
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
# Generate non-deducible predicates.
# ---------------------------------------------------------------------------
def _format_relations(
    preds: set[str],
    profiles: dict[str, PredicateProfile],
    targets: dict[str, int],
    deducible_preds: set[str],
) -> str:
    """Formats each predicate of `preds` as `name current/target (status)`, where
    current is how many triples were generated so far (its target minus its
    remaining `PredicateProfile.frequency`). Deducible predicates are marked."""

    def relation(pred: str) -> str:
        profile = profiles[pred]
        status = "closed" if profile.closed else "open"
        if pred in deducible_preds:
            status += ", deducible"
        current = targets[pred] - profile.frequency
        return f"{short_term(pred)} {current}/{targets[pred]} ({status})"

    return ", ".join(relation(p) for p in sorted(preds, key=short_term))


def generate_non_deducible_predicates(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    initial_facts_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile],
    skip_reflexivity: bool = False,
) -> None:
    """Generates a set of initial facts from a set of rules and predicate profiles.

    With `skip_reflexivity`, F0 is generated as before the self-loop fix (`35a1b81`):
    no `place_self_loops`, and rule groundings and random triples may make self-loops
    by chance, so a relation's self-loops don't match the source's. Relations
    generated as undirected edges place theirs either way.

    Args:
        client: Wrapper for SPARQL queries.
        rules: Mapping of rule IDs to HornRule objects.
        term_mapping: Mapping of terms to their string representations.
        initial_facts_uri: The URI where the initial facts (F0) will be generated.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        profiles: The metrics for each predicate in the original graph.
        skip_reflexivity: Generate as before the self-loop fix (see above).
    """
    # Instantiate a new graph
    initialize_graph(
        client=client,
        source=None,
        new_graph_uri=initial_facts_uri,
        chunk_size=chunk_size,
    )

    # Buffers triples decided by direct-match/random assignment (no DB read
    # needed to produce them), so they're inserted in fewer, larger batches
    # instead of one round trip per call. Flushed before any step that needs
    # to read `initial_facts_uri`, and unconditionally before this function returns.
    buffer = TripleBuffer()

    # Target frequency per predicate, copied before the steps below spend
    # `PredicateProfile.frequency` as a remaining budget.
    targets = {p: pr.frequency for p, pr in profiles.items()}
    deducible_preds = get_deducible_preds(rules)
    symmetric_preds = get_undirected_preds(rules)
    # Relations that only their symmetric rule derives.
    undirected_preds = symmetric_preds - deducible_preds
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
            initial_facts_uri=initial_facts_uri,
            profiles={
                p: pr for p, pr in directed_profiles.items() if p not in deducible_preds
            },
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
        )
    # Complete rules are grounded before any partial rule, so they get the budget
    # first; each group is ordered on its own (a complete rule never waits for a
    # partial one).
    complete_ids = {r_id for r_id, r in rules.items() if r.is_complete}
    non_deducible_dependency = get_non_deducible_dependencies(
        {r_id: r for r_id, r in rules.items() if r_id in complete_ids}
    ) | get_non_deducible_dependencies(
        {r_id: r for r_id, r in rules.items() if r_id not in complete_ids}
    )
    # Budget of deducible relations held back from the partial rules for what the
    # complete rules derive in completion (`_reserve_derived_budget`).
    reserved: dict[str, int] | None = None
    non_deducible_profiles = {
        pred: profiles[pred] for pred in (profiles.keys() - deducible_preds)
    }
    if not non_deducible_profiles:
        logger.warning("Retrieved 0 non-deducible predicates, F0 will be empty.")

    checked_rules: set[str] = set()
    # Head triples placed by the groundings of partial rules.
    partial_heads = [0]
    step = 0

    def _end_initial_facts() -> bool:
        """Updates closed predicates and logs progress. Returns True if all
        non-deducible profiles are closed."""

        if update_closed_preds(profiles):
            logger.info(
                "[Step %d] Closed predicates [%d/%d].",
                step,
                sum(1 for pr in profiles.values() if pr.closed),
                len(profiles),
            )

        return all(pr.closed for pr in non_deducible_profiles.values())

    logger.info("Creating F0")
    open_ext_preds = {p for p, pr in non_deducible_profiles.items() if not pr.closed}
    logger.info(
        "Non-deducible relations to generate (%d/%d, current/target): %s",
        len(open_ext_preds),
        len(non_deducible_profiles),
        _format_relations(open_ext_preds, profiles, targets, deducible_preds) or "none",
    )
    logger.info(
        "Closed predicates [%d/%d].",
        sum(1 for pr in profiles.values() if pr.closed),
        len(profiles),
    )

    while not _end_initial_facts():
        step += 1
        progress = False

        # Step 1: Check direct matches
        if direct_count := check_direct_matches(
            client=client,
            initial_facts_uri=initial_facts_uri,
            profiles=directed_profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
            checked=direct_checked,
        ):
            progress = True
            logger.debug("[Step %d] Added %d triples directly.", step, direct_count)

            if _end_initial_facts():
                break

        # Step 2: Check rule bodies
        if not progress and (len(checked_rules) < len(rules)):
            # check_triples_from_rule queries initial_facts_uri, so it needs every
            # triple to already be visible in the DB, not just buffered.
            buffer.flush(
                client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size
            )

            for r_id, r in rules.items():
                if (
                    r_id in checked_rules
                    or non_deducible_dependency[r_id] - checked_rules
                    or (not r.is_complete and complete_ids - checked_rules)
                ):
                    continue
                if not r.is_complete and reserved is None:
                    reserved = _reserve_derived_budget(rules, profiles)

                produced = produced_groundings(
                    client, r, deducible_preds, initial_facts_uri
                )
                if produced >= r.grounding_target:
                    checked_rules.add(r_id)
                    r.closed = True
                    continue

                if len(r.get_initial_fact_atoms(deducible_preds)) <= 1:
                    # We can just use random assignments
                    checked_rules.add(r_id)
                    continue

                # Open rule with 2 or more atoms to ground (a partial rule's head
                # included) and no dependencies left. Get triples by sampling
                # groundings.
                r_count = check_triples_from_rule(
                    rule=r,
                    deducible_preds=deducible_preds,
                    profiles=profiles,
                    client=client,
                    term_mapping=term_mapping,
                    initial_facts_uri=initial_facts_uri,
                    chunk_size=chunk_size,
                    undirected_preds=undirected_preds,
                    skip_reflexivity=skip_reflexivity,
                    head_triples=partial_heads,
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

        if _end_initial_facts():
            break

        # Step 3: Assign randomly
        if not progress:
            # Symmetric relations wait for every rule's groundings to spend their
            # budget first.
            if len(checked_rules) >= len(rules) and any(
                not profiles[p].closed for p in undirected_preds
            ):
                buffer.flush(
                    client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size
                )
                generate_undirected_relations(
                    client=client,
                    predicates=undirected_preds,
                    profiles=profiles,
                    initial_facts_uri=initial_facts_uri,
                    term_mapping=term_mapping,
                    chunk_size=chunk_size,
                )
                continue

            open_preds = [
                p
                for p, pr in non_deducible_profiles.items()
                if not pr.closed and p not in undirected_preds
            ]
            if not open_preds:
                continue
            predicate = random.choice(open_preds)
            profile = profiles[predicate]

            ran_count = insert_random_triples(
                client=client,
                initial_facts_uri=initial_facts_uri,
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

            if _end_initial_facts():
                break

    # Guarantee every buffered triple lands before this graph is considered
    # complete by callers (e.g. get_triple_count, complete_graph).
    buffer.flush(client=client, graph_uri=initial_facts_uri, chunk_size=chunk_size)
    if any(not r.is_complete for r in rules.values()):
        logger.info(
            "[Partial] Head triples placed by the groundings of partial rules: %d.",
            partial_heads[0],
        )
    for predicate, amount in (reserved or {}).items():
        profile = profiles[predicate]
        profile.frequency += amount
        if profile.frequency > 0:
            profile.closed = False


def _reserve_derived_budget(
    rules: dict[str, HornRule], profiles: dict[str, PredicateProfile]
) -> dict[str, int]:
    """Holds back, from each deducible relation's remaining frequency, the triples
    the complete rules deriving it will add in completion: the sum of their
    supports, at most what remains. Partial rules then add only the facts the
    complete rules leave, so that relation doesn't pass its frequency. Only the
    frequency is held back, not the counts of each entity.

    Returns:
        The amount held back per relation, to give back once initial-fact generation is
        done.
    """
    derived: dict[str, float] = {}
    for rule in rules.values():
        if rule.is_complete and not rule.is_symmetric():
            pred = rule.head.predicate
            derived[pred] = derived.get(pred, 0) + rule.support
    reserved = {}
    for predicate, amount in derived.items():
        if (profile := profiles.get(predicate)) is None or profile.closed:
            continue
        reserved[predicate] = min(max(profile.frequency, 0), int(amount))
        profile.frequency -= reserved[predicate]
    if reserved:
        logger.info(
            "[Partial] Grounding the partial rules; held back for the complete "
            "rules (held/remaining before): %s",
            ", ".join(
                f"{short_term(p)} {n}/{profiles[p].frequency + n}"
                for p, n in sorted(reserved.items(), key=lambda i: short_term(i[0]))
            ),
        )
    return reserved
