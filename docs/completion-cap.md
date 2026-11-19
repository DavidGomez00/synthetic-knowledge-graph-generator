# Capping completion at the source frequency

Completion forward-chains the complete rules over the EDB and, by default, inserts every head triple they derive, with no frequency cap. On `family` this makes the copies 4.3 to 4.9 times the source, because the copy keeps each complete rule's body groundings but spreads them over new head pairs ([`family-growth.md`](family-growth.md)). `--cap-completion` stops each relation at its source frequency. This page describes the cap and what it changes on `family` and `french`, measured on 19 November 2026 in the PCA setting (a rule is complete when its PCA confidence is 1, the only setting since that day). The KGE training on the capped copies is still to run (see [Still to run](#still-to-run)).

## The cap

`cli/main.py --cap-completion` (and `cli/generate_datasets.py --cap-completion`, which passes it on) calls `engine/completion.complete_graph` with `caps`, each relation's source frequency (`_ClosureProgress.targets`, copied before the EDB spends the profiles), and `degree_targets`, a copy of the source profiles. Without the flag, completion is unchanged.

With `caps`, each pass is a capped pass (`completion._capped_pass`):

1. Every complete rule is evaluated on the same snapshot of the graph, and its new head triples (`generator.novel_head_pairs`) are pooled per head relation, remembering which rules derive each triple. Pooling lets the rules that derive one relation share its budget: without it, rule 12 (`?a brother ?f & ?b mother ?f => ?a son ?b`) would take all 2,110 `son` facts before rules 13 and 14 run.
2. A relation gets at most its source frequency minus its current frequency. When the pool is larger, the triples are taken greedily in three tiers: first those whose subject is below its source out-degree and whose object is below its source in-degree in that relation (counted as triples are taken), then those where one of the two is, then the rest. Within a tier, a triple derived by more rules comes first, then a random order.
3. A self-loop is taken only while the relation has fewer self-loops than the source. A relation written as undirected edges is taken in pairs, `(a, b)` with `(b, a)`, so it stays symmetric.
4. Passes repeat until one adds nothing. Each relation that reached its cap logs its final count and the derivable head triples left out, e.g. `[initial] Cap nephew: 3633/3633, 17059 derivable head triples left out.`

Because the cap leaves derivable heads out, a complete rule whose head relation is capped no longer has confidence 1 in the copy. That is the cost of the cap; the measurements below give its size.

## The copies

The copies are in `exp_datasets_capped/`, next to symlinks to the sources, to kbgen's and PyGraft's copies and to `exp_datasets/rule_levels.tsv`, so the analysis scripts can take it as `--root`. Each run's log is next to its copy and in `logs/ours_capped/`:

```bash
python -m skgg.cli.generate_datasets --cap-completion --datasets family french --levels pca1 r25 r50 r75 r100 --output-root exp_datasets_capped --rule-levels exp_datasets/rule_levels.tsv --log-dir logs/ours_capped --times exp_datasets_capped/times.tsv
python scripts/split_vanilla.py --root exp_datasets_capped exp_datasets_capped/family_ours_*/family_ours_*.tsv
```

The ten copies took 5 minutes. Only `family` and `french` were made: the other Family and French datasets have no complete rule left after cycle removal, so completion adds nothing to them and the cap can't change their copies. The uncapped copies they are compared with are the ones in `exp_datasets/` (`family_ours_<level>`, `french_ours_<level>`), made in the same setting from the same levels.

A first set of capped copies was made earlier the same day in the std setting (std confidence 1 makes a rule complete), before that setting was removed; it gave the same picture (27,339 to 28,356 triples, the 18 complete rules at a mean std confidence of 0.44 to 0.50) and was replaced by these.

## french: the cap never applies

No `french` log has a `Cap` line: completion never brings a relation to its source frequency (rules 2, 3 and 4 add about 1,660 triples, and `parent` stays up to 47 facts short, as in the uncapped copies). The capped `french` copies are therefore new draws of the uncapped method, with 10,285 to 10,332 triples against the source's 10,332, and the same rule confidences: the 3 complete rules keep confidence 1, and the mean std confidence of the partial rules is within 0.01 of the uncapped copies at every level. They need no training of their own.

## family: size and relations

| Level | Uncapped | Capped | Source |
|---|---:|---:|---:|
| `pca1` | 122,086 | 27,325 | 28,358 |
| `r25` | 126,605 | 27,581 | 28,358 |
| `r50` | 131,902 | 28,352 | 28,358 |
| `r75` | 134,916 | 28,351 | 28,358 |
| `r100` | 137,589 | 28,354 | 28,358 |

Per relation at `r100`:

| Relation | Source | Uncapped | Capped | Left out by the cap |
|---|---:|---:|---:|---:|
| aunt | 2,990 | 2,989 | 2,987 | |
| husband | 1,138 | 11,874 | 1,138 | 4,227 |
| nephew | 3,633 | 66,479 | 3,633 | 17,059 |
| niece | 2,815 | 11,306 | 2,815 | 8,373 |
| son | 2,110 | 8,813 | 2,110 | 6,721 |
| uncle | 3,486 | 13,206 | 3,486 | 3,519 |
| wife | 1,138 | 11,874 | 1,138 | 4,227 |

