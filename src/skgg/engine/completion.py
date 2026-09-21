"""Builds the "complete" graph used as the source for metric extraction.

Used by `cli/upload.py` after a base graph (`.nt` file) is uploaded: forward-chains
every rule over the base graph, assuming rule bodies are fully grounded, until no
rule can add any more triples. The result (`graph.complete_uri`) is what
`engine.metrics.GraphMetrics.from_uri` later profiles for EDB/IDB generation.
"""

import copy
import logging
import random
from collections import defaultdict

from SPARQLWrapper import SPARQLWrapper

from skgg.core.queries import (
    execute_select_query,
    from_binding_row,
    get_existing_triples,
    get_predicate_frequencies,
    get_support,
    initialize_graph,
    insert_triples_sparql,
)
from skgg.core.rules import Atom, HornRule
from skgg.engine.generator import apply_rule, decrement_counts, is_assignment_solvable
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


_SolvedAtoms = tuple[
    dict[str, str], dict[str, PredicateProfile], list[tuple[str, str, str]]
]


def _solve_open_atoms(
    open_atoms: list[Atom],
    known_values: dict[str, str],
    profiles: dict[str, PredicateProfile],
    client: SPARQLWrapper,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> _SolvedAtoms | None:
    """Finds a value for every variable in `open_atoms` not already fixed by
    `known_values`, such that every atom becomes a profile-valid, solvable
    triple. Returns (all_values, updated_profiles, novel_triples) --
    `novel_triples` is the subset of open_atoms' (subject, predicate, obj)
    triples confirmed not to already exist -- or None if no profile-valid
    assignment exists at all.

    At least one atom is guaranteed novel by construction: the caller's query
    wraps every open atom in `FILTER NOT EXISTS`, with the linking variables
    free inside it, so no value of them (including whatever `_solve_open_atoms`
    picks) can make the full conjunction already hold in the graph. So
    `novel_triples` is never empty here -- no explicit reject-and-backtrack
    branch for that case is needed.

    `known_values` may be incomplete: variables shared only among open atoms
    ("linking variables", e.g. `w` in `p2(z,w), p3(w,y)` when `z`/`y` are
    already known but `w` isn't) are solved for here, by intersecting the
    relevant predicates' remaining domain/range budgets and trying candidates
    at random. Forward-checking: an atom is validated (and, if valid,
    decremented) as soon as every one of its variables is resolved, rather
    than waiting for the full assignment -- an invalid choice is caught and
    backtracked on immediately instead of wasting work assigning the rest.

    Existence is pre-fetched once, up front, for every triple any atom could
    possibly produce given `known_values` and each free variable's full
    candidate pool -- mirroring `engine/edb.py`'s `_select_valid_bindings`,
    which uses the same "pre-fetch once, then treat an already-existing
    triple as a free no-op" pattern to avoid both a live query per candidate
    tried and over-decrementing a profile for a triple that doesn't actually
    consume any new budget.

    Called independently per binding row by the caller -- rows are solved
    greedily, not jointly, so an earlier row's choice can consume a scarce
    term a later row also needed even when a different (still valid) choice
    for the earlier row would have let both succeed. Unlike
    `_select_valid_bindings`, there's no backtracking across rows to recover
    from that; a known, deliberately deferred limitation.
    """
    free_vars = sorted(
        {
            v
            for atom in open_atoms
            for v in (atom.subject, atom.obj)
            if v.startswith("?") and v not in known_values
        }
    )

    def candidates_for(
        var: str, branch_profiles: dict[str, PredicateProfile]
    ) -> list[str]:
        """Returns the possible values that fit for given variable."""
        options: set[str] | None = None
        for atom in open_atoms:
            profile = branch_profiles[atom.predicate]
            if atom.subject == var:
                atom_options = set(profile.domain)
            elif atom.obj == var:
                atom_options = set(profile.range)
            else:
                continue
            options = atom_options if options is None else options & atom_options
        return list(options or set())

    def term_options(term: str) -> list[str]:
        """Every value `term` could possibly take: itself if already known or
        a constant, else its full (unnarrowed) candidate pool."""
        if term in known_values:
            return [known_values[term]]
        if not term.startswith("?"):
            return [term]
        return candidates_for(term, profiles)

    all_potential_triples = {
        format_triple(s, atom.predicate, o, term_mapping)
        for atom in open_atoms
        for s in term_options(atom.subject)
        for o in term_options(atom.obj)
    }
    existing_triples = get_existing_triples(
        client=client,
        graph_uri=graph_uri,
        candidate_triples=all_potential_triples,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
    )

    def is_resolved(atom: Atom, values: dict[str, str]) -> bool:
        return all(
            not t.startswith("?") or t in values for t in (atom.subject, atom.obj)
        )

    def is_valid(
        atom: Atom,
        values: dict[str, str],
        branch_profiles: dict[str, PredicateProfile],
        branch_triples: set[str],
        novel_triples: list[tuple[str, str, str]],
    ) -> bool:
        """Check if the triple violates profle constraints.

        If the formated triple is included in the existing triples, no profile needs to
        be updated. Else, check profile constraints and if passes, update profiles and
        add the triple to the novel triples."""
        subject = values.get(atom.subject, atom.subject)
        obj = values.get(atom.obj, atom.obj)
        triple = format_triple(subject, atom.predicate, obj, term_mapping)
        if triple in existing_triples or triple in branch_triples:
            return True

        profile = branch_profiles[atom.predicate]
        if (
            profile.frequency <= 0
            or subject not in profile.domain
            or obj not in profile.range
            or not is_assignment_solvable(profile, subject, obj)
        ):
            return False

        decrement_counts(profile.domain, subject)
        decrement_counts(profile.range, obj)
        profile.frequency -= 1
        branch_triples.add(triple)
        novel_triples.append((subject, atom.predicate, obj))
        return True

    def backtrack(
        remaining_variables: list[str],
        values: dict[str, str],
        branch_profiles: dict[str, PredicateProfile],
        valid_atoms: set[Atom],
        branch_triples: set[str],
        novel_triples: list[tuple[str, str, str]],
    ) -> _SolvedAtoms | None:
        """Tries to extend `values` to a full assignment of `remaining_variables`.

        Returns None if no assignment of the remaining free variables yields a
        profile-valid triple for every atom in `open_atoms`; otherwise returns
        a tuple of the complete variable assignment (`known_values` plus every
        solved free variable), the resulting state of `profiles` after
        decrementing each accepted atom, and the list of confirmed-novel
        triples.
        """
        # Check for each atom resolved that it is valid. If there is an invalid atom,
        # skip this branch (return None). Unresolved atoms cannot be invalid.
        for atom in open_atoms:
            if atom in valid_atoms or not is_resolved(atom, values):
                continue
            if not is_valid(
                atom, values, branch_profiles, branch_triples, novel_triples
            ):
                return None
            valid_atoms.add(atom)

        # End of recursion
        if not remaining_variables:
            return values, branch_profiles, novel_triples

        # Solve one variable
        var, *rest = remaining_variables
        options = candidates_for(var, branch_profiles)
        random.shuffle(options)
        for candidate in options:
            # Check if there is a valid branch with these option
            result = backtrack(
                rest,
                {**values, var: candidate},
                copy.deepcopy(branch_profiles),
                set(valid_atoms),
                set(branch_triples),
                list(novel_triples),
            )
            if result is not None:
                # Solution found
                return result
        # There is no solution
        return None

    return backtrack(
        free_vars, dict(known_values), copy.deepcopy(profiles), set(), set(), []
    )


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
    present; a triple this function proposes, having survived its own FILTER NOT EXISTS,
    can't also be a new witness for another rule's body, or apply_rule would already
    have inserted it.

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
        ## ---- Query graph to retrieve values that don't generate new heads ----
        other_atoms = [atom for atom in rule.body if atom not in open_atoms]
        other_patterns = "\n            ".join(
            [f"{atom} ." for atom in other_atoms] + [f"{rule.head} ."]
        )
        other_patterns_vars = {
            var
            for atom in (*other_atoms, rule.head)
            for var in (atom.subject, atom.obj)
            if var.startswith("?")
        }
        open_patterns = "\n            ".join([f"{atom} ." for atom in open_atoms])
        open_patterns_vars = {
            t
            for atom in open_atoms
            for t in (atom.subject, atom.obj)
            if t.startswith("?")
        }
        # A var in open_pattern_vars that is not in other_pattern_vars is just a "link"
        # variable shared only among open atoms.
        proj_vars = sorted(open_patterns_vars & other_patterns_vars)
        proj = " ".join(proj_vars) if proj_vars else "*"

        query = f"""
        SELECT DISTINCT {proj} WHERE {{
          GRAPH <{graph_uri}> {{
            {other_patterns}
            FILTER NOT EXISTS {{
                {open_patterns}
            }}
          }}
        }}
        """

        logger.debug("%s", query)

        bindings = execute_select_query(client, query)
        if not bindings:
            continue

        open_preds_for_atoms = {atom.predicate for atom in open_atoms}
        working_profiles = {
            pred: copy.deepcopy(profiles[pred]) for pred in open_preds_for_atoms
        }
        solved_bindings = 0
        row_atom_values: list[list[tuple[str, str, str]]] = []
        for row in bindings:
            known_values = {var: from_binding_row(var, row)[0] for var in proj_vars}
            solved = _solve_open_atoms(
                open_atoms,
                known_values,
                working_profiles,
                client,
                graph_uri,
                term_mapping,
                chunk_size,
            )
            if solved is None:
                continue
            _, working_profiles, novel_triples = solved
            row_atom_values.append(novel_triples)
            solved_bindings += 1
            if solved_bindings >= missing:
                break

        if not row_atom_values:
            logger.debug(
                "Rule %s retrieved %d bindings not fitting current profiles.",
                rule.rule_id,
                len(bindings),
            )
            continue

        new_triples: list[str] = []
        for triples in row_atom_values:
            for subject, predicate, obj in triples:
                profile = profiles[predicate]
                decrement_counts(profile.domain, subject)
                decrement_counts(profile.range, obj)
                profile.frequency -= 1
                new_triples.append(format_triple(subject, predicate, obj, term_mapping))

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
