# Synthetic Knowledge Graph Generation: Algorithm

*Working draft. Describes the ideas behind the method, independently of any implementation.*

## 1. Preliminaries

### 1.1 Knowledge graphs

A Knowledge Graph (KG) is a directed edge-labeled graph $KG=(V,E,L)$ where $V$ is a set of entities, $L$ is a set of relation labels, and $E\subseteq V\times L\times V$ is a set of labeled edges represented as triples $(s,p,o)$ \cite{Hogan_2021_knowledge_graphs}.

Triple pattern: .... Important: In this project all triple patterns may contain variables in the subject and object, but never in the predicate.

### 1.2 Horn rules

A Horn rule has the form $$r:\quad B_1(\mathbf{x}_1) \wedge \dots \wedge B_n(\mathbf{x}_n) \;\Rightarrow\; H(\mathbf{x}_h)$$ where the body atoms $B_i$ and the head atom $H$ are triple patterns whose subject and object are variables or constants. A **grounding** $$\sigma(t, \mathcal{G})$$ is an assignment of entities from $\mathcal{G}$ to the variables in $t$ such that $\forall (s,p,o)\in t,\sigma(t)\in\mathcal{G}$.

### 1.3 Rule metrics

The **support** of a rule $r$ in $\mathcal{G}$ measures its evidence in $\mathcal{G}$. It is computed as the count of distinct groundings of the rule's head that have at least one corresponding grounded body: $$ \operatorname{support}(r, \mathcal{G}) = \bigl|\{H_r\in \sigma(\overrightarrow{B_r}\land H_r, \mathcal{G}) \}\bigr|$$.

**Standard confidence** ....

**PCA confidence** ....

### 1.4 Initial and deduced facts

#### 1.4.1. Initial facts (F0) and deduced facts (F*)

The initial facts $F_0$ are the set of triples $(s,p,o)$ the generator builds before applying any rule (Section 3). The deduced facts $F^*$ are $F_0$ closed under the complete rules of the rule set: every triple that forward chaining derives from $F_0$, together with $F_0$ itself (Section 4). $F^*$ is the synthetic graph.

We say a relation-type (predicate) is **deducible** if it can be produced by a rule, i.e., a triple pattern containing this relation appears in the head of a complete rule of the rule set, or **non-deducible** otherwise. Non-deducible relations can never be produced by the rules, so $F_0$ must hold all the triples of these relations. We call these triples "facts", and we use them to derive the triples of the deducible relations. This split organises the method in two steps: generate $F_0$, then complete it into $F^*$ with the rules.

### 2.4 Relation profiles

We define a descriptor of a graph $\mathcal{G}$ as a profile $P = (\mathcal{G}, p, Ran, Dom)$, where $\mathcal{G}$ is a graph, $p$ is a relation-type, $Dom$ is a counter of each subject that appears with $p$ in $\mathcal{G}$ (describes the domain of $p$ in $\mathcal{G}$ and the frequency of each subject), and $Ran$ is a counter of each object that appears with $p$ in $\mathcal{G}$ (describes the range of $p$ in $\mathcal{G}$ and the frequency of each object).

The profile fixes the amount of triples that contain the relation $p$ and how many times each entity takes part in them, but not which subject-object actually appear together.

The **frequency of a relation** $p$ in a graph $\mathcal{G}$ is the count of distinct triples in $\mathcal{G}$ that contain the relation $p$. $$\operatorname{frequency}(p, \mathcal{G}) = \bigl|\{t\in\mathcal{G}: p\in t\}\bigr|$$

The profiles are computed over the target graph. We say a relation is closed when its frequency in the new graph is equal to its frequency on the target graph.
#### 2.4.1 Profile constraints

Given degree maps $a$ (over subjects) and $b$ (over objects) with equal total $f$, a set of pairs $S$ with exactly those degrees, and no repeated pair, may or may not exist. For target maps $a_t$ and $b_t$ at least one set of pairs $S$ exists, since these maps are extracted from the target graph, which is assumed to contain only unique triples.

The classical **Gale–Ryser** condition characterises when $S$ exists. Sort the subject degrees as $a_1 \ge a_2 \ge \dots \ge a_m$. A simple bipartite graph with these degrees exists if and only if the totals agree and $$ \sum_{i=1}^{k} a_i \;\le\; \sum_{j} \min(b_j,\, k)\ \forall  k = 1,\dots,m . $$ Its first instance ($k=1$) says that no subject may need more distinct partners than there are objects with non-zero degree, and the symmetric statement holds for objects.

The generator (described further...) commits to facts one at a time. A commitment consumes one unit of degree from a subject and one from an object. If commitments are made carelessly, a later state can arise in which some entity still requires more partners than exist. For example, for a relation $p$, take the domain $\{x{:}2,\ y{:}1\}$ and range $\{w{:}2,\ z{:}1\}$. If the fact $p(y, w)$ is chosen first, subject $x$ needs two distinct objects, and $w$ and $z$ have one unit left each. The state $p\{x{:}2\}$, $\{w{:}1, z{:}1\}$ can produce a set of pairs $S$. In the other hand, choosing $p(y, z)$ first leaves the domain as $\{x{:}2\}$ and the range as $\{w{:}2\}$, which cannot resolve without duplicated facts.

