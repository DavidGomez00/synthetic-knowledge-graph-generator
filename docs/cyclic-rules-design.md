# Keeping complete cyclic rules: design draft (19 November 2026)

Status: draft, not implemented. One open problem (below) blocks the plan.

## Context

Rules are complete (PCA confidence 1, forward-chained by completion) or partial (grounded with their head in F0, never forward-chained), and cyclic or non-cyclic in the relation graph of the complete rules. `core/cycles.remove_minimal_cyclic_rules` deletes complete cyclic rules because their head relation, being deducible, gets no facts in F0, so completion could never produce its first fact. At `r100` it removes 62 rules in 11 datasets: 50 recursive (family's "siblings share parents" 6–11, 43, 45, 50, uncle/aunt propagation; french's `spouse`/`hasSpouse`, `name`/`gender`; YAGO3-10's `hasNeighbor`, `isMarriedTo`), 10 compositions on a cycle (SGKG1–4, family 5 and 24), 2 inverses (`wife ⇒ husband`, `successor ⇒ predecessor`), 2 symmetric `name` rules. Partial rules are kept, cyclic or not. In family's source, one step from a seed closes the parent rules: 384 of 386 sibling groups are cliques, and `mother` has 1,714 facts that 701 seed facts would generate (`father` 2,010 from 967).

## Agreed so far

1. **Renaming for F0 only.** In a complete cyclic rule, a body occurrence of the head relation `p` is renamed `p_0` for F0 initialization (`?a mother ?c & ?c brother ?b ⇒ ?a mother ?b` becomes `?a mother_0 ?c & ?c brother ?b ⇒ ?a mother ?b`), which removes the cycle from the rule set F0 uses. Completion uses the original rules. Partial rules are not renamed.
2. **Which rules are cyclic.** A simple check per complete rule (its body has a relation from which the head is reachable, i.e. in the head's strongly connected component, or the head relation itself) replaces the selection of `remove_minimal_cyclic_rules`.
3. **Shared profile.** `p_0` and `p` share one `PredicateProfile`; every `p_0` triple spends `p`'s frequency and degrees, and the profiles have priority over reaching a rule's target. `p` is deducible: never filled at random. `p_0` is never in `non_deducible_profiles` and never given to the direct-match step (`check_direct_matches`, which today runs on every non-symmetric profile, deducible relations included).
4. **Bodies only.** F0 places the bodies of complete cyclic rules (the `p_0` seed facts and the other atoms); their heads come from completion, as for every complete rule.
5. **Order in F0.** Complete cyclic rules first, then complete non-cyclic rules, then partial rules; within each group today's restrictiveness order (`core/rules.get_non_deducible_dependencies`: more atoms first, then smaller target).
6. **Counting and rounds.** `count_producible_heads` reads `p_0` atoms as `p` (same predicate in F0). Grounding runs in rounds of `target_heads / (n + 1)` groundings, `n` being the number of `p_0` atoms in the body (a join that uses `n` `p_0` facts can enable further heads), recounting after each round, stopping at the rule's support or when a round adds nothing.
7. **Harmonization.** Once all complete cyclic rules are grounded, `p_0` facts become `p` facts and the aliases are dropped, before the other rules, the random step and completion.
8. **Completion.** Original rules, cyclic ones included, to a fixpoint; no cycle is stale since every cyclic relation has facts in F0. Compare with and without `--cap-completion`, since closing a recursive rule on a random propagating relation can derive more than the source.

## Open problem: a deducible relation in the body

Reading `p_0` as `p` counts the heads that the F0 facts of `p` yield. It does not help when another body relation `q` is itself deducible: `q` has no facts in F0 while F0 is built, so how many `q` facts a rule `p ∧ q ⇒ p` needs to reach its target can't be counted. Today's count (`count_producible_heads` over the non-deducible body) ignores deducible atoms, which is optimistic, and doesn't project a head variable that occurs only in them.

Keeping the cyclic rules makes this the common case. Their heads become deducible: in family `aunt`, `brother`, `daughter`, `father`, `husband`, `mother`, `nephew`, `niece`, `son` and `uncle` (only `sister` stays non-deducible); in french `gender`, `hasSpouse`, `name`, `predecessor`, `spouse`; in SGKG1–4 `hasPredecessor`, `hasSibing`. Of the 52 recursive rules removed today:

| Shape | Rules | Counting with `p_0 = p` |
|---|---:|---|
| One `p` atom, other atom non-deducible | 23 | exact; for a chain the closure is one property-path query (`?a mother/^brother* ?b`) |
| One `p` atom, other atom deducible (family 15, 16, 19, 28, 31, 36, …) | 20 | not countable: `q` has no F0 facts |
| Two or more `p` atoms (`name`/`name`, `isInterestedIn`) | 7 | one step only (lower bound) |
| Symmetry | 2 | one step is the closure |

Options discussed, none chosen:
- rename every deducible body relation of a cyclic rule to its `_0` seed copy (sharing its profile), so every body atom is grounded in F0 and counted; F0 then seeds many deducible relations, and completion adds to them (needs `--cap-completion`);
- keep `q` unseeded and, before each count, run the rules that derive `q` on a scratch copy of F0 (exact, one small completion per round);
- estimate from the source the share of body matches that also satisfy `q` (cheap, unreliable on copies that lose the alignment between relations);
- apply the scheme only to the 23 linear rules with a non-deducible second atom and the 2 symmetric ones, and keep removing the rest.

Related: complete non-cyclic rules that read a relation made deducible by a kept cyclic rule (family rules 3, 4, 12, 13 read `mother`/`father`) hit the same problem.
