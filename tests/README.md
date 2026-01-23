# pytest-ezmon Test Suite

This directory contains the test suite for pytest-ezmon, verifying that the plugin correctly selects and deselects tests based on code changes.

## Test Structure

```
tests/
├── conftest.py                 # Shared fixtures including example_project
├── test_ezmon_selection.py     # Integration tests for selection behavior
├── test_process_code.py        # Unit tests for fingerprint generation
└── README.md                   # This file
```

## Running Tests

### Quick Start

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=ezmon --cov-report=term-missing
```

### Using tox (Multi-Version Testing)

```bash
# Install tox
pip install tox

# Run all environments
tox

# Run specific Python version
tox -e py37
tox -e py310

# Run specific pytest version
tox -e py310-pytest7
tox -e py310-pytest8
tox -e py310-pytest9

# Run linting
tox -e lint
```

### Specific Test Categories

```bash
# Run only baseline collection tests
pytest tests/test_ezmon_selection.py::TestBaselineCollection -v

# Run function-level granularity tests
pytest tests/test_ezmon_selection.py::TestFunctionLevelGranularity -v

# Run module-level change tests
pytest tests/test_ezmon_selection.py::TestModuleLevelChanges -v

# Run comment-only change tests
pytest tests/test_ezmon_selection.py::TestCommentOnlyChanges -v
```

## Test Categories

### Integration Tests (test_ezmon_selection.py)

These tests use pytest's `pytester` fixture to create temporary projects and verify ezmon's selection behavior:

| Category | Description |
|----------|-------------|
| `TestBaselineCollection` | First run executes all, second run deselects all |
| `TestFunctionLevelGranularity` | Modifying function X only selects tests using X |
| `TestModuleLevelChanges` | Module-level changes (constants) properly detected |
| `TestIndirectDependencies` | Indirect dependencies (A→B→C) handled correctly |
| `TestNewAndDeletedTests` | New tests always run, deleted tests handled gracefully |
| `TestCommentOnlyChanges` | Comments don't trigger test selection |
| `TestFailingTests` | Failing tests re-run until fixed |
| `TestNoSelectMode` | `--ezmon-noselect` runs all but still collects |
| `TestMultipleChanges` | Multiple simultaneous file changes |

### Unit Tests (test_process_code.py)

These tests verify the core fingerprinting logic:

| Test | Description |
|------|-------------|
| `test_module_fingerprint` | Module creates correct fingerprint |
| `test_function_blocks` | Function bodies extracted as separate blocks |
| `test_comment_stripping` | Comments stripped before checksum |
| `test_fingerprint_matching` | Fingerprints match/mismatch correctly |

## Example Project Structure

The `example_project` fixture creates this test structure:

```
src/
├── __init__.py
├── math_utils.py       # add, subtract, multiply, divide + MODULE_CONSTANT
├── string_utils.py     # uppercase, lowercase, reverse, join_strings
└── calculator.py       # Calculator class using add/subtract

tests/
├── test_math_add.py        # Tests only add() - 3 tests
├── test_math_subtract.py   # Tests only subtract() - 2 tests
├── test_math_multiply.py   # Tests only multiply() - 2 tests
├── test_math_all.py        # Tests all math functions - 2 tests
├── test_strings.py         # Tests string_utils - 3 tests
├── test_calculator.py      # Tests Calculator (indirect) - 3 tests
└── test_import_only.py     # Imports module, uses constant - 2 tests
```

This structure allows testing:
- **Function isolation**: Modify `add()` → only `test_math_add` and `test_calculator` run
- **Module isolation**: Modify `string_utils` → only `test_strings` runs
- **Constant tracking**: Modify `MODULE_CONSTANT` → `test_import_only` runs
- **Indirect deps**: `Calculator` uses `add`/`subtract` → changes propagate

## Python Version Compatibility

Tests are designed to work with:
- Python 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
- pytest 5.x, 6.x, 7.x, 8.x, 9.x

Some edge cases may behave differently across versions due to AST changes.
