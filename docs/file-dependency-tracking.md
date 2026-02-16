# Dependency Tracking

This document describes how pytest-ezmon tracks dependencies and triggers test re-runs when those dependencies change.

## Overview

pytest-ezmon tracks three types of dependencies for each test:

1. **Local Python modules** - Project's own `.py` files that are imported
2. **External Python packages** - Third-party packages (requests, numpy, etc.) tracked by name + version when available
3. **File dependencies** - Non-Python files (JSON, YAML, config files, etc.) that are read and git-tracked

Python file changes are detected via AST checksums with docstrings removed, so comment-only or docstring-only edits do not trigger reruns.

All dependency tracking is done at **runtime** by hooking into Python's import system and file I/O. This approach:
- Captures actual dependencies (not just static analysis)
- Handles dynamic imports (e.g., `importlib.import_module()`)
- Works with any import pattern

## Dependency Scopes

The tracker records dependencies in three scopes:

1. **Global scope**: anything discovered before per-file collection context starts
2. **Test-file scope**: anything newly discovered while collecting that file
3. **Test scope**: anything newly discovered while executing that test

Test-level dependencies are stored as deltas over the file baseline. This avoids
the previous checkpoint/restore workflow and significantly reduces import-tracking
overhead.

## How Dependencies Are Tracked

### The DependencyTracker

**Key file:** `ezmon/dependency_tracker.py`

The `DependencyTracker` class hooks into:
- `builtins.__import__` - Tracks module imports
- `importlib.import_module` - Tracks dynamic imports
- `builtins.open` - Tracks file reads
- `io.open` - Tracks file reads (common in libraries)

### Two Tracking Phases

#### 1. Collection-Time Tracking (Module Load Time)

Captures dependencies that occur when test files are imported during pytest collection.

```
pytest_configure
    └── TestmonCollect.__init__
        └── dependency_tracker.start_collection_tracking()  # Install hooks early

pytest_collectstart (for each test file)
    └── dependency_tracker.set_collection_context(test_file)
        # Any imports/file reads are now associated with this test file

pytest_collection_modifyitems
    └── dependency_tracker.stop_collection_tracking()
        # Returns: (file_deps, local_imports, external_imports) per test file
```

**What gets tracked:**
- Module imports when test files are loaded
- File reads in `__init__.py` or module-level code
- Transitive imports (if A imports B which imports C, all are captured)

**Granularity:** Per test **file** (not per individual test), because Python imports modules once and caches them.

Collection also captures **global scope** when hooks are active but no file context
is set yet (for example, conftest/bootstrap activity).

#### 2. Execution-Time Tracking (Test Runtime)

Captures dependencies that occur during actual test execution.

```
pytest_runtest_protocol (for each test)
    └── dependency_tracker.start(test_nodeid)
        # Any imports/file reads during execution are tracked

pytest_runtest_makereport
    └── dependency_tracker.stop()
        # Returns: (files, local_imports, external_imports) for this test
```

**What gets tracked:**
- Dynamic imports inside test functions
- File reads during test execution
- Any runtime dependencies

**Granularity:** Per individual **test**.
Recorded dependencies are test-level deltas over the file cumulative baseline.

### Data Flow

```
Test Collection (per test file):
    test_a.py imported
        → import mylib          → tracked in _collection_local_imports["tests/test_a.py"]
        → mylib reads config.json → tracked in _collection_file_deps["tests/test_a.py"]

Test Execution (per test):
    test_a.py::test_func runs
        → importlib.import_module("plugin") → tracked in _tracked_local_imports
        → open("data.yaml")                 → tracked in _tracked_files

Merge & Save:
    _merge_collection_deps() adds collection-time deps to each test in file
    _merge_tracked_deps() adds execution-time deps
    → Saved to database
```

## Import Tracking Details

### What Gets Tracked

| Import Type | Example | Tracked As |
|------------|---------|------------|
| Direct import | `import mylib` | Local module path |
| From import | `from mylib import foo` | Local module path |
| Submodule | `import mylib.utils` | Local module path |
| Dynamic | `importlib.import_module("mylib")` | Local module path |
| External | `import numpy` | External package name |
| Stdlib | `import os` | Not tracked |

