# ezmon-nocov Plugin Status

**Last Updated**: 2026-04-05

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

## DB Write Optimization: Process Each Batch As It Arrives (2026-03-15)

### Problem

The original controller accumulated ALL worker batch data into a write queue, then drained everything at session end in one massive `save_test_deps_raw` call. For pandas (230K tests):

- **23.5s** building the pending list (230K tests × ~1000 deps = ~230M dict lookups + bitmap serializations)
- The "prewarming" optimization (pre-computing file IDs as batches arrived) saved ~22s on file ID resolution, but the pending-list-build bottleneck remained since everything was still processed in one giant call at the end
- The write queue also caused double-processing when stale timing data was present

### Solution

Simplified to process each batch immediately when it arrives from workers. The controller is single-threaded — no concurrency concerns.

In `_handle_worker_output`, instead of `_enqueue_write_raw()` + `_prewarm_file_metadata()`, the controller now calls `save_test_deps_raw()` directly with just that batch's tests and outcomes. Each batch is ~246 tests (pandas avg per file).

`save_test_deps_raw` already has lazy caching via `_file_id_cache` and `file_cache._content_cache` — both persist on the `TestmonData` instance across calls. First batch resolves ~1000 unique file paths; subsequent batches hit cache (~0.1s each).

### What was removed (~90 lines deleted, ~10 added)

- `_precomputed_file_ids` dict and all accumulation logic
- `_enqueue_write()` and `_enqueue_write_raw()` methods
- `_prewarm_file_metadata()` method (60 lines) — redundant with `save_test_deps_raw`'s own lazy caching
- `precomputed_file_ids` parameter from `save_test_deps_raw` and Branch A that consumed it
- `"deps_raw"` case from `_drain_write_queue` merge loop and re-enqueue logic
- Separate `failed_tests` list construction for the controller path (Phase 1 of `save_test_deps_raw` handles failure marking via `get_or_create_test_ids_batch`)

### What was kept

- `_drain_write_queue` still handles `"deps"` (bitmap) and `"sync"` items as a session-end safety net
- `save_test_deps_raw` internals unchanged — Phases 0-3 (collect filenames → checksums → file IDs → test IDs → skip unchanged → batch write) all stay
- Single-process fallback path (`else` branch in `_handle_worker_output`) unchanged

### Wire format regression (fixed earlier)

Commit 83cac38 changed the worker→controller wire format from `file_common_unique_v2` to `plain_v1`, removing the common/unique dependency compression. This caused a **+125s regression** on pandas (187s → 313s). Fixed by reverting to `file_common_unique_v2`.

### Expected performance

- Each batch: ~246 tests, ~0.1s after file IDs are cached
- First batch: ~1-2s (one-time file ID resolution for ~1000 unique paths)
- DB writes spread across the run, overlapping with worker test execution
- No end-of-session burst — queue is empty by session finish
- Target: <100s total plugin time on pandas commit f39609216d (baseline 83.5s)

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

## Schema Simplification (2026-03-15)

Reduced the database from 17 tables to 5. Removed environment partitioning, execution tracking, and the junction-table dependency model. All dependency data is now stored as roaring bitmaps in a single `test_deps` table. See `docs/SCHEMA.md` for the current schema reference.

## DepStore: Unified In-Memory Cache (2026-03-16)

### Problem

At pandas scale (230K tests, ~1000 deps each), the controller made millions of individual DB round-trips for file ID lookups, test ID creation, and checksum comparisons. This caused +190% cold-start overhead (339s vs 117s no-plugin).

### Solution

`DepStore` (`ezmon/dep_store.py`) pre-loads the entire `files`, `tests`, and `test_deps` tables into Python dicts at session start. All lookups become O(1) dict access. New entries INSERT immediately; dirty metadata flushes in batch via `save_batch()`.

### Impact

| Metric | Before DepStore | After DepStore |
|--------|----------------|----------------|
| Pandas cold-start overhead | +190% | +123% |
| Pandas pipeline (68 commits) | Cancelled (+300%) | +49% net |
| Matplotlib pipeline (15 commits) | 69% savings | 72% savings |
| Pandas DB write time | ~250s | ~140s |

The DB write bottleneck is solved. Remaining overhead is worker-side import tracing (~60s across 16 workers).

