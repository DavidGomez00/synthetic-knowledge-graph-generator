# Backlog Archive

Historical record of resolved `BACKLOG.md` items — moved here verbatim (not
summarized) once done, so the rationale/verification notes behind each fix
stay easy to find without digging through `git log`/`blame`. See
`BACKLOG.md` for the current open TODO list, and `AGENTS.md` for the
architecture map.

## `cli/`

- [x] **`main.py`** — No se ejecuta correctamente. `TODO: Debug main.py`.
  - **Found and fixed the actual bug**: `run_synthetic_graph_experiment`
    called `get_term_mapping(..., default_namespace=config.graph.complete_uri)`
    — should be `config.graph.namespace`, matching `cli/upload.py`'s
    already-correct usage. `default_namespace` becomes the fallback
    namespace for any unprefixed ontology term; with the bug, any term
    falling through to that fallback got wrongly resolved against the
    named-graph URI instead of the entity namespace, silently producing
    garbage URIs rather than an error. Impact on `mario.json` specifically
    was masked — its rules CSV and EDB/IDB entity terms are already
    fully-qualified `<...>` URIs, which `format_term` returns unmodified
    without ever touching the "default" fallback — but the fix is
    unambiguously correct (matches the parameter's documented intent and
    `cli/upload.py`'s sibling implementation) and matters for any dataset
    whose ontology/rules use bare, unprefixed terms.
  - **Confirmed working end-to-end**: ran the actual entrypoint
    (`python -m skgg.cli.main`, not just calling the function directly)
    against `mario.json`. Completes cleanly, reaches "All predicates
    closed" (producing 30 synthetic triples from the 26-triple source),
    and the synthetic graph's triples inspected via SPARQL are
    structurally sane — clean, correctly-resolved entity/predicate names,
    no malformed/garbage URIs. `AGENTS.md`'s "Known issues" note updated
    to match.
- [x] **`main.py`** — Add a summary of the metrics comparing the original
      metrics with the generated graph.
  - Added a `summary()` function, called at the end of
    `run_synthetic_graph_experiment` right after `generate_idb`. For both
    the original (`graph.complete_uri`) and synthetic (`graph.synthetic_uri`)
    graphs it logs: total triple count, every predicate's domain/range
    distributions and frequency (`GraphMetrics.from_uri`), and every rule's
    support (`get_support`) — via the new `_format_graph_block` helper. It
    then logs a synthetic-minus-original delta block
    (`_format_delta_block`): triple-count delta, per-predicate
    frequency/domain-size/range-size deltas, and per-rule support deltas.
  - All logging-only (`logger.info`), no new graph-URIs or DB writes.
- [x] **`main.py`**: The generation of triples is incorrect. IDB must take
      into account intensional dependencies, even under the "complete rules"
      assumption.
  - **The bug**: `engine/idb.py`'s generation loop applied every rule whose
    body predicates were grounded, in no particular order, whenever multiple
    rules shared a head predicate (including recursive rules, e.g.
    `p(x,y) :- p(x,z), p(z,y)`). A more general/looser rule could fire before
    a more specific/restrictive same-head rule and consume search-space
    triples the stricter rule specifically needed, or a recursive rule could
    fire before its predicate had enough non-recursively-derived base facts
    to recurse over — producing wrong/limited synthetic triples even though
    `check_uninferrable_preds` confirmed the rule set was structurally
    complete.
  - **The fix**: revived the previously-dead `get_dependencies_intensional`
    (explicitly commented as "not necessary for complete rules") in
    `core/rules.py` and rewrote it as `get_intensional_dependencies`. For
    each group of rules sharing a head predicate, it splits into recursive
    (head predicate also in body) vs. non-recursive rules, orders both by
    body size, and derives a dependency in the restrictive-first direction:
    a rule depends on (must wait for) every other same-head rule whose body
    is a strict superset of its own — i.e. more restrictive, so it closes
    first. Ties on equal-size bodies are broken by support (lower-support/
    rarer rule goes first). Recursive rules additionally depend on *every*
    non-recursive rule for the same head, plus the same superset-based
    dependency among themselves. `engine/idb.py`'s `generate_idb` now calls
    `get_intensional_dependencies` and skips a rule in its main loop until
    every rule ID in its dependency set is in `closed_rule_ids` (previously
    dead/commented-out code).
  - **Known caveat, now documented** (`docs/concepts.md`,
    `docs/architecture.md`): this only reorders *when* a
    structurally-derivable rule is allowed to run — `check_uninferrable_preds`'s
    upfront guarantee is unaffected, since it never consults the dependency
    graph. But runtime closure is now coupled to the ordering: if a
    dependency rule itself never closes (its search space runs dry before
    its `support` target is met), everything gated on it stays skipped
    indefinitely, and the loop can reach "stale state"
    (`if not added_triples: break`) with a structurally-derivable predicate
    still short of its target frequency — a failure mode that didn't exist
    before this change.
  - Verified via `mypy` (no new errors from the `rules.py`/`idb.py` changes)
    and end-to-end against `mario.json` (GraphDB already up, `complete_uri`
    pre-populated from an earlier `cli/upload.py` run): the dependency
    gating is visibly exercised in the logs — `rule_5`/`rule_6` (both
    depend on `rule_4`) are skipped ("Rule depends on non closed rules
    {'rule_4'}") in step 1 while `rule_4` is still open, then fire in step 2
    once `rule_4` closes — and the run still reaches "All predicates
    closed": full closure, no stale-state stall for this dataset.

## `config.py`

- [x] **Dead/unused config variables and classes.** `RunConfig` and its
      sub-configs carried several fields/classes with zero readers anywhere
      in `src/`.
  - **Deleted** `HardwareConfig` (`n_gpus`, `device`, `precision`,
    `max_memory_mb`) entirely — it was never read outside its own
    `default_factory` construction, and its only live effect was forcing
    the `torch` import in `config.py`. Removed the `RunConfig.hardware`
    field, its `from_json` section handling, and the `import torch`.
  - **Deleted** `DataConfig.crud_endpoint` — defined but never read
    anywhere. Also removed the matching (equally unused) `crud_endpoint`
    key from `configurations/french_royalty.json` and
    `configurations/simpsons.json`'s `data` sections — since `DataConfig`
    is built via `DataConfig(**get_section("data", ...))`, that stray key
    would otherwise now raise a `TypeError` on load.
  - **Deleted** `FineTuningConfig` / `CoTGenerationConfig` — initially kept
    as documented forward-looking scaffolding, but reconsidered as out of
    scope for now and removed along with `RunConfig.fine_tuning`/
    `cot_generation` and their `from_json` handling (no config JSON
    referenced either section). `AGENTS.md` updated to note the
    fine-tuning/CoT pipeline isn't implemented under `src/` without
    pointing at now-nonexistent config classes; reintroduce them once that
    pipeline is actually built.
  - **`classification`/`pca_threshold` filtering inconsistency** — resolved
    as docs-only: `RulesConfig.pca_threshold`'s docstring and `AGENTS.md`
    both claimed/implied `"NEGATIVE"`-classified rules are excluded from
    generation, but nothing downstream actually checks
    `rule.classification` before feeding rules into
    `generate_edb`/`generate_idb`, and no `utils.filter_rules` function
    exists. Corrected both docs to describe the actual (classify-only,
    no filtering) behavior instead of implementing the missing filter.
  - Verified via `mypy` (clean) and `RunConfig.from_json` against all three
    `configurations/*.json` — `mario.json`/`french_royalty.json` load as
    before, `simpsons.json` still fails only on its already-tracked missing
    `namespace` field.
- [x] **Smelly logic in `config.py`.**
  - **Deleted** `RunConfig.__post_init__` — it was a no-op (`pass`) despite a
    docstring claiming "Validate config."
  - **`RulesConfig.pca_threshold` is now a required `float`, never `None`** —
    its docstring already documented `None` as skipping classification, and
    it fed into `core.rules.parse_rule_set(pca_threshold: float | None)`,
    but no caller (`cli/main.py`, `cli/upload.py`) ever actually passed
    `None`. Tightened `parse_rule_set`'s signature to plain `float` and
    dropped its now-dead `None`-skips-classification branch to match.
  - **Unified error surfacing in `RunConfig.from_json`** — missing/malformed
    top-level sections used to raise clean `KeyError`/`ValueError`s via a
    `get_section` helper, but errors *inside* a section (missing/unexpected
    field) fell through to a raw dataclass `TypeError` with no
    "Configuration Error" context (e.g. the `simpsons.json` case below).
    Replaced `get_section` with `load_section`, which builds the section's
    dataclass directly and wraps any `TypeError` from its constructor into a
    `ValueError` prefixed the same way as the other config errors. Also
    added the missing `"Configuration Error: "` prefix to the
    not-a-mapping-section case, which lacked it.
  - Verified via `mypy` (clean) and by exercising all four error paths
    (missing section, non-mapping section, missing field, unexpected field)
    plus an end-to-end run against `configurations/mario.json`.

## `core/`

- [x] **`rules.py`** — Refactor dataclass functions to delete obsolete code;
      refactor and delete obsolete functions.
  - Deleted dead methods with zero callers anywhere in `src/`:
    `Atom.__contains__`, `Atom.get_variables`, `Atom.to_natural_language`
    (and `CAMEL_CASE_PATTERN`), `RuleSignature.__iter__`,
    `RuleSignature.to_natural_language`, `RuleSignature.__str__`,
    `RuleSignature.get_head_variables`/`HornRule.get_head_variables`,
    `HornRule.__str__`.
  - `get_dependencies_intensional` kept as-is (flagged dead-for-now by the
    author, needed once incomplete-rule support is built).
  - Renamed internal-only parsing helpers to signal they're private:
    `parse_body`→`_parse_body`, `parse_head`→`_parse_head`,
    `parse_horn_rule`→`_parse_horn_rule`, `RuleRow`→`_RuleRow`.
  - Fixed the `intesional_preds`→`intensional_preds` misspelling in
    `RuleSignature.get_extensional_body`, and rewrote `parse_rule_set`'s
    stale docstring (referenced a nonexistent `rules_df` param and a tuple
    return value).
  - Verified via `mypy` (no new errors) and an end-to-end run of
    `run_synthetic_graph_experiment` against `configurations/mario.json`.
  - Follow-ups spun out below: `parse_rule_set` return type, and the
    `classification`/`pca_threshold` filtering inconsistency.
- [x] **`queries.py`: 3 funciones para escribir queries** — reducir o
      eliminar hasta sólo tener las que se usan.
  - Audited all 3 insert-triples functions
    (`insert_triples_sparql`/`insert_triples_bulk`/`insert_graph_sparql`,
    the last since renamed to `insert_graph` — see below): all 3 are
    actually used somewhere in `src/`, so this wasn't a dead-code deletion.
    `insert_triples_gsp()` was already renamed to `insert_triples_bulk()`
    in an earlier session and now supports GraphDB as well as Virtuoso
    (detects the backend from the endpoint, `/repositories/` ⇒ GraphDB, and
    POSTs raw N-Triples to each store's bulk-load REST endpoint —
    `.../statements?context=<grafo>` on GraphDB/RDF4J,
    `sparql-graph-crud-auth` on Virtuoso — instead of parsing `INSERT DATA`
    for hundreds of thousands of triples).
  - Found and fixed the one real gap: `insert_graph_sparql` (formerly
    listed here under its old name `insert_graph_from_nt_sparql`, already
    renamed in an earlier session but never updated here) — used by
    `cli/upload.py` to load the base graph from a `.nt` file, potentially
    the largest/most performance-sensitive insert path in the pipeline —
    was still going through the slow, chunked `insert_triples_sparql`
    (SPARQL `INSERT DATA`) instead of `insert_triples_bulk`'s fast REST
    path. Switched it over. `edb.py`/`generator.py`'s other
    `insert_triples_sparql` call sites are left as-is: they insert
    smaller, filtered/deduplicated runtime-generated streams with no
    documented at-scale problem pushing them onto the bulk path.
  - Since it no longer builds/executes SPARQL itself, renamed
    `insert_graph_sparql` → `insert_graph` (only caller: `initialize_graph`
    within `queries.py` itself).
  - `build_filtered_query`/`generate_triples_from_rule` — no longer exist
    anywhere in the codebase (already removed in an earlier session); the
    old sub-bullet about them was stale.
  - Verified via `mypy` (no new errors) and by running `cli/upload.py`
    end-to-end against `mario.nt` (16/16 non-comment lines loaded,
    matching exactly) followed by a full
    `run_synthetic_graph_experiment` run.
- [x] **`queries.py`: `download_graph_raw()` removed** — dropped the
      "produce a file with a graph in it" feature entirely for now. It had
      zero callers anywhere in `src/`/`notebooks/` (its only caller,
      `cli/download.py`, was already removed in an earlier session) and was
      the function responsible for the 17MB artifact previously cleaned out
      of the repo (`output_path.mkdir()` followed by `output_path /
      file_name` produces a nested directory when `output_path` already
      ends with the file name). Deleted the whole "Download from database"
      section, including its stale `# TODO: Tiene esto que estar aquí??`.
      `requests`/`URL`/`Path` imports are all still used elsewhere in the
      file, so no import cleanup was needed. As a side effect, this also
      removed one of the file's pre-existing `mypy` errors (a type mismatch
      inside the deleted function). Verified via `mypy` and end-to-end runs
      of `cli/upload.py` and `run_synthetic_graph_experiment`. Re-add a
      download/export feature from scratch if/when it's actually needed.
- [x] **`queries.py`: `copy_graph_sparql`** — reordenar, revisar uso y
      refactorizar.
  - **Uso**: confirmed used — its only caller is `initialize_graph` (for
    the "source is a graph URI, not a `.nt` file" branch), and the pipeline
    exercises it every run (e.g. copying `graph/base` → `graph/complete`).
  - **Refactor**: `clear_graph_sparql`, `execute_insert_query`, and
    `copy_graph_sparql` each duplicated the same ~15-line
    "configure the update client, work around `SPARQLWrapper`'s
    query/update param quirk, execute, log-and-raise on failure"
    boilerplate — and `clear_graph_sparql` was even missing the
    `setRequestMethod(URLENCODED)` call the other two had (harmless in
    practice, since that's already `SPARQLWrapper`'s own default, but
    still an inconsistency). Extracted the shared logic into a new
    `_execute_update_query()` helper (grouped with `_get_update_client` in
    the "Helper functions" section); all three now just build their query
    string and delegate to it. Also fixed `clear_graph_sparql`'s stale
    docstring (documented a nonexistent `database_endpoint` arg instead of
    `client`).
  - **Reorder**: moved `copy_graph_sparql`'s definition to sit right after
    `clear_graph_sparql` — both are graph-level SPARQL UPDATE operations
    feeding `initialize_graph`, previously separated by the unrelated
    "Handle SPARQL query responses" section.
  - As a side effect, de-duplicating the boilerplate also collapsed 2 of
    the file's pre-existing `mypy` errors (duplicate copies of the same
    type mismatch) down to 1 occurrence.
  - Verified via `mypy` and end-to-end runs of `cli/upload.py` (including
    its "Successfully copied ..." debug log) and
    `run_synthetic_graph_experiment`.
