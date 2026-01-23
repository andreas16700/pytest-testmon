# Integration Tests for pytest-ezmon

This directory contains integration tests that verify ezmon's test selection behavior using realistic scenarios.

## Structure

```
integration_tests/
├── run_integration_tests.py  # Test runner script
├── test_all_versions.py      # Multi-version test script
├── scenarios/
│   └── __init__.py           # Scenario definitions (13 scenarios)
└── sample_project/           # Example project with various code patterns
    ├── src/
    │   ├── math_utils.py      # Basic functions (add, subtract, etc.)
    │   ├── string_utils.py    # String manipulation functions
    │   ├── calculator.py      # Class using math_utils
    │   ├── formatter.py       # Class using string_utils
    │   ├── data_processor.py  # Complex: inheritance, nested classes,
    │   │                      # static/class methods, properties
    │   ├── cache_manager.py   # Decorators, context managers, closures
    │   └── generators.py      # Generators, iterators, pipelines
    └── tests/
        ├── test_math_utils.py       # 10 tests
        ├── test_string_utils.py     # 6 tests
        ├── test_calculator.py       # 8 tests
        ├── test_formatter.py        # 6 tests
        ├── test_data_processor.py   # 21 tests
        ├── test_cache_manager.py    # 22 tests
        └── test_generators.py       # 31 tests (104 total)
```

## Method-Level Fingerprinting

Ezmon tracks dependencies at the **method level**, not just file level. Each test has a fingerprint **per Python module** it used, containing:
1. The module-level checksum (file with function/method bodies stripped)
2. Checksums for each function/method body the test actually executed (as reported by coverage.py)

When you modify a specific function, only tests that have that function's checksum in their fingerprint will be re-run.

For example:
- Modify `math_utils.add()` → Only `TestCalculator::test_add` runs (it has add() checksum)
- Modify `calculator.clear_history()` → Only `TestCalculatorHistory::test_clear_history` runs

## Coverage Limitation

**Important**: Due to a fundamental limitation in coverage.py's dynamic context tracking, only the **first test to execute a code path** gets recorded as depending on that code. Subsequent tests calling the same code (under different contexts) don't get the dependency recorded.

This affects both:
1. **Tests across different files**: First file to import gets the dependency
2. **Tests within the same file**: First test to call a function gets the dependency

For example:
- `test_calculator.py` imports `math_utils` **first** → Gets math_utils dependencies
- `test_math_utils.py` runs **later** → Does NOT get math_utils dependency recorded

## Individual Test Dependencies

Due to the coverage limitation, here's what each test depends on (based on execution order):

```
test_calculator.py (runs first, gets math_utils dependency):
  TestCalculator::test_add         → add()
  TestCalculator::test_subtract    → subtract()
  TestCalculator::test_multiply    → multiply()
  TestCalculator::test_divide      → divide()
  TestCalculator::test_divide_by_zero → divide()
  TestCalculator::test_unknown_operator → (no math function)
  TestCalculatorHistory::test_history_recording → (add already traced, no dep)
  TestCalculatorHistory::test_clear_history → clear_history()

test_formatter.py (runs second, gets string_utils dependency):
  TestFormatter::test_upper_style  → uppercase()
  TestFormatter::test_lower_style  → lowercase()
  TestFormatter::test_title_style  → capitalize()
  TestFormatter::test_default_style → (uppercase already traced, no dep)
  TestFormatter::test_unknown_style → (no string function)
  TestFormatterStyleChange::test_change_style → set_style()

test_math_utils.py / test_string_utils.py:
  → Only depend on themselves (source deps already traced by earlier tests)
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

| Scenario | Description | Expected Selected Test |
|----------|-------------|----------------------|
| `modify_math_utils` | Change math_utils.add() | `TestCalculator::test_add` |
| `modify_string_utils` | Change string_utils.uppercase() | `TestFormatter::test_upper_style` |
| `modify_calculator_only` | Change Calculator.clear_history() | `TestCalculatorHistory::test_clear_history` |
| `modify_formatter_only` | Change Formatter.set_style() | `TestFormatterStyleChange::test_change_style` |
| `modify_test_only` | Change test_math_utils.py::TestAdd::test_positive_numbers | `TestAdd::test_positive_numbers` |
| `no_changes` | No modifications | (none) |
| `add_new_test` | Add a new test file | `test_new_feature`, `test_another_new` |
| `multiple_modifications` | Change subtract() and lowercase() | `test_subtract`, `test_lower_style` |

### Complex Code Pattern Scenarios

These scenarios test fingerprinting with advanced Python constructs:

| Scenario | Description | Pattern Tested |
|----------|-------------|----------------|
| `modify_nested_class_method` | Change Statistics.mean() | Nested class methods |
| `modify_static_method` | Change BaseProcessor.validate_data() | Static methods |
| `modify_generator` | Change fibonacci() | Generator functions (yield) |
| `modify_decorator` | Change memoize() | Decorators and closures |
| `modify_context_manager` | Change CacheManager.__enter__() | Context managers |

## How It Works

1. **Setup**: Creates a fresh temp directory with a copy of `sample_project/`
2. **Initialize Git**: Initializes a git repo (ezmon uses git for optimization)
3. **Create venv**: Creates a virtual environment and installs ezmon
4. **Version verification**: Verifies Python version matches expected (if `--expect-version` used)
5. **Baseline run**: Runs `pytest --ezmon` to build the dependency database
6. **Apply modifications**: Makes the code changes defined in the scenario
7. **Test run**: Runs `pytest --ezmon` again
8. **Verify**: Parses output to verify expected individual tests were selected/deselected

## Version Verification

The test runner verifies Python versions at three levels:
1. **Before tests**: Checks the Python executable matches `--expect-version`
2. **After venv creation**: Verifies the venv Python matches expected version
3. **After pytest runs**: Parses pytest output to verify it ran with correct Python

This ensures tests are actually running with the intended Python interpreter.

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

## Supported Python Versions

- Python 3.7
- Python 3.8
- Python 3.9
- Python 3.10
- Python 3.11
- Python 3.12
- Python 3.13