## Fix: Failed Test Persistence in xdist (2026-03-16)

### Bug

In xdist mode, failed tests were never written to the DB with `failed=1`. Workers correctly excluded failed tests from dependency payloads, but the controller's `ensure_tests_batch()` only marked entries dirty in the in-memory DepStore — `save_batch()` was never called afterward, so the SQL `UPDATE tests SET failed = ?` never executed. On the next run, `determine_stable()` didn't re-select them.

### Fix

Added `save_batch([])` call after `ensure_tests_batch()` in `pytest_sessionfinish` to flush dirty test metadata. Removed dead code: workers' `fail` list (always empty since failed tests are filtered before building `unique`), the `failed_idx` reader on the controller side, and the `all_failed` accumulator in `_drain_write_queue`.

### Tests

3 new integration tests in `integration_tests/test_failed_test_reselection.py`:
- `test_failed_test_reselected_single` — single-process mode
- `test_failed_test_reselected_xdist` — xdist mode (catches the original bug)
- `test_fixed_test_deselected` — verifies fixed tests are deselected

## NetDB: Download/Upload SQLite Flow (2026-03-16)

### Problem

The original NetDB integration was a ~900-line RPC class (`ezmon/net_db.py`) mimicking the local DB API over HTTP — one network round-trip per SQL-equivalent call. This didn't scale: pandas-scale sessions made hundreds of thousands of RPC calls and made NetDB-mode runs 5–10× slower than local-mode runs even with a zero-latency LAN server.

### Solution

Replaced with a two-call flow:

1. **Session start**: `GET /api/client/download?repo_id=...&job_id=...` fetches the stored `.testmondata` file to a local path.
2. **Session body**: runs exactly as in local mode — all operations hit the local SQLite file.
3. **Session end**: `POST /api/client/upload` replaces the stored file with the modified copy.

The server (`ez-viz` Flask app) is a thin storage layer. It never parses the SQLite and has no schema awareness — the plugin can change its schema without touching the server.

### Benefits

- Eliminated ~900 lines of RPC glue and server-side schema endpoints
- NetDB-mode performance is now identical to local-mode (minus download/upload latency)
- Server can be upgraded independently of the plugin
- Integration tests can write to a real DB file instead of mocking an RPC layer

### Cost

No concurrent access to the same `(repo, job)` DB — "last writer wins" semantics if two jobs run against the same entry simultaneously. For A/B benchmarking this is fine (we partition by `job_id` per platform), but fan-out CI scenarios that need concurrent writers would require a proper server-side merge. Commit `c21ac6e`.

## Race Condition Fix: Snapshot `fromlists` in `_reconcile` (2026-03-17)

### Bug

Pipeline-04 of the matplotlib A/B benchmark surfaced `INTERNALERROR> RuntimeError: Set changed size during iteration` in `dependency_tracker.py:375` on 2 of 3 platforms. The `_reconcile()` method iterates `recording.items()` and nested `fromlists` sets, but the import hook can fire on another thread (pytest-xdist worker setup, test fixture loading) and mutate those collections concurrently.

### Fix

Snapshot the three hot loops with `list(...)`:

- `for key, fromlists in list(recording.items())` — outer loop
- `for fl in list(fromlists)` — re-export detection loop
- `for fl in list(fromlists)` — fromlist expansion loop

Commit `8e35af5`. Pipeline-05 through pipeline-11 have been clean since.

## A/B Benchmark Pipeline on Matplotlib (2026-03-16 → ongoing)

### Methodology

A 27-commit sequence on `andreas16700/matplotlib` (tags `pipeline-01` through `pipeline-27`) runs a GitHub Actions workflow that executes the matplotlib test suite both with and without ezmon on 3 platforms in parallel:

- macos-14 / Python 3.11
- ubuntu-22.04 / Python 3.12
- ubuntu-24.04-arm / Python 3.12

Each commit dispatches 6 jobs (3 ezmon + 3 vanilla). Ezmon jobs download the previous session's DB at session start and upload the updated DB at session end. Vanilla jobs run the same test set without the plugin for a baseline.

The commits are real matplotlib history — selected from upstream PRs — so the invalidation pattern reflects realistic day-to-day development. Workflow file: `.github/workflows/tests.yml` on the matplotlib fork.

