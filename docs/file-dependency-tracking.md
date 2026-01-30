# Dependency Tracking

This document describes how pytest-ezmon tracks dependencies and triggers test re-runs when those dependencies change.

## Overview

pytest-ezmon tracks three types of dependencies for each test:

1. **Local Python modules** - Project's own `.py` files that are imported
2. **External Python packages** - Third-party packages (requests, numpy, etc.)
3. **File dependencies** - Non-Python files (JSON, YAML, config files, etc.) that are read

All dependency tracking is done at **runtime** by hooking into Python's import system and file I/O. This approach:
- Captures actual dependencies (not just static analysis)
- Handles dynamic imports (e.g., `importlib.import_module()`)
- Works with any import pattern

## How Dependencies Are Tracked

### The DependencyTracker

**Key file:** `ezmon/dependency_tracker.py`

The `DependencyTracker` class hooks into:
- `builtins.__import__` - Tracks module imports
- `importlib.import_module` - Tracks dynamic imports
- `builtins.open` - Tracks file reads

### Two Tracking Modes

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
| `_get_committed_file_sha(relpath)` | Only track git-committed files |

### Why Only Committed Files?

The plugin uses `git ls-tree HEAD` to get file SHAs:
- Ephemeral files (temp, cache) are ignored
- Reproducible tracking based on committed state
- Efficient change detection via SHA comparison

## Database Storage

### Schema

```sql
-- Local module fingerprints (existing)
CREATE TABLE file_fp (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    fingerprint BLOB,
    mtime REAL,
    UNIQUE (filename)
);

-- File dependencies (non-Python files)
CREATE TABLE file_dependency (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    sha TEXT NOT NULL,
    UNIQUE (filename, sha)
);

-- Links tests to file dependencies
CREATE TABLE test_execution_file_dependency (
    test_execution_id INTEGER,
    file_dependency_id INTEGER,
    FOREIGN KEY(test_execution_id) REFERENCES test_execution(id),
    FOREIGN KEY(file_dependency_id) REFERENCES file_dependency(id)
);

-- External package dependencies
CREATE TABLE test_external_dependency (
    id INTEGER PRIMARY KEY,
    test_execution_id INTEGER,
    package_name TEXT NOT NULL,
    package_version TEXT,
    FOREIGN KEY(test_execution_id) REFERENCES test_execution(id)
);
```

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

        # Add collection-time local imports as module dependencies
        for module_path in self._collection_local_imports.get(test_file, set()):
            if module_path not in data:
                data[module_path] = {0}  # Line 0 = module-level

        # Add collection-time file deps
        for tracked_file in self._collection_file_deps.get(test_file, set()):
            data[f"__file_deps__{test_nodeid}"].add((tracked_file.path, tracked_file.sha))

        # Add collection-time external imports
        data[f"__external_deps__{test_nodeid}"].update(
            self._collection_external_imports.get(test_file, set())
        )
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
| `ezmon/testmon_core.py` | Coverage data processing |
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

2. **Git requirement for file deps** - File dependency tracking requires git. Uncommitted files are not tracked.

3. **Project boundary** - Only files within the project root are tracked.

4. **No network tracking** - Network requests, database queries, etc. are not tracked.

5. **Stdlib not tracked** - Standard library modules are not tracked as dependencies.

## Historical Note

Previous versions used AST parsing to find imports statically. This was replaced with runtime tracking because:
- AST parsing was slow for large codebases (caused timeouts)
- AST parsing missed dynamic imports
- Runtime tracking captures actual dependencies
