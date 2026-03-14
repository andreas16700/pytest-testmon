# Dependency Tracker Refactor (Current Design)

## Status
Implemented direction: event-driven checkpointing with deferred module-key resolution.

The import hook now uses a zero-processing approach that records `(name, result.__name__, fromlist)` per import call, deferring all resolution to reconciliation. 26 edge-case tests validate the approach in `tests/test_import_hook_approach.py`. See `docs/checkpoint-import-tracking.md` for the full import tracking design reference including the evolution from coverage.py through sys.modules diffing to the current hook approach.

## Core model
Two checkpoint families are tracked:

1. Module checkpoints (`sys.modules` keys)
- `global_module_keys`: established once at first test-file collection start.
- `file_module_keys[test_file]`: established once per file from collection delta over global.
- `test_unique_module_keys`: computed at test end as delta over global+file.

2. File-read checkpoints
- `global_file_reads`: non-Python reads before first file collection starts.
- `file_file_reads[test_file]`: non-Python reads during that file's collection.
- `test_unique_reads`: non-Python reads during test execution minus global+file reads.

## Events (and only these events)

1. `on_collect_file_start(test_file)`
- If global not set:
  - snapshot current module keys.
  - process delta first to classify stdlib and fill stdlib keep-set.
  - set global checkpoint keys.
  - skip immediate restore on this first set.
- Otherwise:
  - finalize previous file checkpoint (if needed).
  - restore `sys.modules` to global checkpoint + stdlib keep-set using set-diff.
- activate collection context for `test_file`.

2. `on_test_start(test_id, test_file)`
- If file checkpoint not set:
  - derive file unique module keys from current-global delta.
  - process stdlib first from that delta.
  - skip immediate restore on this first set.
- Otherwise:
  - restore `sys.modules` to `global ∪ file ∪ stdlib_keep` via set-diff.
- begin test read tracking.

3. `on_test_end(test_id)`
- compute test unique module keys:
  - `current - global - file - stdlib_keep`.
- compute test unique reads:
  - `test_reads - global_reads - file_reads[test_file]`.
- resolve module keys to local/external/file deps only here (deferred processing).

## Restore rule
Restore is set-diff based:
- `keep = checkpoint_non_stdlib_keys ∪ stdlib_keep_keys`
- remove `current - keep` from `sys.modules`
- rehydrate missing `keep - current` from `real_modules`

No full scans for phase transitions beyond key snapshots and set operations.

## Invariants
- A module object is never intentionally re-imported if cached:
  `real_modules` is the source of truth, `sys.modules` is a mutable view.
- Stdlib modules discovered in checkpoint deltas are moved to stdlib keep-set and never removed by checkpoint restore.
- Module key to file/package attribution is deferred and cached.
