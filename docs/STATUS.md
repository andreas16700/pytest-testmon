# ezmon-nocov Plugin Status

**Last Updated**: 2026-03-14

## Import Tracking Refactor (2026-03-14)

The import tracking subsystem has been refactored from `sys.modules` diffing to a pure import hook approach with deferred reconciliation. The hook records raw `(name, result.__name__, fromlist)` tuples with zero processing at import time; all path resolution and deduplication happens once per test at reconciliation.

This resolves three classes of failures documented in the old approach:
1. **Fromlist resolution gap** — re-exported classes (e.g., `from pkg import Class`) now correctly traced via `__module__`
2. **Checkpoint absorption** — already-loaded modules now captured because `builtins.__import__` fires on every import statement
3. **O(N) performance** — eliminated `sys.modules` scanning (329M `str.startswith` calls, 33s of overhead at pandas scale)

26 edge-case tests validate the approach in `tests/test_import_hook_approach.py`, covering relative imports, star imports, circular imports, namespace packages, failed imports, `importlib.import_module`, deep re-export chains, and `__module__ = None`.

See `docs/checkpoint-import-tracking.md` for the full design reference.

## Recent Fixes

### Checkpoint Dependency Fix (2026-02-01)

**Problem**: conftest.py imports were not being tracked in xdist parallel mode. Workers would save their checkpoint (295 base deps) but the controller couldn't access them.

**Root Cause**: Workers call `_build_nodes_files_lines()` to build coverage data, but the controller's `_merge_collection_deps()` was checking its own (empty) tracker instead of the data already in nodes_files_lines.

**Fix Applied**: Added checkpoint deps directly in `_build_nodes_files_lines()` (testmon_core.py:700-708):

```python
# Add checkpoint base dependencies (conftest.py imports)
checkpoint_deps = getattr(self.dependency_tracker, '_checkpoint_local_imports', set())
nodes_files_lines[test_name]["deps"].update(checkpoint_deps)
```

Also added worker checkpoint save in `pytest_ezmon.py:429-433`:
```python
if running_as == "worker":
    self.testmon.dependency_tracker.save_checkpoint()
```

**Result**: 100% of tests (237,866) now correctly depend on conftest.py.

## Run Data Location

All run data is stored on external drive: `/Volumes/2tb/pandas/run_data/`

Symlinked from: `/Users/andrew_yos/pandas/run_data -> /Volumes/2tb/pandas/run_data`

### Run Data Structure
```
/Volumes/2tb/pandas/run_data/
├── results.csv           # Aggregated metrics
├── run1/
│   ├── noplugin/        # Baseline (no plugin)
│   ├── testmon/         # pytest-testmon
│   ├── ezmon/           # ezmon with coverage
│   └── ezmon-nocov/     # ezmon without coverage (checkpoint-fixed)
├── run2/
│   └── ...
└── run17/
```

### Run1 ezmon-nocov Data (Checkpoint-Fixed)
- **Database**: 4.6 GB (`.testmondata`)
- **Output**: `output.txt` (99 MB)
- **Metrics**: `metrics.json`
- **Duration**: 32 min 54 sec
- **Tests**: 229,162 passed, 749 failed, 6,050 skipped

### Build Artifact Tracking Fix (2026-02-01)

**Problem**: ezmon-nocov was tracking 45 `.so` files in `build/cp313/` as import dependencies. ALL 230,551 tests depended on these build artifacts. When pandas was rebuilt (on each commit checkout), all `.so` files got new content/hashes, causing ALL tests to be marked as "affected by changes".

**Root Cause**: The `_get_module_file()` function returned file paths for any module in the project directory, including compiled extension modules (`.so` files). These aren't git-tracked but were being tracked as import dependencies.

**Fix Applied**: Modified `_get_module_file()` in `dependency_tracker.py` to:
1. For `.py` files: return `(relpath, True)` - track as import (AST fingerprint)
2. For non-`.py` files: check if git-tracked
   - If git-tracked: return `(relpath, False)` - track as file dependency
   - If not git-tracked: return `None` - don't track

