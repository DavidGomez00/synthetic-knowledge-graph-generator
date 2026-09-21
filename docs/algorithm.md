# Synthetic Knowledge Graph Generation: Algorithm

*Working draft. Describes the ideas behind the method, independently of any implementation.*

## 1. Problem

Let $G$ be a source knowledge graph that cannot be shared or used directly (for privacy, size or licensing reasons). We want a graph $G'$ that is *structurally similar* to $G$ and *logically consistent with the same rules*, built using only

1. a small collection of **topological metrics** extracted once from $G$, and
2. a set $\mathcal{R}$ of **Horn rules** that hold (approximately) over $G$.

After the metrics are extracted, $G$ is never consulted again.

Structural similarity is made precise in two ways. For every relation, $G'$ should reproduce how many facts the relation has and how those facts are distributed over entities. For every rule, $G'$ should contain about as many instances of the rule as $G$ does. The algorithm below builds $G'$ in three phases: seeding it with base facts that satisfy the metrics and the rule bodies, deriving everything the rules imply, and repairing situations in which derivation cannot start.

## 2. Preliminaries

### 2.1 Knowledge graphs

A knowledge graph is a set of triples $G \subseteq \mathcal{E} \times \mathcal{P} \times \mathcal{E}$, where $\mathcal{E}$ is a set of entities and $\mathcal{P}$ a set of predicates (relations). For a predicate $p$ we write $G_p = \{(s,o) : (s,p,o) \in G\}$ for the set of pairs it connects. Each $G_p$ is a bipartite relation between subjects and objects. It contains no repeated pairs.

### 2.2 Horn rules

A Horn rule has the form
$$
r:\quad B_1(\mathbf{x}_1) \wedge \dots \wedge B_n(\mathbf{x}_n) \;\Rightarrow\; H(\mathbf{x}_h),
$$
where the body atoms $B_i$ and the head atom $H$ are triple patterns whose subject and object are variables or constants. For example,
$$
\text{parent}(a,b) \wedge \text{parent}(b,c) \Rightarrow \text{grandparent}(a,c).
$$
A **grounding** (or binding) of $r$ in a graph $G$ is an assignment $\sigma$ of entities to the variables of $r$ such that every body atom $\sigma(B_i)$ belongs to $G$. Following the usual convention in rule mining, different variables are bound to different entities.
The **support** of a rule in $G$ counts the distinct head instantiations that are both derivable and present:
$$
\operatorname{supp}_G(r) = \bigl|\{\, \sigma|_{\mathrm{vars}(H)} \;:\; \sigma \text{ grounds } r \text{ in } G,\ \sigma(H) \in G \,\}\bigr|.
$$
Variables that occur only in the body are not counted. A single witness for them is enough. The support measures how much evidence the rule has, and it is the quantity the synthetic graph must reproduce for each rule. Other rule statistics (head coverage, standard and PCA confidence) are used upstream to decide which mined rules to trust. Only the trusted rules enter $\mathcal{R}$.

### 2.3 Extensional and intensional predicates

A predicate is **intensional** if it is the head of at least one rule in $\mathcal{R}$, and **extensional** otherwise. Extensional predicates can only be established by asserting facts, since nothing derives them. Intensional predicates can be derived from other facts. This split organises the whole method: base facts are generated only for extensional predicates, and intensional predicates are obtained by inference.

### 2.4 Predicate profiles

The topological description of $G$ is a **profile** for each predicate $p$:
$$
\pi_p = \bigl(f_p,\ d^{\mathrm{dom}}_p,\ d^{\mathrm{ran}}_p\bigr),
$$
where

- $f_p = |G_p|$ is the number of facts using $p$;
- $d^{\mathrm{dom}}_p : \mathcal{E} \to \mathbb{N}$ maps each entity $e$ to the number of facts in which $e$ is the subject of $p$, that is $d^{\mathrm{dom}}_p(e) = |\{o : (e,o) \in G_p\}|$;
- $d^{\mathrm{ran}}_p : \mathcal{E} \to \mathbb{N}$ is defined symmetrically for objects.

