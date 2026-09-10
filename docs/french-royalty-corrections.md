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
| 1 | 10 ontology-declaration rows leaked into the instance data: a predicate's own name used as subject, e.g. `predecessor type http://www.w3.org/1999/02/22_rdf_syntax_ns#Property` — object URI also malformed (`rdf_syntax_ns` instead of the real `rdf-syntax-ns`). Subjects: `predecessor`, `parent`, `child`, `successor`, `type`, `name`, `gender`, `spouse`, `mother`, `father`. | 10 | Deleted. |
| 2 | `Pope`: a single node typed `Person` with three conflicting `name` values (`Pope`, `Pontifex_maximus`, `Rome`), plus `gender=male` and `hasSpouse=No` — looks like an extraction artifact conflating an ambiguous title with a specific individual. `Pope` never appears as an object, only as subject. | 6 | Deleted (all triples with `Pope` as subject). |

Result: 12,554 → 12,538 rows. No other rows touched (verified via diff
against the backup: exactly 16 lines removed, 0 added).

### Why point 1 is safe to remove: comparison against `french_royalty.ttl`

The ontology declares 11 properties: `father`, `hasSpouse`, `mother`,
`parent`, `successor`, `predecessor`, `gender`, `child`, `spouse`,
`marriedTo`, `foaf:name`. The leaked rows only covered 9 of those — no
self-declaration existed for `hasSpouse` or `marriedTo` — plus one extra for
`type` itself, which isn't a named property in the ttl at all (`rdf:type` is
used implicitly via `a`, never declared as its own `fr:` term). So the leak
was a partial, inconsistent dump, not a mirror of the real ontology; nothing
of schema value was lost by deleting it — `french_royalty.ttl` remains the
single source of truth for the schema.

## Reviewed, no action taken

| # | Issue | Decision |
|---|-------|----------|
| 3 | Every accented character in the source labels (é, à, û, ç, apostrophes, …) was stripped to `_` before this file was written, e.g. `Ren__II_Duke_of_Lorraine` (René), `Fran_oise_d_Aubign_` (Françoise d'Aubigné), `Orl_ans` (Orléans), `Ch_tillon` (Châtillon), `de__Medici` (de' Medici). | Left as-is. Assumption: no two distinct names in this dataset collide once accents are stripped. |
| 4 | `child`/`parent` aren't maintained as consistent inverses: 71% of `child` edges have no matching reverse `parent` edge, and 55% of `parent` edges have no matching reverse `child` edge (`father`/`mother`, by contrast, are 100% consistently mirrored as `parent`). | Left as-is. The ontology (`french_royalty.ttl`) doesn't declare `child`/`parent` as `owl:inverseOf` each other, so there's no inconsistency to fix relative to the schema — just incomplete source coverage. |

## Flagged for manual, case-by-case review

Nothing in `french_royalty.ttl` restricts `foaf:name` to a single value per
entity, so these aren't treated as errors — but they're listed here for a
later manual look, since each may be a legitimate alternate name or may need
disambiguating:

- `Fran_oise_Ath_na_s_de_Rochechouart_Marquise_de_Montespan` → `Fran_oise_Ath_na_s_de_Rochechouart_de_Mortemart`, `Madame_de_Montespan`
- `Lorenzo_de_Medici` → `Lorenzo_di_Piero_de__Medici`, `Lorenzo_de__Medici`
- `Carlo_Buonaparte` → `Carlo_Buonaparte`, `Carlo_Maria_Buonaparte`
- `Owen_Tudor` → `Owen_ap_Maredudd_ap_Tudur`, `Sir_Owen_Tudor`

(`Pope`'s conflicting `name` values are not listed here — that entity was
deleted entirely, see point 2 above.)

## Deferred to backlog

- **`spouse` ⊑ `marriedTo` materialization is incomplete** — not fixed here,
  tracked in `BACKLOG.md` under `data/`.
