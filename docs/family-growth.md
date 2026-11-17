# Why the family copies grow

This generator's copies of `family` are 3.0 to 4.9 times the size of the source, while the copies of the other 23 benchmark datasets keep their source's size. This page explains why, with the measurements of 17 November 2026. It is a limitation of the method, not a bug in the code, and nothing here has been fixed yet (see [Possible fixes](#possible-fixes)).

In short: the generator keeps each relation's frequency and degrees and each rule's support, but draws the extensional relations independently of each other, so the copy loses how they line up in the source (a child's father and mother are a couple, siblings share their parents). Completion forward-chains the complete rules without a cap. A complete rule whose body reaches the same head pair through several groundings in the source then reaches a new pair with nearly every grounding in the copy, and the relations it inflates feed other complete rules. Only `family` has complete rules that do this and survive cycle removal.

## The size of the copies

`family` has 28,358 triples. The copies of each rule level ([`rule-levels.md`](rule-levels.md)), in the std setting (`exp_datasets/`), in the PCA setting (`exp_datasets_pca/`, see "Complete and partial rules" in [`concepts.md`](concepts.md)) and by kbgen given the same rules (`exp_datasets/family_kbgen_<level>/`):

| Level | This generator, std setting | × source | This generator, PCA setting | × source | kbgen |
|---|---|---|---|---|---|
| `std1` / `pca1` | 86,443 | 3.05 | 122,086 | 4.31 | 28,262 |
| `r25` | 91,369 | 3.22 | 126,605 | 4.46 | 28,262 |
| `r50` | 98,362 | 3.47 | 131,902 | 4.65 | 28,264 |
| `r75` | 96,780 | 3.41 | 134,916 | 4.76 | 28,262 |
| `r100` | 101,956 | 3.60 | 137,589 | 4.85 | 28,262 |

The extensional relations keep their source frequency; the relations that complete rules derive do not:

| Relation | Source | Ours `std1` | Ours `pca1` | Ours `r100` | Ours PCA `r100` | kbgen `r100` |
|---|---|---|---|---|---|---|
| aunt | 2,990 | 1,976 | 1,976 | 2,981 | 2,989 | 3,275 |
| brother | 3,076 | 3,075 | 3,075 | 3,076 | 3,076 | 3,362 |
| daughter | 1,589 | 1,589 | 1,589 | 1,589 | 1,589 | 1,748 |
| father | 2,010 | 2,010 | 2,010 | 2,010 | 2,010 | 779 |
| husband | 1,138 | 1,703 | 11,945 | 1,950 | 11,874 | 1,252 |
| mother | 1,714 | 1,714 | 1,714 | 1,714 | 1,714 | 807 |
| nephew | 3,633 | 42,891 | 55,011 | 53,831 | 66,479 | 3,839 |
| niece | 2,815 | 5,075 | 8,217 | 8,180 | 11,306 | 3,097 |
| sister | 2,659 | 2,659 | 2,658 | 2,657 | 2,659 | 2,899 |
| son | 2,110 | 8,829 | 8,844 | 8,830 | 8,813 | 2,321 |
| uncle | 3,486 | 13,219 | 13,102 | 13,188 | 13,206 | 3,631 |
| wife | 1,138 | 1,703 | 11,945 | 1,950 | 11,874 | 1,252 |
| total | 28,358 | 86,443 | 122,086 | 101,956 | 137,589 | 28,262 |

`nephew` ends at 12 to 18 times its source frequency. The copies also have self-loops in derived relations (58 to 182 in all), which the source has none of; `scripts/check_outputs.py` lists them per relation.

## What the generator keeps

Section 3 of [`algorithm.md`](algorithm.md) builds the EDB to match each extensional relation's frequency and degree maps (`PredicateProfile`), and grounds each complete rule's extensional body until the rule can reach its support. Random triples (Mechanism 3) then fill each relation to its frequency, drawing subjects and objects from that relation's own degree map. Nothing ties one relation's random triples to another's, except the groundings that a rule builds. Completion (section 4) then adds every head that the complete rules derive, with no frequency cap.

So the copy has the source's number of fathers per child and children per father, but which father goes with which mother, and which siblings share a parent, is left to chance.

## Several groundings per head pair

A rule's support counts distinct head pairs, but in the source a head pair can be reached through several body groundings. In `family`, siblings share their parents, so rule 12, `?a brother ?f & ?b mother ?f => ?a son ?b`, reaches the pair `(a, b)` once through each sibling `f` of `a`: 3,022 body groundings give 772 pairs, 3.91 per pair.

The copy keeps the 3,022 groundings (Mechanism 2 builds them, and the random step fills `brother` and `mother` to their frequencies), but since `a`'s siblings no longer share `a`'s mother, nearly every grounding reaches a pair of its own: 3,009 pairs. Completion adds a `son` fact for each, so this rule alone gives `son` 3,009 heads where the source has 772. The growth factor of a rule is about its groundings per pair in the source.

The complete rules of `family_ours_r100` (`python scripts/rule_witnesses.py --suffix _ours_r100 --datasets family`):

| Rule | Body ⇒ head | Source groundings / pairs | Per pair | Copy groundings / pairs |
|---|---|---|---|---|
| 2 | `?b husband ?a => ?a wife ?b` | 1,138 / 1,138 | 1.00 | 1,950 / 1,950 |
| 3 | `?a father ?f & ?b mother ?f => ?a husband ?b` | 1,706 / 691 | 2.47 | 1,706 / 1,705 |
| 4 | `?b father ?f & ?a mother ?f => ?a wife ?b` | 1,706 / 691 | 2.47 | 1,706 / 1,705 |
| 12 | `?a brother ?f & ?b mother ?f => ?a son ?b` | 3,022 / 772 | 3.91 | 3,022 / 3,009 |
| 13 | `?a brother ?f & ?b father ?f => ?a son ?b` | 3,074 / 809 | 3.80 | 3,074 / 3,066 |
| 14 | `?a brother ?e & ?e daughter ?b => ?a son ?b` | 2,812 / 1,174 | 2.40 | 2,812 / 2,803 |
| 20 | `?b aunt ?f & ?a sister ?f => ?a niece ?b` | 6,156 / 1,226 | 5.02 | 5,792 / 5,666 |
| 21 | `?f father ?b & ?a sister ?f => ?a aunt ?b` | 1,976 / 1,976 | 1.00 | 1,971 / 1,971 |
| 22 | `?b brother ?f & ?a daughter ?f => ?a niece ?b` | 1,498 / 1,498 | 1.00 | 1,498 / 1,497 |
| 23 | `?a daughter ?f & ?f sister ?b => ?a niece ?b` | 1,026 / 1,026 | 1.00 | 1,026 / 1,025 |
| 25 | `?a brother ?f & ?b daughter ?f => ?a uncle ?b` | 1,498 / 1,498 | 1.00 | 1,498 / 1,497 |
| 26 | `?a brother ?f & ?b son ?f => ?a uncle ?b` | 1,973 / 1,973 | 1.00 | 8,369 / 8,265 |
| 29 | `?a brother ?e & ?e mother ?b => ?a uncle ?b` | 1,318 / 1,318 | 1.00 | 1,318 / 1,318 |
| 32 | `?a brother ?e & ?e father ?b => ?a uncle ?b` | 2,167 / 2,167 | 1.00 | 2,167 / 2,167 |
| 33 | `?a brother ?f & ?b uncle ?f => ?a nephew ?b` | 7,888 / 1,772 | 4.45 | 33,222 / 30,413 |
| 34 | `?b aunt ?f & ?a brother ?f => ?a nephew ?b` | 6,572 / 1,485 | 4.43 | 5,877 / 5,783 |
| 35 | `?a brother ?f & ?f niece ?b => ?a nephew ?b` | 6,865 / 2,618 | 2.62 | 23,121 / 22,053 |
| 37 | `?b brother ?f & ?a son ?f => ?a nephew ?b` | 1,973 / 1,973 | 1.00 | 8,369 / 8,265 |
| all 18 | | 54,368 / 25,805 | 2.11 | 108,498 / 104,158 (1.04) |

## The copy loses the family structure

`scripts/family_structure.py` measures how the parent and sibling relations line up:

| Graph | Couples | Children per couple | Sibling facts sharing a parent | `son` facts pointing to a parent |
|---|---|---|---|---|
| source | 691 | 2.47 | 5,734 of 5,734 | 2,110 of 2,110 |
| ours `std1` | 1,703 | 1.00 | 18 of 5,733 | 14 of 8,829 |
| ours `pca1` | 1,705 | 1.00 | 19 of 5,732 | 15 of 8,844 |
| ours `r100` | 1,705 | 1.00 | 23 of 5,732 | 21 of 8,830 |
| ours PCA `r100` | 1,702 | 1.00 | 26 of 5,734 | 27 of 8,813 |
| kbgen `std1` | 1,284 | 1.00 | 6 of 3,099 | 1 of 2,321 |
| kbgen `r25` | 622 | 1.17 | 1,417 of 3,707 | 297 of 2,321 |
| kbgen `r50` | 350 | 1.45 | 1,840 of 2,891 | 865 of 2,321 |
| kbgen `r100` | 423 | 1.44 | 1,650 of 3,011 | 804 of 2,321 |

A couple is a father and a mother of the same child; sibling facts are the `brother` and `sister` facts whose two people both have a parent. In the source every sibling pair shares a parent and every `son` fact `(c, p)` has `p` as a parent of `c`. In this generator's copies almost none do: every child has its own couple, and the `son` facts that completion derives point to the mother or father of a sibling who, in the copy, is not the child's parent.

## Derived relations feed other rules

The heads of some complete rules are in the bodies of others, so the inflation compounds. Rules 12 to 14 inflate `son` (2,110 → 8,829). Rule 26, `?a brother ?f & ?b son ?f => ?a uncle ?b`, has one grounding per pair in the source, but its body reads the inflated `son`, so it derives 8,265 `uncle` pairs where the source has 1,973. Rule 33, `?a brother ?f & ?b uncle ?f => ?a nephew ?b`, then reads the inflated `uncle` and has 4.45 groundings per pair in the source: 30,413 `nephew` pairs against 1,772. Rule 35 does the same through `niece`. Completion repeats its passes until nothing is added, so every step of the chain is applied.

## The rules alone don't make the graph grow

Forward-chaining the same rules over the source itself, where the structure is intact, adds little (`python scripts/family_structure.py --chain <used rules> exp_datasets/family/family.unified.tsv`):

- the 18 complete rules of the std setting (`family_ours_std1_used.csv`) add nothing: the source is closed under them;
- the 26 complete rules of the PCA setting (`family_ours_pca1_used.csv`) add 1,831 triples (28,358 → 30,189): `husband` and `wife` 1,138 → 2,044 each, `nephew` +16, `niece` +3.

The copies add 58,085 (std `std1`) to 109,231 (PCA `r100`) triples, so nearly all of the growth comes from the lost structure, not from the rules.

## Why only family

A copy grows this way only if all of these hold:

1. A rule is complete (std confidence 1, or PCA confidence 1 in the PCA setting). The heads of partial rules are placed by the EDB within each relation's frequency budget, so partial rules can't overshoot.
2. It survives cycle removal (`core/cycles.remove_minimal_cyclic_rules`, section 5.2 of [`algorithm.md`](algorithm.md)), which always removes recursive rules (a body atom with the head's relation).
3. Its body reaches a head pair through more than one grounding in the source.
4. The relations it inflates feed other complete rules, which multiplies the growth (not required, but it is what takes `family` from 2 to 3.6 times).

16 of the 24 datasets have no complete rule left after cycle removal, at any level and in either setting: Family1_1, Family1_N, FamilyM_N, FamilyN_1, all five YAGO3-10 datasets, WN18RR1_1, WN18RR1_N, WN18RRN_1, french1_1, french1_N, frenchN_1 and frenchN_M. Their rules are all partial, so nothing is left to unbounded derivation. The seven others with complete rules (`french`, `WN18RR`, `WN18RRN_M`, `SGKG1` to `SGKG4`) keep only rules with one grounding per pair: one-atom rules (symmetry, inversion, `?a father ?b => ?a parent ?b`), which can't derive more heads than their body has facts, and SGKG's `?f hasPredecessor ?a & ?b hasSibing ?f => ?a hasSuccessor ?b`, whose generator built each path once (12,150 groundings for 12,150 pairs).

Datasets other than `family` do have complete rules with several groundings per pair, but each of them is recursive, so cycle removal drops it in both settings:

| Dataset | Rule | Setting where complete | Per pair | Recursive in |
|---|---|---|---|---|
| french | 10, 12 | std (and PCA) | 1.18 | `hasSpouse` |
| french | 181, 184 | PCA | 1.13 | `gender`, `name` |
| french1_1 | 5 | std (and PCA) | 1.16 | `hasSpouse` |
| french1_1 | 145, 149 | PCA | 1.13 | `gender`, `name` |
| frenchN_1 | 1 | std (and PCA) | 2.00 | `hasSpouse` |
| YAGO3-10_1_N | 1 | std (and PCA) | 1.67 | `isInterestedIn` |
| YAGO3-10_N_1 | 43 | PCA | 1.10 | `owns` |

`family` is the only dataset whose complete rules with several groundings per pair are compositions of other relations (`son` from `brother` and `mother`, `nephew` from `brother` and `uncle`), so they survive: rules 3, 4, 12, 13, 14, 20, 33, 34 and 35, plus 201, 202, 209 and 210 in the PCA setting. The other datasets keep their size because of where their rules happen to fall, not because the method handles such rules: a graph with non-recursive compositions that hold every time, such as another kinship graph, would grow the same way.

French Royalty is also a family graph, but its kinship rules don't hold every time (the genealogy is incomplete), so they are partial, and its complete rules with several groundings per pair are the recursive ones above.

## The rules that would keep the structure are dropped

The source does have rules that say siblings share their parents, and the generator mines them:

- rules 7 to 11 (std confidence 1), e.g. 7, `?b brother ?f & ?a mother ?f => ?a mother ?b`, and 8, the same with `father`, at 2.45 to 3.91 groundings per pair. They are recursive (`mother` and `father` in body and head), so cycle removal drops them at every level;
- rules 43, 45 and 50 (std confidence 0.997 to 0.998, PCA confidence 1): `?a father ?f & ?b sister ?f => ?a father ?b`, the same with `mother`, and `?a brother ?f & ?b sister ?f => ?a brother ?b`. In the std setting they are partial and kept from `r25` on, but the complete rules are grounded first and spend the budgets of `father`, `mother` and `sister`, so in `family_ours_r100` they reach 7 of 612, 5 of 595 and 46 of 1,346 groundings (see its log in `runs/rule-levels/ours/`). In the PCA setting they are complete and recursive, so cycle removal drops them.

kbgen, given the same rules from `r25` on, fires a rule each time it adds a fact that matches its body, rules 43, 45 and 50 included, so each new `sister` fact gets the father and mother of the sibling. Its copies keep part of the structure from `r25` on (1,417 to 1,840 sibling facts sharing a parent), while its `std1` copy, without those rules, keeps none (6 of 3,099).

## The PCA setting

The PCA setting makes four more `family` compositions complete: rules 201, 202, 209 and 210, e.g. 201, `?b father ?f & ?f son ?a => ?a wife ?b`. Their std confidence is 0.44 to 0.45: only 533 of the 1,182 couples that rule 201's body implies have a marriage fact. Their PCA confidence is 1 because every implied couple whose mother has some `wife` fact has the right one; the others have no marriage fact at all. On the source they add 906 `husband` and 906 `wife` facts, the couples with children and no marriage fact. In the copy their bodies join `father` with the inflated `son` (15 of whose 8,844 facts point to a parent), at 1.01 groundings per pair, so they derive 8,735 couples each and `husband` and `wife` reach 11,945, against 1,703 in the std copy. The PCA setting also makes rules 39, 41, 47 and 54 complete (one grounding per pair), and drops rules 43, 45 and 50, which it makes complete and which are recursive.

Since the PCA setting makes more rules complete (105 of the 2,057 mined rules against 84), it puts more rules beyond the frequency budget, and so more datasets at risk of this growth. On the current benchmark the rules it adds outside `family` are all recursive and removed.

## kbgen

kbgen ([`runs/rule-levels/kbgen/kb_models/model_m3.py`](../runs/rule-levels/kbgen/kb_models/model_m3.py)) has no completion step. Each time it adds a fact, every rule with that fact's relation in its body fires with probability equal to its PCA confidence (`pca=True`, the default of `synthesize.py`), and the rule's facts can trigger further rules, up to 10 levels deep (`max_recurr`). A fact made by a rule spends the same budget as a random fact: kbgen keeps counts per relation, per relation and subject type, and per relation, subject type and object type, and adds a rule's fact only if the last of these is above 0 (`produce_rules`); each fact costs 1/1.1 of a count, so a relation can reach about 1.1 times its source frequency, and synthesis stops at the source's total. Its `family` copies therefore keep the source's size (28,262 triples at every level), but the cap hides the lost structure instead of keeping it: a rule whose head relation has no budget left doesn't fire, which lowers its confidence in the copy, and `father` and `mother` end at 779 and 807 against 2,010 and 1,714.

## Consequences

- The copies of `family` made by this generator, `family_ours_std1` to `family_ours_r100` in `exp_datasets/` and `family_ours_pca1` to `family_ours_r100` in `exp_datasets_pca/`, don't preserve the source's relation frequencies or the support of its rules (rule 33: 1,772 → 30,413, at std confidence 1), and are not plausible family graphs. KGE results on them compare models on a graph 3 to 4.9 times larger with a different structure, and should be read with that in mind.
- The method preserves relation frequencies only while no complete rule with several groundings per pair survives cycle removal. On the current benchmark that holds for every dataset but `family`, for the reasons above, not by design.

## Possible fixes

None of these is applied. From the least to the most work:

1. Cap completion at each relation's frequency, as kbgen caps its rules: `apply_rule` would check and spend each head triple against the head relation's remaining profile ("`generator.py`" in `BACKLOG.md`). The copies would keep their size, but complete rules would lose std confidence 1, and the structure would still be lost.
2. Keep the random step (Mechanism 3) from making body groundings of complete rules that reach new head pairs. This removes the groundings the random triples add, but doesn't rebuild the source's convergence.
3. Ground each complete rule with the source's number of groundings per head pair, so that rule 12's 3,022 groundings reach 772 pairs: `a`'s siblings get `a`'s mother. Rules 3, 4, 12 and 13 depend on the same parent structure, so the generator would have to build it once, consistently, for all of them.
4. Apply the structure rules: rules 7 to 11, 43, 45 and 50 say that siblings share their parents. Exempting them from cycle removal, or grounding them before the compositions, would build the convergence that the compositions need, as kbgen's incremental firing partly does.

`BACKLOG.md` tracks them under "Complete rules with several witnesses per head pair overshoot".

## Reproducing the numbers

```bash
python scripts/rule_witnesses.py --suffix _ours_r100 --totals-only                       # groundings per pair of the complete rules of every r100 copy
python scripts/rule_witnesses.py --suffix _ours_r100 --datasets family                   # per rule
python scripts/family_structure.py exp_datasets/family/family.unified.tsv exp_datasets/family_ours_std1/family_ours_std1.tsv exp_datasets_pca/family_ours_pca1/family_ours_pca1.tsv exp_datasets/family_kbgen_r100/family_kbgen_r100.tsv
python scripts/family_structure.py --completeness pca --chain exp_datasets_pca/family_ours_pca1/family_ours_pca1_used.csv exp_datasets/family/family.unified.tsv
python scripts/check_outputs.py --suffix _ours_std1 --datasets family                    # frequencies and self-loops per relation
```

`rule_witnesses.py` reads copies under one root, so for the PCA copies, link `exp_datasets/family` and `exp_datasets_pca/family_ours_<level>` into one folder and pass it as `--root`. The per-relation counts of the tables above are the second column of each `.tsv` file counted with `cut -f2 | sort | uniq -c`.
