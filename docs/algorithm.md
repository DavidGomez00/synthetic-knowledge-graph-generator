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

### 1.4 Deductive databases

Deductive databases ....

#### 1.4.1. Extensional database (EDB) and intensional database (IDB)

The extensional database (EDB) is a set of triples from which we can produce the rest of the graph applying the rules defined in the intensional database (IDB). In this case, the EDB is composed of triples $(s,p,o)$ and the IDB is a set of **Horn Rules**.

We say a relation-type (predicate) is **intensional** if it can be produced by a rule, i.e., a triple pattern containing this relation appears in the head of a rule from the IDB, or **extensional** otherwise. Extensional relations can never be produced by the rules in the IDB, so we must populate the EDB with all necessary triples containing these extensional reltaions. We call these triples "facts", and we use them to derive the triples containing intensional relations. This split organises the method in two steps: Generate the EDB and complete the graph using the IDB.

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

This is the $k=1$ instance of Gale–Ryser applied to the residual profile after each tentative commitment. It is a **necessary** condition, cheap enough to check for every candidate, and it rules out the failures that occur in practice. It is not sufficient, so the method does not formally guarantee that every intermediate state is completable. Section 9 returns to this.

## 3. Extensional database (EDB)

We build a set of facts such that, for every extensional relation $p_{ext}$, their degree maps are exactly as the degree maps for this relation type in the target graph. We do so while also creating enough joint structure for the rules to fire later.
### 3.1 The problem of independent sampling

If each predicate is filled independently to match its profile in the target graph, all relation frequencies would be closed, but the rules (the patterns arising in the interaction between relation-types) would be blind to it. Consider rule $$r_1: t(x,y) \Leftarrow p(z,x) \wedge q(z,y). $$ This rule is satisfied only by triples containing $p$ and $q$ that share entity $z$. Two independently generated relations would share such entities only by chance, and the support of $r_1$ in the generated graph could be far below its support in the target graph, or even 0. A rule as simple as (A hasMother B) <= (A hasFather X) and (X hasWife B), that has a confidence of 1 in the target graph may have a low confidence in the new graph, challenging the semantic meaning of the relations.

Our aim is to generate **correlated** facts across the relations that co-occur in a rule body, while respecting the individual profiles.

### 3.2 Creating the EDB

Committing a fact $(s,p,o)$ decrements the amount of triples to achieve the target frequency of relation $p$, and the remaining available entities in the range and domain counters of $P_p$. A relation $p$ is **closed** when the amount of triples needed to achieve its target frequency is 0. EDB generaation ends when all extensional relations are closed. To this end, three mechanisms add facts, apllied in a fixed priority.

#### 3.2.1 Mechanism 1: forced assignments

Some commitments are forced by the degree sequences and involve no choice. Let $s$ be a subject in the domain of relation $p$ with residual demand $k$ ($Dom_p=\{\dots, s:k, \dots\})$. If $k = |Ran_p|$, then $s$ must be connected to *every* member of $Ran_p$, i.e., it needs $k$ distinct partners and exactly $k$ distinct objects exist. The symmetric rule applies to objects. 

These facts can be directly deduced directly from each relation profile. Committing them may make other entities forced in turn, so the rule is applied repeatedly until nothing more is forced. This mechanism costs nothing in freedom, every fact it adds is one fact that any completion of the profile must contain.

#### 3.2.2. Mechanism 2: rule-driven grounding

This mechanism assigns triples by generating groundings for a rule body so that the joins needed by the rule actually exist in the generated graph. This mechanism is only used when no triples can be assigned using mechanism 1. The rules are first orfered by priority and each rule body is used only once. Whenever a set of triples is assigned using this mechanism, we check mechanism 1 again using the updated profiles. If all rules have been used with this mechanism, it is never applied again and skipped.

Rules whose bodies share an extensional relation compete for the same degree budget. A rule with more extensional atoms (triple patterns containing an extensional relation) in its body is *more restrictive*: it imposes more joins, so fewer facts can satisfy it. If a less restrictive rule were used first, it could consume the entities that were the only way to satisfy a more restrictive one. Ties in the number of atoms are broken by support, so the rule with lower needed joints to achieve its target support is more restrictive. 

