# File Dependency Tracking

This document describes how pytest-ezmon tracks non-Python file dependencies (JSON, YAML, config files, etc.) and triggers test re-runs when these files change.

## Overview

pytest-ezmon tracks three types of dependencies for each test:

1. **Local Python modules** - Project's own `.py` files that are imported
2. **External Python packages** - Third-party packages (requests, numpy, etc.)
3. **File dependencies** - Non-Python files (JSON, YAML, config files, etc.) that are read during tests

This document focuses on **file dependencies** - how they are tracked, stored, and used for test selection.

## How File Dependencies Are Tracked

### Mechanism

File dependencies are tracked by hooking `builtins.open()` at runtime. When any file is opened for reading, the hook checks if it should be tracked.

**Key file:** `ezmon/dependency_tracker.py`

```
DependencyTracker._tracking_open()  # Hook replaces builtins.open
    └── _track_file()               # Decides if file should be tracked
        ├── Check: is read operation? ('r' in mode)
        ├── Check: is file in project? (_is_in_project)
        ├── Check: is NOT a .py file? (Python files use import tracking)
        └── Check: is file committed to git? (_get_committed_file_sha)
```

### Two Tracking Modes

#### 1. Collection-Time Tracking (Import Time)

**Added in this version.** Captures file reads that happen during module initialization, before tests execute.

```
pytest_configure
    └── TestmonCollect.__init__
        └── dependency_tracker.start_collection_tracking()  # Install hooks early

pytest_collectstart (for each test file)
    └── dependency_tracker.set_collection_context(test_file)
        # Any file reads during import are now associated with this test file

pytest_collection_modifyitems
    └── dependency_tracker.stop_collection_tracking()
        # Returns: {test_file: set of TrackedFile}
```

**Example:** When `tests/test_a.py` imports `mylib`, and `mylib/__init__.py` reads `config.json`, that file read is associated with `tests/test_a.py`.

#### 2. Test Execution Tracking (Runtime)

Captures file reads that happen during actual test execution.

```
pytest_runtest_protocol (for each test)
    └── dependency_tracker.start(test_nodeid)
        # Any file reads during test execution are associated with this test

pytest_runtest_makereport
    └── dependency_tracker.stop()
        # Returns: (files, local_imports, external_imports)
```

### Data Flow

```
Test Collection:
    test_a.py imported → mylib.__init__ reads config.json
                         └── Tracked in _collection_file_deps["tests/test_a.py"]

Test Execution:
    test_a.py::test_func runs → function reads data.yaml
                                └── Tracked in _tracked_files["tests/test_a.py::test_func"]

Merge & Save:
    _merge_collection_file_deps() combines both into nodes_files_lines
    └── Saved to database via get_tests_fingerprints() + save_test_execution_file_fps()
```

## What Files Are Tracked

A file read is tracked if ALL of the following are true:

| Check | Purpose |
|-------|---------|
| `'r' in mode or '+' in mode` | Only track read operations, not writes |
| `_is_in_project(filepath)` | Only track files inside the project root |
| `not relpath.endswith('.py')` | Skip Python files (handled by import tracking) |
| `_get_committed_file_sha(relpath)` returns SHA | Only track files committed to git |

### Why Only Committed Files?

The plugin uses `git ls-tree HEAD` to get the file's SHA. This ensures:

1. **Ephemeral files are ignored** - Temp files, build artifacts, cache files
2. **Reproducible tracking** - Tests depend on committed state, not local modifications
3. **Efficient change detection** - Compare git SHAs instead of file contents

## Database Storage

### Schema

No schema changes were made. The existing tables are used:

```sql
-- Stores unique (filename, sha) pairs
CREATE TABLE file_dependency (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    sha TEXT NOT NULL,
    UNIQUE (filename, sha)
);

-- Links tests to their file dependencies (many-to-many)
CREATE TABLE test_execution_file_dependency (
    test_execution_id INTEGER,
    file_dependency_id INTEGER,
    FOREIGN KEY(test_execution_id) REFERENCES test_execution(id) ON DELETE CASCADE,
    FOREIGN KEY(file_dependency_id) REFERENCES file_dependency(id)
);
```

### Backward Compatibility

