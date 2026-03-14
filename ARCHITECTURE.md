# pytest-ezmon-nocov Architecture Documentation

**Version:** 3.0.0-nocov
**Fork of:** [pytest-testmon](https://github.com/tarpas/pytest-testmon)
**Python Support:** 3.7+ (we maintain compatibility with Python 3.7, unlike upstream which requires 3.10+)

## Overview

pytest-ezmon-nocov is a pytest plugin that automatically selects and re-executes only tests affected by recent code changes. Unlike the original testmon which uses coverage.py, **ezmon-nocov uses import-based tracking**:

1. **Import-based dependency tracking**: Hooks Python's import system to track which files each test imports
2. **AST-based fingerprinting**: Creates checksums of code blocks to detect meaningful changes
3. **Roaring bitmap storage**: Compact storage of test dependencies using compressed bitmaps
4. **Smart test selection**: Only runs tests whose dependencies have changed
5. **Git-aware file cache**: Uses git blob SHAs for tracked files and avoids repeated stat calls

## Core Approach: Import-Based Tracking

### Fundamental Principle

**No code in Python outside the current module can execute unless it is imported.**

This observation enables import-based test dependency tracking. If a test depends on code in `module_x.py`, then at some point during the test's lifecycle, `module_x` must be imported. By tracking imports, we identify all file dependencies.

### Comparison with Coverage-Based Tracking

| Aspect | ezmon-nocov (Import-Based) | Original testmon (Coverage-Based) |
|--------|----------------------------|-----------------------------------|
| Dependency detection | Import hooks | coverage.py contexts |
| Granularity | File-level | Line/function-level |
| First-run overhead | ~3x faster (no coverage) | Higher |
| False negatives | Never (conservative) | Possible (coverage limitations) |
| False positives | More (file-level) | Fewer (line-level) |

### Trade-offs

**Pros of Import-Based:**
- Faster test execution (no coverage.py overhead)
- No coverage context limitation bugs
- Simpler mental model
- More reliable tracking

**Cons of Import-Based:**
- Less precise: changes to any function in a file trigger all tests importing that file
- Conservative: may run tests that didn't actually use the changed code

## Core Concepts

### FileInfoCache

`FileInfoCache` provides a git-aware cache for file metadata and content fingerprints:
- Batch APIs for fetching git blob SHAs and checksums
- Realpath normalization to avoid duplicate work
- Optional parallel workers for checksum computation

This reduces repeated `stat` calls across large trees and avoids recomputing AST checksums for unchanged Python files.

#### Git State Tracking

The cache uses git commands to track file state:

```python
_head_shas    = git ls-tree -r HEAD        # blob SHAs at HEAD commit
_index_shas   = git ls-files -s            # blob SHAs in staging area
_modified     = git diff --name-only       # files changed in working tree vs index
```

The design principle: **we only consider files as they are in the current HEAD commit**. Local uncommitted edits are ignored because we want to evaluate whether the previous test run had the same base to run tests.

#### Optimization: Avoiding Disk Reads

For files that are NOT modified locally (working tree == HEAD):
- **fsha**: Use `_head_shas` directly (no disk read needed)
- **source**: Reading from disk gives HEAD content

For files that ARE modified locally (working tree != HEAD):
- **fsha**: Use `_head_shas` (we want HEAD state, not local edits)
- **source**: Must read from git or use cached HEAD content

The `get_source_and_fsha()` method implements this optimization by returning `source=None` for unmodified files when only the fsha is needed (e.g., for `batch_get_fshas`). This avoids unnecessary disk I/O for thousands of files.

#### Checksum Computation

The `batch_get_checksums()` method computes AST-based checksums for Python files. Since checksums require the actual source code, this method handles the case where `get_source_and_fsha()` returns `source=None`:

```python
def compute_one(norm: str) -> Tuple[str, Optional[int]]:
    source, fsha, mtime = self.get_source_and_fsha(norm)

    # For unmodified files, get_source_and_fsha returns source=None as an
    # optimization. We need the actual source to compute the checksum, so
    # read the file directly. This is safe because unmodified means disk == HEAD.
    if source is None:
        content = self._read_file(norm)
        if content is None:
            return norm, None
        source = content.source
        # ... cache and compute checksum
```

This ensures checksums are always computed correctly while preserving the disk I/O optimization for operations that don't need source content.

#### Performance Caches

Two additional caches reduce repeated lookups:
- `_norm_cache`: Caches path normalization results
- `_is_tracked_cache`: Caches git tracking status

Both use `try/except KeyError` pattern for fast cache hits (single hash lookup vs two lookups for `if key in dict`).

### File Fingerprints

Each Python file gets a single **AST-based checksum**:

1. Parse source code into AST (comments are automatically excluded)
2. Strip docstrings from the AST
3. Compute CRC32 checksum of the normalized AST dump

**What affects fingerprints:**
| Change Type | Affects Fingerprint | Triggers Tests |
|-------------|---------------------|----------------|
| `# comment` lines | No (stripped by AST parser) | No |
| `"""docstring"""` | No (explicitly stripped) | No |
| Code logic changes | Yes | Yes |
| Function signature | Yes | Yes |
| Import statements | Yes | Yes |

### Test Dependencies

Each test has a set of file dependencies tracked as a **Roaring bitmap**:

```
Test "tests/test_calculator.py::TestCalculator::test_add":
  Dependencies (file IDs): [1, 5, 10, 23]
    └─ 1 = tests/test_calculator.py
    └─ 5 = src/calculator.py
    └─ 10 = src/math_utils.py
    └─ 23 = src/__init__.py
```

When any of these files change, the test is marked as affected.

External package dependencies are tracked by **package name + version** when available, and stored alongside the bitmap.

### Dependency Phases

Dependency tracking is event-driven with checkpoint deltas:

```
collect-file-start(test_file):
    - set global checkpoint once (lazy)
    - activate file collection context
    - for later files, restore sys.modules to global checkpoint

test-start(test_id, test_file):
    - set file checkpoint once per file (lazy)
    - for later tests in that file, restore sys.modules to global+file

test-end(test_id):
    - compute test-unique module/read deltas over global+file
```

Dependencies persisted for each test are:
`global + file + test_unique`.

Checkpoints include both:
1. module keys (`sys.modules`)
2. file-read sets (non-Python reads)

## Roaring Bitmap Storage

### Why Roaring Bitmaps?

Traditional approach uses junction tables:
```sql
test_execution_file_fp (test_id, file_id)  -- 8 bytes per dependency
```

For 1000 tests × 50 dependencies = 50,000 rows = ~400KB

Roaring bitmap approach:
```sql
test_deps (test_id, bitmap_blob)  -- ~50-200 bytes per test
```

For 1000 tests = ~100-200KB (4x smaller, faster queries)

### Data Structures

```python
@dataclass
class TestDeps:
    """Test dependencies stored as a Roaring bitmap."""
    test_id: int
    file_ids: BitMap  # Roaring bitmap of file IDs
    external_packages: Set[str]  # e.g., {"numpy==2.2.1", "pandas==2.1.4"}

    def serialize(self) -> bytes:
        """Serialize with zstd compression for database storage."""
        raw_bytes = self.file_ids.serialize()
        return zstd.compress(raw_bytes)

    def depends_on_any(self, changed_ids: Set[int]) -> bool:
        """Fast bitmap intersection to check if affected."""
        return bool(self.file_ids & BitMap(changed_ids))
```

### Fallbacks

- **pyroaring not available**: Pure Python set-based BitMap fallback
- **zstandard not available**: gzip compression fallback

## Database Schema (v18)

### Core Tables

```sql
-- Unified file registry with stable integer IDs
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
    name TEXT NOT NULL UNIQUE,
    duration REAL,
    failed INTEGER DEFAULT 0
);

-- Test dependencies (Roaring bitmap, zstd compressed)
CREATE TABLE test_deps (
    test_id INTEGER PRIMARY KEY,
    file_bitmap BLOB NOT NULL,     -- Roaring bitmap of file IDs
    external_packages TEXT,        -- Comma-separated: "numpy,pandas"
    FOREIGN KEY(test_id) REFERENCES tests(id)
);
```

### Previous Schema Tables (Kept for Compatibility)

The old schema tables are retained during transition:
- `file_fp` - File fingerprints with checksums
- `test_execution` - Test execution records
- `test_execution_file_fp` - Junction table

## Project Structure

```
pytest-ezmon-nocov/
├── ezmon/                      # Main plugin code
│   ├── __init__.py            # Version: 3.0.0-nocov
│   ├── pytest_ezmon.py        # Pytest plugin hooks, test selection
│   ├── testmon_core.py        # Core fingerprinting and collection
│   ├── file_cache.py          # Git-aware file info cache
│   ├── db.py                  # SQLite database schema and operations
│   ├── bitmap_deps.py         # Roaring bitmap storage (NEW)
│   ├── dependency_tracker.py  # Import and file dependency tracking
│   ├── process_code.py        # AST parsing, fingerprint generation
│   ├── configure.py           # Configuration and decision logic
│   ├── common.py              # Shared utilities and type definitions
│   ├── server_sync.py         # Server synchronization for distributed CI
│   ├── net_db.py              # Network database client
│   └── graph.py               # Dependency graph generation
├── ez-viz/                    # Frontend visualization server (Flask)
├── integration_tests/         # Scenario-based integration tests
└── tests/                     # Unit tests
    ├── test_db.py             # Database operations and change detection
    ├── test_process_code.py   # AST fingerprinting and checksums
    └── test_file_cache_checksum.py  # FileInfoCache checksum computation
```

## Key Classes

### Core Domain

| Class | File | Responsibility |
|-------|------|----------------|
| `Module` | process_code.py | AST parsing, checksum generation |
| `TestDeps` | bitmap_deps.py | Roaring bitmap test dependencies |
| `FileRecord` | bitmap_deps.py | File entry with stable ID |
| `FileInfoCache` | file_cache.py | Git-aware file info cache |
| `TestmonData` | testmon_core.py | Orchestrates stability analysis |
| `DependencyTracker` | dependency_tracker.py | Import/file tracking hooks |
| `CheckpointManager` | dependency_tracker.py | Checkpoint state management |
| `DB` | db.py | SQLite database operations |
| `NetDB` | net_db.py | Network-based DB for CI |

### Pytest Plugin

| Class | File | Responsibility |
|-------|------|----------------|
| `TestmonCollect` | pytest_ezmon.py | Collection-time dependency tracking |
| `TestmonSelect` | pytest_ezmon.py | Test selection/deselection |
| `TestmonXdistSync` | pytest_ezmon.py | xdist parallel execution support |

## Dependency Tracking Details

### Import Hook: Zero-Processing Approach

The `DependencyTracker` hooks `builtins.__import__` to intercept all imports. The hook records raw data with zero processing in the hot path:

```python
def _hook(self, name, globals=None, locals=None, fromlist=(), level=0):
    result = self._original_import(name, globals, locals, fromlist, level)
    fl = tuple(fromlist) if fromlist is not None else None
    self._current[name].add(fl)
    self._current[result.__name__].add(fl)
    return result
```

Two keys are recorded per import: the raw `name` (captures dotted paths like `mypkg.utils`) and `result.__name__` (captures absolute names for relative imports). Both are stored with their associated `fromlist` tuples. All path resolution, deduplication, and classification is deferred to reconciliation time.

The production hook also handles rehydration (pre-populating `sys.modules` from a permanent cache before calling `_original_import`) and wraps `importlib.import_module` separately since it bypasses `builtins.__import__`.

See `docs/checkpoint-import-tracking.md` for the full design rationale, the three approaches that were tried, and how each failure informed the current design.

### Reconciliation: Deferred Processing

After a test completes, reconciliation converts raw recorded data to file paths:

1. **Prefix expansion**: For `"mypkg.sub.deep"`, resolves `mypkg`, `mypkg.sub`, and `mypkg.sub.deep` — capturing all intermediate `__init__.py` files
2. **Fromlist expansion**: For `from mypkg.models import Product`, tries `mypkg.models.Product` as a submodule (fails), then traces `Product.__module__` = `"mypkg.models.product"` → `models/product.py`
3. **Project root filtering**: Rejects paths outside the project root (stdlib, third-party)

### Class Import Tracking

When a class is imported via package re-export (e.g., `from pandas import Series`), the reconciliation step traces `Series.__module__` to find the defining module file:

```python
# Series.__module__ = 'pandas.core.series'
# Reconciliation resolves this to pandas/core/series.py
```

This happens at reconciliation time (not in the hook), so the defining module is always available in `sys.modules`. Earlier approaches that attempted this resolution in the hook itself ran into issues where checkpoint restore had removed the defining module from `sys.modules` — see `docs/class-import-tracking.md` for the full history.

### File Dependency Tracking

Non-Python files (JSON, YAML, etc.) are tracked when read. The tracker hooks both `builtins.open` and `io.open`:

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

## Test Selection Flow

```
1. pytest --ezmon (first run)
   ├─ Capture current HEAD commit ID
   ├─ Build expected file/package sets from HEAD
   ├─ Install import/open hooks (filtered to expected sets)
   ├─ For each test file:
   │   ├─ Track imports/reads during collection
   │   ├─ Track imports/reads during execution
   │   └─ Save dependencies as Roaring bitmap
   └─ Store in .testmondata SQLite database (including commit_id)

2. Code changes occur (committed)

3. pytest --ezmon (subsequent run)
   ├─ Load .testmondata database
   ├─ Diff last-run commit → HEAD (ignore dirty working tree)
   ├─ Identify git_new/git_mod/git_del
   ├─ Recompute AST checksums only for tracked .py files in git_mod/git_del
   ├─ Build git_affected (meaningful changes)
   ├─ expected_imports/reads = all git-tracked files at HEAD (never filter out real imports/reads)
   ├─ Compute pack_affecting (removed/changed packages)
   ├─ Bitmap intersection: tests depending on git_affected or pack_affecting
   ├─ Add previously failing tests → min_selected_tests
   ├─ Derive min_collected_files from min_selected_tests
   ├─ Ignore all other known test files at collection time
   ├─ Run only selected tests
   └─ Update dependencies in database
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--ezmon` | Enable test selection and dependency collection |
| `--ezmon-noselect` | Reorder tests by failure likelihood, but don't deselect |
| `--ezmon-nocollect` | Selection only, no dependency collection |
| `--ezmon-forceselect` | Force selection even with pytest selectors (-k, -m) |
| `--no-ezmon` | Disable ezmon completely |
| `--ezmon-env` | Separate dependency data for different environments |
| `--ezmon-graph` | Generate interactive dependency graph |

## NetDB Architecture (Server Communication)

For CI environments, ezmon-nocov supports direct server communication:

```
┌─────────────────┐         HTTPS/REST          ┌──────────────────┐
│  pytest-ezmon   │◄──────────────────────────►│   Flask Server   │
│    (NetDB)      │   JSON (gzip compressed)    │   (ez-viz/app)   │
└─────────────────┘                             └────────┬─────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────┐
                                                │  SQLite per job  │
                                                └──────────────────┘
```

Enable with:
```bash
TESTMON_NET_ENABLED=true
TESTMON_SERVER=https://your-server.com
REPO_ID=owner/repo
JOB_ID=test-py311
```

If `TESTMON_SERVER` points at the ezmon public backend, the plugin stays local and does not make network calls.

## Parallel Execution (pytest-xdist)

ezmon-nocov fully supports parallel test execution:

1. **Controller** computes selection once before workers spawn
2. **Controller** passes selection + expected file/package sets via `workerinput`
3. **Workers** avoid loading the database and skip recomputation
4. **Workers** prune collection using an explicit no-collect file list
5. **Workers** track deps only (no SHA/checksum computation)
6. **Controller** computes checksums and writes dependency updates

Detailed flow:
- Startup: controller loads the database, diffs last run commit to HEAD, and builds the selected test set.
- Worker bootstrap: selection data + expected tracking sets are injected into `workerinput`; workers do not compute SHAs or checksums.
- Collection: workers use `pytest_ignore_collect` to skip explicitly non-collected files.
- Execution: dependency tracking runs only for collected tests; collection-time dependencies are merged into per-test data.
- Finish: worker reports are sent to the controller; the controller resolves SHAs/checksums and writes final dependency updates.

### Worker Payload Encoding

To reduce xdist overhead, workers encode dependency payloads in two ways:

1) **Deterministic encoding for files + packages**
- The controller builds deterministic code maps:
  - git-tracked files are encoded using directory-ordered, stable indices
  - packages are encoded by sorted package name