### Local vs External Detection

A module is considered **local** if:
- Its `__file__` is within the project root directory
- Or it's detected as a locally-installed package (editable install)

A module is considered **external** if:
- It's installed (found via `importlib.util.find_spec`)
- It's not stdlib
- It's not a local package

External dependencies are stored with their version when it can be resolved from the package metadata.

### Hook Implementation

```python
def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
    result = self._original_import(name, globals, locals, fromlist, level)

    # Track in either collection mode or execution mode
    should_track = self._active or (self._collection_mode and self._collection_context)
    if should_track:
        self._track_import(result, name)
        # Also track submodules and parent packages...

    return result
```

## File Dependency Tracking Details

### What Gets Tracked

A file read is tracked if ALL conditions are met:

| Check | Purpose |
|-------|---------|
| `'r' in mode` | Only track read operations |
| `_is_in_project(filepath)` | Only track project files |
| `not relpath.endswith('.py')` | Skip Python files (use import tracking) |
| `relpath in expected_reads` | Only track files in the expected set for this run (expected_reads is all git-tracked files at HEAD) |

In parallel runs (xdist), workers report dependencies using integer IDs derived from
`expected_files_list` and `expected_packages_list` passed by the controller. The controller
decodes IDs back to paths/packages, resolves git SHAs, and computes checksums before
writing to the database.

### Why Only Committed Files?

Selection is based on **committed changes only**. The plugin diffs the last recorded commit to `HEAD` and builds expected file sets from those commits:
- Ephemeral files (temp, cache) are ignored
- Reproducible tracking based on committed state
- Efficient change detection via SHA comparison

### Change Detection Cache (FileInfoCache)

Change detection uses git diff between the last run commit and HEAD to find candidate changes, then recomputes AST checksums only for those modified tracked `.py` files. SHA/checksum computation happens once on the controller for each changed file.

#### How It Works

**Key design principle:** We only consider files as they are at `HEAD`. Local uncommitted edits are ignored because we compare the current commit against the database from a previous commit.

#### Disk I/O Optimization

For unmodified files (working tree == HEAD), `get_source_and_fsha()` can return `source=None` and uses the git blob SHA directly. This avoids disk reads when only the fsha is needed.

For checksum computation (which requires source code), the controller reads from `HEAD` when needed.

```python
source, fsha, mtime = self.get_source_and_fsha(norm)

# For unmodified files, source=None is an optimization.
# Read the file since we need source for checksum computation.
if source is None:
    content = self._read_file(norm)
    source = content.source
```

This ensures correct checksum computation while preserving the optimization for operations that don't need source content.

#### Performance Caches

Two additional caches reduce repeated lookups:
- `_norm_cache`: Caches path normalization (path → relative path)
- `_is_tracked_cache`: Caches git tracking status (path → bool)

Both use `try/except KeyError` for fast cache hits (single hash lookup).

## Database Storage

### Schema (v18 - Roaring Bitmaps)

```sql
-- Unified file registry (both Python and non-Python files)
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    checksum INTEGER,          -- AST checksum (Python) or content CRC32
    fsha TEXT,                 -- Git blob SHA for fast change detection
    file_type TEXT DEFAULT 'python'  -- 'python' or 'data'
);

-- Test records
CREATE TABLE tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment_id INTEGER,
    name TEXT NOT NULL,
    test_file TEXT,
    duration REAL,
    failed INTEGER DEFAULT 0,
    UNIQUE (environment_id, name),
    FOREIGN KEY(environment_id) REFERENCES environment(id)
);

-- Test dependencies (Roaring bitmap, zstd compressed)
CREATE TABLE test_deps (
    test_id INTEGER PRIMARY KEY,
    file_bitmap BLOB NOT NULL,     -- Roaring bitmap of file IDs
    external_packages TEXT,        -- Comma-separated: "numpy,pandas"
    FOREIGN KEY(test_id) REFERENCES tests(id)
);
```