Both degree maps sum to the frequency:
$$
\sum_{e} d^{\mathrm{dom}}_p(e) \;=\; \sum_{e} d^{\mathrm{ran}}_p(e) \;=\; f_p .
$$
The profile also records how many facts are reflexive ($s = o$). The profile is exactly the degree sequence of the bipartite graph $G_p$. It fixes how many facts a relation has and how many facts each entity takes part in, but not which pairs are actually connected. Choosing the pairs is the job of the generator, and the rules constrain that choice.

The profiles are extracted from a *completed* version of the source: the closure of $G$ under $\mathcal{R}$ (Section 5), so that facts implied by the rules but absent from $G$ are counted. The rule supports used as targets are likewise measured on this completed graph.

## 3. Realizability of a profile

Given degree maps $a$ (over subjects) and $b$ (over objects) with equal total $f$, a set of pairs $S$ with exactly those degrees, and no repeated pair, may or may not exist. The classical **Gale–Ryser** condition characterises when it does. Sort the subject degrees as $a_1 \ge a_2 \ge \dots \ge a_m$. A simple bipartite graph with these degrees exists if and only if the totals agree and
$$
\sum_{i=1}^{k} a_i \;\le\; \sum_{j} \min(b_j,\, k) \qquad \text{for all } k = 1,\dots,m .
$$
Its first instance ($k=1$) says that no subject may need more distinct partners than there are objects with non-zero degree, and the symmetric statement holds for objects.

This matters because the generator commits to facts one at a time. A commitment consumes one unit of degree from a subject and one from an object. If commitments are made carelessly, a later state can arise in which some entity still requires more partners than exist. For example, take $d^{\mathrm{dom}} = \{X{:}2,\ Y{:}1\}$ and $d^{\mathrm{ran}} = \{W{:}2,\ Z{:}1\}$. Choosing the fact $(Y, W)$ first leaves subject $X$ needing two distinct objects, but $W$ has one unit left and $Z$ has one unit left, so the state $\{X{:}2\}$, $\{W{:}1, Z{:}1\}$ is still realizable. Choosing $(Y, Z)$ instead leaves $\{X{:}2\}$ against $\{W{:}2\}$, which is not, because $X$ cannot connect to $W$ twice.

The generator therefore accepts a candidate fact $(s,o)$ for predicate $p$ only if the residual profile obtained by decrementing $d^{\mathrm{dom}}_p(s)$ and $d^{\mathrm{ran}}_p(o)$ (removing entities whose degree reaches zero) still satisfies

$$
\max_e d^{\mathrm{dom}}_p(e) \le |\operatorname{supp} d^{\mathrm{ran}}_p|
\qquad\text{and}\qquad
\max_e d^{\mathrm{ran}}_p(e) \le |\operatorname{supp} d^{\mathrm{dom}}_p| .
$$

This is the $k=1$ instance of Gale–Ryser applied to the residual profile after each tentative commitment. It is a **necessary** condition, cheap enough to check for every candidate, and it rules out the failures that occur in practice. It is not sufficient, so the method does not formally guarantee that every intermediate state is completable. Section 9 returns to this.

## 4. Phase I: generating the base facts

Let $\mathcal{P}_{\mathrm{ext}}$ be the extensional predicates. Predicates that occur in no rule at all are treated as extensional. Phase I builds a set of facts $F_0$ such that, for every $p \in \mathcal{P}_{\mathrm{ext}}$, the set $F_{0,p}$ has exactly the degree maps of $\pi_p$. It does so while also creating enough joint structure for the rules to fire later.

### 4.1 Why independent sampling is not enough

If each predicate were filled independently to match its profile, the profiles would be satisfied but the rules would be blind to it. Consider
$$
t(x,y) \Leftarrow p(z,x) \wedge q(z,y).
$$
The rule fires only for entities $z$ that appear as the subject of both $p$ and $q$. Two independently generated relations would share such entities only by chance, and the rule's support in $G'$ could be far below its support in $G$. The generator must therefore create **correlated** facts across the predicates that co-occur in a rule body, while still never violating the individual profiles.

### 4.2 Working state

Throughout Phase I, each extensional predicate carries a *residual profile* $\tilde\pi_p = (\tilde f_p, \tilde d^{\mathrm{dom}}_p, \tilde d^{\mathrm{ran}}_p)$, initially equal to $\pi_p$. Committing a fact $(s,p,o)$ decrements $\tilde f_p$, $\tilde d^{\mathrm{dom}}_p(s)$ and $\tilde d^{\mathrm{ran}}_p(o)$. A predicate is **closed** when $\tilde f_p = 0$. Phase I ends when all extensional predicates are closed. Three mechanisms add facts, tried in a fixed priority.