The generator therefore accepts a candidate fact $p(s,o)$ for predicate $p$ only if the residual profile obtained by decrementing an unit of its subject in the domain and an unit of its object in the range (removing entities whose degree reaches zero) still satisfies that no subject has more frequency that the amount of unique objects and no object has more frequency that the amount of unique subjects.

$$\operatorname{solvableProfile}(P) = (\max e \in Dom  \le |Ran|) \land (\max  e\in Ran \le |Dom|)$$

The self-loops of a relation are placed before any other fact (Section 3.2), so the rest of the relation has none, and an entity in both $Dom$ and $Ran$ can't be its own partner. The test the generator uses counts this: $Dom(e) + [e \in Ran] \le |Ran|$ for every subject $e$, and $Ran(e) + [e \in Dom] \le |Dom|$ for every object $e$. Relations generated as undirected edges (Section 5.4) place their self-loops last and keep the test above.

This is the $k=1$ instance of Gale–Ryser applied to the residual profile after each tentative commitment. It is a **necessary** condition, cheap enough to check for every candidate, and it rules out the failures that occur in practice. It is not sufficient, so the method does not formally guarantee that every intermediate state is completable. Section 9 returns to this.

## 3. Initial facts (F0)

We build a set of facts such that, for every non-deducible relation $p_{ext}$, their degree maps are exactly as the degree maps for this relation type in the target graph. We do so while also creating enough joint structure for the rules to fire later.
### 3.1 The problem of independent sampling

If each predicate is filled independently to match its profile in the target graph, all relation frequencies would be closed, but the rules (the patterns arising in the interaction between relation-types) would be blind to it. Consider rule $$r_1: t(x,y) \Leftarrow p(z,x) \wedge q(z,y). $$ This rule is satisfied only by triples containing $p$ and $q$ that share entity $z$. Two independently generated relations would share such entities only by chance, and the support of $r_1$ in the generated graph could be far below its support in the target graph, or even 0. A rule as simple as (A hasMother B) <= (A hasFather X) and (X hasWife B), that has a confidence of 1 in the target graph may have a low confidence in the new graph, challenging the semantic meaning of the relations.

Our aim is to generate **correlated** facts across the relations that co-occur in a rule body, while respecting the individual profiles.

### 3.2 Creating F0

Committing a fact $(s,p,o)$ decrements the amount of triples to achieve the target frequency of relation $p$, and the remaining available entities in the range and domain counters of $P_p$. A relation $p$ is **closed** when the amount of triples needed to achieve its target frequency is 0. Initial-fact generation ends when all non-deducible relations are closed. To this end, three mechanisms add facts, apllied in a fixed priority.

**Self-loops.** Before the three mechanisms, each non-deducible relation gets as many self-loops $p(e,e)$ as the source has, its reflexivity in the profile, and no mechanism makes another one. A self-loop spends one unit of $e$ in $Dom_p$ and one in $Ran_p$. An entity that can only reach its counts with a self-loop, because it needs every member of $Ran_p$ or of $Dom_p$, itself included, gets one first. The others go to entities drawn uniformly from those in both $Dom_p$ and $Ran_p$, if the realizability test of Section 2.4.1 accepts them. Relations generated as undirected edges place theirs when they are sampled (Section 5.4).

#### 3.2.1 Mechanism 1: forced assignments

Some commitments are forced by the degree sequences and involve no choice. Let $s$ be a subject in the domain of relation $p$ with residual demand $k$ ($Dom_p=\{\dots, s:k, \dots\})$. If $k = |Ran_p| - [s \in Ran_p]$, then $s$ must be connected to *every* other member of $Ran_p$, i.e., it needs $k$ distinct partners and exactly $k$ distinct objects other than $s$ exist (its self-loops are already placed). The symmetric rule applies to objects. 

These facts can be directly deduced directly from each relation profile. Committing them may make other entities forced in turn, so the rule is applied repeatedly until nothing more is forced. This mechanism costs nothing in freedom, every fact it adds is one fact that any completion of the profile must contain. Relations generated as undirected edges (Section 5.4) are skipped, because a forced fact would be written in one direction only.

#### 3.2.2. Mechanism 2: rule-driven grounding

This mechanism assigns triples by generating groundings for a rule body so that the joins needed by the rule actually exist in the generated graph. This mechanism is only used when no triples can be assigned using mechanism 1. The rules are first orfered by priority and each rule body is used only once. Whenever a set of triples is assigned using this mechanism, we check mechanism 1 again using the updated profiles. If all rules have been used with this mechanism, it is never applied again and skipped.

Rules whose bodies share a non-deducible relation compete for the same degree budget. A rule with more non-deducible atoms (triple patterns containing a non-deducible relation) in its body is *more restrictive*: it imposes more joins, so fewer facts can satisfy it. If a less restrictive rule were used first, it could consume the entities that were the only way to satisfy a more restrictive one. Ties in the number of atoms are broken by support, so the rule with lower needed joints to achieve its target support is more restrictive. 

