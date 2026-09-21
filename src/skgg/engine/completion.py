"""Builds the "complete" graph used as the source for metric extraction.

Used by `cli/upload.py` after a base graph (`.nt` file) is uploaded: forward-chains
every rule over the base graph, assuming rule bodies are fully grounded, until no
rule can add any more triples. The result (`graph.complete_uri`) is what
`engine.metrics.GraphMetrics.from_uri` later profiles for EDB/IDB generation.
"""

import logging
import random
import uuid
from collections import defaultdict

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    clear_graph,
    execute_select_query,
    from_binding_row,
    get_predicate_frequencies,
    get_support,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule
from skgg.engine.edb import _select_valid_bindings
from skgg.engine.generator import apply_rule, create_searchspace, decrement_counts
from skgg.engine.idb import get_closed_preds, get_closed_rules
from skgg.engine.metrics import PredicateProfile
from skgg.utils import format_triple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph completion.
# ---------------------------------------------------------------------------
def complete_graph(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    term_mapping: dict[str, str],
    source: str,
    target_uri: str,
    chunk_size: int,
    profiles: dict[str, PredicateProfile] | None = None,
) -> None:
    """Completes a graph by applying rules if able.

    `profiles`, if given, carries the target frequency each predicate should
    reach (e.g. extracted from the original source graph) -- when omitted
    (as from `cli/upload.py`'s initial completion step, before any such
    targets exist), predicate closure is simply skipped; rule closure always
    runs, since a rule's `support` target is intrinsic to it.
    """

    if source != target_uri:
        initialize_graph(
            client=client,
            source=source,
            new_graph_uri=target_uri,
            chunk_size=chunk_size,
        )

    grounded_preds = set(get_predicate_frequencies(client, target_uri).keys())
    state = {r_id: 0 for r_id in rules.keys()}
    step = 0
    while True:
        step += 1
        added = 0
        for r_id, rule in rules.items():
            count = apply_rule(
                client=client,
                graph_uri=target_uri,
                rule=rule,
                term_mapping=term_mapping,
                chunk_size=chunk_size,
            )
            if count:
                logger.debug("Rule %s added %d triples.", r_id, count)
                state[r_id] += count
                added += count
                grounded_preds.add(rule.head.predicate)

        if added:
            state_msg = " \n".join(
                [
                    f"\tRule {r_id} added {state[r_id]} triples."
                    for r_id in rules.keys()
                    if state[r_id] > 0
                ]
            )
            logger.info("[Step %d] Added %d triples.", step, added)
            logger.debug("\n%s", state_msg)

        else:
            logger.info("[Step %d]: No triples added. Reached stale state.", step)
            break

    for rule_id in get_closed_rules(client, target_uri, rules):
        rules[rule_id].closed = True

    if profiles is not None:
        for predicate in get_closed_preds(client, target_uri, profiles):
            profiles[predicate].closed = True


# A join against an unselective correlation (e.g. two atoms sharing only a
# common rdf:type) can return a candidate binding set orders of magnitude
# larger than `missing` actually requires -- `_select_valid_bindings`
# recurses once per binding it examines, so an uncapped set risks hitting
# Python's recursion limit (confirmed in practice: 693594 bindings for 17
# needed hit "maximum recursion depth exceeded"), on top of the wasted
# query/transfer cost of retrieving all of them. Capping the query itself to
# a generous-but-bounded multiple of `missing` keeps the candidate set large
# enough to absorb some rejected/no-op bindings without reintroducing the
# undershoot risk `LIMIT missing` (with no headroom at all) would have.
_BINDING_LIMIT_FACTOR = 20