- [x] **`queries.py`: `chunk_iter`** — already resolved in an earlier
      session: it's `_chunk_iter()` and already grouped in the "Helper
      functions" section alongside `_get_update_client`/
      `_execute_update_query`. Stale sub-bullet, no action needed.
- [x] **`queries.py`: renombrar `run_select_query`/`execute_ask_query`/
      `execute_insert_query`** — `execute_ask_query` turned out to not
      exist in the file at all (confirmed via grep; likely never existed
      under that name, or was removed before this file's history was
      tracked here). Converged the "run a raw/pre-built query string
      against the store" layer on one verb, **"execute"** — matching
      `execute_insert_query` and the `_execute_update_query()` helper added
      in the previous pass, so only `run_select_query` needed to move
      (cheaper than moving the other two):
  - `run_select_query` → `execute_select_query`.
  - Also renamed for consistency, per user decision:
    - `clear_graph_sparql`/`copy_graph_sparql` → `clear_graph`/`copy_graph`
      — dropped the redundant `_sparql` suffix (the module talks SPARQL by
      default per its own docstring, so the suffix added no signal here).
      Left `insert_triples_sparql`/`insert_triples_bulk` alone: that pair's
      suffixes are load-bearing, disambiguating two real implementations
      of the same operation (SPARQL `INSERT DATA` vs. REST bulk-load).
    - `count_triples` → `get_triple_count` — the only metric-query function
      not prefixed `get_*` (siblings: `get_domain`, `get_range`,
      `get_reflexivity`, `get_support`, `get_frequency`,
      `get_predicate_frequencies`, `get_existing_triples`).
  - Updated every external caller: `engine/generator.py`, `engine/edb.py`
    (`execute_select_query`, `clear_graph`), `engine/metrics.py`,
    `cli/main.py`, `cli/upload.py` (`get_triple_count`) — plus re-sorted
    the import blocks touched, alphabetically (`ruff`'s `I` rules).
  - Verified via `mypy` (identical error set/line numbers to baseline —
    zero new issues) and end-to-end runs of `cli/upload.py` and
    `run_synthetic_graph_experiment`.
- [x] **`queries.py`: refactorizar el script en general** — ordenar
      funciones y organizar en secciones. Rewrote the file into 6 coherent
      sections instead of the previous scattered/mislabeled ones:
      `Helper functions` (private), `Query execution` (the public
      `execute_select_query`/`execute_insert_query` primitives, moved up
      from ~230 lines further down — `insert_triples_sparql` calls
      `execute_insert_query`, so this also fixes a forward-reference
      ordering issue), `SPARQL query generation` (`build_rule_query`),
      `Write to database` (`insert_triples_sparql`/`insert_triples_bulk`/
      `insert_graph`/`clear_graph`/`copy_graph`/`initialize_graph` — merges
      the old "Insert to database" and "initialize graph in database."
      sections, which mislabeled `clear_graph`/`copy_graph` as inserts and
      gave `initialize_graph` its own redundant section), `Query metrics`,
      and `Helpers for IDB/EDB generation` (fixed a `"ofr"` → `"for"`
      typo). Also moved the `SparqlBinding` type alias up next to `logger`
      since it's used throughout the file, not just by the "response
      handling" functions it used to sit next to. Pure reordering — no
      logic changes; verified the function set is byte-identical
      (`diff`'d sorted `def` names before/after) and behavior is unchanged
      via `mypy` (identical error set, just shifted line numbers) and
      end-to-end runs of `cli/upload.py` and `run_synthetic_graph_experiment`.
  - `build_rule_query` — called from several scripts (`engine/edb.py`,
    `engine/generator.py`); this was always just a "don't delete it, it's
    alive" note, not an actionable TODO.
  - This closes out the `queries.py` module entirely — every sub-item
    tracked under it (function consolidation, `download_graph_raw`
    removal, `copy_graph_sparql`/`clear_graph_sparql` refactor+rename,
    `run_select_query`/`execute_insert_query`/`count_triples` renames,
    and this reorganization pass) is now resolved.
- [x] **`generator.py`**: Deleted the creation of searchspace in the generation
  of triples by applying rules. It was specified that the frequency of the 
  predicates is an upper bound, not strict. This leaves many rules with the
  support not met either, but this may be the correct way.
- [x] **`queries.py`**: Inserting small amounts of triples in different
      queries is slow — added a buffer that accumulates them and inserts in
      fewer, larger batches.
  - Traced `engine/edb.py`'s `generate_edb` loop: Step 1
    (`check_direct_matches`) and Step 3 (`insert_random_triples`) both
    decide their triples purely from the in-memory `PredicateProfile`
    domain/range dicts — no DB read is needed to produce them — yet each
    call immediately issued its own `INSERT DATA` via
    `insert_triples_sparql`. Step 3 in particular is invoked once per
    outer-loop iteration and typically commits just one subject's row
    (often a single triple), so most of `generate_edb`'s round trips came
    from there. Step 2 (`check_triples_from_rule`) is different — it needs
    `get_support`/`get_existing_triples` against `edb_uri` *before* it can
    decide what to generate — so it was left inserting immediately.
  - Added `TripleBuffer` (`core/queries.py`, next to `insert_triples_sparql`,
    which it wraps unchanged): `add()` accumulates triples in memory,
    `flush()` inserts everything buffered in one batched call and clears
    it, and `flush_if_full()` flushes only once the buffer reaches
    `chunk_size` (bounds memory without reintroducing per-call round
    trips).
  - `check_direct_matches`/`insert_random_triples` now take a
    `buffer: TripleBuffer` and buffer instead of inserting immediately.
    `generate_edb` owns one shared buffer for the whole call, flushes it
    immediately before Step 2 runs (so its `get_support`/`get_existing_triples`
    reads always see every previously-decided triple, buffered or not), and
    flushes it unconditionally right after the main loop exits, so callers
    (`get_triple_count`, `generate_idb`/`complete_graph`) always see a
    fully-committed EDB. `check_triples_from_rule` itself is unchanged.
  - No other code calls `check_direct_matches`/`insert_random_triples`, so
    no signatures rippled beyond `edb.py`. Verified via `python -m
    py_compile` on both files; no test suite exists in this repo to run
    (see `BACKLOG.md`'s "docs/" section), so behavioral verification still
    requires an end-to-end run against a live DB comparing final EDB
    triple counts before/after.
- [x] *(urgent)*: Refresh how intensional dependencies work and why they are
      useful/necessary (see `core/rules.py`'s `get_intensional_dependencies`
      and `docs/concepts.md`'s "Intensional rule dependencies" section).
  - **Turned out to be dead code, not a doc gap**: `get_intensional_dependencies`
    and the `generate_idb` function that consumed it (`engine/idb.py`) were
    only ever called from each other — `git log -S"generate_idb("` on
    `cli/main.py` shows the pipeline stopped calling `generate_idb` back
    around commit `fdfd4ca`, long before `HEAD`, once `engine/completion.py`'s
    `complete_graph` + `engine/cycles.py`'s `break_cycles` took over building
    the synthetic graph. `check_uninferrable_preds` and `get_predicate_mapping`
    were in the same boat (only called from `generate_idb`). All four were
    removed instead of refreshed: `generate_idb`/`update_closure` from
    `engine/idb.py` (which now only keeps `get_closed_rules`/`get_closed_preds`,
    still used by `completion.py`/`cli/main.py`), and
    `get_intensional_dependencies`/`check_uninferrable_preds`/
    `get_predicate_mapping` from `core/rules.py`.
  - `docs/concepts.md`'s "Intensional rule dependencies" section became
    "Rule application order", describing what `complete_graph` actually
    does (no rule ordering, no profile budget during the loop — a plain
    forward-chaining fixpoint) instead of the removed dependency-gated
    design. `docs/architecture.md` (diagram, "Data flow" step 5,
    "Components" diagram/prose), `docs/edb-generation.md`, and `AGENTS.md`
    updated to match. Verified via `python -c "import skgg.cli.main, ..."`
    (all modules still import cleanly) and `mypy` (no new errors beyond a
    pre-existing, unrelated `pandas.itertuples` typing issue in
    `core/rules.py`).

## `engine/`

- [x] **`generator.py`** — Refactor and delete obsolete functions.
  - **No dead functions found** — same pattern as `rules.py`/`queries.py`:
    every function (`decrement_counts`, `update_closed_preds`,
    `is_assignment_solvable`, `create_searchspace`, `GraphSources`,
    `triples_from_bindings`, `apply_rule`) is actually called from
    `edb.py`, `idb.py`, or `completion.py`.
  - **Found and fixed a real correctness bug** while auditing:
    `apply_rule`'s inner `filter_triples` checked
    `profile.range.get(subject, 0)` instead of
    `profile.range.get(obj, 0)`. `range` is object-keyed everywhere else
    in the codebase (`get_range()`, `is_assignment_solvable`,
    `edb.py`'s domain/range usage all pair `domain`↔`subject` /
    `range`↔`obj`), so this line effectively always evaluated to "reject
    the triple" whenever a profile was passed — which `idb.py`'s IDB
    generation loop always does. Before the fix, every run in this
    session's history ended in "Reached stale state" without full
    predicate closure and produced ~10-20 synthetic triples for
    `mario.json`; after the fix, runs consistently reach "All predicates
    closed" and produce ~28-30 triples. Fixed by keying on `obj` instead
    of `subject`.
  - Cleaned up dead commented-out debug-log lines in `filter_triples`,
    and fixed several stale docstrings: `create_searchspace` documented
    nonexistent params (`database_endpoint`/`predicate`/`profile`) and a
    wrong return type (said it returns a URI; it returns `None`);
    `triples_from_bindings` said it returns "a set" but it's a generator;
    `decrement_counts`/`update_closed_preds` called themselves "private"
    despite being public, cross-module functions (plus a "helpter" typo
    in the latter).
  - Verified via `mypy` (identical error set to baseline — zero new
    issues) and multiple end-to-end runs of `cli/upload.py` and
    `run_synthetic_graph_experiment` against `mario.json`.
- [x] **`edb.py`** — Idea for reaching stale state with a rule still short
      of its support: if an open rule's only remaining open predicate is in
      its body (everything else already closed), directly instantiate the
      triples needed to close it without exceeding the support upper bound.
  - **Implemented in `engine/completion.py`, not `edb.py`**, as
    `complete_open_rules_with_closed_head`: scans rules that are open but
    whose head predicate is already closed, orders candidates by least
    missing support (most restrictive first, ties broken by fewest open
    body atoms), and generates triples for their remaining open body
    predicate(s) directly. Guards against a predicate shared with an
    already-closed sibling rule's body (that rule has no support headroom
    left, so a new witness for it would be unsafe).
  - Generalized beyond the original single-open-predicate idea to handle
    any number of open body atoms, via a forward-checking CSP
    (`_solve_open_atoms`) over "linking" variables shared only among open
    atoms — profile-valid, with existence pre-fetched once up front
    (mirroring `edb.py`'s `_select_valid_bindings`) so novelty is
    guaranteed by construction (the caller's `FILTER NOT EXISTS` makes at
    least one atom per solved row provably new) and an already-existing
    triple is never double-decremented.
  - Wired into `cli/main.py`'s pipeline in a retry loop alongside
    `complete_graph`, looping until a full pass adds nothing.
  - **Known, deliberately deferred limitation**: rows are solved greedily,
    not jointly — an earlier binding row's choice can consume a scarce term
    a later row also needed even when a different (still valid) choice for
    the earlier row would have let both succeed. `edb.py`'s
    `_select_valid_bindings` avoids this by backtracking across its whole
    binding list; doing the same here would mean merging the per-row
    variable search with row-level backtracking — deferred as a separate,
    larger task.
  - Intensional dependencies (the other half of the original idea) were
    *not* wired in — see the `## Next session` item about refreshing how
    those work, which is still open.
- [x] **`completion.py`** — Tried a second implementation of
      `complete_open_rules_with_closed_head` reusing `edb.py`'s searchspace
      machinery instead of the bespoke CSP above, to compare tradeoffs
      (branch `same_approach_searchspace`, commit `8de2353`). **Rejected as
      too heavy; removed from the pipeline.**
  - **The approach**: materialize a scratch searchspace graph
    (`generator.create_searchspace`) for a rule's open body predicates, join
    it against the real graph's closed atoms + head in one two-graph SPARQL
    query (resolving every open-atom variable via the join itself, including
    ones private to the open atoms — no bespoke "linking variable" solver
    needed), then run the resulting bindings through `edb.py`'s
    `_select_valid_bindings` (reused as-is) to pick a subset respecting
    profile budgets.
  - **What it got right, relative to the bespoke CSP**: `_select_valid_bindings`
    backtracks across the *whole* binding list, so it doesn't share the
    bespoke version's "greedy, not jointly" row-ordering limitation.
  - **Why it was rejected**: an unselective correlation (two atoms joined
    only on a shared `rdf:type`, in `french_royalty.json`) produced a
    693,594-row candidate binding set for a rule that only needed 17 new
    witnesses. `_select_valid_bindings` recurses once per binding examined,
    so this first hit Python's `RecursionError` outright. A
    `LIMIT {missing * 20}` cap on the query avoided the crash, but the
    fundamental cost (materializing a full cartesian-product scratch graph
    and a two-graph join, for every candidate rule tried) was judged too
    expensive for what's meant to be a lightweight last-resort fallback.
    Confirms, empirically, the general concern already on file about the
    searchspace technique's cartesian-product cost (see the `edb.py`
    *(low priority)* item in `BACKLOG.md`).
  - **Found and fixed along the way, kept regardless of the rejection**: this
    was apparently the first time `_select_valid_bindings` got exercised
    end-to-end in a while — its internal `backtrack()` was missing the
    `current_missing_heads` argument at all three call sites (the initial
    kickoff and both recursive calls), a `TypeError` that would have broken
    `edb.py`'s own `check_triples_from_rule`/`_filter_bindings` path too, not
    just this new caller. Fixed in the same commit.
  - **Recoverable via git history**: the full searchspace implementation
    (and this bug fix) is preserved at commit `8de2353` on
    `same_approach_searchspace` (`git show 8de2353 --
    src/skgg/engine/completion.py`); `complete_open_rules_with_closed_head`
    itself was then deleted from that branch's pipeline entirely — this
    branch keeps only `complete_graph`, no closed-head-completion fallback
    at all.

- [x] **`edb.py`/`generator.py`**: Replaced the searchspace technique
  (`create_searchspace` + join query + `_filter_bindings`/`_select_valid_bindings`)
  with `generator.sample_groundings`, which constructs only the `support -
  current_support` groundings a rule still needs directly from the predicate
  profiles. The old approach materialized `domain × range` per predicate and
  pulled every join binding into Python, running out of memory on bodies like
  `?e father ?b ∧ ?e mother ?a`. Variables shared between atoms are drawn once
  from the intersection of their profile positions; head variables must produce
  a new projection per grounding (support counts distinct head projections),
  while existential variables are reused as witnesses. Recoverable via git
  history.

- [x] **`cycles.py`** — `break_cycles` was implemented and unit-tested offline
      (`tests/test_cycles.py`) but had not yet been run end to end against a
      graph store.
  - **Confirmed working end-to-end**: ran `python -m skgg.cli.main -f
    french_royalty_source.json` against a live GraphDB instance
    (`logs/french_royalty.log`, 2026-09-22 run). The `[4/5] Breaking rule
    cycles` phase shows `break_cycles` seeding a real cycle (`Broke cycle
    successor -> predecessor -> successor: seeded 471 triples for rule 1.`),
    the follow-up `complete_graph` pass consuming those seeds (`[cycle round
    1] +471 triples in 2 passes (stale state)`), and a second round finding
    nothing left to seed or complete (`[cycle round 2] +0 triples`, `No
    cycles to break or rules to complete.`) before the run reaches `[5/5]
    Summary`.
  - **Not covered by this run, left open in `BACKLOG.md`**: the `--skip-edb`
    reseeding behavior and whether a seeded cycle can overshoot its head
    predicate's target frequency (`apply_rule` isn't profile-capped in the
    completion loop) — see the `engine/` item in `BACKLOG.md`.

## `docs/`

- [x] **concepts.md** *(low priority)*: Check remaining definitions are as
      intended (head coverage, std/PCA confidence).
  - Verified against the current file: the "Horn Rules" section defines
    support (matching AMIE3, already audited separately), head coverage
    (support / total head-predicate triples), std confidence (support /
    body-satisfying bindings that don't also satisfy the head), and PCA
    confidence (the PCA-relaxed version of std confidence, ≥ std confidence,
    and what `rules.pca_threshold` filters on) — all consistent with
    `core/queries.py`/`core/rules.py`. Nothing left ambiguous.
- [x] **getting_started.md** *(low priority)*: Define clearly all the
      necessary inputs for the execution of the repo.
  - The file (now `docs/getting-started.md`, hyphenated) walks through
    install, starting a graph DB (Virtuoso or GraphDB, including the
    GraphDB manual-repository-creation step), building the source graph via
    `cli/upload.py` (`--complete` flag explained), running the experiment
    via `cli/main.py` (all CLI flags documented), a "where things live"
    table, and a troubleshooting pointer to `architecture.md`/`concepts.md`/
    `BACKLOG.md`. Covers all necessary inputs end to end.

## `configurations/`

- [x] **`simpsons.json` fails to load** — verified via `RunConfig.from_json`:
      raised `ValueError: Configuration Error: Invalid 'graph' section:
      GraphConfig.__init__() missing 1 required positional argument:
      'namespace'` — its `graph` section was missing the required
      `namespace` field that `mario.json`/`french_royalty.json` both have.
  - Resolved by deletion rather than a fix: the Simpsons dataset
    (`configurations/simpsons.json`, `.data/Simpsons/`) and the unrelated
    but similarly stale `.data/Movies/` were removed as irrelevant toy
    data. `configurations/` now only contains `mario.json` and
    `french_royalty.json`, both of which load successfully.
