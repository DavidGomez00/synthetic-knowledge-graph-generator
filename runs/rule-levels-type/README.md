# Rule-level copies of the datasets with type triples

`exp_datasets_type/` holds `french`, `french1_1` and `frenchN_1` with their 2,222 `type` triples (see its `README.md`). On 19 November 2026 `generate.sh` made their rule-level copies with this generator and with kbgen, at the levels of `exp_datasets_type/rule_levels.tsv` (PCA setting: pca1, r25 to r100), next to each dataset: `<d>_ours_<level>/` and `<d>_kbgen_<level>/`. 13 copies per generator: `french1_1` and `frenchN_1` use no rule at pca1, so that level gets no copy.

The script ran at commit `2c7dfd5d` plus the working tree of that day, from 11:55 to 12:21 on Hefesto, with `--completeness pca` on `cli/rule_levels.py` and `cli/generate_datasets.py`. That option was removed later the same day, when the PCA setting became the only one, and the script now runs without it; the copies are the same.

## Rules

The rules are those mined without types, `exp_datasets/<d>/<d>.csv`, copied to `exp_datasets_type/<d>/<d>.csv`. None of them uses `type`, so the levels file has the same rows as `exp_datasets/rule_levels.tsv` for these three datasets. kbgen was given the rules this generator used at each level (`--rules-from-ours`), and at every level its `given_rule_ids` equal our `_used.csv`.

## How each generator treats `type`

This generator copies the source's `type` triples (`cli/main.py --copy-relations type`): `type` has no profile and no frequency target, and after completion every `type` triple of the base graph is added to the synthetic graph. Each entity keeps its source class. Our generator keeps every entity's name and its count in each relation, so the classes fit. Without the copy, random pairing would type some of the 10 relation names (`child`, `father`, ...) as `Person` and some people as `Property`.

kbgen reads the `type` triples as classes: its tensor keeps them as a type matrix, not as a relation, its models learn the distribution of type sets and, per relation, which subject and object types occur, and its synthesis gives each entity a class before choosing the entities of each fact by class. Its driver now writes every triple of kbgen's output (before 19 November it dropped every `rdf:type` line): `Entity_N type Type_0` (Person) or `Type_1` (Property), and kbgen's schema, `Type_i type owl:Class` and `relation_j type owl:ObjectProperty`. `write_stats` counts these lines as a relation `type` of the copy, so the summary lists `type` as lost in load, and `synthetic_triples` includes them; `scripts/check_kbgen_levels.py` counts as facts the lines whose relation is not `type`.

## Copies

Triples of the source and the copies. Type-only: entities that appear only as the subject of `type` triples, in no other relation; in the source and in every ours copy these are the 10 relation names typed `Property`.

| Dataset | Level | Rules | Source | Ours | kbgen (type and schema lines) | kbgen type-only |
|---|---|---:|---:|---:|---:|---:|
| french | pca1 | 3 | 12,554 | 12,507 | 8,805 (2,233) | 40 |
| french | r25 | 47 | 12,554 | 12,554 | 8,805 (2,233) | 72 |
| french | r50 | 107 | 12,554 | 12,554 | 8,805 (2,233) | 332 |
| french | r75 | 167 | 12,554 | 12,551 | 8,805 (2,233) | 683 |
| french | r100 | 227 | 12,554 | 12,549 | 8,805 (2,233) | 701 |
| french1_1 | r25 | 45 | 10,179 | 10,179 | 6,435 (2,233) | 164 |
| french1_1 | r50 | 101 | 10,179 | 10,179 | 6,436 (2,233) | 470 |
| french1_1 | r75 | 157 | 10,179 | 10,179 | 6,435 (2,233) | 755 |
| french1_1 | r100 | 212 | 10,179 | 10,179 | 6,437 (2,233) | 733 |
| frenchN_1 | r25 | 31 | 7,393 | 7,393 | 4,028 (2,232) | 658 |
| frenchN_1 | r50 | 64 | 7,393 | 7,393 | 4,028 (2,232) | 1,072 |
| frenchN_1 | r75 | 100 | 7,393 | 7,393 | 4,028 (2,232) | 1,284 |
| frenchN_1 | r100 | 130 | 7,393 | 7,393 | 4,028 (2,232) | 1,377 |

kbgen's copies have fewer facts than the source because its tensor drops every triple whose object is never a subject, as for the copies without types. It makes 2,222 entities, the subjects it loads, gives each a class, and spreads the facts it kept over them; the more rules, the fewer entities those facts reach, so up to 1,377 of its `Person` entities have only their class. In the source every person is in some other relation.

## Checks

- `check_ours.txt` (`scripts/check_outputs.py`): `french1_1` and `frenchN_1` match their source on every relation at every level. `french` has the source's frequency, degrees and self-loops on every relation but `parent`: 0 to 47 triples short, with its degrees off, as in the copies without types. `parent` is derived by complete rules 3 and 4 from `father` and `mother`, and 47 of the source's 1,239 `parent` facts have neither, so at pca1, where `parent` gets no triple of its own, the copy lacks exactly those 47.
- Every ours copy's `type` triples equal the source's.
- `check_kbgen.txt` (`scripts/check_kbgen_levels.py`): its 3 failures were the `r0` rows, which the check didn't skip when no other level named them; it now skips rows with `rules_ours` 0, and all 13 copies pass.
