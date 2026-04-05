# Ezmon-nocov Fingerprinting System

This document explains how ezmon-nocov tracks test dependencies and determines which tests to run when code changes.

## Overview

Ezmon-nocov uses **import-based dependency tracking** combined with **AST-based fingerprinting**:

1. **Import tracking**: Hook `builtins.__import__` to track which files each test imports
2. **AST fingerprinting**: Compute checksums of file ASTs to detect meaningful changes
3. **File-level granularity**: A test depends on entire files, not individual functions
4. **External packages**: Track package names and select based on package version changes between runs
5. **Committed changes only**: Selection compares the last run commit to `HEAD` (dirty working tree ignored)

## How It Works

### 1. Import Tracking

When a test runs, ezmon hooks `builtins.__import__` to record raw import events with zero processing:

```python
def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
    result = self._original_import(name, globals, locals, fromlist, level)
    fl = tuple(fromlist) if fromlist is not None else None
    self._recording[name].add(fl)
    self._recording[result.__name__].add(fl)
    return result
```

The hook records `name` (what CPython asked for) and `result.__name__` (what Python resolved) into a `defaultdict(set)`. No path lookups or classification at import time. After the test completes, a single `_reconcile()` pass resolves all recorded module names to file paths via prefix expansion, fromlist expansion, and `__module__` tracing. See `docs/IMPORT_TRACKING_OPTIMIZATION.md` for the full design.

### 2. AST Fingerprinting

Each Python file gets a single checksum computed from its AST:

```python
def get_file_checksum(source_code):
    # Parse to AST (comments automatically excluded)
    tree = ast.parse(source_code)

    # Strip docstrings
    tree = strip_docstrings(tree)

    # Compute checksum
    return zlib.crc32(ast.dump(tree).encode())
```

**What affects checksums:**
| Change Type | Affects Checksum | Triggers Tests |
|-------------|------------------|----------------|
| Code logic changes | Yes | Yes |
| Function signatures | Yes | Yes |
| Import statements | Yes | Yes |
| `# comment` lines | No (stripped by AST) | No |
| `"""docstring"""` | No (explicitly stripped) | No |

### 3. Dependency Storage

Test dependencies are stored as **Roaring bitmaps** with zstd compression:

```python
@dataclass
class TestDeps:
    test_id: int
    file_ids: BitMap  # Roaring bitmap of file IDs
    external_packages: Set[str]  # e.g., {"numpy", "pandas"}

    def serialize(self) -> bytes:
        return zstd.compress(self.file_ids.serialize())

    def depends_on_any(self, changed_ids: Set[int]) -> bool:
        return bool(self.file_ids & BitMap(changed_ids))
```

## Tracking Phases

### Collection Phase

When pytest collects test files, ezmon tracks:
- **Imports**: Which modules are imported during collection (module-level imports)
- **File reads**: Which non-Python files are read

This data becomes the **baseline** for ALL tests in that test file.

### Execution Phase

When each test runs, ezmon tracks:
- **Imports**: Any additional imports during test execution (dynamic imports)
- **File reads**: Any files read during test execution

This data is **merged** with the collection baseline to form the complete dependency set.

### Expected Tracking Sets

Before collection begins, ezmon computes the **expected** files/packages for the run:
- `expected_imports`: all `.py` files at HEAD (git-tracked), so real imports are never filtered out
- `expected_reads`: all git-tracked files at HEAD, so real reads are never filtered out
- `expected_packages`: external package names that are expected to change (added/changed packages)

The dependency tracker ignores anything outside these sets to minimize overhead.

### Parallel Execution

In xdist mode, each worker runs its assigned tests locally and records dependencies
with the hook-based tracker. Workers send batches of `{test_name: {deps, file_deps,
external_deps}}` payloads to the controller using plain relative paths — no encoding
or integer-ID layer. Each batch uses a `file_common_unique_v2` compression: within a
test file, dependencies common to all its tests are factored out once and only the
per-test differences are serialized.

