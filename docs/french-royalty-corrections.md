# French Royalty dataset corrections

Corrections log for `.data/french_royalty/french_royalty.tsv`. This file
records changes that aren't visible in `git log`, because `.data` is a
git-tracked **symlink** to `/home/master/Datasets/knowledge_graphs/`, a plain
directory with no version control of its own — edits to the data leave no
git diff. A one-time pre-correction backup was kept alongside the data at
`.data/french_royalty/french_royalty.pre-correction.tsv` for the same
reason.

The issues below were found by a manual consistency review (predicate
counts, entity-typing, inverse-relation checks, `type`-object audit,
character-encoding check) run against the original 12,554-row file. See
`BACKLOG.md` for the one deferred item.

## Applied — 2026-09-10

| # | Issue | Rows affected | Action |
|---|-------|---------------|--------|
| 1 | ontology-declaration rows leaked into the instance data: a predicate's own name used as subject, e.g. `predecessor type http://www.w3.org/1999/02/22_rdf_syntax_ns#Property` — object URI also malformed (`rdf_syntax_ns` instead of the real `rdf-syntax-ns`). Subjects: `predecessor`, `parent`, `child`, `successor`, `type`, `name`, `gender`, `spouse`, `mother`, `father`. | 10 | Deleted. |
| 2 | marriedTo was added as a relation for an experiment in other research. This leads to a new relation declared and some rows having this relation. | 20 | Delete all relation containing "marriedTo" and delete the relation from the ontology. |
| 3 | `hasSpouse` triples have `Yes`/`No` as their object — a literal, not an entity URI. The project's methods (EDB/IDB generation, rule application) only operate over entity-to-entity triples, so this predicate can't be used as-is. | 2,211 | Deleted all triples with predicate `hasSpouse`. |
| 4 | `gender` triples aren't consumed by any rule or downstream method in this project. | 1,048 | Deleted all triples with predicate `gender`. `gender` was never declared as a property in `french_royalty.ttl`, so there was nothing to remove from the ontology. |

Result: 12,519 → 9,260 rows (measured directly from the file at the time of this edit; earlier rows in this table may not sum exactly to this starting count, since the data file has also had untracked manual edits — see the note in the intro about `.data` being an unversioned symlink).

## Reviewed, no action taken

| # | Issue | Decision |
|---|-------|----------|
| 3 | Every accented character in the source labels (é, à, û, ç, apostrophes, …) was stripped to `_` before this file was written (e.g. `Ren__II_Duke_of_Lorraine`, `Fran_oise_d_Aubign_`). | Left as-is. No two distinct names in this dataset collide once accents are stripped. |
| 4 | `child`/`parent` aren't consistent inverses (`father`/`mother`, by contrast, are 100% mirrored as `parent`). | Left as-is. The ontology (`french_royalty.ttl`) doesn't declare `child`/`parent` as `owl:inverseOf` each other, so there's no inconsistency to fix relative to the schema. |

