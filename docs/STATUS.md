# ezmon-nocov Plugin Status

**Last Updated**: 2026-03-15

## Trie Removal & xdist Fix (2026-03-14)

Major refactor: removed the `TrieEncoder` path encoding layer and fixed multiple xdist bugs that were causing dependency loss and broken test selection in parallel mode.

### Changes Made

**Trie removal** — All paths are now plain relative strings (`lib/matplotlib/figure.py`) throughout the entire pipeline. No encode/decode cycles. The `TrieEncoder` is retained only for deterministic package name encoding in `deterministic_coding.py`.

Files changed:
- `file_cache.py` — Added `get_file_info(relpath)` method (replaces `TrieEncoder.get_file_info()`)
- `dependency_tracker.py` — Removed `get_encoder` import; direct relpath comparisons everywhere
- `testmon_core.py` — Removed `path_encoder` usage; added `_file_id_cache` dict for direct DB lookups; deleted `_decode_path()`
- `pytest_ezmon.py` — Deleted `_encode_worker_deps()`; simplified worker/controller paths
- `deterministic_coding.py` — Deleted `build_file_code_map()` and `encode_files()`
- `trie.py` — Stripped to minimal (TrieNode + TrieEncoder.encode only)

**xdist dep-loss fix** — Workers now call `_merge_collection_deps()` in `_finalize_worker_test_file` before sending deps to the controller. Previously, workers subtracted collection-time deps (global + per-file baselines) from test-specific deps, then the controller's `_merge_collection_deps()` was supposed to add them back — but the controller's DependencyTracker never captures collection imports because xdist controllers don't import test modules. Result: avg deps per test went from 3.3 (broken) to 53.7 (correct).

**xdist deselection fix** — Workers now run `pytest_collection_modifyitems` to deselect stable tests within collected files. Previously, workers returned early from this hook, so all tests in collected files would run even if stable. In xdist, the controller does NOT run `pytest_collection_modifyitems` (xdist delegates collection to workers).

**Detached HEAD fix** — `git_current_head()` in `common.py` now handles detached HEAD (raw SHA in `.git/HEAD`). Previously it only handled branch refs, returning None for detached HEAD, which caused `determine_stable()` to always treat every run as fresh.

**Fresh DB None propagation** — `unstable_test_names=None` (meaning "run all, fresh DB") is now correctly preserved through `workerinput` to workers. Previously, `None or set()` collapsed it to an empty set, which workers interpreted as "no tests affected" → deselect everything.

### Matplotlib Benchmark (2026-03-14)

Tested on matplotlib (~10K tests, xdist `-n auto` with 16 workers):

| Metric | Value |
|--------|-------|
| No-plugin baseline | 168.7s |
| Plugin first run (all tests) | 171.4s |
| **First-run overhead** | **1.6%** |
| Plugin second run (no changes) | 12.0s (3 failing tests only) |
| **Time saved (no changes)** | **93%** |

Dependency quality:
| Metric | Value |
|--------|-------|
| Total tests | 10,252 |
| Total dep files | 1,676 (268 python, 1,408 data) |
| Avg deps per test | 53.7 (median 37) |
| Zero-dep tests | 0 |
| Min/Max deps | 1 / 237 |
| Universal dep files (99%+ tests) | 29 (10.8% of python dep files, 3.2% of tracked .py) |
| Test files in DB | 102 |

Distribution:
- 81.8% of tests: 26-50 deps (matplotlib core imports)
- 16.1% of tests: 101-200 deps (heavier test files like test_axes, test_image)
- 0.0% zero-dep tests

### Pandas Benchmark (2026-03-14)

Tested on pandas (~230K tests, xdist `-n auto` with worksteal, Python 3.14):

| Metric | Value |
|--------|-------|
| No-plugin baseline | 117.0s |
| Plugin first run (all tests) | 339.3s |
| **First-run overhead** | **190% (222s)** |
| Plugin second run (no changes) | 8.0s (0 tests ran) |
| **Time saved (no changes)** | **93%** |

Dependency quality:
| Metric | Value |
|--------|-------|
| Total tests | 230,906 |
| Total dep files | 1,844 (1,401 python, 443 data) |
| Avg deps per test | 948.6 (median 1,265) |
| Zero-dep tests | 0 |
| Min/Max deps | 1 / 1,405 |
| Universal dep files (99%+ tests) | 253 (18.1% of python dep files) |
| Unique deps after removing 99% superset | median 1,012 |
| Test files in DB | 934 |
| DB size | 201MB |

Distribution:
- 57.0% of tests: 1001-1500 deps
- 34.4% of tests: 201-500 deps
- 8.6% of tests: 501-1000 deps

The high dep count is inherent to pandas: `pandas/__init__.py` eagerly imports most of its codebase. Even after removing the 253 universal deps, 74.4% of tests have 100+ unique deps. The 190% first-run overhead is dominated by writing 230K bitmaps averaging ~1000 IDs each to SQLite.

### Comparison

