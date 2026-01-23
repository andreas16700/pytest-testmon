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

A **fingerprint** is a collection of CRC32 checksums representing the code blocks that a test depends on. When any of these checksums change, the test is marked as "affected" and needs to re-run.

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
├── scenarios/__init__.py       # Declarative scenario definitions (8 scenarios)
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

The integration tests use declarative scenarios that modify code and verify selection:

| Scenario | Description | Expected Selected | Expected Deselected |
|----------|-------------|-------------------|---------------------|
| `modify_math_utils` | Change math_utils.add() | test_math_utils, test_calculator | test_string_utils, test_formatter |
| `modify_string_utils` | Change string_utils.uppercase() | test_string_utils, test_formatter | test_math_utils, test_calculator |
| `modify_calculator_only` | Change calculator.clear_history() | test_calculator | test_math_utils, test_string_utils, test_formatter |
| `modify_formatter_only` | Change formatter.set_style() | test_formatter | test_math_utils, test_string_utils, test_calculator |
| `modify_test_only` | Change only test_math_utils.py | test_math_utils | test_calculator, test_string_utils, test_formatter |
| `no_changes` | No modifications | (none) | (all tests) |
| `add_new_test` | Add a new test file | test_new | (all existing tests) |
| `multiple_modifications` | Change both math_utils and string_utils | (all tests) | (none) |

### Sample Project Structure

```
sample_project/
├── src/
│   ├── math_utils.py     # No dependencies
│   ├── string_utils.py   # No dependencies
│   ├── calculator.py     # Depends on math_utils
│   └── formatter.py      # Depends on string_utils
└── tests/
    ├── test_math_utils.py
    ├── test_string_utils.py
    ├── test_calculator.py
    └── test_formatter.py
```

This verifies:
- **Function isolation**: Modify `add()` → only tests using `add()` run
- **Module isolation**: Modify `string_utils` → only string tests run
- **Indirect deps**: `Calculator` uses `math_utils` → changes propagate
- **Comment immunity**: Comment-only changes don't trigger re-runs (AST-based)