# ---------------------------------------------------------------------------
# Closing open rules with an already-closed head.
# ---------------------------------------------------------------------------
def complete_open_rules_with_closed_head(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    profiles: dict[str, PredicateProfile],
    term_mapping: dict[str, str],
    graph_uri: str,
    chunk_size: int,
) -> int:
    """Generates triples from open rules that have a closed head.

    A rule is a candidate when it is open and its head predicate is closed. Candidates
    are processed in descending order of current missing support, so the most
    restrictive rule (the one closest to its target support) always gets first claim. If
    two rules are the same distance from its target, the most restrictive is defined by
    the one that has more open triple patterns (atoms).

    Guard used: before generating any conjunction that uses an open predicate, skip it
    if any closed rule has this predicate in its body (that rule has no support headroom
    left, so a new witness for it would be unsafe. This does not protect a
    not-yet-closed rule with some headroom from being pushed over by several triples
    added in aggregate. Any `open_pred` appearing as some other rule's *head* is safe
    and needs no check: `complete_graph` calls `apply_rule` uncapped to a full stale
    state, so every body-satisfying binding for every rule already has its head fact
    present; a triple this function proposes can't also be a new witness for another
    rule's body, or apply_rule would already have inserted it.

    Generation approach (searchspace variant): for the chosen rule, materializes a
    scratch searchspace graph (`generator.create_searchspace`) holding the cartesian
    product of each open predicate's remaining domain x range, then runs one join
    query spanning two graphs -- the real graph (for the already-closed body atoms +
    head, grounding the shared variables) and the searchspace (for the open atoms,
    resolving every one of their variables via the join, including ones private to
    the open atoms -- no separate "linking variable" handling needed here, unlike the
    FILTER-NOT-EXISTS approach this replaces). The resulting candidate bindings are
    run through `edb.py`'s `_select_valid_bindings` -- the same CSP-with-backtracking
    used for EDB generation -- to pick a subset that doesn't violate any open
    predicate's remaining domain/range/frequency budget.

    Unlike the bespoke per-row solver this replaces, `_select_valid_bindings`
    backtracks across the *whole* binding list (pop a row back out and try a
    different one if a later row can't be satisfied), so it doesn't share that
    approach's "greedy, not jointly" row-ordering limitation. It also doesn't share
    its novelty guarantee: a binding whose triples already all exist is treated as a
    no-op rather than excluded outright (matching edb.py's own semantics), so a
    successful-looking selection can occasionally contribute less new support than
    `missing` -- the post-hoc `get_support` recheck below still catches this and
    simply leaves the rule open for the next retry-loop iteration.

    The function returns whenever triples are generated from any rule, breaking
    the loop. This is because in the current pipeline this method of generating triples
    is a "fallback" if we reach a stale state and have not yet reached the target.
    """
    # Map every predicate to the set of closed rules that include this predicate in its
    # body
    predicate_to_closed_rules: dict[str, set[str]] = defaultdict(set)
    for r_id, r in rules.items():
        if r.closed:
            for pred in r.get_body_predicates():
                predicate_to_closed_rules[pred].add(r_id)

    # Order all open rules with closed head by missing support and number of open triple
    # patterns.
    candidates: list[tuple[int, HornRule, list[Atom]]] = []
    for rule in rules.values():
        if rule.closed or not profiles[rule.head.predicate].closed:
            continue
        missing = rule.support - get_support(client, rule, graph_uri)
        if missing <= 0:
            logger.warning("Rule %s is closed but it just got updated.", rule.rule_id)
            rule.closed = True
            if missing < 0:
                logger.warning(
                    "Rule %s violated upper bound support (%d).",
                    rule.rule_id,
                    missing,
                )
            continue

        open_atoms = [atom for atom in rule.body if not profiles[atom.predicate].closed]
        if not open_atoms:
            logger.warning(
                "Rule %s is open, but all body and head atoms are closed.",
                rule.rule_id,
            )
            continue

        # Here the dependency check
        open_preds = {atom.predicate for atom in open_atoms}
        blocked_pred = next(
            (p for p in open_preds if predicate_to_closed_rules[p]), None
        )
        if blocked_pred is not None:
            logger.debug(
                "Rule %s - <%s> is in the body of a closed rule.",
                rule.rule_id,
                blocked_pred,
            )
            continue

        candidates.append((missing, rule, open_atoms))

    if not candidates:
        return 0

    candidates.sort(key=lambda candidate: (candidate[0], len(candidate[2])))
    logger.info("Using closed head completion on %d rules.", len(candidates))

    for missing, rule, open_atoms in candidates:
        ## ---- Build searchspace for the open predicates ----
        other_atoms = [atom for atom in rule.body if atom not in open_atoms]
        open_preds = {atom.predicate for atom in open_atoms}
        # Same objects as `profiles`, not copies -- mirrors check_triples_from_rule's
        # own convention, so _select_valid_bindings's (copy-based) exploration never
        # touches the real profiles, and the commit step below decrements them directly.
        searchspace_profiles = {pred: profiles[pred] for pred in open_preds}

        searchspace_uri = f"http://SearchSpace.org/{uuid.uuid4().hex}"
        try:
            create_searchspace(
                client=client,
                profiles=searchspace_profiles,
                term_mapping=term_mapping,
                searchspace_uri=searchspace_uri,
            )

            other_patterns = "\n            ".join(
                [f"{atom} ." for atom in other_atoms] + [f"{rule.head} ."]
            )
            open_patterns = "\n            ".join([f"{atom} ." for atom in open_atoms])
            # Every open-atom variable gets resolved by this join -- no more "linking
            # variable" distinction, unlike the FILTER NOT EXISTS approach this
            # replaces.
            proj_vars = sorted(
                {
                    var
                    for atom in open_atoms
                    for var in (atom.subject, atom.obj)
                    if var.startswith("?")
                }
            )
            proj = " ".join(proj_vars) if proj_vars else "*"

            query = f"""
            SELECT DISTINCT {proj} WHERE {{
              GRAPH <{graph_uri}> {{
                {other_patterns}
              }}
              GRAPH <{searchspace_uri}> {{
                {open_patterns}
              }}
            }}
            LIMIT {int(missing * _BINDING_LIMIT_FACTOR)}
            """

            logger.debug("%s", query)

            bindings = execute_select_query(client, query)
        finally:
            # Guaranteed cleanup even if the query times out or raises.
            clear_graph(client=client, graph_uri=searchspace_uri)

        if not bindings:
            continue

        random.shuffle(bindings)

        selected_indices = _select_valid_bindings(
            client=client,
            edb_uri=graph_uri,
            bindings=bindings,
            rule=rule,
            missing_heads=missing,
            searchspace_profiles=searchspace_profiles,
            term_mapping=term_mapping,
            chunk_size=chunk_size,
        )
        if not selected_indices:
            logger.debug(
                "Rule %s retrieved %d bindings not fitting current profiles.",
                rule.rule_id,
                len(bindings),
            )
            continue

        new_triples: list[str] = []
        for idx in selected_indices:
            binding_row = bindings[idx]
            for atom in open_atoms:
                subject = from_binding_row(atom.subject, binding_row)[0]
                obj = from_binding_row(atom.obj, binding_row)[0]
                profile = searchspace_profiles[atom.predicate]
                decrement_counts(profile.domain, subject)
                decrement_counts(profile.range, obj)
                profile.frequency -= 1
                new_triples.append(
                    format_triple(subject, atom.predicate, obj, term_mapping)
                )

        if added := insert_triples_sparql(
            client=client,
            graph_uri=graph_uri,
            triple_stream=new_triples,
            chunk_size=chunk_size,
        ):
            logger.info(
                "Rule %s added %d triples using closed head completion.",
                rule.rule_id,
                added,
            )

            new_support = get_support(client, rule, graph_uri)

            if new_support >= rule.support:
                rule.closed = True
                if new_support > rule.support:
                    logger.warning(
                        "%s: exceeded support (%d) after using closed head completion.",
                        rule.rule_id,
                        rule.support - new_support,
                    )
            return added

    return 0