| | matplotlib | pandas |
|---|---|---|
| Tests | 10,252 | 230,906 |
| Avg deps/test | 53.7 | 948.6 |
| Universal deps (99%+) | 29 (10.8% of py) | 253 (18.1% of py) |
| First-run overhead | 1.6% | 190% |
| Rerun savings (no changes) | 93% | 93% |
| DB size | 6.2 MB | 201 MB |

The overhead difference is entirely driven by dep count × test count: pandas has 22× more tests and 18× more deps per test, yielding ~400× more bitmap write work.

## DB Write Optimization & Wire Format Fix (2026-03-15)

Two optimizations to the xdist controller DB write path, plus a wire format regression fix:

### Optimizations kept

1. **Pre-warmed file metadata** — As worker batches arrive in `_handle_worker_output`, the controller calls `_prewarm_file_metadata()` to pre-compute `(checksum, fsha, file_id)` for each new file path. The accumulated `_precomputed_file_ids` map is passed to `save_test_deps_raw`, skipping Phases 0/0b/0c (file checksum computation + file ID resolution). Saves ~22s on pandas.

2. **Batched test ID resolution** — Failed tests use `get_or_create_test_ids_batch()` instead of individual `get_or_create_test_id()` calls. Reduces per-test DB round-trips.

### Wire format regression (fixed)

Commit 83cac38 changed the worker→controller wire format from `file_common_unique_v2` to `plain_v1`, removing the common/unique dependency compression. This caused a **+125s regression** on pandas (187s → 313s):

- `file_common_unique_v2` sends common deps once per file, unique deps per test — at pandas scale (~230K tests, ~1000 shared deps), the common set is nearly 100%.
- `plain_v1` sent the full dep set for every test, inflating payloads ~97x.

**Fix**: Reverted wire format to `file_common_unique_v2` while keeping all DB write optimizations. The `file_common_unique_v2` format drops shas from `file_deps` (sends paths only as `(path, None)`), which is compatible with the prewarm method — it falls back to `file_cache.get_tracked_sha(fname)` when sha is None.

Also reverted the batch flush threshold from `>= 1` (flush every file) back to `>= self._worker_batch_size` (batch multiple files per payload).

**Expected net result**: ~187s pre-prewarm baseline → ~165s target (keeping the ~22s prewarm savings).

## Import Tracking Refactor (2026-03-14 → 2026-03-15)

The import tracking subsystem uses a pure import hook approach with deferred reconciliation. The hook records raw `(name, result.__name__, fromlist)` tuples with zero processing at import time; all path resolution and deduplication happens once per test at reconciliation.

**History**: The refactor was designed on 2026-03-14 with 26 edge-case tests written and committed. The production code was modified correctly, but during debugging the expensive `set(sys.modules.keys())` diff was re-added as a "fix" for `__init__.py` transitive imports — completely undoing the optimization. On 2026-03-15, the `sys.modules` diff was confirmed redundant (recursive hook firing, prefix expansion in `_reconcile`, and re-export detection already cover all cases) and removed for real.

This resolves three classes of failures documented in the old approach:
1. **Fromlist resolution gap** — re-exported classes (e.g., `from pkg import Class`) now correctly traced via `__module__`
2. **Checkpoint absorption** — already-loaded modules now captured because `builtins.__import__` fires on every import statement
3. **O(N) performance** — eliminated `sys.modules` scanning (329M `str.startswith` calls, ~200s of overhead at pandas scale)

**Pandas first-run benchmark (pre-refactor)**: 322s total (+313% overhead vs 78s baseline). The `sys.modules` diff accounted for ~185s of per-worker overhead across 16 xdist workers.

26 edge-case tests validate the approach in `tests/test_import_hook_approach.py`, covering relative imports, star imports, circular imports, namespace packages, failed imports, `importlib.import_module`, deep re-export chains, and `__module__ = None`.

See `docs/checkpoint-import-tracking.md` for the full design reference.

## Historical Fixes

### Checkpoint Dependency Fix (2026-02-01)

**Problem**: conftest.py imports were not being tracked in xdist parallel mode.

**Root Cause**: Workers subtracted checkpoint deps but the controller couldn't add them back.

**Fix Applied**: Superseded by the 2026-03-14 xdist dep-loss fix (workers now merge collection deps before sending).

### Build Artifact Tracking Fix (2026-02-01)

**Problem**: ezmon-nocov was tracking `.so` files in `build/cp313/` as import dependencies, causing ALL tests to be marked as "affected by changes" on every rebuild.

**Fix Applied**: Modified `dependency_tracker.py` to only track non-`.py` files if they are git-tracked. Build artifacts (`.so` files) are excluded.

## Run Data Location

All historical run data is stored on external drive: `/Volumes/2tb/pandas/run_data/`

## Known Issues

### Database Size

The roaring bitmap storage is much more compact than the old junction table approach. On matplotlib (10K tests, avg 53.7 deps): 6.2MB database. At pandas scale (~230K tests) this may be larger but should still be manageable.

### Existing Database Contamination

Databases created before the trie removal contain encoded paths. Delete `.testmondata` and start fresh after upgrading.