### Infrastructure

- **Plugin source**: `pip install git+https://github.com/andreas16700/pytest-testmon.git@main`
- **Storage server**: `ezmon.aloiz.ch` (production) — `ez-viz` Flask app with `/api/client/download` and `/api/client/upload` endpoints, backed by a per-`(repo, job)` file store at `ez-viz/testmon_data/<repo-hash>/<job-id>/.testmondata`
- **Dev server**: `ezmon-dev.aloiz.ch` → `localhost:6133` — same codebase, used during development; retired for this benchmark after pipeline-05 when 502 outages blocked progress
- **Parsers**: `scripts/parse_ab_results.py` extracts per-job pytest summaries from GHA logs; `scripts/snapshot_db_sizes.py` appends DB size/row counts to `profile/matplotlib/db_sizes.csv`
- **Trigger**: `gh workflow run "Tests (A/B ezmon benchmark)" --repo andreas16700/matplotlib --ref pipeline-XX`

### Results through pipeline-11

Cold-start (pipeline-01, fresh DB):

| Platform | Ezmon | Vanilla | Delta |
|---|---|---|---|
| macos-14 | 7m58s | 6m51s | +16% |
| ubuntu-22.04 | 12m57s | 11m59s | +8% |
| ubuntu-24.04-arm | 9m12s | 8m21s | +10% |

Hot cache, small code diff (pipeline-05):

| Platform | Ezmon | Vanilla | Savings |
|---|---|---|---|
| macos-14 | 7m03s | 12m04s | -42% |
| ubuntu-22.04 | 3m58s | 12m55s | -69% |
| ubuntu-24.04-arm | 6m02s | 10m19s | -42% |

Tiny diff / ~99.97% deselection (pipeline-09 and pipeline-11):

| Pipeline | Platform | Ezmon test time | Tests run |
|---|---|---|---|
| p09 | macos-14 | 20s | 227 passed, 2 failed |
| p09 | ubuntu-22.04 | 22s | 227 passed, 2 failed |
| p09 | ubuntu-24.04-arm | 16s | 227 passed, 2 failed |
| p11 | macos-14 | 13s | 2 failed, 2 skipped |
| p11 | ubuntu-22.04 | 8s | 2 failed, 2 skipped |
| p11 | ubuntu-24.04-arm | 8s | 2 failed, 2 skipped |

Large-diff pipelines (p07, p10) invalidate most fingerprints and re-run ~100% of tests — ezmon performs the same as vanilla within margin.

### Issues encountered and resolved

| Issue | Surfaced in | Resolution |
|---|---|---|
| Failed tests never persisted with `failed=1` in xdist mode | p01–p05 (benchmarking noticed 0 failures on ezmon vs 2 on vanilla) | `save_batch([])` after `ensure_tests_batch` — commit `1eb0405` |
| `RuntimeError: Set changed size during iteration` in `_reconcile` | p04 | Snapshot `fromlists` with `list(...)` — commit `8e35af5` |
| Dev server (`ezmon-dev.aloiz.ch`) returned 502 on upload | p06 | Transferred 3 DBs to production server; updated all 21 remaining pipeline tag workflow files via Git Data API to use `ezmon.aloiz.ch` |

### Insights

1. **Hot-cache savings are real and large** — on incremental commits with small diffs, ezmon eliminates 90–99% of test time. Ubuntu-22.04 at pipeline-11 dropped from 11m53s (vanilla) to 4m03s (ezmon).
2. **Cold-start overhead is small** — matplotlib's ~10K tests add 8–16% overhead on the first run. Roughly 10× better than coverage-based testmon.
3. **Test selection quality is high** — no false deselections observed. Known-failing tests are correctly re-selected every run after the persistence fix.
4. **The "savings floor" is GHA job overhead** — pip install, matplotlib build, ccache warming account for ~3–4 minutes even when the test phase is near-zero. Ezmon can't speed these up; they dominate the ezmon-job wall clock on trivial diffs.
5. **Runner quality varies** — ARM runners occasionally stall for 30+ minutes on environment setup (vanilla/ubuntu-22.04 in pipeline-08 took 2h57m). The per-pipeline median across platforms is more reliable than any single measurement.

## Migration Framework (2026-04-05)

