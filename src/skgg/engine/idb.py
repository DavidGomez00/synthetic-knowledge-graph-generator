"""Builds the Intensional Database (IDB): grows the synthetic graph from the EDB by
forward-chaining rule heads over already-grounded predicates, under the "complete
rules" assumption (a rule set that can, in principle, deduce every intensional
predicate — checked up front via `core.rules.check_uninferrable_preds`).

`generate_idb` repeatedly applies every rule whose body predicates are already
grounded (`engine.generator.apply_rule`), tracking which rules/predicates have
reached their target support/frequency ("closed", via `update_closure`), until a
step adds no new triples or everything relevant is closed.
"""

import logging

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    get_frequency,
    get_support,
    initialize_graph,
)
from skgg.core.rules import (
    HornRule,
    check_uninferrable_preds,
    get_intensional_dependencies,
    get_predicate_mapping,
)
from skgg.engine.generator import (
    apply_rule,
)
from skgg.engine.metrics import PredicateProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Update support and frequencies.
# ---------------------------------------------------------------------------
def get_closed_rules(
    client: SPARQLWrapper, graph_uri: str, rules: dict[str, HornRule]
) -> set[str]:
    """Queries the database to find which rules reached their target support.

    A rule is considered 'closed' when the count of distinct bindings
    satisfying both its body and head in the graph meets or exceeds
    its defined support threshold.
    """
    closed_rules: set[str] = set()

    # Check support
    for r_id, rule in rules.items():
        support = get_support(client, rule, graph_uri)
        if support:
            if support >= rule.support:
                closed_rules.add(r_id)

    return closed_rules


def get_closed_preds(
    client: SPARQLWrapper, graph_uri: str, profiles: dict[str, PredicateProfile]
) -> set[str]:
    """Queries the database to find which predicates reached their target frequency.

    A predicate is considered 'closed' when the count of distinct triples containing the
    predicate is equal to the predicate's frequency.
    """

    closed_predicates: set[str] = set()

    for predicate, profile in profiles.items():
        frequency = get_frequency(client, predicate, graph_uri)
        if frequency >= profile.frequency:
            closed_predicates.add(predicate)

    return closed_predicates


def update_closure(
    client: SPARQLWrapper,
    graph_uri: str,
    profiles_to_check: dict[str, PredicateProfile],
    rules_to_check: dict[str, HornRule],
    closed_rule_ids: set[str],
    closed_preds: set[str],
) -> bool:
    """Updates the sets that track closed rules and predicates. Returns True if elements
    are added to any set.

    Args:
        client: Wrapper for SPARQL queries.
        graph_uri: URI where closure is measured.
        profiles_to_check: Predicates to check for closure.
        rules_to_check: Rules to check for closure.

    Returns:
        True if any rules or predicates are closed, False otherwise.
    """

    update = False

    if new_preds := get_closed_preds(client, graph_uri, profiles_to_check):
        closed_preds.update(new_preds)
        update = True

    if closed_rules := get_closed_rules(client, graph_uri, rules_to_check):
        closed_rule_ids.update(closed_rules)
        update = True

    return update


