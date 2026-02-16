# Integration Tests for pytest-ezmon-nocov

This directory contains integration tests that verify ezmon-nocov's test selection behavior using realistic scenarios.

## Key Differences from Original testmon

Ezmon-nocov uses **import-based tracking** instead of coverage.py:

| Feature | Original testmon | Ezmon-nocov |
|---------|------------------|-------------|
| Dependency tracking | coverage.py contexts | Import hooks |
| Granularity | Line/method-level | File-level |
| Transitive imports | ❌ Not fully tracked | ✅ Tracked via import hooks |
| File dependencies (JSON, YAML) | ❌ Not tracked | ✅ Tracked via file hashing |
| First-run speed | Slower (coverage overhead) | ~3x faster |
| False negatives | Possible (coverage limitations) | Never (conservative) |
| Storage | Junction tables | Roaring bitmaps + zstd |

### Trade-offs

**Import-based tracking is conservative**: When any code in a file changes, all tests that import that file (directly or transitively) are re-run. This means:
- **Pros**: No false negatives - if code changes, dependent tests run
- **Cons**: May run more tests than strictly necessary (tests that import but don't use changed code)

### Verified Improvements

We tested both plugins on limitation scenarios:

| Scenario | Original testmon | Ezmon-nocov |
|----------|------------------|-------------|
| `modify_globals` (change app_globals.py) | 0 selected / 23 deselected | **23 selected** |
| `modify_config_file` (change config.json) | 0 selected / 11 deselected | **11 selected** |

## Structure

```
integration_tests/
├── run_integration_tests.py  # Test runner script
├── test_all_versions.py      # Multi-version test script
├── scenarios/
│   └── __init__.py           # Scenario definitions (24 scenarios)
└── sample_project/           # Example project with various code patterns
    ├── config.json            # Config file (for file dependency tests)
    ├── src/
    │   ├── math_utils.py      # Basic functions (add, subtract, etc.)
    │   ├── string_utils.py    # String manipulation functions
    │   ├── calculator.py      # Class using math_utils
    │   ├── formatter.py       # Class using string_utils
    │   ├── data_processor.py  # Complex: inheritance, nested classes,
    │   │                      # static/class methods, properties
    │   ├── cache_manager.py   # Decorators, context managers, closures
    │   ├── generators.py      # Generators, iterators, pipelines
    │   ├── config_reader.py   # File dependency demonstration
    │   ├── external_deps.py   # External package dependency demo
    │   ├── import_only.py     # Import without execution demo
    │   ├── app_globals.py     # Global constants (for transitive import tests)
    │   ├── globals_consumer.py # Module that uses globals
    │   └── models/            # Package re-export pattern demo
    │       ├── __init__.py    # Re-exports User, Product from submodules
    │       ├── user.py        # User class definition
    │       └── product.py     # Product class definition
    └── tests/
        ├── test_math_utils.py       # 10 tests
        ├── test_string_utils.py     # 6 tests
        ├── test_calculator.py       # 8 tests
        ├── test_formatter.py        # 6 tests
        ├── test_data_processor.py   # 21 tests
        ├── test_cache_manager.py    # 22 tests
        ├── test_generators.py       # 31 tests
        ├── test_config_reader.py    # 11 tests (file dependency)
        ├── test_external_deps.py    # 9 tests (external deps)
        ├── test_import_only.py      # 9 tests (import tracking)
        ├── test_globals_consumer.py # 23 tests (transitive import tracking)
        ├── test_models.py           # Tests importing via package __init__
        ├── test_user_only.py        # Tests User class only
        └── test_product_only.py     # Tests Product class only
```

## How Import-Based Dependency Tracking Works

### Core Principle

**No code in Python outside the current module can execute unless it is imported.**

This observation enables import-based test dependency tracking. If a test depends on code in `module_x.py`, then at some point during the test's lifecycle, `module_x` must be imported.

### 1. Import Hook

Ezmon-nocov hooks `builtins.__import__` to intercept all imports:

```python
def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
    result = self._original_import(name, globals, locals, fromlist, level)

    # Track the imported module
    self._track_import(result, name)

    # For 'from X import Y', also track Y's defining module
    if fromlist:
        for attr_name in fromlist:
            imported_obj = getattr(result, attr_name, None)
            if hasattr(imported_obj, '__module__'):
                # Class/function - track its defining module
                defining_module = sys.modules.get(imported_obj.__module__)
                if defining_module:
                    self._track_import(defining_module, imported_obj.__module__)

    return result
```

### 2. Class Import Tracking (Package Re-exports)

When a class is imported via package re-export, we track the defining module:

```python
# src/models/__init__.py
from src.models.user import User  # Re-export User

# test_user.py
from src.models import User  # User.__module__ = 'src.models.user'

# Ezmon tracks src/models/user.py, not just src/models/__init__.py
```

This handles common patterns like:
- `from pandas import Series` (Series is defined in pandas.core.series)
- `from django.db import models` (models is defined in django.db.models.base)

### 3. Transitive Import Tracking

When Python imports module M1, which imports M2, both modules are tracked:

```python
# src/app_globals.py
MAX_ITEMS = 100  # Global constant

# src/globals_consumer.py
from src.app_globals import MAX_ITEMS  # Import happens at load time

def validate_count(count):
    return count <= MAX_ITEMS

# tests/test_globals_consumer.py
from src.globals_consumer import validate_count

def test_valid_count():
    assert validate_count(50)  # Uses MAX_ITEMS transitively
```

If `MAX_ITEMS` changes in `app_globals.py`:
- **Original testmon**: ❌ 0 tests selected (coverage doesn't see transitive dependency)
- **Ezmon-nocov**: ✅ All tests selected (import hooks track the dependency)

### 4. File Dependency Tracking

Non-Python files (JSON, YAML, etc.) are tracked when read:

```python
def _tracking_open(self, file, mode='r', *args, **kwargs):
    result = self._original_open(file, mode, *args, **kwargs)
    if 'r' in mode:
        relpath = self._is_in_project(file)
        if relpath and not relpath.endswith('.py'):
            sha = self._get_committed_file_sha(relpath)
            if sha:  # Only track git-committed files
                self._track_file(relpath, sha)
    return result
```

Example:
```python
# tests/test_config.py
def test_threshold():
    with open("config.json") as f:
        config = json.load(f)
    assert config["threshold"] == 50
```

If `config.json` changes:
- **Original testmon**: ❌ 0 tests selected
- **Ezmon-nocov**: ✅ Tests that read the file are selected

### 5. Checkpoint System

The checkpoint system tracks which modules are in `sys.modules` at key points:

```
pytest starts
    |
    v
conftest.py loads (imports packages)
    |
    v
[SAVE GLOBAL CHECKPOINT] -- base dependencies for ALL tests
    |
    v
For each test file:
    |
    +-- [RESTORE TO GLOBAL CHECKPOINT] -- clean state
    |
    +-- Collect test file (imports tracked)
    |
    +-- [SAVE PER-FILE CHECKPOINT]
    |
    v
For each test:
    |
    +-- [RESTORE TO PER-FILE CHECKPOINT]
    |
    +-- Run test (more imports may happen)
    |
    v
Test dependencies = base + collection + execution imports
```

## Running Integration Tests

### Run all scenarios:
```bash
python integration_tests/run_integration_tests.py
```

### Run with verbose output:
```bash
python integration_tests/run_integration_tests.py -v
```

### Run a specific scenario:
```bash
python integration_tests/run_integration_tests.py --scenario modify_math_utils
```

### Run with a specific Python version:
```bash
python integration_tests/run_integration_tests.py --python python3.7
```

### Verify Python version matches expected:
```bash
# This will fail if the interpreter is not Python 3.7
python integration_tests/run_integration_tests.py --python python3.7 --expect-version 3.7
```

### List available scenarios:
```bash
python integration_tests/run_integration_tests.py --list
```

## Multi-Version Testing

The `test_all_versions.py` script runs the integration tests across all supported Python versions.

### List available Python versions:
```bash
python integration_tests/test_all_versions.py --list-versions
```

### Run all scenarios on all available Python versions:
```bash
python integration_tests/test_all_versions.py
```

### Run on specific versions only:
```bash
python integration_tests/test_all_versions.py --versions 3.7,3.10,3.11
```

### Run specific scenario on all versions:
```bash
python integration_tests/test_all_versions.py --scenario modify_math_utils
```

## Available Scenarios

### Basic Scenarios

| Scenario | Description | Expected Behavior |
|----------|-------------|-------------------|
| `modify_math_utils` | Change math_utils.add() | All tests importing math_utils |
| `modify_string_utils` | Change string_utils.uppercase() | All tests importing string_utils |
| `modify_calculator_only` | Change Calculator.clear_history() | Tests importing calculator |
| `modify_formatter_only` | Change Formatter.set_style() | Tests importing formatter |
| `modify_test_only` | Change test file | Only the modified test file |
| `no_changes` | No modifications | No tests selected |
| `add_new_test` | Add a new test file | Only new tests |
| `multiple_modifications` | Change subtract() and lowercase() | Tests importing either module |
| `comment_only_change` | Add comments only | No tests selected (AST unchanged) |
| `docstring_only_change` | Add docstrings only | No tests selected (docstrings stripped) |

### Complex Code Pattern Scenarios

These scenarios test fingerprinting with advanced Python constructs:

| Scenario | Description | Pattern Tested |
|----------|-------------|----------------|
| `modify_nested_class_method` | Change Statistics.mean() | Nested class methods |
| `modify_static_method` | Change BaseProcessor.validate_data() | Static methods |
| `modify_generator` | Change fibonacci() | Generator functions (yield) |
| `modify_decorator` | Change memoize() | Decorators and closures |
| `modify_context_manager` | Change CacheManager.__enter__() | Context managers |

### Class Import Tracking Scenarios

These scenarios test class imports via package re-exports:

| Scenario | Description | Expected Behavior |
|----------|-------------|-------------------|
| `from_package_import_class` | Change user.py | Tests importing User via `from src.models import User` |
| `from_package_import_class_product` | Change product.py | Tests importing Product via `from src.models import Product` |

### Module-Level Import Scenarios

| Scenario | Description | Expected Behavior |
|----------|-------------|-------------------|
| `modify_import_only_module_level` | Change module-level constant | All tests importing the module |
| `modify_globals` | Change app_globals.py | All tests importing transitively |

### File Dependency Scenarios

| Scenario | Description | Expected Behavior |
|----------|-------------|-------------------|
| `modify_config_file` | Change config.json | Tests that read config.json |

## Implementation Notes

### Challenges Encountered

1. **Checkpoint interaction with class imports**: When a class is imported via package re-export (e.g., `from src.models import User`), the defining module (`src.models.user`) may be removed from `sys.modules` by checkpoint restore while the class still exists as an attribute on the cached parent module. Solution: Restore the defining module from cache before tracking.

2. **File dependency tracking requires careful hook management**: The `builtins.open()` hook must be installed/uninstalled per-test to avoid tracking file reads from other tests or the test framework itself.

3. **Git SHA optimization**: Ezmon uses git blob SHAs for fast change detection before computing AST checksums. This significantly speeds up stability analysis.

### Key Insights

1. **Import tracking is conservative**: Any change to a file triggers all tests that import it. This guarantees no false negatives but may run more tests than strictly necessary.

2. **AST-based fingerprinting ignores comments and docstrings**: Only actual code changes trigger tests. Comments and docstrings are stripped before computing checksums.

3. **Class `__module__` tracks defining location**: When tracking class imports, we use `cls.__module__` to find the actual module where the class is defined, not just where it's re-exported from.

## How It Works

1. **Setup**: Creates a fresh temp directory with a copy of `sample_project/`
2. **Initialize Git**: Initializes a git repo (ezmon uses git for optimization)
3. **Create venv**: Creates a virtual environment and installs ezmon
4. **Version verification**: Verifies Python version matches expected (if `--expect-version` used)
5. **Baseline run**: Runs `pytest --ezmon` to build the dependency database
6. **Apply modifications**: Makes the code changes defined in the scenario
7. **Test run**: Runs `pytest --ezmon` again
8. **Verify**: Parses output to verify expected individual tests were selected/deselected

## Adding New Scenarios

Edit `scenarios/__init__.py` and use the `register()` function:

```python
register(Scenario(
    name="my_scenario",
    description="Description of what this tests",
    modifications=[
        Modification(
            file="src/some_file.py",
            action="replace",  # or "append", "create", "delete"
            target="old code",  # For "replace" action
            content="new code",
        )
    ],
    expected_selected=["tests/test_file.py::TestClass::test_method"],
    expected_deselected=["tests/other_test.py::TestClass::test_other"],
))
```

**Important**:
- Ezmon uses AST-based fingerprinting, so modifications must change actual code structure, not just comments!
- Use full test paths like `tests/test_file.py::TestClass::test_method` for expected_selected/deselected

## Parallel Execution Tests (pytest-xdist)

The `test_parallel_execution.py` file contains integration tests for parallel test execution with pytest-xdist.

### Tests

| Test | Mode | Description |
|------|------|-------------|
| `test_sequential_baseline` | Local SQLite | Verifies sequential execution works as baseline |
| `test_parallel_small_subset` | Local SQLite | Tests parallel execution with a small test subset |
| `test_parallel_coverage_saved` | Local SQLite | Verifies dependency data is saved from parallel workers |
| `test_parallel_netdb_basic` | NetDB | Tests parallel execution with network database |
| `test_parallel_netdb_coverage_collection` | NetDB | Verifies dependency collection works in parallel NetDB mode |

### Running Parallel Tests

```bash
# Run all parallel execution tests
pytest integration_tests/test_parallel_execution.py -v

# Run only local SQLite tests
pytest integration_tests/test_parallel_execution.py::TestParallelExecution -v

# Run only NetDB tests (requires server access)
pytest integration_tests/test_parallel_execution.py::TestParallelExecutionNetDB -v
```

### The Race Condition Problem

When using pytest-xdist, workers independently compute which tests to deselect. If workers see different database states (due to SQLite WAL snapshots), they compute different `stable_test_names` sets, causing:

```
Different tests were collected between gw0 and gw1. The difference is: ...
```

### The Solution

Ezmon solves this by having the controller compute stability once and pass pre-computed data to workers via `workerinput`:

1. Controller calls `determine_stable()` in `init_testmon_data()`
2. `TestmonXdistSync.pytest_configure_node()` passes `exec_id`, `stable_test_names`, etc. to workers
3. Workers use `TestmonData.for_worker()` with pre-computed data instead of recomputing

See `ARCHITECTURE.md` for detailed documentation.

## Supported Python Versions

- Python 3.7
- Python 3.8
- Python 3.9
- Python 3.10
- Python 3.11
- Python 3.12
- Python 3.13
- Python 3.14
