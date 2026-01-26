# Integration Tests for pytest-ezmon

This directory contains integration tests that verify ezmon's test selection behavior using realistic scenarios.

## Key Improvements Over Original testmon

Ezmon provides significant improvements over the original `pytest-testmon` plugin:

| Feature | Original testmon | Ezmon |
|---------|------------------|-------|
| Transitive imports (globals pattern) | ❌ Not tracked | ✅ Tracked via AST parsing |
| File dependencies (JSON, YAML, etc.) | ❌ Not tracked | ✅ Tracked via file hashing |
| Method-level precision | ✅ Yes | ✅ Yes (via coverage.py) |

### Verified Improvements

We tested both plugins on limitation scenarios:

| Scenario | Original testmon | Ezmon |
|----------|------------------|-------|
| `modify_globals` (change app_globals.py) | 0 selected / 23 deselected | **23 selected** |
| `modify_config_file` (change config.json) | 0 selected / 11 deselected | **11 selected** |

## Structure

```
integration_tests/
├── run_integration_tests.py  # Test runner script
├── test_all_versions.py      # Multi-version test script
├── scenarios/
│   └── __init__.py           # Scenario definitions (16 scenarios)
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
    │   └── globals_consumer.py # Module that uses globals
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
        └── test_globals_consumer.py # 23 tests (transitive import tracking)
```

## How Dependency Tracking Works

### 1. Method-Level Fingerprinting (via coverage.py)

Ezmon tracks dependencies at the **method level**, not just file level. Each test has a fingerprint **per Python module** it used, containing:
1. The module-level checksum (file with function/method bodies stripped)
2. Checksums for each function/method body the test actually executed (as reported by coverage.py)

When you modify a specific function, only tests that have that function's checksum in their fingerprint will be re-run.

### 2. Transitive Import Tracking (NEW)

**Problem**: When Python imports module M1, which imports M2, the interpreter executes M2's top-level code. This creates a transitive dependency that coverage.py doesn't track (because no code is "executed" during test runtime - it was executed during import).

**Example - The Globals Pattern**:
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
- **Original testmon**: ❌ 0 tests selected (doesn't see the transitive dependency)
- **Ezmon**: ✅ All tests selected (tracks transitive imports via AST parsing)

**How ezmon solves this**:
1. Uses AST parsing to find import statements in each module
2. For modules tracked by coverage, gets their transitive imports
3. Adds module-level fingerprints for transitively imported modules
4. When any transitive dependency changes, affected tests are selected

### 3. File Dependency Tracking (NEW)

**Problem**: Tests that read non-Python files (JSON, YAML, CSV, etc.) aren't tracked by coverage.py.

**Example**:
```python
# tests/test_config.py
def test_threshold():
    with open("config.json") as f:
        config = json.load(f)
    assert config["threshold"] == 50
```

If `config.json` changes:
- **Original testmon**: ❌ 0 tests selected
- **Ezmon**: ✅ Tests that read the file are selected

**How ezmon solves this**:
1. Hooks into Python's `builtins.open()` during test execution
2. Tracks which files each test reads
3. Computes SHA hashes for tracked files
4. When a tracked file changes, affected tests are selected

## Coverage Limitation

**Important**: Due to a fundamental limitation in coverage.py's dynamic context tracking, only the **first test to execute a code path** gets recorded as depending on that code. Subsequent tests calling the same code (under different contexts) don't get the dependency recorded.

This affects both:
1. **Tests across different files**: First file to import gets the dependency
2. **Tests within the same file**: First test to call a function gets the dependency

### How This Interacts with Transitive Import Tracking

For tests that don't call specific functions (only import modules transitively):
- They get **module-level** fingerprints (any change to the module triggers them)

For tests that do call specific functions:
- They get **method-level** fingerprints (only changes to called methods trigger them)

This means:
- `test_add` calls `add()` → Only changes to `add()` trigger it
- `test_unknown_operator` doesn't call math functions → Any math_utils.py change triggers it (module-level)

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
| `modify_math_utils` | Change math_utils.add() | Tests calling add() + tests with module-level deps |
| `modify_string_utils` | Change string_utils.uppercase() | Tests calling uppercase() + tests with module-level deps |
| `modify_calculator_only` | Change Calculator.clear_history() | Tests with matching fingerprints |
| `modify_formatter_only` | Change Formatter.set_style() | Tests with matching fingerprints |
| `modify_test_only` | Change test file | Only the modified test |
| `no_changes` | No modifications | No tests selected |
| `add_new_test` | Add a new test file | Only new tests |
| `multiple_modifications` | Change subtract() and lowercase() | Tests for both functions |

### Complex Code Pattern Scenarios

These scenarios test fingerprinting with advanced Python constructs:

| Scenario | Description | Pattern Tested |
|----------|-------------|----------------|
| `modify_nested_class_method` | Change Statistics.mean() | Nested class methods |
| `modify_static_method` | Change BaseProcessor.validate_data() | Static methods |
| `modify_generator` | Change fibonacci() | Generator functions (yield) |
| `modify_decorator` | Change memoize() | Decorators and closures |
| `modify_context_manager` | Change CacheManager.__enter__() | Context managers |

### Improvement Demonstration Scenarios

These scenarios demonstrate ezmon's improvements over the original testmon:

| Scenario | Description | Status |
|----------|-------------|--------|
| `modify_config_file` | Change config.json | ✅ PASSES - file dependency tracking |
| `modify_globals` | Change app_globals.py | ✅ PASSES - transitive import tracking |

### Remaining Limitation Scenarios

| Scenario | Description | Current Status |
|----------|-------------|----------------|
| `modify_uncalled_method` | Change imported but uncalled function | Partial - only tests with module-level deps are selected |

## Implementation Notes

### Challenges Encountered

1. **Namespace inspection doesn't work for primitives**: Initially tried to track imports by inspecting module namespaces. This fails for imported constants (integers, strings, etc.) because they don't have `__module__` attributes. Solution: Use AST parsing instead.

2. **Transitive imports need careful scoping**: Adding all transitive imports as dependencies caused false positives (unrelated tests being selected). Solution: Only add transitive imports for modules that coverage.py already tracks.

3. **Module-level vs method-level trade-offs**: Tests that call functions get precise method-level tracking. Tests that only import modules (but don't call functions) get broader module-level tracking. This is the correct behavior - if a test doesn't exercise specific code, it should be conservatively re-run when anything in its dependencies changes.

### Key Insights

1. **Python executes module-level code during import**: When you `import M1`, and M1 has `from M2 import X`, Python executes M2's module-level code. This means:
   - Test → M1 → M2 = Test depends on M2's module-level code
   - Changes to M2's structure (not just functions) can affect the test

2. **AST parsing is reliable for import detection**: Unlike runtime inspection, AST parsing always finds import statements regardless of what objects they import (modules, classes, functions, or primitives).

3. **File dependency tracking requires careful hook management**: The `builtins.open()` hook must be installed/uninstalled per-test to avoid tracking file reads from other tests or the test framework itself.

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
| `test_parallel_coverage_saved` | Local SQLite | Verifies coverage data is saved from parallel workers |
| `test_parallel_netdb_basic` | NetDB | Tests parallel execution with network database |
| `test_parallel_netdb_coverage_collection` | NetDB | Verifies coverage collection works in parallel NetDB mode |

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