# ---------------------------------------------------------------------------
# Complete the Synthetic graph.
# ---------------------------------------------------------------------------
def generate_idb(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    edb_uri: str,
    synthetic_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile],
) -> None:
    """Generates a synthetic DB from an EDB by creating triples using the rules.

    Args:
        client: Wrapper for SPARQL queries.
        rules: Mapping of rule IDs to HornRule objects.
        term_mapping: Mapping of terms to their string representations.
        edb_uri: The URI where the Extensional Database (EDB) will be generated.
        synthetic_uri: The URI where the Synthetic Database will be completed.
        chunk_size: Maximum number of triples to insert per SPARQL query.
        profiles: The metrics for each predicate in the original graph.
    """

    # Check that the rules allow to deduce the complete synthetic graph
    intensional_preds = {r.head.predicate for r in rules.values()}
    extensional_preds = profiles.keys() - intensional_preds

    if uninferrable_preds := check_uninferrable_preds(
        rules=rules,
        intensional_predicates=intensional_preds,
        extensional_predicates=extensional_preds,
    ):
        error_msg = (
            f"Rule set not inferrable under complete rule assumption. "
            f"The following predicates cannot be deduced: {uninferrable_preds}."
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # Instatiate the synthetic graph starting from the EDB
    initialize_graph(
        client=client,
        source=edb_uri,
        new_graph_uri=synthetic_uri,
        chunk_size=chunk_size,
    )

    # Get intensional dependencies
    intensional_dependencies = get_intensional_dependencies(rules=rules)

    # Stratify and apply rules iteratively
    closed_rule_ids: set[str] = set()
    closed_preds: set[str] = set(extensional_preds)

    grounded_predicates = set(extensional_preds)
    predicate_to_rules = get_predicate_mapping(rules)

    # Update closure from EDB before starting graph completion
    update_closure(
        client=client,
        graph_uri=synthetic_uri,
        profiles_to_check=profiles,
        rules_to_check=rules,
        closed_preds=closed_preds,
        closed_rule_ids=closed_rule_ids,
    )

    if not (intensional_preds - closed_preds):
        logger.info("All predicates closed.")
        return

    if not (rules.keys() - closed_rule_ids):
        logger.info("All rules closed.")
        return

    logger.info(
        "Completing Synthetic graph... Rules [%d/%d] | Predicates [%d/%d].",
        len(closed_rule_ids),
        len(rules),
        len(closed_preds),
        len(profiles),
    )

    # Start iterating over the rules to generate the rest of the triples
    step = 0
    while True:
        step += 1

        applied_rules: dict[str, HornRule] = {}
        added_triples = 0

        for rule_id, rule in rules.items():
            predicate = rule.head.predicate
            profile = profiles[predicate]

            # Check if rule is closed
            if rule_id in closed_rule_ids:
                logger.debug("[Step %d] %s: rule is closed.", step, rule_id)
                continue

            # Check if head predicate is closde
            if predicate in closed_preds:
                logger.debug("[Step %d] %s: %s is closed.", step, rule_id, predicate)
                continue

            # Check if rule is grounded
            body_predicates = rule.get_body_predicates() - {predicate}
            if not body_predicates.issubset(grounded_predicates):
                logger.debug("[Step %d] %s: Body not grounded.", step, rule_id)
                continue

            # Check if rule has pending dependencies
            dependency_ids = intensional_dependencies[rule_id]
            _rules = dependency_ids - closed_rule_ids
            if len(_rules):
                logger.debug(
                    "[Step %d] %s: Depends on non closed rules %s",
                    step,
                    rule_id,
                    _rules,
                )
                continue

            # Apply the rule and generate triples from it
            count = apply_rule(
                client=client,
                graph_uri=synthetic_uri,
                rule=rule,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
                profile=profile,
            )
            logger.debug(
                "[Step %d] %s: Added %d triples for %s.",
                step,
                rule_id,
                count,
                predicate,
            )
            added_triples += count
            if count:
                grounded_predicates.add(predicate)

            # Keep track of the rules appliead
            applied_rules[rule_id] = rule

        logger.info("[Step %d] Added %d triples.", step, added_triples)
        if not added_triples:
            logger.info("[Step %d] Reached stale state.", step)
            break

        # Determine which rules should be checked for closure
        pending_rule_ids = {
            id
            for r in applied_rules.values()
            for id in predicate_to_rules[r.head.predicate]
            if id not in closed_rule_ids
        }
        rules_to_check = {r_id: rules[r_id] for r_id in pending_rule_ids}
        logger.debug("[Step %d] Rules to check: %s.", step, rules_to_check.keys())

        # Update the closure for the rules and predicates
        if update_closure(
            client=client,
            graph_uri=synthetic_uri,
            profiles_to_check=profiles,
            rules_to_check=rules_to_check,
            closed_preds=closed_preds,
            closed_rule_ids=closed_rule_ids,
        ):
            logger.info(
                "[Step %d] Closed - Rules [%d/%d] | Predicates [%d/%d].",
                step,
                len(closed_rule_ids),
                len(rules),
                len(closed_preds),
                len(profiles),
            )

        if not (intensional_preds - closed_preds):
            logger.info("All predicates closed.")
            break

        if not (rules.keys() - closed_rule_ids):
            logger.info("All rules closed.")
            break
