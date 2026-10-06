# Concepts

A glossary of the domain terms used throughout the code and in [`architecture.md`](architecture.md), for anyone navigating this repo without a Knowledge Graph / Datalog background.

## Knowledge Graph (KG)

A set of **triples** `(subject, predicate, object)` — e.g. `(ex:Mario, ex:hasAge, "25")`. The **predicate** is also called a *relation*. This project stores every graph in a graph database (Virtuoso or GraphDB) and manipulates it via SPARQL, never in-memory (see the project summary in [`AGENTS.md`](../AGENTS.md)).

## Synthetic KG generation

The goal of this project: produce a new graph that has the same *statistical shape* as a source graph (same predicate frequencies, same domain/range distributions, same rules holding over it) without needing continued access to the source graph itself, only its extracted metrics and a rule set. Useful when the source graph can't be shared or shipped as-is (privacy, size, licensing) but its structural properties still need to be available for e.g. downstream LLM fine-tuning experiments.

## Predicate profile

A `PredicateProfile` (`engine/metrics.py`) is the per-predicate summary the whole pipeline is built on:

- **frequency** — how many triples use this predicate.
- **domain** — `{subject → count}`, how many triples each subject appears in as the subject of this predicate.
- **range** — `{object → count}`, the same for objects.
- **reflexivity** — how many triples have the same subject and object.
- **closed** — whether this predicate has reached its target `frequency` (see [Closure](#closure)); `False` until then.

`GraphMetrics` (same module) is just a `{predicate → PredicateProfile}` map plus a total triple count, extracted from a graph either over SPARQL (`from_uri`, the production path) or from an in-memory `rdflib.Graph` (`from_rdflib`, used elsewhere for small/offline graphs).

## Horn Rules

A **Horn Rule** is an implication: `body → head`, where the body is a conjunction of **atoms** and the head is a single atom. An **atom** (`core/rules.py: Atom`) is a triple pattern where subject/object may be variables (`?x`) instead of concrete resources, e.g.:

```
?a ex:parentOf ?b AND ?b ex:parentOf ?c -> ?a ex:grandparentOf ?c
```

Rules are mined with AMIE3 (`mine_rules/run_amie.py`) and loaded from a CSV per dataset (`rules.rules_file` in each config), parsed by `core.rules.parse_rule_set`. Each row has a `rule_id`, which `run_amie.py` numbers 1..N after sorting the rules by std confidence, then PCA confidence, both descending; logs, the run summary and the relation-graph PNG name rules by it, so a rule keeps its ID whatever the PCA threshold.

Rule quality metrics carried alongside each rule (from the CSV, used to filter which rules are trusted enough to drive generation):

- **Support** — count of distinct bindings of the *head atom's* variables for which the head fact holds in the source graph and the body holds for at least one binding of its own extra variables (if any). Those extra body-only variables aren't projected over, so each one only needs a single witness — matching AMIE3's definition. How much evidence the rule has.
- **Head coverage** — support divided by the total number of head-predicate triples in the graph. What fraction of the target relation this rule explains.
- **Std(ard) confidence** — support divided by the number of bindings that satisfy the body (closed-world: body-satisfying bindings that *don't* also satisfy the head count against the rule).
- **PCA confidence** — like standard confidence, but under the *Partial Completeness Assumption*: only counts a body-satisfying binding as contradicting evidence if some other object is already known for the same subject/predicate. More forgiving of open-world incompleteness, so PCA confidence is normally ≥ standard confidence, and is what `rules.pca_threshold` filters on when it is set (`RulesConfig` in `core/config.py`); otherwise the pipeline keeps the rules with standard confidence 1.

## Extensional database (EDB) and Intensional database (IDB)

Standard Datalog terminology, used directly as named-graph URIs in each config (`graph.edb_uri`, `graph.synthetic_uri`):

- **Extensional predicate** — never appears as a rule's head; its truth is asserted directly as ground facts (there's no rule to derive it from). The **Extensional Database (EDB)** is the set of such ground facts — `engine/edb.py` generates it to satisfy both the predicate profiles and any rule bodies that reference these predicates.
- **Intensional predicate** — appears as some rule's head; its truth is *derived* by applying rules over already-known facts. The **Intensional Database (IDB)** is the EDB plus everything derivable from it by forward-chaining the rules — this is the final synthetic graph (`graph.synthetic_uri`), built by `engine/completion.py`'s `complete_graph` starting from the EDB. The pipeline removes every [cyclic rule](#cyclic-rule) first, so completion can derive every intensional predicate from the EDB.

## Closure

A predicate or rule is **closed** once it has reached its target count:

- A predicate is closed when the number of triples using it in the graph reaches its profile's `frequency` — recorded on the `PredicateProfile` itself as `closed: bool` (`engine/metrics.py`), flipped to `True` once and never reset.
- A rule is closed when the number of distinct bindings satisfying both its body and head reaches its `support` — recorded the same way on `HornRule` as `closed: bool` (`core/rules.py`).

Both EDB generation and synthetic-graph completion loop until everything relevant is closed (or a step makes no more progress), and both treat these `closed` fields as the single source of truth rather than separately maintained state: `engine/edb.py`'s `generator.update_closed_preds` sets `PredicateProfile.closed`, and the rule-support check in `generate_extensional_predicates` sets `HornRule.closed` directly (any `closed_preds` seen locally in that module, or in `check_triples_from_rule`, is a short-lived set derived from `PredicateProfile.closed` for set algebra, not separately maintained state). `engine/completion.py`'s `complete_graph` sets both the same way, via `engine/generator.py`'s `get_closed_rules`/`get_closed_preds` — small SPARQL queries checking a rule's support or a predicate's frequency against the graph — called once after each forward-chaining pass reaches a stale state.

## Rule application order

`engine/completion.py`'s `complete_graph` does not order rules at all: every pass it attempts every rule in the set (`generator.apply_rule`), and repeats until a pass adds nothing. Whichever rules can fire, fire, in whatever order the rule dict happens to iterate in — the loop's repetition substitutes for explicit ordering (e.g. a rule whose body depends on another rule's head simply produces nothing until a later pass, once that head exists). Multiple rules sharing a head predicate, or a recursive rule (head predicate also in its own body), are not gated on each other in any way; whichever fires first in a pass, fires.

`complete_graph` also calls `apply_rule` without a `profile`, so this loop is not budget-constrained by a predicate's target `frequency` either — it runs to full saturation (every triple every rule can derive) each time it's invoked, and target `support`/`frequency` are only checked *afterward* (see [Closure](#closure)) to report what's closed, not to cap generation.

Only EDB generation still orders work explicitly: `core/rules.get_extensional_dependencies` makes a less restrictive rule wait for a more restrictive one that shares an extensional predicate, so satisfying the looser rule first can't consume bindings the stricter rule still needs — see [`algorithm.md` §3.2.2](algorithm.md#322-mechanism-2-rule-driven-grounding). That ordering exists only because EDB generation is profile-budget-constrained in a way completion isn't, so it has no equivalent here.

**Note**: an earlier version of this pipeline (`engine/idb.py`'s `generate_idb`, since removed) took a different approach — a same-head "more restrictive first" dependency order (`get_intensional_dependencies`) gating which rule could fire, plus an upfront `check_uninferrable_preds` check that every intensional predicate has some derivation path back to extensional ones. Neither is wired into the pipeline today: `complete_graph` is a plain brute-force fixpoint instead, so a rule set that can't actually be fully derived currently surfaces late, as a stale/under-target result, rather than failing upfront.

## Term mapping / namespace

RDF terms are written as bare short names in rules/data (`hasAge`) but need a full URI (`<http://example.org/hasAge>`) for SPARQL. `core/utils.build_term_mapping` builds a `{short name → namespace}` dict from `core/utils.DEFAULT_PREFIXES`, overridden by the experiment's own `graph.term_namespaces` config entries, plus a `"default"` fallback set to `graph.namespace` for any term without an explicit override — `core/utils.format_term`/`format_triple` use that mapping to resolve terms wherever a query or triple is built.

## Grounding sampler

`engine/generator.sample_groundings` builds only the groundings of a rule body that the rule still needs (`rule.support` minus the heads the EDB already yields, per `queries.count_producible_heads`), straight from the predicate profiles, instead of materializing every candidate triple and joining them. Variables shared between atoms are drawn once from the intersection of the profile positions they occupy, so the atoms connect the way the rule requires. Support counts distinct projections onto the head variables: head variables must take a new combination in every accepted grounding, while body-only (existential) variables add no support and are reused as witnesses.

## Stale cycle

EDB generation only seeds extensional predicates (never a rule head). If every rule producing a predicate depends on that predicate itself (`p -> p`, e.g. `spouse(a,b) => spouse(b,a)`) or on a rule that depends back on it (`A -> B -> A`), completion can never start: the predicates stay empty. A cycle of the relation graph (`core/rules.get_relation_graph`) none of whose predicates has triples after completion is *stale*.

The pipeline avoids stale cycles by removing [cyclic rules](#cyclic-rule) before EDB generation until no cycle is left. `engine/cycles.break_cycles`, which the pipeline no longer calls, picks one rule of one stale cycle (fewest ungrounded body atoms, non-recursive first, most restrictive first), instantiates its ungrounded body atoms with `sample_groundings` as if they were extensional (joined via `fixed_bindings` with any body atoms already grounded), inserts them into the synthetic graph only, and returns; the caller then re-runs completion and calls it again until nothing more is seeded. Intensional dependencies do not gate the choice: the non-recursive rules a recursive rule would wait for can never fire inside a stale cycle.

## Cyclic rule

A rule with an edge `body predicate -> head predicate` on a cycle of the relation graph (`core/rules.get_relation_graph`), meaning both predicates are in the same strongly connected component. A recursive rule (`p -> p`) is cyclic, and so is a rule whose body predicate depends back on its head through other rules, even if only one of its body predicates does.

`cli/main.py` removes cyclic rules right after the rule set is parsed and before EDB generation, so no [stale cycle](#stale-cycle) can occur. By default `core/rules.remove_minimal_cyclic_rules` removes the fewest rules that leave no cycle ([method](algorithm.md#52-removing-the-fewest-rules), [code](architecture.md#cyclic-rule-removal)); with `rules.cycle_removal: "all"` or `--cycle-removal all`, `core/rules.remove_cyclic_rules` removes every cyclic rule. The head predicate of a removed rule becomes extensional unless a remaining rule still derives it, and the summary lists the removed rules with their support, original -> synthetic.

## Filling open relations

After completion every kept rule is usually closed, but a predicate can still be short of its target frequency (on `fr.no-literals`, `predecessor` had 6 of 358 triples). Phase 4 of `cli/main.py` (`engine/fill.py`) adds the missing triples without changing the support of any kept rule. Rules dropped by the confidence filter or by `remove_cyclic_rules` are not protected.

The support of a rule is the number of distinct head-variable bindings for which some body grounding holds and the head triple is in the graph (`core/queries.get_support`). Completion stops when a pass adds nothing, so every body grounding that `apply_rule` matches already has its head.

Random triples, drawn from the remaining domain and range counts, are safe only for a predicate that is in no kept rule's body. Such a triple changes no body grounding, and as a head it can only complete a grounding whose head is missing. After completion that happens only for groundings `apply_rule` skips: `build_rule_query` requires all variables to take different values and `get_support` does not, so a reflexive triple can still raise a support. When the predicate is in a rule body, a random triple can create a body grounding whose head is present (support goes up) or absent (the graph no longer satisfies the rule, and completing it again would raise the support).

A candidate triple `(s, p, o)` is kept when, for every kept rule in which `p` occurs:

- Head check, when `p` is the rule's head predicate (`build_head_impact_query`): no body grounding has the head values `(s, o)`.
- Body check, for each body atom with predicate `p` (`build_body_impact_query`): every new body grounding that matches the candidate to that atom has a head binding already in the support, meaning another grounding has the same head values and the head triple is present. Other body atoms with predicate `p` may match the candidate as well.

No kept rule has `p` in both its body and its head, since that rule would be cyclic. Kept triples leave both the supports and the closure under the kept rules unchanged, so completing the graph again adds nothing. Each kept triple is inserted at once, so the checks of the next candidates see it. A predicate is given up after `engine/fill.MAX_FILL_DRAWS` draws in a row without a kept triple.

## Solvability check

While selecting which groundings to commit to the EDB (`generator.sample_groundings`), each candidate grounding is checked: it is accepted only if committing it would still leave every affected predicate's remaining domain/range degree sequence realizable as a graph (`generator.is_assignment_solvable`, a Gale-Ryser/Havel-Hakimi style check) before accepting it — so early choices don't paint later predicates into an impossible corner.