The other relations (`brother`, `daughter`, `father`, `mother`, `sister`) keep their source frequency, within 3 facts, in both. At `pca1` and `r25`, `aunt` falls short (1,962 and 2,214 of 2,990), as it does without the cap (1,976 and 2,189): rule 21 derives it and no other kept rule adds to it. `scripts/check_outputs.py --root exp_datasets_capped --suffix _ours_<level>` finds no relation above its source frequency at any level, and no self-loop (the source has none).

## family: degrees

From `scripts/characterize_datasets.py --root exp_datasets_capped --set all --output-dir exp_analysis_capped` (`dataset_characteristics.csv`), and the same script on the uncapped copies of `exp_datasets/`:

| Graph | Density | In-degree max | Out-degree max | In-degree std | Out-degree std |
|---|---:|---:|---:|---:|---:|
| source | 0.0031 | 53 | 52 | 9.7 | 9.7 |
| uncapped `pca1` | 0.0174 | 551 | 505 | 56.4 | 64.7 |
| uncapped `r100` | 0.0196 | 416 | 494 | 61.9 | 71.0 |
| capped `pca1` | 0.0039 | 62 | 70 | 8.9 | 10.1 |
| capped `r100` | 0.0040 | 106 | 71 | 10.8 | 10.9 |

All copies have 2,652 entities against the source's 3,007, with or without the cap.

## family: rule confidence

From `scripts/rule_confidence.py --root exp_datasets_capped --families family french --levels pca1 r25 r50 r75 r100 --output-dir exp_analysis_capped/rule_confidence`, and the same on `exp_datasets/` for the uncapped copies, mean confidence on the copy of the rules the generator used:

| Rules | Level | Uncapped std | Capped std | Uncapped PCA | Capped PCA |
|---|---|---:|---:|---:|---:|
| 26 complete | `pca1` | 1.000 | 0.365 | 1.000 | 0.394 |
| 26 complete | `r25` | 1.000 | 0.358 | 1.000 | 0.388 |
| 26 complete | `r100` | 1.000 | 0.314 | 1.000 | 0.353 |
| 45 partial | `r25` | 0.062 | 0.060 | 0.071 | 0.072 |
| 322 partial | `r100` | 0.026 | 0.019 | 0.031 | 0.027 |

Every complete rule has PCA confidence 1 in the source. At `r100` only rule 21 (`?f father ?b & ?a sister ?f => ?a aunt ?b`), whose head relation stays below its cap, keeps it. The rules that read a capped relation lose the most: rule 26 (`?a brother ?f & ?b son ?f => ?a uncle ?b`) falls to 0.011, because the 2,110 `son` facts kept are a small part of the 8,813 the body implied, and rules 201 and 202 (`husband` and `wife` from `father` and `son`) fall below 0.01. Rule 2 (`?b husband ?a => ?a wife ?b`) falls to 0.65 because `husband` and `wife` are capped separately, each with the triples of its own rules.

## family: structure

`scripts/family_structure.py` on the capped `r100` copy: 1,705 couples with 1.00 children each, 26 of 5,733 sibling facts sharing a parent, 5 of 2,110 `son` facts pointing to a parent (source: 691 couples, 2.47 children each, all sibling and `son` facts). `scripts/rule_witnesses.py --root exp_datasets_capped --suffix _ours_r100 --datasets family --totals-only` gives, for the 18 rules with std confidence 1, 52,189 body groundings for 51,651 head pairs (1.01 per pair; source 2.11). The cap keeps the size and the degrees, but the copy is no more a plausible family graph than before: the cause of the growth, the lost alignment between the parent and sibling relations, is untouched (fixes 2 to 4 of [`family-growth.md`](family-growth.md#possible-fixes)).

## Still to run

Neither the capped nor the uncapped `family` copies of the PCA setting have KGE results yet: the uncapped ones replaced the std copies in `exp_datasets/` on 19 November 2026, and the results of those std copies were removed with them (git history keeps them). On the training machine:

```bash
python scripts/train_kge.py --datasets family_ours_pca1 family_ours_r25 family_ours_r50 family_ours_r75 family_ours_r100 --workers 4
python scripts/train_kge.py --root exp_datasets_capped --output exp_results_capped --checkpoints exp_checkpoints_capped --datasets family_ours_pca1 family_ours_r25 family_ours_r50 family_ours_r75 family_ours_r100 --workers 4
python kgc_benchmark/report_results.py --families family
python kgc_benchmark/report_results.py --results exp_results_capped --output exp_analysis_capped --families family
python scripts/compare_results.py --family family
python scripts/compare_results.py --analysis exp_analysis_capped --family family --output-dir exp_analysis_capped/results_tables
```

`exp_results_capped/` holds symlinks to the results of `family`, `family_kbgen_r25` to `_r100` and `family_pygraft` in `exp_results/`, so `compare_results.py` writes the same `mse` and `agreement` tables as `exp_analysis/results_tables/family/`, with the capped copies as ours. Copy `exp_analysis/family/{original,pygraft,kbgen_*}/` into `exp_analysis_capped/family/` first, so `report_results.py` ranks only the new runs. Compare the two `family/mse.csv` and `family/agreement.csv` to see the effect of the cap on the MSE and on the agreement of the model rankings.
