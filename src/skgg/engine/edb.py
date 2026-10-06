"""Builds the Extensional Database (EDB): a set of ground triples over extensional
predicates (those never inferred by a rule head) that satisfy both the source
graph's predicate profiles and the rule bodies that will later drive IDB generation.

`generate_extensional_predicates` iterates three strategies per predicate, in
order, until every extensional predicate's target frequency is reached
("closed"):
  1. `check_direct_matches`: deterministic matches forced by profile counts.
  2. `check_triples_from_rule`: bindings satisfying a rule's extensional body,
     constructed directly from the profiles (`generator.sample_groundings`) so a
     chosen triple never violates another predicate's remaining domain/range budget.
  3. `insert_random_triples`: random assignment for whatever isn't pinned down
     by the first two steps, with subjects and objects drawn weighted by their
     remaining budget, still respecting the Gale-Ryser/Havel-Hakimi
     solvability check (`generator.is_assignment_solvable`).
"""

import heapq
import logging
import random
from collections.abc import Iterator

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    TripleBuffer,
    count_producible_heads,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import HornRule, get_extensional_dependencies
from skgg.core.utils import format_triple, short_term
from skgg.engine.generator import (
    decrement_counts,
    sample_groundings,
    update_closed_preds,
)
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)

# Draws of an object set that `insert_random_triples` tries before giving up.
MAX_RANDOM_DRAWS = 1000


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
) -> int:
    """Retrieves the triples that must be added to the EDB from a set of rules and
    profiles and buffers them for insertion into the EDB (see `TripleBuffer`) —
    deciding them never requires a DB read, so they don't need to land
    immediately.

    Returns the number of triples buffered for the EDB."""

    def direct_triples() -> Iterator[str]:
        """Yields triples that must be added to the EDB."""
        while True:
            progress_made = False

            for predicate, profile in profiles.items():
                if profile.closed:
                    continue

                # Check for direct matches on domain
                for subject, frequency in list(profile.domain.items()):
                    obj_choices = list(profile.range.keys() - {subject})
                    if frequency == len(obj_choices):
                        for obj in obj_choices:
                            yield format_triple(subject, predicate, obj, term_mapping)
                            decrement_counts(profile.range, obj)
                            profile.frequency -= 1
                        del profile.domain[subject]
                        progress_made = True

                # Check for direct matches on range
                for obj, frequency in list(profile.range.items()):
                    subj_choices = list(profile.domain.keys() - {obj})
                    if frequency == len(subj_choices):
                        for subject in subj_choices:
                            yield format_triple(subject, predicate, obj, term_mapping)
                            decrement_counts(profile.domain, subject)
                            profile.frequency -= 1
                        del profile.range[obj]
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
            "Checking triples from rule: No profile for %s (absent from the source ",
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
def _weighted_sample(weights: dict[str, int], k: int) -> list[str]:
    """Draws `k` distinct keys of `weights` without replacement, each with
    probability proportional to its (positive) weight (Efraimidis-Spirakis)."""
    return heapq.nlargest(
        k, weights, key=lambda key: random.random() ** (1 / weights[key])
    )


def insert_random_triples(
    client: SPARQLWrapper,
    edb_uri: str,
    profile: PredicateProfile,
    predicate: str,
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
) -> int:
    """Generates random triples from a single predicate's profile and buffers them
    for insertion (see `TripleBuffer`) — the random assignment never requires a DB
    read, so the triples don't need to land immediately.

    The subject is drawn with probability proportional to its remaining domain
    count and gets all of its triples at once. Its objects are drawn without
    replacement, with probability proportional to their remaining range count,
    until a set passes the solvability check. Serving large demands while the
    most partners remain means fewer sets fail that check.

    Args:
        client: Wrapper for SPARQL queries.
        edb_uri: The URI where the EDB triples will be inserted.
        profile: The predicate profile tracking available domains and ranges.
        predicate: The predicate string for the triples.
        term_mapping: Mapping of terms to their string representations.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        buffer: Shared buffer that accumulates triples across calls.

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

    subject = random.choices(
        available_subjects, weights=[profile.domain[s] for s in available_subjects]
    )[0]
    required_count = profile.domain[subject]

    available_objects = list(profile.range.keys())
    if len(available_objects) < required_count:
        raise ValueError(
            f"Error assigning triples for {predicate}. "
            f"{subject} must be in {required_count} triples. "
            f"There are only {len(available_objects)} different objects available."
        )

    # Pre-calculate simulated domain metrics (remains constant for this choice)
    sim_domain_len = len(profile.domain) - 1
    max_domain = max((v for k, v in profile.domain.items() if k != subject), default=0)
    chosen_objects: list[str] = []

    for draw in range(1, MAX_RANDOM_DRAWS + 1):
        chosen_objects = _weighted_sample(profile.range, required_count)
        chosen_set = set(chosen_objects)

        # Simulate the range length: Count the objects that won't drop to 0
        sim_range_len = sum(
            1
            for obj, count in profile.range.items()
            if not (count == 1 and obj in chosen_set)
        )

        max_range = max(
            (
                count - 1 if obj in chosen_set else count
                for obj, count in profile.range.items()
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
) -> None:
    """Generates an EDB from a set of rules and predicate profiles.

    Args:
        client: Wrapper for SPARQL queries.
        rules: Mapping of rule IDs to HornRule objects.
        term_mapping: Mapping of terms to their string representations.
        edb_uri: The URI where the Extensional Database (EDB) will be generated.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        profiles: The metrics for each predicate in the original graph.
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
    intensional_preds = {r.head.predicate for r in rules.values()}

    # Warm-up: Direct matches for any predicate untill no new triples.
    logger.info("Start warm-up.")

    while True:
        if count := check_direct_matches(
            client=client,
            edb_uri=edb_uri,
            profiles=profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
        ):
            logger.info("[Warm-up] Added %d direct matches to EDB.", count)
        else:
            break

    update_closed_preds(profiles=profiles)

    # Direct matches cover every predicate, so intensional ones may get triples too.
    if warm_preds := {p for p, pr in profiles.items() if pr.frequency < targets[p]}:
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
            profiles=profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            buffer=buffer,
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
                )
                checked_rules.add(r_id)

                if r_count:
                    progress = True
                    logger.debug(
                        "[Step %d] Added %d triples using rule %s body (to respeect selectivity)",
                        step,
                        r_count,
                        r_id,
                    )
                    break

        if _end_edb():
            break

        # Step 3: Assign randomly
        if not progress:
            open_preds = [p for p, pr in extensional_profiles.items() if not pr.closed]
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