```python
def _get_module_file(self, module) -> Optional[tuple]:
    # ... get filepath ...
    relpath = self._is_in_project(filepath)
    if not relpath:
        return None

    # For .py files: track as imports (AST fingerprint)
    if relpath.endswith('.py'):
        return (relpath, True)

    # For non-.py files: only track if git-tracked
    sha = self._get_committed_file_sha(relpath)
    if sha:
        return (relpath, False)  # Git-tracked - track as file dep

    return None  # Not git-tracked (e.g., build/*.so) - don't track
```

**Result**: `.so` files in `build/` are now excluded from tracking. Only git-tracked files are considered as dependencies.

## Known Issues

### Database Size (4.6 GB vs testmon's 300 MB)

**Cause**: ezmon-nocov stores ~296 deps per test vs testmon's ~30 deps per test.

| Metric | ezmon-nocov | testmon |
|--------|-------------|---------|
| test_execution_file_fp rows | 70.4 million | 6.8 million |
| Avg deps per test | 296 | 30 |

The 295 checkpoint deps (conftest.py imports) are duplicated for every test.

**Potential Optimizations**:
1. Store base deps once, reference by ID (normalization)
2. Use delta encoding (only store per-test differences)
3. Add a `shared_deps` table

### Existing Database Contamination

Databases created before the build artifact fix still contain `.so` file dependencies. To get accurate test selection, either:
1. Delete the existing `.testmondata` and start fresh
2. Or run a migration to remove `build/%` entries from `file_fp` table

## Comparison Script

Location: `/Users/andrew_yos/pytest-super/pandas-eval/run_plugin_comparison.sh`

Usage (MUST run in bash, not zsh):
```bash
# Run in pure bash to avoid micromamba shell hook issues
/bin/bash -c '
export PANDAS_REPO=/Users/andrew_yos/pandas/pandas-repo
export RUN_DATA_DIR=/Volumes/2tb/pandas/run_data
export CSV_FILE=/Users/andrew_yos/pandas/macos_job_runs.csv
cd /Users/andrew_yos/pytest-super/pandas-eval
./run_plugin_comparison.sh --start 1 --end 17 --plugins "ezmon-nocov"
'

# Or for all plugins:
/bin/bash -c '
export PANDAS_REPO=/Users/andrew_yos/pandas/pandas-repo
export RUN_DATA_DIR=/Volumes/2tb/pandas/run_data
export CSV_FILE=/Users/andrew_yos/pandas/macos_job_runs.csv
cd /Users/andrew_yos/pytest-super/pandas-eval
./run_plugin_comparison.sh --start 1 --end 17 --plugins "noplugin,testmon,ezmon,ezmon-nocov"
'
```

**Important**: The script uses micromamba which requires bash shell. Running from zsh will fail with "(eval):1: permission denied" errors.

## Files Modified for Checkpoint Fix

1. `/Users/andrew_yos/pytest-super/pytest-testmon-nocov/ezmon/testmon_core.py`
   - Lines 700-708: Added checkpoint deps in `_build_nodes_files_lines()`

2. `/Users/andrew_yos/pytest-super/pytest-testmon-nocov/ezmon/pytest_ezmon.py`
   - Lines 429-433: Workers save checkpoint on init

## Performance Profiling

See `docs/profiling-analysis.md` for detailed bottleneck analysis from the old `sys.modules` diffing approach.

**Note (2026-03-14):** The bottlenecks below are from the old approach and have been largely eliminated by the import hook refactor. The `str.startswith` (329M calls, 21.3s) and module set creation (33s) overhead no longer exists. Re-profiling is needed to identify current bottlenecks.

Previous bottlenecks (old approach):
1. sqlite3.executemany: 31.1s (15%) — still relevant
2. db.determine_tests: 29.4s (14%) — still relevant
3. str.startswith: 21.3s (329M calls) — **eliminated** by hook refactor
4. Module set creation: 33s total — **eliminated** by hook refactor