**No migration needed.** The changes are purely in the tracking logic:

- Same database tables are used
- Same data format is stored
- Existing `.testmondata` files work without modification
- Old data remains valid; new runs will capture more dependencies

### Impact on Existing Data

| Scenario | Behavior |
|----------|----------|
| Existing database, no new runs | Works as before, no changes |
| Existing database, new run | New file deps are added alongside existing data |
| New database | Collection-time deps captured from first run |

## Change Detection

When determining which tests to run, ezmon checks if any file dependencies have changed:

```python
# In db.py:_check_file_dependency_changes()
for each (test_name, filename, stored_sha) in database:
    current_sha = git_ls_tree_HEAD(filename)
    if current_sha != stored_sha:
        mark test as affected
```

## Observations and Pitfalls

### 1. Python Module Caching

**Issue:** Python caches modules. Once `mylib` is imported, its `__init__.py` won't re-run.

**Solution:** Collection-time tracking captures the first import. All tests in that test file share the dependency.

### 2. Timing of Hook Installation

**Issue:** Hooks must be installed BEFORE test collection starts, otherwise import-time file reads are missed.

**Solution:** `start_collection_tracking()` is called in `TestmonCollect.__init__`, which happens during `pytest_configure` → `register_plugins`.

### 3. Collection Context Association

**Issue:** During collection, multiple test files may be processed. We need to associate file reads with the correct test file.

**Solution:** `pytest_collectstart` hook fires for each collector. We set the context when a Module (test file) collector starts.

### 4. File Path Resolution

**Issue:** File paths during import may be absolute or relative, and may point to installed package locations.

**Solution:** `_is_in_project()` resolves absolute paths and checks if they're within the project root. Editable installs (`pip install -e .`) work because files remain in the project directory.

### 5. Git Requirement

**Issue:** File dependency tracking requires git to be initialized and files to be committed.

**Workaround:** If git is not available or file is not committed, the file read is simply not tracked (fails silently).

### 6. Hook Check Bug (Fixed)

**Issue:** The original `_tracking_open()` only checked `self._active`, which is only set during test execution, not collection.

**Fix:** Changed to check both modes:
```python
should_track = self._active or (self._collection_mode and self._collection_context)
```

## Testing

The integration test for this feature is at:

```
integration_tests/test_file_dependency_indirect.py
```

It verifies:
1. A project where `__init__.py` reads a config file at import time
2. The config file is recorded as a dependency
3. Changing the config file triggers test re-runs

Run it with:
```bash
python integration_tests/test_file_dependency_indirect.py
```

## Implementation Files

| File | Changes |
|------|---------|
| `ezmon/dependency_tracker.py` | Added collection-time tracking state and methods |
| `ezmon/pytest_ezmon.py` | Added `pytest_collectstart` hook, updated `TestmonCollect` |
| `integration_tests/test_file_dependency_indirect.py` | New test for import-time file reads |

### Key Code Locations

| Function | File:Line | Purpose |
|----------|-----------|---------|
| `start_collection_tracking()` | `dependency_tracker.py:610` | Start tracking before collection |
| `set_collection_context()` | `dependency_tracker.py:623` | Set current test file being collected |
| `stop_collection_tracking()` | `dependency_tracker.py:644` | Stop and return collected deps |
| `_tracking_open()` | `dependency_tracker.py:363` | Hook that intercepts file opens |
| `_track_file()` | `dependency_tracker.py:281` | Decides if file should be tracked |
| `pytest_collectstart` | `pytest_ezmon.py:419` | Sets collection context per test file |
| `_merge_collection_file_deps()` | `pytest_ezmon.py:504` | Merges collection deps into test data |

## Limitations

1. **Dynamic file paths** - If a file path is computed at runtime and not predictable, changes may not be detected on the first run after the path changes.

2. **Files outside project** - Files outside the project root are never tracked.

3. **Uncommitted files** - Uncommitted files are not tracked. Commit files before expecting them to be tracked.

4. **Binary files** - Binary files that are read (images, etc.) are tracked the same way as text files - by their git SHA.

5. **Network/remote files** - Only local file reads via `open()` are tracked. Network requests, database queries, etc. are not tracked.