### 4.3 Mechanism 1: forced assignments

Some commitments are forced by the degree sequences and involve no choice. Let $s$ be a subject of $p$ with residual demand $\tilde d^{\mathrm{dom}}_p(s) = k$, and let
$$
O_s = \{\,o \in \operatorname{supp}\tilde d^{\mathrm{ran}}_p : o \neq s\,\}
$$
be the objects it could still connect to. If $k = |O_s|$, then $s$ must be connected to *every* member of $O_s$: it needs $k$ distinct partners and exactly $k$ exist. All these facts can be committed at once. The symmetric rule applies to objects. Committing them may make other entities forced in turn, so the rule is applied repeatedly until nothing more is forced. This mechanism costs nothing in freedom. Every fact it adds is one that any completion of the profile would have to contain.

### 4.4 Mechanism 2: rule-driven grounding

The second mechanism builds groundings of rule bodies so that the joins the rules need actually exist.

**Order of rules.** Rules whose bodies share an extensional predicate compete for the same degree budget. A rule with more extensional atoms in its body is *more restrictive*: it imposes more joins, so fewer facts can satisfy it. If a less restrictive rule were served first, it could consume the entities that were the only way to satisfy a more restrictive one. Ties in the number of atoms are broken by support, with the rule of lower support being more restrictive. The generator induces a dependency order from this: a rule is processed only after every more restrictive rule that shares an extensional predicate with it. Only rules with at least two extensional body atoms are processed this way, because with fewer there is no join to correlate. The facts of a rule with a single extensional atom are simply left to the other two mechanisms.

**How many groundings are needed.** For a rule $r$ the target is its support $\operatorname{supp}(r)$ in the completed source. Base facts already committed may yield some head instantiations. Let $h_r$ be the number of distinct head instantiations that the rule's extensional body already produces. The generator needs
$$
m_r = \operatorname{supp}(r) - h_r
$$
additional groundings. If $m_r \le 0$, the rule needs nothing more.

**Sampling.** Let $A_r$ be the extensional body atoms of $r$ whose predicates are not yet closed. Each variable $v$ of $A_r$ occupies subject or object positions of one or more atoms. Its *candidate pool* is the set of entities that appear in the residual profile of every position it occupies:
$$
\mathcal{C}(v) = \bigcap_{(p,\,\mathrm{pos}) \ni v} \operatorname{supp} \tilde d^{\mathrm{pos}}_p ,
$$
where $\mathrm{pos} \in \{\mathrm{dom}, \mathrm{ran}\}$. The capacity of $e \in \mathcal{C}(v)$ is the minimum of its residual degrees across those positions. A candidate grounding draws one entity per variable, with probability proportional to capacity, and a variable is drawn **once** even if it occurs in several atoms. This sharing is what forces the atoms to join.

A candidate is **accepted** if

1. its projection onto the head variables has not been produced already. Accepting duplicates would add no support, and body-only variables contribute nothing to it;
2. at least one of its facts is new, meaning it is neither in the graph already nor produced earlier in the same round; and
3. every new fact $(s,p,o)$ passes the realizability test of Section 3 against the current residual profile. If any fact fails, all tentative decrements made for this candidate are undone and it is rejected.

Accepted candidates commit their new facts and decrement the residual profiles. Sampling stops after $m_r$ acceptances, or when repeated batches of candidates yield none, because the residual profiles no longer allow it. In that case the shortfall is left to the third mechanism. Drawing by capacity favours entities that can still take many facts, so shared witnesses tend to be reused. Reusing witnesses conserves budget.

### 4.5 Mechanism 3: random completion

When neither forced assignments nor rule-driven grounding can make progress, the remaining budget of a predicate is spent without regard to rules. A still-open predicate $p$ is chosen at random, and then a subject $s$ from its residual domain, with residual demand $k = \tilde d^{\mathrm{dom}}_p(s)$. Since nothing will pick $s$ up later, it is closed in this single step. A set of $k$ distinct objects is drawn at random, and the whole set is committed only if the residual profile after removing $s$ passes the realizability test of Section 3. Otherwise a new set is drawn. If fewer than $k$ objects remain, the profile is unsatisfiable at this point, and generation fails.