- These maps are shared with workers via `workerinput`.
- Workers send only integer IDs for dependency paths and package names.
- The controller decodes IDs back to paths/packages when writing fingerprints.

2) **Per-file test-name prefix encoding**
- Each test file payload includes a `pm` prefix map (list of class/param prefixes).
- Test suffixes are encoded as `prefix_id|last_part`, where `last_part` is the final `::` segment.
- Prefix IDs are 1-based indexes into `pm` (id 1 maps to `pm[0]`); id 0 means no prefix.
- The controller expands this into full test suffixes and reattaches the test file.

Payload grouping:
- Dependencies are grouped by test file.
- Each file payload has:
  - `com`: deps shared by all tests in the file
  - `t_names`: list of encoded test suffixes
  - `etc`: list of indices into `t_names` for tests with no unique deps
  - `dur`: list of durations aligned to `t_names`
  - `fail`: list of indices into `t_names` for failed tests
  - per-test entries only when unique deps exist, keyed by index
- Workers send batches of **up to 5 test files** per payload.

This preserves full per-test dependency information while shrinking payloads significantly.

### Payload Capture (optional)

To capture xdist worker communications, set `EZMON_WORKER_PAYLOAD_DIR`:

```bash
EZMON_WORKER_PAYLOAD_DIR=/tmp/ezmon_payloads pytest --ezmon -n auto ...
```