Workers attach batch data to test reports via `user_properties`. The controller's
`pytest_runtest_logreport` extracts each batch and calls `_handle_worker_output`,
which processes it immediately via `save_test_deps_raw` — no end-of-session queue
drain for the dependency data. Any residual batches are flushed via
`pytest_testnodedown` when a worker tears down. The DepStore in-memory cache (see
`docs/STATUS.md` → "DepStore: Unified In-Memory Cache") eliminates per-row DB
round-trips so batch processing stays fast even at pandas scale.

Workers filter out failed tests before building the wire payload. The controller
marks failed tests with `failed=1` in its own post-processing pass using the outcomes
it observes directly from the xdist results events (see "Fix: Failed Test Persistence
in xdist" in `STATUS.md`).

## Dependency Phase Model

The tracker uses explicit phases to attribute dependencies:

```
pytest starts
    |
    v
conftest.py loads
    |
    v
[GLOBAL TRACKING ACTIVE] -- base dependencies for ALL tests
    |
    v
For each test file:
    +-- [STOP GLOBAL TRACKING] (first file only)
    +-- [START FILE TRACKING]
    +-- Collect test file (imports/reads tracked)
    +-- [STOP FILE TRACKING]
    |
    v
For each test:
    +-- [START TEST TRACKING]
    +-- Run test (imports/reads tracked)
    +-- [STOP TEST TRACKING]
    v
Test deps = global + file + test dependencies
```

## When Code Changes

### File Content Changes

When we change `src/math_utils.py`:
1. AST checksum changes
2. All tests that import `math_utils.py` (directly or transitively) are selected

### Example

```python
# src/math_utils.py
def add(a, b):
    return a + b      # CHANGED to: return a + b + 0

def subtract(a, b):
    return a - b
```

**Tests affected:**
- ALL tests importing `src/math_utils.py` → SELECTED
- Tests NOT importing `src/math_utils.py` → NOT SELECTED

### Why File-Level Granularity?

Import-based tracking is inherently file-level:
- We know which files a test imports
- We don't know which specific functions within those files are called

This is a trade-off:
- **Pro**: No coverage.py overhead, faster execution
- **Pro**: No false negatives (conservative approach)
- **Con**: May run more tests than strictly necessary

## Class Import Tracking

When importing classes via package re-exports:

```python
# src/models/__init__.py
from src.models.user import User

# test_user.py
from src.models import User  # User.__module__ = 'src.models.user'
```

Ezmon tracks `src/models/user.py`, not just `src/models/__init__.py`, by checking the class's `__module__` attribute.

## Non-Python File Tracking

Ezmon also tracks non-Python files that tests read:

```python
def _tracking_open(self, file, mode='r', *args, **kwargs):
    result = self._original_open(file, mode, *args, **kwargs)
    if 'r' in mode:
        relpath = self._is_in_project(file)
        if relpath and not relpath.endswith('.py'):
            sha = self._get_committed_file_sha(relpath)
            if sha:  # Only track git-tracked files
                self._track_file(relpath, sha)
    return result
```

This enables tracking JSON, YAML, CSV, and other data files. Files that are not git-tracked are ignored.

## Summary

| Aspect | Description |
|--------|-------------|
| **Tracking method** | Import hooks (`builtins.__import__`) |
| **Granularity** | File-level |
| **Fingerprinting** | AST checksum (CRC32) |
| **Storage** | Roaring bitmaps + zstd |
| **Comments/docstrings** | Ignored (AST strips them) |
| **External packages** | Name + version where available |
| **False negatives** | None (conservative) |
| **False positives** | Possible (file-level, not function-level) |

## Comparison with Coverage-Based Tracking

| Aspect | Ezmon-nocov (Import-Based) | Original testmon (Coverage-Based) |
|--------|----------------------------|-----------------------------------|
| Dependency detection | Import hooks | coverage.py contexts |
| Granularity | File-level | Line/function-level |
| First-run overhead | ~3x faster | Higher |
| False negatives | Never | Possible (coverage limitations) |
| False positives | More (file-level) | Fewer (line-level) |
| Transitive imports | Fully tracked | Partial |
