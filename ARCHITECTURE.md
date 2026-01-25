# pytest-ezmon Architecture Documentation

**Version:** 2.1.4-a2
**Fork of:** [pytest-testmon](https://github.com/tarpas/pytest-testmon)
**Python Support:** 3.7+ (we maintain compatibility with Python 3.7, unlike upstream which requires 3.10+)

## Overview

pytest-ezmon is a pytest plugin that automatically selects and re-executes only tests affected by recent code changes. It achieves this through:

1. **Coverage-based dependency tracking**: Uses `coverage.py` to track which lines of code each test executes
2. **Fingerprint-based change detection**: Creates checksums of code blocks to detect meaningful changes
3. **Smart test selection**: Only runs tests whose dependencies have changed

## Core Concepts

### Code Blocks

A **code block** is a unit of code that ezmon tracks for changes. There are two types:

1. **Module-level block**: The entire Python file with function/method bodies stripped out (imports, class definitions, module-level statements)
2. **Function/Method body block**: The body of each function or method

This distinction exists because of Python's dynamic nature. When the interpreter encounters a function definition, we can safely assume the function body will NOT be executed at definition time. So we only need to track that block if a test actually executes code within it.

### Fingerprints

Each test has a **fingerprint per Python module** it used during execution. A fingerprint is a collection of CRC32 checksums representing the code blocks within that module that the test depends on.

**Fingerprint structure (per test, per module):**
```
Test → Module Fingerprint = [checksum1, checksum2, ...]
  where checksums include:
  - Module-level checksum: The entire file with function/method bodies stripped
    (imports, class definitions, module-level statements, function signatures)
  - Method/function body checksums: One for each function/method the test executed
    (as reported by coverage.py)
```

**Example:** `test_calculator.py::TestCalculator::test_add` might have:
```
Fingerprints:
  src/calculator.py: [3477891697, 2557190835, 2213528436]
    └─ 3477891697 = module-level (class Calculator definition, imports)
    └─ 2557190835 = Calculator.__init__() body
    └─ 2213528436 = Calculator.calculate() body

  src/math_utils.py: [86328506, 377580465]
    └─ 86328506 = module-level (function definitions)
    └─ 377580465 = add() body

  tests/test_calculator.py: [291892047, 3281913381]
    └─ 291892047 = module-level (imports, class TestCalculator)
    └─ 3281913381 = test_add() body
```

**Fingerprint creation process:**
```
Source File → AST Parse → Extract Blocks → For each block:
  1. Strip comment lines
  2. Calculate CRC32(code_content)
  3. Store (start_line, end_line, checksum, function_name)
```

### Test Selection Flow

```
1. pytest --ezmon (first run)
   ├─ Coverage initialized for all tests
   ├─ Each test: coverage.switch_context(test_name)
   ├─ Extract covered lines per file
   ├─ Create fingerprints for each test
   ├─ Store in .testmondata SQLite database
   └─ Ready for incremental runs

2. Code changes occur

3. pytest --ezmon (subsequent run)
   ├─ Load .testmondata database
   ├─ Calculate SHA1 hashes of all project files
   ├─ Query DB: which files have changed SHA?
   ├─ For changed files: extract method checksums
   ├─ Query DB: which tests depend on changed methods?
   ├─ Mark affected tests as UNSTABLE
   ├─ Mark other tests as STABLE
   ├─ Deselect stable test files
   ├─ Run only affected tests (+ always_run, prioritized)
   ├─ Collect new coverage data
   └─ Update fingerprints in database
```

## Project Structure

```
pytest-testmon/
├── ezmon/                      # Main plugin code
│   ├── __init__.py            # Version: TESTMON_VERSION
│   ├── pytest_ezmon.py        # Pytest plugin hooks, test selection
│   ├── testmon_core.py        # Core fingerprinting and coverage collection
│   ├── db.py                  # SQLite database schema and operations
│   ├── process_code.py        # AST parsing, fingerprint generation
│   ├── configure.py           # Configuration and decision logic
│   ├── common.py              # Shared utilities and type definitions
│   ├── server_sync.py         # Server synchronization for distributed CI
│   ├── dependency_tracker.py  # Import and file dependency tracking
│   ├── net_db.py              # Network database client for server communication
│   └── graph.py               # Dependency graph generation
├── ez-viz/                    # Frontend visualization server (Flask)
│   └── app.py                 # Multi-project dashboard and API
├── analyze.py                 # Database analysis utilities
└── extract_db_data.py         # Low-level DB inspection tools
```

## Key Classes

### Core Domain

| Class | File | Responsibility |
|-------|------|----------------|
| `Module` | process_code.py | AST parsing, block extraction, checksum generation |
| `Block` | process_code.py | Represents a code section with line range and checksum |
| `SourceTree` | testmon_core.py | File system caching, mtime/sha tracking |
| `TestmonData` | testmon_core.py | Orchestrates fingerprint creation and stability analysis |
| `TestmonCollector` | testmon_core.py | Coverage collection during test execution |
| `DB` | db.py | SQLite database schema, CRUD operations |
| `DependencyTracker` | dependency_tracker.py | Tracks file reads and imports during test execution |
| `NetDB` | net_db.py | Network-based DB implementation for server communication |

### Pytest Plugin

| Class | File | Responsibility |
|-------|------|----------------|
| `TestmonCollect` | pytest_ezmon.py | Hook implementation for coverage collection |
| `TestmonSelect` | pytest_ezmon.py | Hook implementation for test selection/ordering |
| `TestmonXdistSync` | pytest_ezmon.py | Synchronization for pytest-xdist (distributed) |

### Configuration

| Class | File | Responsibility |
|-------|------|----------------|
| `TmConf` | configure.py | Configuration dataclass (collect, select, message) |

## Database Schema

ezmon uses SQLite with WAL (Write-Ahead Logging) mode for performance. The database is stored in `.testmondata`.

### Core Tables

```sql
-- Environment tracking (Python version, installed packages)
environment (
    id INTEGER PRIMARY KEY,
    environment_name TEXT,
    system_packages TEXT,
    python_version TEXT
)

-- File fingerprints (checksums of code blocks)
file_fp (
    id INTEGER PRIMARY KEY,
    filename TEXT,
    method_checksums BLOB,  -- Binary array of CRC32 checksums
    mtime FLOAT,
    fsha TEXT               -- SHA1 hash of file content
)

-- Test execution records
test_execution (
    id INTEGER PRIMARY KEY,
    environment_id INTEGER,
    test_name TEXT,
    duration FLOAT,
    failed BIT,
    forced BIT
)

-- Many-to-many: tests to file fingerprints
test_execution_file_fp (
    test_execution_id INTEGER,
    fingerprint_id INTEGER
)
```

### Historical Tracking Tables (ezmon extensions)

These tables are ezmon-specific additions for the visualization frontend:

```sql
-- Run identifiers for historical tracking
run_uid (
    id INTEGER PRIMARY KEY,
    repo_run_id INTEGER,
    create_date TEXT
)

-- Run-level statistics
run_infos (
    run_time_saved REAL,
    run_time_all REAL,
    tests_saved INTEGER,
    tests_all INTEGER,
    run_uid INTEGER
)

-- Historical test data
test_infos (
    id INTEGER PRIMARY KEY,
    test_execution_id INTEGER,
    test_name TEXT,
    duration FLOAT,
    failed BIT,
    forced BIT,
    run_uid INTEGER
)

-- Historical fingerprint snapshots
file_fp_infos (
    id INTEGER PRIMARY KEY,
    fingerprint_id INTEGER,
    filename TEXT,
    method_checksums BLOB,
    mtime FLOAT,
    fsha TEXT,
    run_uid INTEGER
)

-- Line-level coverage tracking
test_execution_coverage (
    id INTEGER PRIMARY KEY,
    test_execution_id INTEGER,
    filename TEXT,
    lines TEXT,  -- JSON array of line numbers
    run_uid INTEGER
)
```

## Fingerprint Matching Algorithm

The fingerprint matching algorithm is conservative - it marks a test as affected if ANY of its recorded checksums are no longer present:

```python
def match_fingerprint(module: Module, fingerprint):
    """
    Returns True if test's recorded fingerprint matches current code.
    Returns False if any recorded checksums are missing (code changed).
    """
    if set(fingerprint) - set(module.checksums):
        return False  # Mismatch: some recorded checksums not in current code
    return True       # Match: all recorded checksums still present
```

This approach:
- **Catches removals/modifications**: Any change to a block invalidates the fingerprint
- **Allows additions**: New code blocks don't invalidate existing fingerprints
- **Ignores comments**: Comment-only changes don't trigger re-runs

## Method-Level Fingerprinting

Ezmon tracks dependencies at the **method level**, not just file level. Each test has a fingerprint **per module** containing:
1. The module-level checksum (file with function bodies stripped)
2. Checksums for each function/method body the test executed

When you modify a function, only tests that have that function's checksum in their fingerprint will be re-run.

**Example:**
```
# test_add has fingerprint for math_utils.py: [module_checksum, add_checksum]
# test_subtract has fingerprint for math_utils.py: [module_checksum, subtract_checksum]

# Modify math_utils.add()
# → add_checksum changes
# → Only test_add re-runs (it has add_checksum in its fingerprint)
# → test_subtract is NOT affected (it only has subtract_checksum)

# Modify calculator.clear_history()
# → Only test_clear_history re-runs (it's the only test with clear_history checksum)
```

## Dependency Tracking

Beyond coverage-based fingerprinting, ezmon tracks additional dependencies that coverage.py might miss:

### Import Tracking

The `DependencyTracker` class hooks into Python's import system to capture:

1. **Local module imports**: All Python files within the project that are imported during test execution
2. **External package imports**: Third-party packages (e.g., `requests`, `numpy`) imported by each test

#### Local Module Detection

A module is considered "local" if:
- Its file path is within the project's root directory
- OR it's a pip-installed editable package that corresponds to a local package directory

This second condition is important for projects like matplotlib where the package is pip-installed (`pip install -e .`) but we still want to track it as a local dependency:

```python
# In matplotlib project:
import matplotlib.pyplot  # This is local, even though it's "installed"

# The tracker checks for: <rootdir>/matplotlib/ or <rootdir>/lib/matplotlib/
# If found, matplotlib is treated as a local package, not external
```

#### External Package Tracking

External packages are tracked at the **package level** (e.g., `requests` not `requests.adapters`):

- Each test has a set of external packages it depends on
- When a package version changes, only tests that import that package are marked as affected
- Standard library modules (os, sys, json, etc.) are excluded

### File Dependency Tracking

Ezmon tracks non-Python files (JSON, YAML, images, etc.) that are read during test execution.

#### How It Works

The `DependencyTracker` hooks `builtins.open()` to intercept file reads:

```python
# During test execution:
with open('config.json') as f:    # Intercepted!
    config = json.load(f)

# The tracker records: test depends on config.json with SHA <committed_sha>
```

#### Git-Based Tracking (Critical Design Decision)

**Only files committed to git are tracked**, and the **committed state** (not the working tree state) is used:

1. **Ephemeral/generated files are NOT tracked**: Files like `result_images/`, `__pycache__/`, or test outputs that aren't in git won't create dependencies

2. **Workflow-modified files use committed state**: If a CI workflow modifies a config file during testing, we track the file's committed SHA, not the modified content

```
# Example: config.json in git with content A (SHA: abc123)
# Workflow modifies it to content B during testing

# Old behavior (WRONG):
#   Track config.json with SHA of content B
#   → Every run sees config.json as "changed"
#   → All dependent tests always run

# New behavior (CORRECT):
#   Track config.json with SHA abc123 (from git HEAD)
#   → Only runs tests when config.json actually changes in a commit
```

#### Implementation Details

```python
def _get_committed_file_sha(self, relpath: str) -> Optional[str]:
    """
    Get git blob hash for the committed version of a file.
    Returns None for files not in git (ephemeral/generated).
    """
    result = subprocess.run(
        ['git', 'ls-tree', 'HEAD', '--', relpath],
        capture_output=True, text=True
    )
    # Parse: "100644 blob <sha>\t<filename>"
    return sha_from_output(result.stdout)
```

This approach ensures:
- `result_images/` (25,284 generated test images in matplotlib) → NOT tracked
- `baseline_images/` (reference images in git) → Tracked correctly
- `config.json` (modified during workflow) → Tracked with committed state

### Database Schema for Dependencies

```sql
-- File dependencies (non-Python files)
file_dependency (
    id INTEGER PRIMARY KEY,
    filename TEXT,        -- Relative path: "config.json"
    sha TEXT              -- Git blob hash of committed version
)

-- Many-to-many: tests to file dependencies
test_execution_file_dependency (
    test_execution_id INTEGER,
    file_dependency_id INTEGER
)
```

### Dependency Flow During Test Execution

```
1. Test starts
   ├─ DependencyTracker.start(test_name)
   ├─ Hook builtins.open() and builtins.__import__()
   └─ Initialize tracking sets for this test

2. Test runs
   ├─ Coverage.py tracks executed lines
   ├─ DependencyTracker captures:
   │   ├─ File reads: open('config.json', 'r') → record if in git
   │   ├─ Local imports: import src.utils → record path
   │   └─ External imports: import requests → record package name
   └─ Test completes

3. Test ends
   ├─ DependencyTracker.stop() returns (files, local_imports, external_imports)
   ├─ Files → stored in file_dependency table
   ├─ Local imports → coverage.py already handles these
   └─ External imports → stored in environment/test metadata
```

## Coverage Context Limitation

**Important**: Due to a fundamental limitation in coverage.py's dynamic context tracking, only the **first test to execute a code path** gets recorded as depending on that code. Subsequent tests calling the same code (under different contexts) don't get the dependency recorded.

This affects both:
1. **Tests across different files**: First file to import gets the dependency
2. **Tests within the same file**: First test to call a function gets the dependency

**Example:**
```
# Test order: test_calculator.py → test_math_utils.py (alphabetical)

# test_calculator.py::TestCalculator::test_add runs first, calls math_utils.add()
# → Gets dependency on math_utils.add() ✓

# test_calculator.py::TestCalculatorHistory::test_history_recording runs later
# → Also calls add() but it's already traced, no dependency recorded ✗

# test_math_utils.py runs later, imports same math_utils
# → math_utils.py already traced, no dependencies recorded ✗
```

**Implications:**
- Changes to `math_utils.add()` will only trigger `test_calculator.py::TestCalculator::test_add` to re-run
- `test_history_recording` and `test_math_utils.py` tests won't re-run even though they use add()

This is a known limitation of the coverage.py context tracking approach used by ezmon (and upstream testmon).

## Known Limitations

### 1. Coverage Context Limitation (see above)

Only the first test to execute a code path gets the dependency recorded. This is a fundamental limitation of coverage.py's dynamic context tracking.

### 2. Import Without Execution

When a module is imported but specific functions are not called during test execution, those functions are **not** tracked in the test's fingerprint.

```python
from mymodule import helper_function  # Imported but not called

def test_something():
    assert callable(helper_function)  # Never executes helper_function body
```

If `helper_function()` body changes, this test will **not** be re-run.

**Note**: This is by design - ezmon tracks executed code, not imported code. If the function body isn't executed, there's no dependency.

## Command Line Options

| Option | Description |
|--------|-------------|
| `--ezmon` | Enable test selection and coverage collection |
| `--ezmon-noselect` | Reorder tests by failure likelihood, but don't deselect |
| `--ezmon-nocollect` | Selection only, no coverage collection |
| `--ezmon-forceselect` | Force selection even with pytest selectors (-k, -m) |
| `--no-ezmon` | Disable ezmon completely |
| `--ezmon-env` | Separate coverage data for different environments |
| `--ezmon-graph` | Generate interactive dependency graph |

## Server Sync (CI Integration)

ezmon supports synchronizing test data with a central server for CI environments:

1. **Download**: Fetch previous `.testmondata` before running tests
2. **Test Preferences**: Load `always_run_tests` and `prioritized_tests` from server
3. **Upload**: Push updated `.testmondata` after tests complete
4. **Graph Upload**: Optionally upload dependency graph visualization

Environment variables for CI:
- `TESTMON_DATAFILE`: Custom path for .testmondata
- `REPO_ID`, `JOB_ID`, `RUN_ID`: CI metadata
- `GITHUB_REPOSITORY`: Auto-detected from GitHub Actions

## NetDB Architecture (Direct Server Communication)

NetDB is an alternative to the download/upload SQLite file approach. Instead of syncing entire database files, the pytest-ezmon plugin communicates directly with the server via RPC-style API calls during test execution.

### Architecture Overview

```
┌─────────────────┐         HTTPS/REST          ┌──────────────────┐
│  pytest-ezmon   │◄──────────────────────────►│   Flask Server   │
│    (NetDB)      │   JSON (gzip compressed)    │   (ez-viz/app)   │
└─────────────────┘                             └────────┬─────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────┐
                                                │  SQLite per job  │
                                                │  .testmondata    │
                                                └──────────────────┘
```

### Key Benefits

- **No local `.testmondata` file needed** in CI/CD ephemeral environments
- **Reduced data transfer**: Only changed data is sent, not entire database files
- **Real-time updates**: Test results are stored immediately on the server
- **Better concurrency**: Server-side locking handles multiple concurrent runs

### Data Flow

A typical CI/CD test run with 500 tests requires only ~5 network requests:

1. **pytest_configure** → `POST /api/rpc/session/initiate`
   - Sends: environment name, system packages, Python version
   - Returns: `exec_id`, `filenames`, `packages_changed`

2. **determine_stable** → `POST /api/rpc/tests/determine`
   - Sends: file hashes, dependency SHAs
   - Returns: `affected` tests, `failing` tests

3. **Every 250 tests** → `POST /api/rpc/test_execution/batch_insert`
   - Sends: gzip-compressed batch of test results + fingerprints
   - Server bulk inserts all data

4. **pytest_sessionfinish** → `POST /api/rpc/session/finish`
   - Server aggregates stats, vacuums orphans, commits

### Key Components

| Component | File | Description |
|-----------|------|-------------|
| `NetDB` | `ezmon/net_db.py` | Client-side class implementing DB interface via HTTP |
| `create_database()` | `ezmon/testmon_core.py` | Factory function selecting NetDB or local DB |
| RPC Endpoints | `ez-viz/app.py` | Server-side `/api/rpc/*` routes |

### NetDB Class Features

- **Same interface as `db.DB`**: Drop-in replacement for local SQLite
- **Client-side LRU cache**: Reduces network calls for fingerprint lookups
- **Gzip compression**: Payloads > 1KB are automatically compressed
- **Connection pooling**: Uses `requests.Session` for efficient HTTP
- **Retry logic**: Exponential backoff for transient failures

### Environment Variables

```bash
# Required for NetDB mode
TESTMON_NET_ENABLED=true
TESTMON_SERVER=https://your-server.com
REPO_ID=owner/repo              # or GITHUB_REPOSITORY
JOB_ID=test-py311               # identifier for this variant

# Optional
TESTMON_AUTH_TOKEN=your-token   # for authentication
RUN_ID=$GITHUB_RUN_ID           # links to CI run
```

### RPC Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/rpc/session/initiate` | POST | Start execution, return exec_id |
| `/api/rpc/session/finish` | POST | Finalize, aggregate stats |
| `/api/rpc/tests/all` | GET | Get all test executions |
| `/api/rpc/tests/determine` | POST | Compute affected tests |
| `/api/rpc/tests/delete` | POST | Delete test executions |
| `/api/rpc/files/fetch_unknown` | POST | Find changed files |
| `/api/rpc/files/list` | GET | List files for environment |
| `/api/rpc/files/fingerprints` | GET | Get fingerprint details |
| `/api/rpc/test_execution/batch_insert` | POST | Bulk insert tests |
| `/api/rpc/coverage/batch_insert` | POST | Bulk insert coverage |
| `/api/rpc/fingerprint/fetch_or_create` | POST | Fetch/create fingerprint |
| `/api/rpc/file_dependency/fetch_or_create` | POST | Fetch/create file dep |
| `/api/rpc/metadata/read` | GET | Read metadata attribute |
| `/api/rpc/metadata/write` | POST | Write metadata attribute |

### Authentication

RPC endpoints support two authentication methods:

1. **Session cookie**: For browser-based OAuth (existing frontend flow)
2. **Authorization header**: For CI/CD tokens (`Bearer <token>`)

```bash
# CI/CD usage
export TESTMON_AUTH_TOKEN=your-service-token
```

### Performance Comparison

| Metric | Download/Upload Approach | NetDB Approach |
|--------|-------------------------|----------------|
| Network calls | 2 (download + upload) | ~5 per run |
| Data transfer | ~5-10 MB each way | ~100-400 KB total |
| Startup latency | High (download entire DB) | Low (single init call) |
| Concurrent safety | Manual locking needed | Server-side locking |

### Backward Compatibility

- Local `.testmondata` mode remains **default**
- NetDB activates only when `TESTMON_NET_ENABLED=true`
- Existing `server_sync.py` upload/download still works
- All visualization endpoints unchanged (read same SQLite files)

## Visualization Frontend (ez-viz)

The Flask-based frontend (`ez-viz/app.py`) provides:

- Multi-project/multi-job test data visualization
- Historical run statistics and trends
- Test dependency exploration
- Interactive dependency graph (via Pyvis)

API endpoints:
- `GET/POST /api/client/download` - Download testmon data
- `POST /api/client/upload` - Upload test results
- `GET /api/client/testPreferences` - Fetch test preferences
- `POST /api/client/upload_graph` - Upload dependency graph

## Differences from Upstream testmon

1. **Package name**: `ezmon` instead of `testmon`
2. **Python support**: We maintain Python 3.7+ compatibility (upstream requires 3.10+)
3. **Historical tracking**: Additional tables for run history and visualization
4. **Line-level coverage**: Stores exact line numbers for detailed analysis
5. **Server sync**: Built-in CI integration for shared test data
6. **Dependency graph**: Interactive visualization generation
7. **Test prioritization**: Server-driven always_run and prioritized test lists

## Upstream Fixes Merged

The following bug fixes from upstream testmon have been merged (as of v2.1.4-a2):

- **Fix #235**: Internal error in some match statements when function defined inside
  - Modified `process_code.py` to use `end_lineno` from last child when `end` is None
- **Fix #255**: Unreliable system packages change detection
  - Added `ORDER BY id DESC` to environment query for consistent results
  - Added `ON DELETE CASCADE` to foreign keys for proper cleanup
  - Improved environment handling when packages change (create new, delete old)
- **Pytest 9 support**: Updated dependency to allow `pytest>=5,<10`

## Test Suite

The project includes a comprehensive test suite with unit tests and scenario-based integration tests:

### Structure

```
tests/
├── conftest.py             # Shared fixtures
├── test_process_code.py    # Unit tests for fingerprint generation (24 tests)
└── README.md

integration_tests/          # Scenario-based integration tests
├── run_integration_tests.py    # Main test runner with version verification
├── test_all_versions.py        # Multi-version testing (Python 3.7-3.13)
├── scenarios/__init__.py       # Declarative scenario definitions (15 scenarios)
├── sample_project/             # Example project with clear dependencies
└── README.md
```

### Running Tests

```bash
# Unit tests
pytest tests/ -v
pytest tests/ -v --cov=ezmon --cov-report=term-missing

# Integration tests - all scenarios
python integration_tests/run_integration_tests.py

# Integration tests - specific scenario
python integration_tests/run_integration_tests.py --scenario modify_math_utils

# Integration tests - with specific Python version
python integration_tests/run_integration_tests.py --python python3.7 --expect-version 3.7

# Multi-version testing (all available Python versions)
python integration_tests/test_all_versions.py

# List available versions
python integration_tests/test_all_versions.py --list-versions
```

### Integration Test Scenarios

The integration tests use declarative scenarios that modify code and verify individual test selection. This verifies method-level fingerprinting is working correctly.

**Note**: Due to the coverage context limitation (see above), only the first test to execute each function gets the dependency recorded.

**Basic Scenarios:**

| Scenario | Description | Expected Selected Test |
|----------|-------------|----------------------|
| `modify_math_utils` | Change math_utils.add() | `TestCalculator::test_add` |
| `modify_string_utils` | Change string_utils.uppercase() | `TestFormatter::test_upper_style` |
| `modify_calculator_only` | Change Calculator.clear_history() | `TestCalculatorHistory::test_clear_history` |
| `modify_formatter_only` | Change Formatter.set_style() | `TestFormatterStyleChange::test_change_style` |
| `modify_test_only` | Change test_math_utils::TestAdd::test_positive_numbers | `TestAdd::test_positive_numbers` |
| `no_changes` | No modifications | (none) |
| `add_new_test` | Add a new test file | `test_new_feature`, `test_another_new` |
| `multiple_modifications` | Change subtract() and lowercase() | `test_subtract`, `test_lower_style` |

**Complex Code Pattern Scenarios:**

| Scenario | Description | Pattern Tested |
|----------|-------------|----------------|
| `modify_nested_class_method` | Change Statistics.mean() | Nested class methods |
| `modify_static_method` | Change BaseProcessor.validate_data() | Static methods |
| `modify_generator` | Change fibonacci() | Generator functions (yield) |
| `modify_decorator` | Change memoize() | Decorators and closures |
| `modify_context_manager` | Change CacheManager.__enter__() | Context managers |

**File Dependency Scenarios:**

| Scenario | Description | Status |
|----------|-------------|--------|
| `modify_config_file` | Change config.json | **PASSES** - tests reading config.json are selected |

**Limitation Demonstration Scenarios:**

| Scenario | Description | Status |
|----------|-------------|--------|
| `modify_uncalled_method` | Change imported but uncalled function | **FAILS** - by design, uncalled code has no dependency |

### Sample Project Structure

```
sample_project/
├── config.json            # Config file (for limitation tests)
├── src/
│   ├── math_utils.py      # Basic functions (add, subtract, etc.)
│   ├── string_utils.py    # String manipulation (uppercase, lowercase)
│   ├── calculator.py      # Class using math_utils
│   ├── formatter.py       # Class using string_utils
│   ├── data_processor.py  # Complex patterns: inheritance, nested classes,
│   │                      # static/class methods, properties
│   ├── cache_manager.py   # Decorators, context managers, closures
│   ├── generators.py      # Generators, iterators, pipelines
│   ├── config_reader.py   # File dependency demonstration
│   ├── external_deps.py   # External package dependency demo
│   └── import_only.py     # Import without execution demo
└── tests/
    ├── test_math_utils.py
    ├── test_string_utils.py
    ├── test_calculator.py
    ├── test_formatter.py
    ├── test_data_processor.py   # 21 tests for complex class patterns
    ├── test_cache_manager.py    # 22 tests for decorators/context managers
    ├── test_generators.py       # 31 tests for generators/iterators
    ├── test_config_reader.py    # 11 tests (file dependency limitation)
    ├── test_external_deps.py    # 9 tests (external deps limitation)
    └── test_import_only.py      # 9 tests (import tracking)
```

This verifies:
- **Function isolation**: Modify `add()` → only tests using `add()` run
- **Module isolation**: Modify `string_utils` → only string tests run
- **Indirect deps**: `Calculator` uses `math_utils` → changes propagate
- **Comment immunity**: Comment-only changes don't trigger re-runs (AST-based)
- **Nested classes**: Modifications to nested class methods correctly trigger dependent tests
- **Static/class methods**: Static and class methods are tracked independently
- **Generators**: Generator functions with `yield` are tracked correctly
- **Decorators**: Decorator functions and closures are tracked correctly
- **Context managers**: `__enter__`/`__exit__` methods are tracked correctly
- **File dependencies**: Non-Python files in git trigger re-runs when changed
- **Git-only tracking**: Ephemeral/generated files don't create spurious dependencies
- **Import without execution**: Imported but uncalled functions don't create dependencies (by design)
