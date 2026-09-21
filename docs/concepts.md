# Concepts

A glossary of the domain terms used throughout the code and in
[`architecture.md`](architecture.md), for anyone navigating this repo without a
Knowledge Graph / Datalog background.

## Knowledge Graph (KG)

A set of **triples** `(subject, predicate, object)` — e.g.
`(ex:Mario, ex:hasAge, "25")`. The **predicate** is also called a *relation*.
This project stores every graph in a graph database (Virtuoso or GraphDB) and
manipulates it via SPARQL, never in-memory (see the project summary in
[`AGENTS.md`](../AGENTS.md)).

## Synthetic KG generation

The goal of this project: produce a new graph that has the same *statistical
shape* as a source graph (same predicate frequencies, same domain/range
distributions, same rules holding over it) without needing continued access to
the source graph itself, only its extracted metrics and a rule set. Useful
when the source graph can't be shared or shipped as-is (privacy, size, licensing)
but its structural properties still need to be available for e.g. downstream
LLM fine-tuning experiments.

## Predicate profile

A `PredicateProfile` (`engine/metrics.py`) is the per-predicate summary the
whole pipeline is built on:

- **frequency** — how many triples use this predicate.
- **domain** — `{subject → count}`, how many triples each subject appears in
  as the subject of this predicate.
