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

After completion a predicate can still be short of its target frequency, and a rule short of its support. Phase 4 of `cli/main.py` (`engine/fill.py`'s `fill_open_predicates`) adds the missing triples of every open predicate. The method is described in [section 4.1 of `algorithm.md`](algorithm.md#41-filling-open-relations); this entry covers the terms and the checks.

On `fr.no-literals.csv` at PCA 0.9, completion leaves `father` at 100/561, `parent` at 508/946, `predecessor` at 354/358 and `spouse` at 108/865, and rules 10 (`father => parent`, 100/431), 19 and 24 (`father & mother => spouse`, 54/215 and 54/200) open.

**Protected and open rules.** Only the kept rules are considered; rules dropped by the confidence filter or by cycle removal are not. A rule closed when phase 4 starts keeps its support exactly. An open rule may gain support up to its target, and is marked closed (`HornRule.closed`) when it reaches it. The graph stays closed under every kept rule: no body grounding is left without its head, so completing it again adds nothing.

**Support.** The support of a rule is the number of distinct head-variable bindings for which some body grounding holds and the head triple is in the graph (`core/queries.get_support`). Completion stops when a pass adds nothing, so every body grounding that `apply_rule` matches already has its head. A new triple with predicate `p` can therefore only change a rule in which `p` occurs: as the head, by completing a grounding with those head values, or in the body, by creating new groundings. Random triples are safe for a predicate in no rule body, with one exception: `build_rule_query` requires all variables to take different values and `get_support` does not, so a grounding with a reflexive triple can still lack its head (see the "Irreflexive relations" item in `BACKLOG.md`).

**Checks.** Every candidate triple `(s, p, o)` is checked against each kept rule in which `p` occurs, before it is inserted (`_FillState.impact`):

- Head check, when `p` is the rule's head predicate (`build_head_impact_query`): does some body grounding have the head values `(s, o)`? If so, the triple adds one to the support.
- Body check, for each body atom with predicate `p` (`build_body_impact_query`, read with `get_new_head_bindings`): the head bindings of the new body groundings that match the candidate to that atom and are not in the support yet, each with whether its head triple is present. Other body atoms with predicate `p` may match the candidate as well.

The triple is rejected if it would add a supported head binding to a closed rule, push an open rule past its target, or create a grounding without its head. The one exception is the head a B2 grounding (below) adds right after its body.

**Cases of an open rule** (`classify_open_rule`):

- A: every body predicate is closed. No new grounding can appear, so the rule can't reach its target; it is logged as a warning.
- B1: a body predicate is open and the head predicate is closed. Fill completes bodies for head triples the rule doesn't explain yet (`get_unsupported_head_bindings`).
- B2: a body predicate and the head predicate are open. Fill adds new groundings with their head triple.

**Passes.** The rule-driven pass takes the open rules of case B1 or B2 in rule ID order, builds groundings with `generator.sample_grounding_groups` on the remaining budgets (source counts minus synthetic counts), joined with the existing facts of the rule's closed body atoms, and inserts each grounding one triple at a time. If a triple is rejected, the grounding's triples already inserted are deleted again and their budget is given back. A rule is given up after `MAX_EMPTY_ROUNDS` sampling rounds in a row without an accepted grounding. The random pass then spends each predicate's budget left, one triple at a time, and gives a predicate up after `MAX_FILL_DRAWS` draws in a row without an accepted triple.

**Why these triples can only appear now.** EDB generation only creates extensional predicates and completion only adds heads, so an intensional predicate in other rules' bodies only gets the facts its own rules derive. `father` is derived by rule 38 alone (100 triples), which keeps rules 10, 19 and 24 below their support. Fill is the first step that creates facts of an intensional predicate outside the rules that derive it. On the run above it adds 171 `father` triples with their `parent` heads, raising rule 10 from 100 to 271.

**Open issue.** A triple that feeds several open rules is rejected when it leaves a grounding of another open rule without its head: `father(e,b)` needs `parent(e,b)` for rule 10 and, if `e` has a mother, a `spouse` triple for rules 19 and 24. In the run above the random pass rejects 1,915 `father` triples because of rule 10 and the rule-driven pass 970 because of rule 19, so `father` stops at 271/561 and rules 19 and 24 stay at 54. "Finish the fill step" in `BACKLOG.md` lists the next steps.

## Solvability check

While selecting which groundings to commit to the EDB (`generator.sample_groundings`), each candidate grounding is checked: it is accepted only if committing it would still leave every affected predicate's remaining domain/range degree sequence realizable as a graph (`generator.is_assignment_solvable`, a Gale-Ryser/Havel-Hakimi style check) before accepting it — so early choices don't paint later predicates into an impossible corner.