This is deliberately a last resort. The first two mechanisms extract every fact that carries information about rule co-occurrence. Whatever remains has no such structure to preserve.

### 4.6 Result of Phase I

If Phase I terminates, then for every extensional predicate the base facts reproduce the source degree maps exactly. The joint structure of rule bodies is approximated by the groundings of Mechanism 2. Intensional predicates are still empty.

## 5. Phase II: derivation by forward chaining

Given the base facts $F_0$, the synthetic graph is obtained by applying the rules until nothing new can be derived. The immediate-consequence operator of a rule set is
$$
T_{\mathcal{R}}(F) \;=\; F \;\cup\; \bigl\{\, \sigma(H_r) \;:\; r \in \mathcal{R},\ \sigma \text{ grounds } r \text{ in } F \,\bigr\},
$$
and the iteration
$$
F_{k+1} = T_{\mathcal{R}}(F_k)
$$
is repeated until $F_{k+1} = F_k$. Because $T_{\mathcal{R}}$ is monotone and the entity and predicate sets are finite, the sequence stabilises at the least fixpoint $F^{*}$, the smallest set that contains $F_0$ and is closed under every rule. This is the standard semantics of Datalog. The same operator, applied to $G$ itself, gives the completed source graph of Section 2.4. Once no rule adds a further fact, the graph is in a *stale state*.

After the fixpoint, rule and predicate **closure** is recorded. A rule is closed when its support in the graph has reached its target, and a predicate is closed when its frequency has reached its profile frequency $f_p$.

## 6. Phase III: breaking rule cycles

### 6.1 The problem

Phase I generates only extensional predicates, and Phase II can only derive a fact from facts that already exist. A predicate may then be impossible to derive from anything. If every rule that produces $p$ needs $p$ itself or needs a predicate that in turn depends on $p$, no first fact can ever appear. Two typical patterns are

- a self-loop, such as $\text{spouse}(a,b) \Rightarrow \text{spouse}(b,a)$, or $p(x,y) \wedge q(y,z) \Rightarrow p(x,z)$;
- a mutual dependency, $A \Rightarrow B$ together with $B \Rightarrow A$, or longer cycles of the same kind.

Both are natural rule sets: symmetry and transitivity rules of this shape are among the most common. Yet they leave the involved predicates empty after Phase II, even though the source graph contained facts for them.

### 6.2 Detecting stale cycles

Define the **relation graph** $D_{\mathcal{R}}$ as a directed graph on predicates, with an edge $q \to p$ whenever some rule has $q$ in its body and $p$ as its head (self-loops included). Each edge remembers the rules that produce it. A directed cycle in $D_{\mathcal{R}}$ is **stale** with respect to a graph $F$ if none of its predicates has a single fact in $F$. A cyclic predicate that is already populated, through some other rule or through the base facts, is an ordinary recursive rule and needs no help.

### 6.3 Breaking a cycle

For a stale cycle $\gamma$, the generator chooses one rule $r$ among the rules whose edges lie on $\gamma$, and treats the *empty* atoms of its body as if they were extensional: it creates facts for them, in the same way as Mechanism 2 of Phase I. Precisely, split the body of $r$ into

- $S_r$, the atoms whose predicates are empty in $F$ (the atoms to seed), and
- $J_r$, the remaining atoms, whose predicates are populated and can be joined against existing facts.

If $J_r$ is non-empty, the seed must join with the facts already present. The generator first retrieves bindings of the variables of $J_r$ that also occur in $S_r$ or in the head. Each candidate grounding then copies one such binding, and only the remaining variables are drawn from the residual profiles as in Section 4.4. The head projection of a grounding includes the bound variables, so distinct heads are counted correctly. For $p(x,y) \wedge q(y,z) \Rightarrow p(x,z)$ with $q$ populated, the atom $p(x,y)$ is seeded, $y$ and $z$ are taken from existing $q$ facts, and $x$ is drawn from the profile of $p$.

The number of seed groundings is
$$
\min\bigl(\operatorname{supp}(r),\ \min_{B \in S_r} \tilde f_{\mathrm{pred}(B)}\bigr).
$$
The first term is the support target of $r$, since all of it is still missing when the body is empty. The second term reflects that each grounding consumes one unit of frequency budget per seeded atom.