The generator induces an order from this: a rule body is used only after every more restrictive rule that shares extensional relations with it. Only rules with at least two extensional body atoms are processed this way, because with fewer there is no join to correlate, the assignments for the extensional relation in this body is simply left to the other two mechanisms.

We aim to have the minimum joints for a rule $r$ to achieve its target support. Base facts already committed may yield some head instantiations. Let $h_r$ be the number of distinct heads that the rule's extensional body already produces. The rule needs $$ m_r = \operatorname{support}(r, \mathcal{G}) - h_r $$ additional groundings. If $m_r \le 0$, the rule already has enough joints of extensional relations to achieve its support. This is an upper bound for the joints of extensional relations. 

>Distinct heads are bound to the projection of body variables, and using all available budget for an extensional relation may not achieve enough groundings for the target support. Current state of this method does not guarantee that all rules will meet its target support.

Let $\overrightarrow{B_{r}^{ext}}$ be the extensional body atoms of $r$ whose relations are not yet closed. Each variable $v\in \overrightarrow{B_{r}^{ext}}$ occupies subject or object positions of two or more atoms (because AMIE's closed rule restriction). Its *candidate pool* is the set of entities that appear in the residual profile of every position it occupies: $$ \mathcal{C}(v) = \bigcap_{\{R_p, D_p\} \ni v}$$. The capacity of $e \in \mathcal{C}(v)$ is the minimum of its residual degrees across those positions. A candidate grounding draws one entity per variable, with probability proportional to capacity (i.e., weighted by its available units), and a variable is drawn **once** even if it occurs in several atoms. This sharing forces the atoms to join.

A candidate is **accepted** if

1. its projection onto the head variables has not been produced already. Accepting duplicates would add no support, and body-only variables contribute nothing to it;
2. at least one of its facts is new, meaning it is neither in the graph already nor produced earlier in the same round; and
3. every new fact $(s,p,o)$ passes the realizability test of Section 2.4.1 against the current residual profile. If any fact fails, all tentative decrements made for this candidate are undone and it is rejected.

Accepted candidates commit their new facts and decrement the residual profiles. Sampling stops after $m_r$ acceptances, or when repeated batches of candidates yield none, because the residual profiles no longer allow it. In that case the shortfall is left to the third mechanism. Drawing by capacity favours entities that can still take many facts, so shared witnesses tend to be reused. Reusing witnesses conserves budget.

#### 3.2.3 Mechanism 3: random completion

When neither forced assignments nor rule-driven grounding can make progress, the remaining budget of a raltion's domain and range is spent without regard to rules. A still-open relation $p$ is chosen at random, and then a subject $s$ from its residual domain, with residual demand $k$. Since nothing will pick $s$ up later, it is closed in this single step. A set of $k$ distinct objects is drawn at random$^{[1]}$, and the whole set is committed only if the residual profile after removing $s$ passes the realizability test of Section 3. Otherwise a new set is drawn. If fewer than $k$ objects remain, the profile is unsatisfiable at this point, and generation fails. This is deliberately a last resort. The first two mechanisms extract every fact that carries information about rule co-occurrence. Whatever remains has no such structure to preserve.

>$^{[1]}$: Maybe using a weighted sampling here depending on availability is better for performance.

### 3.3 Resulting EDB

Whenever the EDB generation terminates, for every extensional relation the base facts reproduce the source degree maps exactly. The joint structure of rule bodies is approximated by the groundings of Mechanism 2. Intensional relations are still not present in the EDB$^{[2]}$.

>$^{[2]}$: We considered it is more convenient to generate any triple containing intensional relations if the profiles describe direct assignments, applying mechanism 1 until no more direct assignments can be done.

## 4. Graph completion: derivation by forward chaining

Given the base facts $F_0$ in the EDB, the synthetic graph is obtained by applying the rules until nothing new can be derived. The immediate-consequence operator of a rule set $\mathcal{R}$ is $$T_{\mathcal{R}}(F)=F \cup \bigl\{\sigma(H_r): \sigma(\overrightarrow{B_r})\in F, r \in \mathcal{R}  \bigr\}$$ and the iteration $$ F_{k+1} = T_{\mathcal{R}}(F_k) $$ is repeated until $F_{k+1} = F_k$. 

Because $T_{\mathcal{R}}$ is monotone and the entity and predicate sets are finite, the sequence stabilises at the least fixpoint $F^{*}$, the smallest set that contains $F_0$ and is closed under every rule. Once no rule adds a further fact, the graph is in a *stale state*.

After the fixpoint, rule and relation **closure** is recorded. A rule is closed when its support in the graph has reached its target, and a predicate is closed when its frequency in the new graph is equal to its frequency on the target graph.
## 5. WIP: breaking rule cycles

Generating the EDB only closes extensional relations, and completing the graph can only derive a fact from facts that already exist. A predicate may then be impossible to derive from anything. If every rule that produces $p$ needs $p$ itself or needs a predicate that in turn depends on $p$, no first fact can ever appear. Two typical patterns are

- a self-loop, e.g.: $p(x,y) \Rightarrow p(y,x)$, or $p(x,y) \wedge q(y,z) \Rightarrow p(x,z)$;
- a mutual dependency, $p(x,y) \Rightarrow q(y, x)$ together with $q(x, y) \Rightarrow p(y, x)$, or longer cycles of the same kind.

Both are natural rule sets: symmetry and transitivity rules of this shape are among the most common. Yet they leave the involved predicates empty after graph completion, even though the source graph contained facts for them. The following section describe one approach to detect and "break" stale cycles, but it is memmory intensive and has been removed from the method.

### 5.1 Detecting stale cycles

Define the **relation graph** $D_{\mathcal{R}}$ as a directed graph on relations, with an edge $q \to p$ whenever some rule has relation $q$ in its body and relation $p$ in its head (self-loops included). Each edge remembers the rules that produce it. A directed cycle in $D_{\mathcal{R}}$ is **stale** with respect to a graph $F$ if none of its predicates has a single fact in $F$. A cyclic predicate that is already populated, through some other rule or through the base facts, is an ordinary recursive rule and needs no help.

### 5.2 Breaking a cycle

For a stale cycle $\gamma$, the generator chooses one rule $r$ among the rules whose edges lie on $\gamma$, and treats the *empty* atoms of its body as if they were extensional: it creates facts for them, in the same way as Mechanism 2 of Phase I. Precisely, split the body of $r$ into

- $S_r$, the atoms whose predicates are empty in $F$ (the atoms to seed), and
- $J_r$, the remaining atoms, whose predicates are populated and can be joined against existing facts.

If $J_r$ is non-empty, the seed must join with the facts already present. The generator first retrieves bindings of the variables of $J_r$ that also occur in $S_r$ or in the head. Each candidate grounding then copies one such binding, and only the remaining variables are drawn from the residual profiles as in Section 4.4. The head projection of a grounding includes the bound variables, so distinct heads are counted correctly. For $p(x,y) \wedge q(y,z) \Rightarrow p(x,z)$ with $q$ populated, the atom $p(x,y)$ is seeded, $y$ and $z$ are taken from existing $q$ facts, and $x$ is drawn from the profile of $p$.

The number of seed groundings is $$ \min\bigl(\operatorname{supp}(r),\ \min_{B \in S_r} \tilde f_{\mathrm{pred}(B)}\bigr). $$ The first term is the support target of $r$, since all of it is still missing when the body is empty. The second term reflects that each grounding consumes one unit of frequency budget per seeded atom.

**Choosing the rule.** A rule is *eligible* if it is not closed and every predicate it would seed is open, profiled and has budget left. Among eligible rules, the preference is

1. fewest atoms to seed, which creates the least new material;
2. non-recursive before recursive;
3. the more restrictive rule first, meaning the one with the larger body, because a restrictive rule's seeded body also satisfies the more general rules that share its head;
4. an arbitrary but fixed tie-break, for reproducibility.

The usual ordering between same-head rules, which makes recursive rules wait for non-recursive ones, is deliberately not applied here. Inside a stale cycle the non-recursive rules that a recursive rule would wait for can never fire, so waiting would deadlock exactly the rule that must go first.

### 5.3 Iteration

Cycles that share predicates are handled one at a time, because seeding one cycle populates predicates of the others. The overall procedure alternates

$$ \text{seed one stale cycle} \;\longrightarrow\; \text{forward chain to fixpoint} \;\longrightarrow\; \text{re-detect stale cycles} $$

until no stale cycle remains or no cycle can be seeded. A cycle is left unbroken, with a warning, if every candidate rule has a closed or exhausted predicate, or has no facts to join with. Seeds are added to the synthetic graph only. The base facts of Phase I are left unchanged.

## 6. The complete procedure

Putting the steps together, the method is:

1. **Extract** the profiles $P$ of every relation-type and the targets $\operatorname{support}(r, \mathcal{G})$ of every rule from the IDB (the set of rules $\mathcal{R}$ mined from the target graph).
2. **Generate** base facts for every extensional relation $p_{ext}$ that exactly match $P_{p_{ext}}$ (Section 3), ordering rules from most to least restrictive, and combining forced assignments, rule-driven grounding and random completion, all guarded by the realizability test.
3. **Derive** the least fixpoint of the rules over the base facts (Section 4).
4. (WIP)**Repair** stale cycles by seeding and re-deriving until none remain (Section 5).

Only step 1 reads statistical data from the source graph. Everything after it uses the profiles, the supports and the rules.

## 7. Properties

- **Exactness for extensional predicates.** If the EDB is generated correctly, each extensional predicate has exactly the frequency and degree distributions of the source. By the realizability test, no commitment made along the way knowingly makes this impossible.
- **Rule consistency.** The result is (WIP: as close as possible) closed under $\mathcal{R}$: every derivable fact is present. It has the same rules holding over it as the source, by construction.
- **Rule evidence.** The support of a rule in the result is driven towards its source support by Mechanism 2 (for rules with extensional bodies). It is not guaranteed to match exactly.
- **Independence from the source.** Only aggregate statistics and rules are used, so individual source facts are not reproduced by design (WIP: Except for those assigned directly looking at the profiles). Whether the aggregates themselves reveal anything about the source is a separate question that this method does not address (out of scope).
- **Randomness.** Some mechanism draws at random, so different runs give different graphs with the same profiles.

## 8. Limitations, future work and open questions

1. **Intensional frequencies are not controlled.** Relations are matched exactly for extensional predicates only. Intensional predicates are obtained by unrestricted derivation, so their frequencies and degree distributions can overshoot the source, or fall short if rules fail to fire. Making derivation respect the profile of the head predicate, that is, restricting derived facts by the same residual-profile and realizability logic, is a natural extension.
2. **The realizability test is only necessary.** Only the first inequality of Gale–Ryser is checked. A full check needs the sorted degree sequences and is more expensive. Phase I can in principle reach a state that passes every local test but cannot be completed. Such states are detected only when the last mechanism finds too few objects.
3. **Competition between rules is handled greedily.** The restrictiveness order is a heuristic. It protects restrictive rules from being starved, but it does not solve the underlying assignment problem, which is a joint constraint satisfaction problem over all rules and profiles. CSP problem, my little brain cannot handle it.
4. **Upper bounds on support.** Support is treated as a target to reach. Because base-fact generation can lower or raise the frequency of predicates that occur in several rule bodies, a rule's final support can be lower or equal to its target. Can it be greater? I think current implementation ensures enough extensional triples to complete support with intensional triples, but I would have to check if, e.g., 100 ext. conjunctions for a rule with support equal to 100 could generate 500 heads using different intensional triples.
5. **Cycle seeding is local and expensive.** A cycle is broken by seeding a single rule with the smallest possible seed. This guarantees that derivation starts. It does not aim to make the cyclic predicates' final profiles match the source beyond respecting their frequency budget. Also, the method is memmory expensive and has been rejected until new notice. This makes some relations to never appear in the synthetic graph, as they are not extensional but also cannot be derived.
6. **Entity identity.** The generator reuses the entity set of the profiles. Whether entities should be replaced by fresh identifiers, and how that interacts with the degree maps, is not treated here.
7. The method assumes the source graph to be as correct and complete as possible. I added a "data preparation" step to "clean" the data, but it currently does not take into account schemas or semantic constraints. In the future we could add SHACL constraints to avoid invalid triples.
8. Current approach does not support literals, so the source graph must be processed beforehand. This could impact the semantics or the rules, so target rule set is always mined from the processed final version of the input graph.