### What changed

Replaced the destructive version-mismatch handler in `ezmon/db.py` with a real migration framework. Previously, `check_data_version()` did `os.remove(datafile)` on any `user_version` mismatch — silently destroying the user's DB whenever the plugin's `DATA_VERSION` moved. This blocked any future schema evolution: every bump would have wiped every user's DB. The new framework applies registered migrations in a single `BEGIN IMMEDIATE … COMMIT` transaction and never deletes files.

### New behavior

| Situation | Old behavior | New behavior |
|---|---|---|
| Fresh DB (`user_version = 0`, no tables) | `init_tables()` | Same |
| Fresh DB but tables already exist | Crash with raw `sqlite3 table already exists` | Raise `IncompatibleDatabaseError` with clear message |
| DB at current version | Open | Open |
| DB older, migration registered | **`os.remove`, recreate** | Apply migration in place, preserve data |
| DB older, **no** migration registered | **`os.remove`, recreate** | Raise `IncompatibleDatabaseError`, file untouched |
| DB newer than plugin | **`os.remove`, recreate** | Raise `IncompatibleDatabaseError`, file untouched |
| Migration raises mid-stream | N/A | Rollback, file byte-identical to pre-open state |

The exception is `ezmon.db.IncompatibleDatabaseError` (subclass of `TestmonDbException`). Error messages always include the file path, the current and target versions, an explicit "the file has NOT been modified" statement, and actionable recovery instructions.

### Operator guidance for pre-v19 databases

This is a **user-visible behavior change**. Any `.testmondata` file from a pre-v19 plugin version will now fail to open instead of being silently wiped. Recovery is one of:

1. **Delete the file** — `rm .testmondata .testmondata-wal .testmondata-shm` from the project directory. The next pytest run creates a fresh v19 DB and rebuilds dependency data from scratch (one full-selection run, same cost as first-time installation).
2. **Downgrade pytest-ezmon** to a version that supports the file's schema version. Only useful if downgrade is an option for your workflow.

There is no automatic in-place migration for pre-v19 DBs because the schema between v17/v18 and v19 was collapsed from 17 tables to 5; there is no preserving transform. Subsequent schema bumps (v19 → v20 and later) will ship with real migrations inside this framework.

### Commit

`tests/test_migrations.py` covers 12 cases: fresh init, fresh-but-corrupt (user_version=0 with existing tables), table-list truncation in the corruption error message, matching version no-op, future version refuses, missing migration refuses, missing migration error message is actionable, single and chained successful migrations, rollback on mid-migration exception, rollback on later-migration exception.

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

## Known Limitations

### No file/test version history

The schema stores only the latest state per file and per test. Both tables have a `run_id` column but it is a "last updated by run N" back-reference, not a version pointer. Updates are in place (`UPDATE files SET checksum = ?...`, `INSERT OR REPLACE INTO test_deps`). When a file's AST checksum changes between runs, the previous checksum is overwritten and lost.

This is efficient (storage is bounded by project size, not by history depth) but prevents:

- Debugging "why did test T get reselected between runs X and Y?" — the fingerprint that triggered the selection is gone
- Analyzing fingerprint churn over time (which files change most often?)
- Rolling back to a previous session state
- Cross-run forensics when a test flips between selected and deselected unexpectedly

A proper versioning design will be planned separately.

### Concurrent writers on NetDB

The download/upload flow assumes one writer per `(repo, job)` entry. Two jobs racing against the same server entry have last-writer-wins semantics. Fan-out CI patterns that would need concurrent writers require a server-side merge layer that doesn't exist today.

### Database Size

Roaring bitmap storage: matplotlib (10K tests) = 6.2 MB, pandas (230K tests) = 201 MB. The 5-table schema (see `docs/SCHEMA.md`) keeps storage compact. A versioning layer would multiply this by history depth, so any future design needs careful sizing.

### Flaky GHA runners

ARM runners occasionally stall on environment setup for 30+ minutes, producing misleading "ezmon is faster than vanilla by 10×" numbers in individual pipelines. Use per-pipeline medians or trimmed means when reporting.

### Existing Database Contamination

Databases created before the trie removal (pre-2026-03-14) contain encoded paths. Delete `.testmondata` and start fresh after upgrading.