**Choosing the rule.** A rule is *eligible* if it is not closed and every predicate it would seed is open, profiled and has budget left. Among eligible rules, the preference is

1. fewest atoms to seed, which creates the least new material;
2. non-recursive before recursive;
3. the more restrictive rule first, meaning the one with the larger body, because a restrictive rule's seeded body also satisfies the more general rules that share its head;
4. an arbitrary but fixed tie-break, for reproducibility.

The usual ordering between same-head rules, which makes recursive rules wait for non-recursive ones, is deliberately not applied here. Inside a stale cycle the non-recursive rules that a recursive rule would wait for can never fire, so waiting would deadlock exactly the rule that must go first.

### 6.4 Iteration

Cycles that share predicates are handled one at a time, because seeding one cycle populates predicates of the others. The overall procedure alternates

$$
\text{seed one stale cycle} \;\longrightarrow\; \text{forward chain to fixpoint} \;\longrightarrow\; \text{re-detect stale cycles}
$$

until no stale cycle remains or no cycle can be seeded. A cycle is left unbroken, with a warning, if every candidate rule has a closed or exhausted predicate, or has no facts to join with. Seeds are added to the synthetic graph only. The base facts of Phase I are left unchanged.

## 7. The complete procedure

Putting the phases together, the method is:

1. **Extract** the profiles $\pi_p$ of every predicate and the targets $\operatorname{supp}(r)$ of every rule from the completed source graph, and keep the rule set $\mathcal{R}$.
2. **Generate** base facts for every extensional predicate that exactly match $\pi_p$ (Phase I), ordering rules from most to least restrictive, and combining forced assignments, rule-driven grounding and random completion, all guarded by the realizability test.
3. **Derive** the least fixpoint of the rules over the base facts (Phase II).
4. **Repair** stale cycles by seeding and re-deriving until none remain (Phase III).

Only step 1 touches the source graph. Everything after it uses the profiles, the supports and the rules.

## 8. Properties

- **Exactness for extensional predicates.** If Phase I terminates, each extensional predicate has exactly the frequency and degree distributions of the source. By the realizability test, no commitment made along the way knowingly makes this impossible.
- **Rule consistency.** The result is closed under $\mathcal{R}$: every derivable fact is present. It has the same rules holding over it as the source, by construction.
- **Rule evidence.** The support of a rule in the result is driven towards its source support by Mechanism 2 (for rules with extensional bodies) and by Phase III (for rules that only cycles can reach). It is not guaranteed to match exactly.
- **Independence from the source.** Only aggregate statistics and rules are used, so individual source facts are not reproduced by design. Whether the aggregates themselves reveal anything about the source is a separate question that this method does not address.
- **Randomness.** Every mechanism draws at random, so different runs give different graphs with the same profiles.

## 9. Limitations and open questions

1. **Intensional frequencies are not controlled.** Profiles are matched exactly for extensional predicates only. Intensional predicates are obtained by unrestricted derivation, so their frequencies and degree distributions can overshoot the source, or fall short if rules fail to fire. Making derivation respect the profile of the head predicate, that is, restricting derived facts by the same residual-profile and realizability logic, is a natural extension.
2. **The realizability test is only necessary.** Only the first inequality of Gale–Ryser is checked. A full check needs the sorted degree sequences and is more expensive. Phase I can in principle reach a state that passes every local test but cannot be completed. Such states are detected only when the last mechanism finds too few objects.
3. **Competition between rules is handled greedily.** The restrictiveness order is a heuristic. It protects restrictive rules from being starved, but it does not solve the underlying assignment problem, which is a joint constraint satisfaction problem over all rules and profiles.
4. **Upper bounds on support.** Support is treated as a target to reach. Because base-fact generation can lower or raise the frequency of predicates that occur in several rule bodies, a rule's final support can be lower than its target. Whether support should also be an upper bound is undecided.
5. **Cycle seeding is local.** A cycle is broken by seeding a single rule with the smallest possible seed. This guarantees that derivation starts. It does not aim to make the cyclic predicates' final profiles match the source beyond respecting their frequency budget.
6. **Entity identity.** The generator reuses the entity set of the profiles. Whether entities should be replaced by fresh identifiers, and how that interacts with the degree maps, is not treated here.