- **range** — `{object → count}`, the same for objects.
- **reflexivity** — how many triples have the same subject and object.
- **closed** — whether this predicate has reached its target `frequency`
  (see [Closure](#closure)); `False` until then.

`GraphMetrics` (same module) is just a `{predicate → PredicateProfile}` map plus
a total triple count, extracted from a graph either over SPARQL (`from_uri`, the
production path) or from an in-memory `rdflib.Graph` (`from_rdflib`, used
elsewhere for small/offline graphs).

## Horn Rules

A **Horn Rule** is an implication: `body → head`, where the body is a
conjunction of **atoms** and the head is a single atom. An **atom**
(`core/rules.py: Atom`) is a triple pattern where subject/object may be
variables (`?x`) instead of concrete resources, e.g.:

```
?a ex:parentOf ?b AND ?b ex:parentOf ?c -> ?a ex:grandparentOf ?c
```

Rules are mined upstream (this repo doesn't mine them, e.g. with AMIE-style
tools) and loaded from a CSV per dataset (`rules.rules_file` in each config),
parsed by `core.rules.parse_rule_set`.

Rule quality metrics carried alongside each rule (from the CSV, used to filter
which rules are trusted enough to drive generation):

- **Support** — count of distinct bindings of the *head atom's* variables for
  which the head fact holds in the source graph and the body holds for at
  least one binding of its own extra variables (if any). Those extra
  body-only variables aren't projected over, so each one only needs a single
  witness — matching AMIE3's definition. How much evidence the rule has.
- **Head coverage** — support divided by the total number of head-predicate
  triples in the graph. What fraction of the target relation this rule
  explains.
- **Std(ard) confidence** — support divided by the number of bindings that
  satisfy the body (closed-world: body-satisfying bindings that *don't* also
  satisfy the head count against the rule).
- **PCA confidence** — like standard confidence, but under the *Partial
  Completeness Assumption*: only counts a body-satisfying binding as
  contradicting evidence if some other object is already known for the same
  subject/predicate. More forgiving of open-world incompleteness, so PCA
  confidence is normally ≥ standard confidence, and is what
  `rules.pca_threshold` filters on (`RulesConfig` in `config.py`).

## Extensional vs. Intensional (EDB / IDB)

Standard Datalog terminology, used directly as named-graph URIs in each config
(`graph.edb_uri`, `graph.synthetic_uri`):

- **Extensional predicate** — never appears as a rule's head; its truth is
  asserted directly as ground facts (there's no rule to derive it from). The
  **Extensional Database (EDB)** is the set of such ground facts —
  `engine/edb.py` generates it to satisfy both the predicate profiles and any
  rule bodies that reference these predicates.
- **Intensional predicate** — appears as some rule's head; its truth is
  *derived* by applying rules over already-known facts. The **Intensional
  Database (IDB)**, built by `engine/idb.py`, is the EDB plus everything
  derivable from it by forward-chaining the rules — this is the final
  synthetic graph.

## Closure

A predicate or rule is **closed** once it has reached its target count:

- A predicate is closed when the number of triples using it in the graph
  reaches its profile's `frequency` — recorded on the `PredicateProfile`
  itself as `closed: bool` (`engine/metrics.py`), flipped to `True` once and
  never reset.
- A rule is closed when the number of distinct bindings satisfying both its
  body and head reaches its `support` — recorded the same way on `HornRule`
  as `closed: bool` (`core/rules.py`).

Both EDB and IDB generation loop until everything relevant is closed (or a
step makes no more progress). `engine/edb.py` treats these `closed` fields as
the single source of truth: `generator.update_closed_preds` sets
`PredicateProfile.closed`, the rule-support check in
`generate_extensional_predicates` sets `HornRule.closed` directly, and any
`closed_preds` seen locally in that module (or in `check_triples_from_rule`)
is a short-lived set derived from `PredicateProfile.closed` for set algebra
(unions, differences), not separately maintained state. `engine/idb.py` still
tracks closure with its own `closed_preds` / `closed_rule_ids` accumulator
sets (`get_closed_preds`/`get_closed_rules`/`update_closure`), independently
of the `closed` fields.

## Intensional rule dependencies

Multiple rules can share the same head predicate. Without an order between
them, a more general (looser) rule could consume bindings/budget (the
head predicate's remaining `frequency`/`support`) that a more specific
(restrictive) rule for the same head still needs, or a recursive rule (one
whose head predicate also appears in its own body) could fire before its
predicate has enough non-recursively-derived facts to recurse over.

`core/rules.get_intensional_dependencies` computes, per rule, the set of
other rule IDs it depends on and must wait for:

- Within a same-head group, a rule depends on every other rule whose body is
  a strict superset of its own — i.e. more restrictive, so it's produced
  first. Ties on equal-size bodies (the same rule up to variable renaming)
  are broken by support: the lower-support/rarer rule goes first.
- Recursive rules additionally depend on *every* non-recursive rule for the
  same head, plus the same superset-based dependency among themselves.

`engine/idb.py`'s generation loop (`generate_idb`) only applies a rule once
every rule ID in its dependency set is in `closed_rule_ids` — so more
restrictive (and, for recursive rules, all non-recursive) same-head rules
always close first.

**Caveat**: this only reorders *when* a structurally-derivable rule is
allowed to run — it doesn't change `check_uninferrable_preds`'s upfront
guarantee that every intensional predicate has some path back to extensional
ones. But it does mean a rule can now be gated on another rule's closure
indefinitely: if a dependency never closes (no more bindings satisfy its
body before its `support` target is met), everything depending on it stays
skipped, and the generation loop can reach a stale state with a predicate
still short of its target — a way of failing to reach full closure that
didn't exist before this ordering was introduced.

## Term mapping / namespace

RDF terms are written as short names in rules/config (`ex:hasAge`) but need a
full URI (`<http://example.org/hasAge>`) for SPARQL. `utils.get_term_mapping`
builds a `{short name → namespace}` dict by scanning a `.ttl` ontology file's
`@prefix` declarations line-by-line (no RDF library, O(1) memory) —
`utils.format_term`/`format_triple` use that mapping to resolve terms wherever
a query or triple is built.

## Grounding sampler

`engine/generator.sample_groundings` builds only the groundings of a rule body
that the rule still needs (`rule.support` minus the heads the EDB already yields, per `queries.count_producible_heads`),
straight from the predicate profiles, instead of materializing every candidate
triple and joining them. Variables shared between atoms are drawn once from the
intersection of the profile positions they occupy, so the atoms connect the way
the rule requires. Support counts distinct projections onto the head variables:
head variables must take a new combination in every accepted grounding, while
body-only (existential) variables add no support and are reused as witnesses.

## Stale cycle

EDB generation only seeds extensional predicates (never a rule head). If every
rule producing a predicate depends on that predicate itself (`p -> p`, e.g.
`spouse(a,b) => spouse(b,a)`) or on a rule that depends back on it
(`A -> B -> A`), completion can never start: the predicates stay empty. A cycle
of the relation graph (`core/rules.get_relation_graph`) none of whose
predicates has triples after completion is *stale*.

`engine/cycles.break_cycles` picks one rule per stale cycle (fewest ungrounded
body atoms, non-recursive first, most restrictive first), instantiates its
ungrounded body atoms with `sample_groundings` as if they were extensional
(joined via `fixed_bindings` with any body atoms already grounded), inserts them
into the synthetic graph only, and re-runs completion. Intensional dependencies
do not gate the choice: the non-recursive rules a recursive rule would wait for
can never fire inside a stale cycle.

## Solvability check

While selecting which groundings to commit to the EDB
(`generator.sample_groundings`), each candidate grounding is checked: it is
accepted only if committing it would still
leave every affected predicate's remaining domain/range degree sequence
realizable as a graph (`generator.is_assignment_solvable`, a
Gale-Ryser/Havel-Hakimi style check) before accepting it — so early choices
don't paint later predicates into an impossible corner.