Behavior:
- Each worker writes `received.json` (workerinput) to its own subdir.
- The controller writes `sent_N.json` per batch to each worker directory.
- To disable capture, unset `EZMON_WORKER_PAYLOAD_DIR`.

### Xdist Timing Trace (optional)

To capture timing events for worker/controller communication and batching, set
`EZMON_XDIST_TIMING_LOG_DIR`:

```bash
EZMON_XDIST_TIMING_LOG_DIR=/tmp/ezmon_timing pytest --ezmon -n auto ...
```

Behavior:
- JSONL files are written per actor:
  - `controller.jsonl`
  - `gw0.jsonl`, `gw1.jsonl`, ...
- Events include:
  - worker first control (`worker_start`)
  - receive start/end (`worker_received_start`, `worker_received_end`)
  - batch start/end (`worker_batch_start`, `worker_batch_end`)
  - send start/end (`worker_send_start`, `worker_send_end`)
  - worker end (`worker_end`)
  - controller send start/end (`controller_send_start`, `controller_send_end`)
  - controller receive/batch start/end (`controller_receive_start`, `controller_batch_start`, `controller_batch_end`, `controller_receive_end`)
- To disable trace logging, unset `EZMON_XDIST_TIMING_LOG_DIR`.


```bash
pytest --ezmon -n auto  # Parallel execution
```

## Integration Tests

24 scenario-based integration tests verify correct behavior:

| Category | Scenarios |
|----------|-----------|
| Basic changes | modify_math_utils, modify_calculator_only, etc. |
| No changes | no_changes, comment_only_change, docstring_only_change |
| Complex patterns | nested classes, generators, decorators, context managers |
| Class imports | from_package_import_class, from_package_import_class_product |
| File deps | modify_config_file |
| Module-level | modify_import_only_module_level, modify_globals |

Run with:
```bash
python integration_tests/run_integration_tests.py
```

## Differences from Upstream testmon

| Feature | ezmon-nocov | Original testmon |
|---------|-------------|------------------|
| Dependency tracking | Import hooks | coverage.py |
| Granularity | File-level | Line-level |
| Storage | Roaring bitmaps | Junction tables |
| First-run speed | ~3x faster | Slower (coverage overhead) |
| Package name | `pytest-ezmon-nocov` | `pytest-testmon` |
| Python support | 3.7+ | 3.10+ |
