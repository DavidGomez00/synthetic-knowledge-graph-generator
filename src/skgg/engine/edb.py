"""Builds the Extensional Database (EDB): a set of ground triples over extensional
predicates (those never inferred by a rule head) that satisfy both the source
graph's predicate profiles and the rule bodies that will later drive IDB generation.

`generate_edb` iterates three strategies per predicate, in order, until every
extensional predicate's target frequency is reached ("closed"):
  1. `check_direct_matches` — deterministic matches forced by profile counts.
  2. `check_triples_from_rule` — bindings satisfying a rule's extensional body,
     selected via CSP backtracking (`_select_valid_bindings`) so a chosen triple
     never violates another predicate's remaining domain/range budget.
  3. `insert_random_triples` — random assignment for whatever isn't pinned down
     by the first two steps, still respecting the Gale-Ryser/Havel-Hakimi
     solvability check (`generator.is_assignment_solvable`).
"""

import copy
import logging
import random
import uuid
from collections.abc import Iterator

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    SparqlBinding,
    TripleBuffer,
    build_rule_query,
    clear_graph,
    execute_select_query,
    from_binding_row,
    get_existing_triples,
    get_support,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import (
    Atom,
    HornRule,
    RuleSignature,
    get_extensional_dependencies,
)
from skgg.engine.generator import (
    create_searchspace,
    decrement_counts,
    is_assignment_solvable,
    triples_from_bindings,
    update_closed_preds,
)
from skgg.engine.metrics import PredicateProfile
from skgg.utils import format_triple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1: Direct matching of triples.
# ---------------------------------------------------------------------------
def check_direct_matches(
    client: SPARQLWrapper,
    edb_uri: str,
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    chunk_size: int,
    closed_preds: set[str],
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
                if predicate in closed_preds:
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
def _filter_bindings(
    client: SPARQLWrapper,
    edb_uri: str,
    intensional_preds: set[str],
    raw_bindings: list[SparqlBinding],
    searchspace_profiles: dict[str, PredicateProfile],
    rule: HornRule,
    term_mapping: dict[str, str],
    closed_preds: set[str],
) -> Iterator[str]:
    """This function processes the bindings retrieved from a query that includes a
    searchspace. It yields valid triples, checking if the triple is allowed by
    predicate metrics.

    Args:
        ...

        rule: The original Horn Rule against which we want to validate the triples (for
            example, to check if we are violating rule's support upper bound).
    """

    # First thing is to check if the rule's body contains any open and intensional
    # predicate. If this is not the case, the support of the rule should be entirely
    # closed by the generated triples. Each binding is at most one different head, so
    # the number of bindings must be at least the difference between the current and
    # target support.

    # If rule like A and B -> p has support = 100, there are 100 triples p(X, Y) that
    # result from the projection of A and B, i.e., at least 100 unique convinations of
    # A and B.

    excluded_preds = intensional_preds | closed_preds

    current_support = get_support(client=client, rule=rule, graph_uri=edb_uri)
    missing_heads = rule.support - current_support

    only_extensional = rule.get_body_predicates().isdisjoint(excluded_preds)
    if only_extensional and len(raw_bindings) < missing_heads:
        raise ValueError(
            f"Missing {missing_heads} heads to close the rule, but only "
            f"{len(raw_bindings)} bindings found."
        )

    # NOTE: If we implement the MRV (Minimum Remaining Values) heuristic later, we would
    # replace random.shuffle with a sort function here.
    candidate_bindings = list(raw_bindings)
    random.shuffle(candidate_bindings)

    selected_bindings = _select_valid_bindings(
        client=client,
        edb_uri=edb_uri,
        bindings=candidate_bindings,
        rule=rule,
        missing_heads=missing_heads,
        searchspace_profiles=searchspace_profiles,
        term_mapping=term_mapping,
    )

    if len(selected_bindings) < rule.support and only_extensional:
        logger.warning(
            "%s's support is %d, but %d bindings were retrieved.",
            rule.rule_id,
            rule.support,
            len(selected_bindings),
        )

    # Produce the new triples from selected bindings
    for idx in selected_bindings:
        binding_row = candidate_bindings[idx]
        for atom in (a for a in rule.body if a.predicate in searchspace_profiles):
            predicate = atom.predicate
            subject = from_binding_row(atom.subject, binding_row)[0]
            obj = from_binding_row(atom.obj, binding_row)[0]

            triple = format_triple(
                subject=subject,
                predicate=predicate,
                obj=obj,
                term_mapping=term_mapping,
            )

            decrement_counts(searchspace_profiles[predicate].domain, subject)
            decrement_counts(searchspace_profiles[predicate].range, obj)
            searchspace_profiles[predicate].frequency -= 1

            yield triple


def _select_valid_bindings(
    client: SPARQLWrapper,
    edb_uri: str,
    bindings: list[SparqlBinding],
    rule: HornRule,
    missing_heads: int,
    searchspace_profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    max_backtracks: int = 10000,
    chunk_size: int = 1000,
) -> list[int]:
    """Selects a subset of `bindings` that define a set of conjunctions thet does not
    violate the upper bound for the rule support and the upper bound for relation
    (predicate) frequency.

    Returns:
        A list of indices corresponding to the accepted bindings.
    """
    logger.debug(
        "Searching through %d bindings (%d needed).",
        len(bindings),
        missing_heads,
    )

    body_atoms = [a for a in rule.body if a.predicate in searchspace_profiles]

    all_potential_triples = triples_from_bindings(bindings, body_atoms, term_mapping)
    existing_triples = get_existing_triples(
        client=client,
        graph_uri=edb_uri,
        candidate_triples=all_potential_triples,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
    )

    added_bindings: list[int] = []
    backtrack_counter = [0]

    def backtrack(
        current_idx: int,
        current_profiles: dict[str, PredicateProfile],
        current_triples: set[str],
        current_missing_heads: int,
    ) -> bool:
        """Recursive DFS CSP solver."""
        if len(added_bindings) >= current_missing_heads:
            return True  # Success state

        if current_idx >= len(bindings):
            return False  # Failure by invalid state

        if backtrack_counter[0] > max_backtracks:
            # Failure by timeout
            raise TimeoutError(
                "CSP Backtrack budget exceeded. Graph may be unsatisfable."
            )

        binding_row = bindings[current_idx]
        is_valid = True

        branch_profiles = copy.deepcopy(current_profiles)
        branch_triples = set(current_triples)

        for atom in body_atoms:
            predicate = atom.predicate
            profile = branch_profiles[predicate]
            logger.debug("Branch_profile state: %s", profile)

            subject = from_binding_row(atom.subject, binding_row)[0]
            obj = from_binding_row(atom.obj, binding_row)[0]

            logger.debug("Subject: %s | Object: %s", subject, obj)

            triple = format_triple(subject, predicate, obj, term_mapping)

            logger.debug("Trying triple: %s", triple)

            if triple in existing_triples or triple in branch_triples:
                logger.debug("Triple already exists in selected triples or EDB.")
                continue

            if (
                not profile.frequency
                or subject not in profile.domain
                or obj not in profile.range
                or not is_assignment_solvable(profile, subject, obj)
            ):
                logger.debug("Triple violates profiles.")
                is_valid = False
                break

            # Apply mutations to the current branch state
            branch_triples.add(triple)
            decrement_counts(profile.domain, subject)
            decrement_counts(profile.range, obj)
            profile.frequency -= 1

        if is_valid:
            added_bindings.append(current_idx)
            logger.debug(
                "Binding %d valid, let's check %d.", current_idx, current_idx + 1
            )

            if backtrack(current_idx + 1, branch_profiles, branch_triples):
                return True  # Bubble up successful state

            added_bindings.pop()
            backtrack_counter[0] += 1

        return backtrack(current_idx + 1, current_profiles, current_triples)

    success = backtrack(0, searchspace_profiles, current_triples=set())

    if not success:
        logger.warning("Could not find a valid combination to satisfy rule support.")

    return added_bindings


def check_triples_from_rule(
    rule: HornRule,
    intensional_preds: set[str],
    closed_preds: set[str],
    profiles: dict[str, PredicateProfile],
    client: SPARQLWrapper,
    term_mapping: dict[str, str],
    edb_uri: str,
    chunk_size: int,
) -> int:
    """Retrieves triples satisfying a rule's body and inserts them into the EDB.

    Args:
        rule: The HornRule being evaluated.
        intensional_preds: Set of intensional predicate strings.
        closed_preds: Set of already closed predicate strings.
        edb_profiles: Global predicate profiles tracking domains and ranges.
        client: Wrapper for SPARQL queries.
        term_mapping: Mapping of terms to their string representations.
        edb_uri: The URI of the target EDB.
        chunk_size: Maximum number of triples to insert per SPARQL query.

    Returns:
        The number of successfully inserted triples.
    """

    # Build a new rule body that excludes atoms containing excluded predicates.
    # This check should be redundant since a similar check is perfomed at generate_edb()
    excluded_preds = intensional_preds | closed_preds
    new_body = {atom for atom in rule.body if atom.predicate not in excluded_preds}
    if not new_body:
        return 0

    # TODO: This line does not seem to be logged ever. Check why.
    logger.debug("Generating predicates from %s: %s", rule.rule_id, list(new_body))

    # TODO: This new rule is built to query the graph, but I am not sure this is correct
    # Maybe the best approach is to define a specific way of querying the graphs for
    # these cases.

    # NOTE: It also is used to identify which relation types / predicates we want in the
    # searchspace.
    new_rule = HornRule(
        signature=RuleSignature(
            rule_id=rule.rule_id,
            body=frozenset(new_body),
            head=Atom("", "", ""),  # Dummy head for query builder
        ),
        support=rule.support,
        head_coverage=rule.head_coverage,
        std_confidence=rule.std_confidence,
        pca_confidence=rule.pca_confidence,
        classification=rule.classification,
    )

    # Create the searchspace
    target_preds = new_rule.get_body_predicates()
    searchspace_profiles = {
        pred: profiles[pred] for pred in target_preds if pred in profiles
    }

    # Concurrency-safe unique URI
    searchspace_uri = f"http://SearchSpace.org/{uuid.uuid4().hex}"

    try:
        create_searchspace(
            client=client,
            profiles=searchspace_profiles,
            term_mapping=term_mapping,
            searchspace_uri=searchspace_uri,
        )

        query = build_rule_query(rule=new_rule.signature, graph_uri=searchspace_uri)
        # logger.debug("Query: %s", query)
        bindings = execute_select_query(client, query)

    finally:
        # Guarantee cleanup even if the query engine timeouts or filtering fails
        clear_graph(client=client, graph_uri=searchspace_uri)

    # Filter the retrieved bindings
    triple_stream = _filter_bindings(
        client=client,
        edb_uri=edb_uri,
        rule=rule,
        raw_bindings=bindings,
        term_mapping=term_mapping,
        searchspace_profiles=searchspace_profiles,
        intensional_preds=intensional_preds,
        closed_preds=closed_preds,
    )

    return insert_triples_sparql(
        client=client,
        graph_uri=edb_uri,
        triple_stream=triple_stream,
        chunk_size=chunk_size,
    )


# ---------------------------------------------------------------------------
# Step 3: Produce triples from a random subject.
# ---------------------------------------------------------------------------
def insert_random_triples(
    client: SPARQLWrapper,
    edb_uri: str,
    profile: PredicateProfile,
    predicate: str,
    term_mapping: dict[str, str],
    chunk_size: int,
    buffer: TripleBuffer,
):
    """Generates random triples from a single predicate's profile and buffers them
    for insertion (see `TripleBuffer`) — the random assignment never requires a DB
    read, so the triples don't need to land immediately.

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
        ValueError: If there are not enough available objects to satisfy the domain.
    """

    available_subjects = list(profile.domain.keys())
    if not available_subjects:
        logger.warning("Empty domain for predicate %s.", predicate)
        return 0

    subject = random.choice(available_subjects)
    required_count = profile.domain[subject]

    # TODO: I am not excluding the subject from the profile, ponder this.
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

    while True:
        chosen_objects = random.sample(available_objects, required_count)
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
            break

        logger.debug("Selected random assignments invalid, trying again.")

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
# Generate EDB.
# ---------------------------------------------------------------------------
def generate_edb(
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

    closed_preds: set[str] = set()

    # Buffers triples decided by direct-match/random assignment (no DB read
    # needed to produce them), so they're inserted in fewer, larger batches
    # instead of one round trip per call. Flushed before any step that needs
    # to read `edb_uri`, and unconditionally before this function returns.
    buffer = TripleBuffer()

    # Warm-up: Direct matches for any predicate untill no new triples.
    logger.info("Start warm-up.")

    while True:
        if count := check_direct_matches(
            client=client,
            edb_uri=edb_uri,
            profiles=profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
            closed_preds=closed_preds,
            buffer=buffer,
        ):
            logger.info("[Warm-up] Added %d direct matches to EDB.", count)
        else:
            break

    update_closed_preds(profiles=profiles, closed_preds=closed_preds)

    rule_dependency = get_extensional_dependencies(rules)
    intensional_preds = {r.head.predicate for r in rules.values()}
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

        if update_closed_preds(profiles, closed_preds):
            logger.info(
                "[Step %d]: Closed ext. predicates [%d/%d].",
                step,
                len(closed_preds),
                len(profiles),
            )

        for pred in extensional_profiles.keys():
            if pred not in closed_preds:
                return False
        return True

    logger.info("Creating EDB")
    logger.info("Closed predicates [%d/%d].", len(closed_preds), len(profiles))

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
            closed_preds=closed_preds,
            buffer=buffer,
        ):
            progress = True
            logger.debug("[Step %d]: Added %d triples directly.", step, direct_count)

            if _end_edb():
                break

        # Step 2: Check rule bodies
        if not progress and (len(checked_rules) < len(rules)):
            # check_triples_from_rule queries edb_uri, so it needs every triple to
            # already be visible in the DB, not just buffered.
            buffer.flush(client=client, graph_uri=edb_uri, chunk_size=chunk_size)

            excluded_preds = intensional_preds | closed_preds
            for r_id, r in rules.items():
                if (
                    r_id in checked_rules
                    or rule_dependency[r_id] - checked_rules
                    # TODO: try to delete this restriction. Rules like A and p -> p can
                    # be used here.
                    or len(r.get_predicates() - excluded_preds) <= 1
                ):
                    # The rule to be used to generate EDB triples needs 2 or more
                    # extensional predicates that are not closed, and does not depend on
                    # other non-checked rules.
                    continue

                r_count = check_triples_from_rule(
                    rule=r,
                    intensional_preds=intensional_preds,
                    closed_preds=closed_preds,
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
                        "[Step %d] Added %d triples from %s", step, r_count, r_id
                    )
                    break

        if _end_edb():
            break

        # Step 3: Assign randomly
        if not progress:
            open_preds = list(extensional_profiles.keys() - closed_preds)
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
                    "[Step %d]: Added %d triples by random assignment.", step, ran_count
                )

            if _end_edb():
                break

    # Guarantee every buffered triple lands before this graph is considered
    # complete by callers (e.g. get_triple_count, generate_idb, complete_graph).
    buffer.flush(client=client, graph_uri=edb_uri, chunk_size=chunk_size)