### Storage Efficiency

Test dependencies are stored as **Roaring bitmaps** with zstd compression:
- ~50-200 bytes per test (vs ~400KB with junction tables for 1000 tests × 50 deps)
- Fast bitmap intersection for finding affected tests
- Pure Python fallback for environments without `pyroaring`

## Key Implementation Details

### Collection Context

During collection, we track which test **file** is being collected:

```python
@pytest.hookimpl(tryfirst=True)
def pytest_collectstart(self, collector):
    if hasattr(collector, 'path'):  # It's a Module collector
        test_file = relative_path(collector.path)
        self.dependency_tracker.set_collection_context(test_file)
```

This ensures imports during `test_a.py`'s collection are associated with `test_a.py`.

### Merging Collection and Execution Dependencies

Collection-time dependencies are per test **file**, but we need them per **test**:

```python
def _merge_collection_deps(self, nodes_files_lines):
    for test_nodeid, data in nodes_files_lines.items():
        # Extract test file from nodeid
        test_file = test_nodeid.split("::")[0]

        # Ensure payload structure exists
        data.setdefault("deps", set())
        data.setdefault("file_deps", set())
        data.setdefault("external_deps", set())

        # Add collection-time local imports as dependencies
        data["deps"].update(self._collection_local_imports.get(test_file, set()))

        # Add collection-time file deps
        for tracked_file in self._collection_file_deps.get(test_file, set()):
            data["file_deps"].add((tracked_file.path, tracked_file.sha))

        # Add collection-time external imports
        data["external_deps"].update(self._collection_external_imports.get(test_file, set()))
```

## Testing

### Integration Test

```bash
python integration_tests/test_file_dependency_indirect.py
```

This test verifies:
1. A project where `__init__.py` reads a config file at import time
2. The config file is recorded as a dependency
3. Changing the config file triggers test re-runs

### Unit Tests

```bash
pytest tests/ -v
```

Key test files:
- `tests/test_file_cache_checksum.py` - Tests for FileInfoCache checksum computation
- `tests/test_process_code.py` - Tests for AST fingerprinting and checksum computation
- `tests/test_db.py` - Tests for database operations and change detection

### Full Integration Suite

```bash
python integration_tests/run_integration_tests.py
python integration_tests/run_integration_tests.py --netdb  # NetDB mode
```

## Implementation Files

| File | Purpose |
|------|---------|
| `ezmon/dependency_tracker.py` | Core tracking logic (hooks, state management) |
| `ezmon/pytest_ezmon.py` | Pytest integration (hooks, merging) |
| `ezmon/testmon_core.py` | Dependency selection and fingerprinting |
| `ezmon/db.py` | Database storage |

### Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `start_collection_tracking()` | dependency_tracker.py | Start tracking before collection |
| `set_collection_context()` | dependency_tracker.py | Set current test file |
| `stop_collection_tracking()` | dependency_tracker.py | Stop and return collected deps |
| `_tracking_import()` | dependency_tracker.py | Hook for `builtins.__import__` |
| `_tracking_open()` | dependency_tracker.py | Hook for `builtins.open` |
| `_track_import()` | dependency_tracker.py | Process and store import |
| `_track_file()` | dependency_tracker.py | Process and store file read |
| `pytest_collectstart` | pytest_ezmon.py | Set collection context |
| `_merge_collection_deps()` | pytest_ezmon.py | Merge collection deps into test data |

## Limitations

1. **Per-file granularity for collection-time deps** - All tests in a file share the same collection-time dependencies (Python imports modules once).

2. **Git requirement for file deps** - File dependency tracking requires git. Untracked files are not tracked.

3. **Project boundary** - Only files within the project root are tracked.

4. **No network tracking** - Network requests, database queries, etc. are not tracked.

5. **Stdlib not tracked** - Standard library modules are not tracked as dependencies.

## Note on Import Tracking

Runtime tracking is used because it captures dynamic imports and avoids the overhead of static analysis over large codebases.