The generator induces an order from this: a rule body is used only after every more restrictive rule that shares non-deducible relations with it. Only rules with at least two non-deducible body atoms are processed this way, because with fewer there is no join to correlate, the assignments for the non-deducible relation in this body is simply left to the other two mechanisms.

We aim to have the minimum joints for a rule $r$ to achieve its target support. Base facts already committed may yield some head instantiations. Let $h_r$ be the number of distinct heads that the rule's non-deducible body already produces. The rule needs $$ m_r = \operatorname{support}(r, \mathcal{G}) - h_r $$ additional groundings. If $m_r \le 0$, the rule already has enough joints of non-deducible relations to achieve its support. This is an upper bound for the joints of non-deducible relations. 

>Distinct heads are bound to the projection of body variables, and using all available budget for a non-deducible relation may not achieve enough groundings for the target support. Current state of this method does not guarantee that all rules will meet its target support.

Let $\overrightarrow{B_{r}^{ext}}$ be the non-deducible body atoms of $r$ whose relations are not yet closed. Each variable $v\in \overrightarrow{B_{r}^{ext}}$ occupies subject or object positions of two or more atoms (because AMIE's closed rule restriction). Its *candidate pool* is the set of entities that appear in the residual profile of every position it occupies: $$ \mathcal{C}(v) = \bigcap_{\{R_p, D_p\} \ni v}$$. The capacity of $e \in \mathcal{C}(v)$ is the minimum of its residual degrees across those positions. A candidate grounding draws one entity per variable, with probability proportional to capacity (i.e., weighted by its available units), and a variable is drawn **once** even if it occurs in several atoms. This sharing forces the atoms to join.

A candidate is **accepted** if

1. its projection onto the head variables has not been produced already. Accepting duplicates would add no support, and body-only variables contribute nothing to it;
2. at least one of its facts is new, meaning it is neither in the graph already nor produced earlier in the same round; and
3. every new fact $(s,p,o)$ passes the realizability test of Section 2.4.1 against the current residual profile. If any fact fails, all tentative decrements made for this candidate are undone and it is rejected.

Accepted candidates commit their new facts and decrement the residual profiles. Sampling stops after $m_r$ acceptances, or when repeated batches of candidates yield none, because the residual profiles no longer allow it. In that case the shortfall is left to the third mechanism. Drawing by capacity favours entities that can still take many facts, so shared witnesses tend to be reused. Reusing witnesses conserves budget.

An atom on a relation generated as undirected edges (Section 5.4) commits both $(s,p,o)$ and $(o,p,s)$ and spends the budget of both. A candidate that binds the subject and object of any atom to the same entity is skipped, because self-loops are placed on their own: before the three mechanisms, or when a relation generated as undirected edges is sampled.

#### 3.2.3 Mechanism 3: random completion

When neither forced assignments nor rule-driven grounding can make progress, the remaining budget of a raltion's domain and range is spent without regard to rules. A still-open relation $p$ is chosen at random, and then a subject $s$ from its residual domain, with probability proportional to its residual demand $k$. Since nothing will pick $s$ up later, it is closed in this single step. A set of $k$ distinct objects other than $s$ is drawn without replacement, each with probability proportional to its residual degree in $Ran_p$, and the whole set is committed only if the residual profile after removing $s$ passes the realizability test of Section 2.4.1. Otherwise a new set is drawn, up to a fixed number of attempts. If fewer than $k$ objects remain, or no drawn set passes the test, generation fails. Weighting both draws by the remaining budget serves the largest demands while the most partners remain, the same idea as the constructive proof of Gale–Ryser, so the residual profile rarely reaches a state that the test rejects. This is deliberately a last resort. The first two mechanisms extract every fact that carries information about rule co-occurrence. Whatever remains has no such structure to preserve. Relations generated as undirected edges are not drawn here: Section 5.4 samples them once every rule has been used by Mechanism 2.

#### 3.2.4 Partial rules

A rule with PCA confidence 1 holds for every body grounding of the source whose functional variable has a value for the head relation, and the fixpoint of Section 4 reproduces that. A rule with lower PCA confidence does not: the source has body groundings without its head, and the fixpoint would give every one of them its head. A run that keeps such rules (a PCA threshold) therefore splits its rule set. A **complete** rule has PCA confidence 1 and is handled as in the rest of this section. A **partial** rule has PCA confidence below 1. The fixpoint never applies it, and it doesn't make its head relation deducible, so initial-fact generation generates that relation to its profile like any other non-deducible relation.

Mechanism 2 instead builds groundings of a partial rule $r$, body and head together, until its support in F0 reaches $$ t_r = \lfloor \operatorname{pca}(r) \cdot \operatorname{support}(r, \mathcal{G}) + 0.5 \rfloor ,$$ so support 300 at PCA confidence 0.5 gives 150. The atoms grounded are the whole body and the head, deducible relations included, since nothing derives them for $r$ later, and $h_r$ of Section 3.2.2 is the support of $r$ in F0. A partial rule whose head relation has no budget left gets no grounding. A body atom whose relation has no budget left is not generated: each candidate copies an existing initial facts fact of that atom, as the fill step does (Section 4.1), and the rule gets no grounding if there is none. A partial rule always has at least two atoms to ground, so a rule $p(x,y) \Rightarrow q(x,y)$ is grounded too, and a partial symmetric rule places $p(a,b)$ and $p(b,a)$ together instead of making its relation undirected (Section 5.4).

**Complete rules first.** Mechanism 2 grounds every complete rule before any partial rule, so the complete rules get the budget first, and each group is ordered as above on its own. When the partial rules start, the frequency the complete rules will derive in the fixpoint (the sum of their supports, per head relation) is held back from each deducible relation, so a partial rule only adds the facts of that relation that the complete rules leave. Only the frequency is held back, not each entity's count, and it is given back once initial-fact generation is done.

The facts that Mechanism 3 spends afterwards add more body groundings without a head, which brings the rule's confidence below 1, as in the source. The aim is that the synthetic graph keeps each rule's PCA confidence; Section 8 reports how close it comes, and the other targets that were tried.

**Complete rules and PCA confidence.** Under the partial completeness assumption, a body grounding whose functional variable has no value for the head relation is not a counterexample, so a rule with PCA confidence 1 has none in the source. Those rules are the complete ones: they are grounded, forward-chained and put through cycle removal (Section 5), and a rule is partial only if its PCA confidence is below 1. Without a PCA threshold the run keeps the rules with PCA confidence 1. The fixpoint gives each body grounding of such a rule its head, so the synthetic graph has standard confidence 1 for it, while the source may not. Until 2026-11-19 the generator also had a standard setting, where standard confidence 1 made a rule complete; commit `2c7dfd5d` has it.

### 3.3 Resulting F0

Whenever initial-fact generation terminates, for every non-deducible relation the base facts reproduce the source degree maps and number of self-loops exactly. The joint structure of rule bodies is approximated by the groundings of Mechanism 2. Deducible relations are still not generated in F0$^{[2]}$, apart from the facts of partial-rule groundings (Section 3.2.4).

>$^{[2]}$: We considered it is more convenient to generate any triple containing deducible relations if the profiles describe direct assignments, applying mechanism 1 until no more direct assignments can be done.

## 4. Graph completion: derivation by forward chaining

Given the base facts $F_0$ in F0, the synthetic graph is obtained by applying the rules until nothing new can be derived. Here $\mathcal{R}$ holds the complete rules only: partial rules placed their heads in F0 and are not applied (Section 3.2.4). The immediate-consequence operator of a rule set $\mathcal{R}$ is $$T_{\mathcal{R}}(F)=F \cup \bigl\{\sigma(H_r): \sigma(\overrightarrow{B_r})\in F, r \in \mathcal{R}  \bigr\}$$ and the iteration $$ F_{k+1} = T_{\mathcal{R}}(F_k) $$ is repeated until $F_{k+1} = F_k$. 

Because $T_{\mathcal{R}}$ is monotone and the entity and predicate sets are finite, the sequence stabilises at the least fixpoint $F^{*}$, the smallest set that contains $F_0$ and is closed under every rule. Once no rule adds a further fact, the graph is in a *stale state*. A symmetric relation that another rule also derives is sampled after this fixpoint, and the fixpoint is computed again (Section 5.4).

After the fixpoint, rule and relation **closure** is recorded. A rule is closed when its support in the graph has reached its target, and a predicate is closed when its frequency in the new graph is equal to its frequency on the target graph.
### 4.1 Filling open relations

The fixpoint closes every rule whose body facts exist, but it can leave relations short of their frequency: a deducible relation only receives the facts its rules derive, and the rules may explain only part of it. A last step adds the missing facts of every open relation, drawing subjects and objects weighted by how many more facts they need to match the source profile. Every new fact must respect two constraints:

- the support of every closed rule stays the same;
- the graph stays closed under every rule: no new body grounding is left without its head.

The support of an open rule may grow, up to its target; a rule that reaches its target is closed from then on.

**Which facts can change a support.** At the fixpoint every body grounding already has its head. A new fact $p(s,o)$ can therefore change a rule $r$ in two ways only. If $p$ is the head relation of $r$, it completes a grounding whose head values are $(s,o)$, if such a grounding exists. If $p$ occurs in the body of $r$, it can create new body groundings: one whose head is present raises the support, and one whose head is absent leaves the graph no longer closed under $r$. A new body grounding whose head values are already in the support changes nothing. So a random fact is safe for a relation that occurs in no rule body (up to the head check), and not otherwise.

**How an open rule can still grow.** Facts are only added for open relations, so an open rule's support can only grow through its open body relations:

- **A.** Every body relation of $r$ is closed: no new grounding can appear, so $r$ cannot reach its target. It is reported.
- **B1.** A body relation is open and the head relation is closed: the head facts are fixed, and the support grows only by completing a body for a head fact that $r$ does not explain yet.
- **B2.** A body relation and the head relation are open: new groundings are added together with their head fact, as the fixpoint would derive it.

The step first builds groundings for every open rule of case B1 or B2, in the order of the rule set, with the grounding sampler of Mechanism 2 (Section 3.2.2) on the remaining budgets of the open relations. A grounding is kept only if each of its facts respects the constraints above; the head fact a B2 grounding adds is allowed to be missing while its body facts are added. Then the budget left is spent at random, under the same constraints.

**Why these facts can only appear now.** initial-fact generation only creates non-deducible relations, and the fixpoint only adds heads. A relation that is the head of one rule and in the body of others therefore only receives the facts its own rules derive. On `french_royalty.csv` at PCA 0.9, rule 38 derives `father` (100 of 561 facts), and rules 10, 19 and 24 have `father` in their body, so they stay below their support after the fixpoint. This step is the first to create facts of a deducible relation outside the rules that derive it, and placing them where they complete the open rules' groundings raises those rules' support (rule 10: 100 to 271 of 431).

**Open issue.** A single fact can feed several open rules at once: `father(e,b)` creates a grounding of rule 10 that needs `parent(e,b)` and, if `e` has a mother `a`, groundings of rules 19 and 24 that need `spouse(a,b)` and `spouse(b,a)`. A grounding built for one rule only adds that rule's head, so the groundings it creates for the others are left without head and the fact is rejected. In the run above this stops `father` at 271 of 561 and leaves rules 19 and 24 at their support after the fixpoint. See Section 8.

## 5. Rule cycles

Generating F0 only closes non-deducible relations, and completing the graph can only derive a fact from facts that already exist. A predicate may then be impossible to derive from anything. If every rule that produces $p$ needs $p$ itself or needs a predicate that in turn depends on $p$, no first fact can ever appear. Two typical patterns are

- a self-loop, e.g.: $p(x,y) \Rightarrow p(y,x)$, or $p(x,y) \wedge q(y,z) \Rightarrow p(x,z)$;
- a mutual dependency, $p(x,y) \Rightarrow q(y, x)$ together with $q(x, y) \Rightarrow p(y, x)$, or longer cycles of the same kind.

Both are natural rule sets: symmetry and transitivity rules of this shape are among the most common. Yet they leave the involved predicates empty after graph completion, even though the source graph contained facts for them.

### 5.1 The relation graph

Define the **relation graph** $D_{\mathcal{R}}$ as a directed graph on relations, with an edge $q \to p$ whenever some rule has relation $q$ in its body and relation $p$ in its head (self-loops included). Each edge remembers the rules that produce it. The method removes rules from $\mathcal{R}$ before F0 is generated until $D_{\mathcal{R}}$ has no cycle. Only the complete rules (Section 3.2.4) are in $D_{\mathcal{R}}$: a partial rule places its own heads in F0, so it can't leave a cycle stale, and every partial rule is kept. A predicate that only the removed rules derived becomes non-deducible, so initial-fact generation generates it to its profile, and a predicate that some remaining rule derives stays deducible. The symmetric rules that Section 5.4 keeps are left out of $D_{\mathcal{R}}$.

A first version removed every rule with an edge on a cycle, that is, every rule whose body and head relations lie in the same strongly connected component of $D_{\mathcal{R}}$. Most mined rules are on some cycle, so few survive: on the French Royalty rules with PCA confidence at least 0.9, it keeps 1 rule of 23, and at 0.7 or below it keeps none. Section 5.2 removes only the rules needed to break every cycle.

### 5.2 Removing the fewest rules

**The problem.** Find the largest subset of $\mathcal{R}$ whose relation graph has no cycle. Removing a rule removes all of its edges at once, so this is a feedback arc set problem in which edges come in groups.

**Rules and orders.** A directed graph has no cycle exactly when its nodes can be put in an order where every edge goes forward (a topological order). For a rule set this means: the relations can be ordered so that, for every rule, all its body relations come before its head relation. So choosing the rules to keep is the same as choosing an order of the relations and keeping every rule that goes forward in it. A recursive rule, whose head relation also appears in its body, goes forward in no order and is always removed, except the symmetric rules that Section 5.4 keeps.

**Why not remove one rule at a time.** A natural idea is to repeat "remove the rule that looks worst" until no cycle is left, for example the rule with the lowest confidence, or the rule on the most cycles. Such a rule only looks at one rule, while a cycle is broken by removing every rule behind one of its edges. Take a single cycle $A \to B \to A$ made by three rules: $r_1$ gives the edge $A \to B$ (confidence 0.99), and $r_2$ and $r_3$ both give the edge $B \to A$ (confidences 0.98 and 0.90). Removing the lowest confidence first deletes $r_3$, which leaves the cycle, then $r_2$: two rules gone. All three rules lie on the one cycle, so "most cycles first" falls back to the same choice. Removing $r_1$ alone was enough. In the order view, $B$ before $A$ keeps $r_2$ and $r_3$, while $A$ before $B$ keeps only $r_1$. On the French Royalty rule files, the two heuristics keep far fewer rules than the exact selection:

| rules file, PCA threshold | rules | every cyclic rule removed | lowest confidence first | most cycles first | exact |
|---|---|---|---|---|---|
| `french_royalty.csv`, 0.9 | 23 | 1 | 4 | 9 | 12 |
| `french_royalty.csv`, 0.8 | 33 | 1 | 4 | 14 | 17 |
| `french_royalty.csv`, 0.5 | 60 | 0 | 4 | - | 33 |
| `fr.fixed.csv`, 0.9 | 23 | 1 | 6 | 9 | 13 |
| `fr.no-literals.fixed.csv`, 0.9 | 36 | 1 | 4 | 9 | 11 |

The two `fixed` rule files were mined from corrected copies of the graph that are no longer in the project.

**Finding the best order.** Build the order one relation at a time. When a relation $v$ is placed after the set $S$ of relations placed so far, the rules with head $v$ whose body relations are all in $S$ go forward, whatever comes later. How many rules the placed relations keep depends on which relations were placed, not on the order they were placed in. So it is enough to know the best result for each set $S$:
$$ \operatorname{best}(\emptyset) = 0, \qquad \operatorname{best}(S) = \max_{v \in S} \Bigl( \operatorname{best}(S \setminus \{v\}) + \operatorname{gain}(v, S \setminus \{v\}) \Bigr), $$
where $\operatorname{gain}(v, T)$ counts the non-recursive rules with head $v$ whose body relations all lie in $T$. The best order for all relations is read back from the choices that reached $\operatorname{best}$ of the full set, and the kept rules are the ones that go forward in it.

**One component at a time.** Two relations in different strongly connected components of $D_{\mathcal{R}}$ are never on a common cycle, so an edge between components never needs to go. The selection runs on each strongly connected component separately, with only that component's relations and only the rules whose head is in it; body relations outside the component are ignored.

**Which best.** Several orders can keep the same number of rules. The selection prefers the most rules, then the largest total support of the kept rules, and then a fixed order of the relations. The same rules therefore always give the same kept set.

**Example.** On `french_royalty.csv` with PCA confidence at least 0.9, the relations `father`, `parent`, `predecessor`, `spouse` and `successor` form one component with 22 rules on its cycles. The best order is `successor < father < parent < predecessor < spouse`, which keeps 11 of them, plus the rule outside the cycle: 12 of 23. The 11 removed rules are the recursive `spouse => spouse` (rule 11), the rules that derive `successor` from `predecessor`, `parent` or `father` (2, 6, 9, 16, 17), the rules that derive `parent` or `father` from a relation placed after them (8, 21, 22, 36), and the rule that derives `father` from `spouse` (14). `successor` becomes non-deducible, while `father`, `parent`, `predecessor` and `spouse` stay deducible.

**Cost.** A component with $n$ relations has $2^n$ sets, and each set tries $n$ relations, so the work grows as $2^n \cdot n$ times the number of rules. French Royalty has 7 relations in all. A component with more than 20 relations falls back to removing every rule on its cycles, as in Section 5.1.

### 5.3 An earlier approach: seeding stale cycles

A cycle is *stale* when none of its predicates has a fact. An earlier version kept the cyclic rules and, for each stale cycle, created body facts for one rule of the cycle (as Mechanism 2 of Section 3.2 does) so that forward chaining could start, then re-ran completion and repeated. It was memory intensive and has been removed from the method.

### 5.4 Symmetric relations as undirected edges

A rule $p(x,y) \Rightarrow p(y,x)$ is a self-loop of $D_{\mathcal{R}}$, so Section 5.2 would remove it, and F0 would then wire $p$ at random in one direction and lose its symmetry. The rule is kept when every entity has the same count in the domain and in the range of $p$, which an undirected graph needs. Every fact of $p$ is then written together with its reverse, and an entity's count is its degree in an undirected graph. A reflexive fact $p(e,e)$ is written once and spends one degree.

Facts that rules can produce come first, and only the budget they leave is sampled at random. The order depends on whether another kept rule derives $p$:

- **Only the symmetric rule derives $p$.** The relation is non-deducible. Mechanism 1 skips it, Mechanism 2 grounds the rule bodies that use it in both directions, and the remaining budget is sampled in F0 once every rule has been used.
- **Another kept rule also derives $p$.** The relation is deducible and initial-fact generation writes nothing for it. The fixpoint of Section 4 derives its facts and their reverses. The budget left, the source degrees minus the degrees in the graph, is then sampled, and the fixpoint is computed again so that rules with $p$ in their body see the new facts. An entity already at or past its source degree gets no new edge.

**Sampling.** As many entities as the source has reflexive facts get a self-loop. Each remaining degree becomes one stub, and the shuffled stubs are paired. A pair that is a self-loop or an edge already in the graph is repaired by swapping it with a random new edge $(c,d)$ into $(a,c)$ and $(b,d)$, if both are new and neither is a self-loop. Swaps only touch new edges, so the facts that rules produced stay in place. A pair that a fixed number of swaps can't repair is dropped with a warning, and so is an odd stub left over, so the relation can end short of its target.

Degrees are exact in the first case. In the second they are as close as the derivation allows, since the fixpoint is not capped by the profile. On the 8 relations with a symmetric rule of std confidence 1 in the benchmark datasets, the sampling reproduced every degree in all runs.

## 6. The complete procedure

Putting the steps together, the method is:

1. **Extract** the profiles $P$ of every relation-type and the targets $\operatorname{support}(r, \mathcal{G})$ of every rule from the rule set (the rules $\mathcal{R}$ mined from the target graph).
2. **Generate** base facts for every non-deducible relation $p_{ext}$ that exactly match $P_{p_{ext}}$ (Section 3), ordering rules from most to least restrictive, and combining forced assignments, rule-driven grounding and random completion, all guarded by the realizability test. Partial rules are grounded with their head, $\operatorname{pca}(r) \cdot \operatorname{support}(r)$ times (Section 3.2.4).
3. **Derive** the least fixpoint of the complete rules over the base facts (Section 4).
4. **Fill** the relations still short of their frequency, keeping the support of every closed rule and raising the support of open rules up to their target (Section 4.1).

Before step 2, the fewest complete rules that break every cycle of the relation graph are removed from $\mathcal{R}$ (Section 5.2), so step 3 can derive every deducible predicate. Symmetric rules whose relation has equal domain and range counts are kept, and those relations are written as undirected edges (Section 5.4).

Only step 1 reads statistical data from the source graph, apart from the remaining budgets of step 4, computed as the source profiles minus the counts in the synthetic graph. Everything after it uses the profiles, the supports and the rules.

## 7. Properties

- **Exactness for non-deducible predicates.** If F0 is generated correctly, each non-deducible predicate has exactly the frequency and degree distributions of the source. By the realizability test, no commitment made along the way knowingly makes this impossible.
- **Rule consistency.** The result is (WIP: as close as possible) closed under $\mathcal{R}$: every derivable fact is present. It has the same rules holding over it as the source, by construction.
- **Rule evidence.** The support of a rule in the result is driven towards its source support by Mechanism 2 (for rules with non-deducible bodies). It is not guaranteed to match exactly.
- **Independence from the source.** Only aggregate statistics and rules are used, so individual source facts are not reproduced by design (WIP: Except for those assigned directly looking at the profiles). Whether the aggregates themselves reveal anything about the source is a separate question that this method does not address (out of scope).
- **Randomness.** Some mechanism draws at random, so different runs give different graphs with the same profiles.

## 8. Limitations, future work and open questions

1. **Deducible frequencies are not controlled.** Relations are matched exactly for non-deducible predicates only. Deducible predicates are obtained by unrestricted derivation, so their frequencies and degree distributions can overshoot the source, or fall short if rules fail to fire. The fill step (Section 4.1) adds the facts of a relation that falls short, but cannot remove the facts of one that overshoots. Making derivation respect the profile of the head predicate, that is, restricting derived facts by the same residual-profile and realizability logic, is a natural extension.
2. **The realizability test is only necessary.** Only the first inequality of Gale–Ryser is checked. A full check needs the sorted degree sequences and is more expensive. Phase I can in principle reach a state that passes every local test but cannot be completed. Such states are detected only when the last mechanism finds too few objects.
3. **Competition between rules is handled greedily.** The restrictiveness order is a heuristic. It protects restrictive rules from being starved, but it does not solve the underlying assignment problem, which is a joint constraint satisfaction problem over all rules and profiles. CSP problem, my little brain cannot handle it.
4. **Support can exceed its target when a head pair has several witnesses.** Support counts distinct head pairs, but in the source a pair can be reached through several body groundings. In `family`, siblings share their parents, so `?a brother ?f & ?b mother ?f => ?a son ?b` reaches each `(a,b)` once per sibling `f` of `a`: 3,022 body groundings give 772 pairs. Mechanism 2 grounds a rule `support` times with one witness each, and Mechanism 3 keeps each relation's degrees but not who shares a parent, so the copy keeps the 3,022 groundings (3,021) and nearly every one reaches its own pair: support 3,010. Over the 18 complete rules of `family`, the source has 54,368 groundings for 25,805 pairs (2.11 per pair) and `family_ours_std1` 92,174 for 88,575 (1.04). Completion adds every head these bodies imply (item 1), and rules that join an inflated deducible relation inflate further: `son` (2,110 → 8,829) feeds rule 26 (`?a brother ?f & ?b son ?f => ?a uncle ?b`, 1,973 → 8,292), and `uncle` (3,486 → 13,219) feeds rule 33 (`?a brother ?f & ?b uncle ?f => ?a nephew ?b`, 1,772 → 30,281). The copy has 86,443 triples against the source's 28,358 (101,956 at `r100`); `nephew` alone goes from 3,633 to 42,891, and every rule keeps std confidence 1. No other benchmark dataset grows, but not by design: the complete rules that survive cycle removal elsewhere have one witness per pair (`french`, `WN18RR`, `WN18RRN_M` and `SGKG1` to `SGKG4`, 1.00), 16 datasets have no complete rule left, and the complete rules with several witnesses per pair that other datasets have (in `french`, `french1_1`, `frenchN_1`, `YAGO3-10_1_N` and, in the PCA setting, `YAGO3-10_N_1`) are all recursive, so cycle removal drops them. `family`'s are compositions of other relations, so they survive. The copy loses the structure that makes the witnesses converge: in `family_ours_std1` 18 of 5,733 sibling facts share a parent, against all 5,734 in the source. The PCA setting adds four such rules (`husband` and `wife` from `father` and `son` or `daughter`), and its `family` copies are 4.3 to 4.9 times the source. [`family-growth.md`](family-growth.md) gives every measurement, the rules that would keep the structure (siblings share their parents) and why the generator drops them, how kbgen avoids the growth with a cap, and the possible fixes; `scripts/rule_witnesses.py` and `scripts/family_structure.py` print the counts. Grounding a complete rule with the source's number of witnesses per pair, and keeping Mechanism 3 from making new body groundings of complete rules, would address it (see `BACKLOG.md`).
5. **Removed rules are not enforced.** Rules removed to break cycles (Section 5.2) do not hold on the output and their support is not controlled. Keeping a rule also makes its head relation deducible even when the rule derives few of its facts: on `french_royalty.csv` at PCA 0.9, rule 38 keeps `father` deducible, but its support covers 60 of the 561 `father` facts of the source, so initial-fact generation no longer generates `father` and completion derives only about 100 of them.
6. **Entity identity.** The generator reuses the entity set of the profiles. Whether entities should be replaced by fresh identifiers, and how that interacts with the degree maps, is not treated here.
7. The method assumes the source graph to be as correct and complete as possible. I added a "data preparation" step to "clean" the data, but it currently does not take into account schemas or semantic constraints. SHACL-SPARQL constraints (`<dataset>.shapes.ttl`) can be checked against a `.tsv` file with `cli/validate.py`, which reports the triples that violate them, but the generator does not use them yet to avoid invalid triples.
8. **Filling is limited by facts shared between open rules.** The fill step (Section 4.1) rejects a fact that leaves a grounding of another open rule without its head, so relations that feed several open rules stay open (`father` at 271 of 561 on `french_royalty.csv` at PCA 0.9). Adding the other rules' heads in the same grounding, or re-running the rule-driven part after the random part, are the next steps (see `BACKLOG.md`).
9. Current approach does not support literals, so the source graph must be processed beforehand. This could impact the semantics or the rules, so target rule set is always mined from the processed final version of the input graph.
10. **Partly symmetric relations.** Section 5.4 needs equal domain and range counts for every entity. A symmetric rule whose relation fails this is removed as in Section 5.2: 11 of the 19 symmetric rules at PCA 0.9 in the benchmark datasets, such as `_also_see` in WN18RR, with std confidence 0.68.
11. **Partial rules keep their PCA confidence only in part.** On `exp_datasets/french` (10,332 triples), PCA 0.9 keeps 53 rules, 12 complete (3 after cycle removal) and 41 partial, and PCA 0.1 keeps every mined rule, 228 of them partial. With the method of Section 3.2.4, two runs of each gave: at PCA 0.9, 26 of 41 partial rules within 0.05 of their source PCA confidence, a median absolute difference of 0.008 to 0.012, and 8 or 9 rules more than 0.3 off; at PCA 0.1, 60 to 63 of 228 within 0.05, a median difference of 0.22 to 0.24, and 82 to 85 more than 0.3 off. Rules with a source PCA confidence of 0.9 or more keep it (median difference 0.000); rules between 0.5 and 0.9 lose a median of 0.30 to 0.39. Every relation ends within 3 facts of its frequency. On 16 November 2026 two other choices were compared on the same runs (commit `4a743d9` has the code). Grounding partial rules in the same order as the complete rules, without holding back the derived budget, left `parent` at about 2,000 of 1,239 facts and `successor` at about 600 of 471, and gave 52 to 53 rules within 0.05 at PCA 0.1 (median 0.26). Targeting the PCA body instead (the full support, then body groundings without their head whose functional variable has a head fact, in the source's ratio of PCA body to support) put 68 rules within 0.05 at PCA 0.1 but had the largest median difference (0.30 to 0.32) and took about twice as long; with every rule, the PCA body of rules between 0.3 and 0.9 is already about 1.25 times the source's from the other rules' groundings and the random facts, which counter-examples can't remove, and only 34 of 228 rules still needed any. Fewer headless body groundings from the random step (Mechanism 3) are the next thing to try. The rule-level copies of all 24 datasets, measured against kbgen's and PyGraft's with `scripts/rule_confidence.py`, are in [`rule-confidence.md`](rule-confidence.md).
