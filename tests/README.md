# pytest-ezmon Test Suite

This directory contains unit tests for pytest-ezmon, verifying the core fingerprinting and code processing logic.

## Test Structure

```
tests/
├── conftest.py             # Shared fixtures
├── test_process_code.py    # Unit tests for fingerprint generation (29 tests)
└── README.md               # This file

integration_tests/          # Separate directory for integration tests
├── run_integration_tests.py        # Main integration test runner (16 scenarios)
├── test_file_dependency_indirect.py # File/import dependency tracking test
├── test_parallel_execution.py      # Parallel execution tests (pytest-xdist)
├── test_all_versions.py            # Multi-version test script
├── scenarios/                      # Declarative test scenarios
│   └── __init__.py
├── sample_project/                 # Example project with clear dependencies
└── README.md                       # Integration test documentation
```

## Running All Tests

### Quick Commands

```bash
# Unit tests
pytest tests/ -v

# Integration tests (normal mode)
python integration_tests/run_integration_tests.py

# Integration tests (NetDB mode)
python integration_tests/run_integration_tests.py --netdb

# Parallel execution tests
pytest integration_tests/test_parallel_execution.py -v

# File dependency tracking test
python integration_tests/test_file_dependency_indirect.py
```

### Full Test Suite

```bash
# Run everything
pytest tests/ -v && \
python integration_tests/run_integration_tests.py && \
python integration_tests/run_integration_tests.py --netdb && \
pytest integration_tests/test_parallel_execution.py -v && \
python integration_tests/test_file_dependency_indirect.py
```

## Unit Tests (test_process_code.py)

29 tests verifying the core fingerprinting logic:

| Category | Tests |
|----------|-------|
| Block Extraction | Function blocks, class methods, nested functions, async functions |
| Comment Stripping | Single-line, indented, inline comments |
| Docstring Stripping | Module, function, class docstrings |
| Checksums | Identical code, whitespace handling, blob roundtrip |
| Fingerprint Matching | Unchanged code, changed functions, added/removed functions |
| Module Checksums | Constant changes, import changes |
| Error Handling | Syntax errors, non-Python files |

## Integration Tests

### Main Scenarios (run_integration_tests.py)

16 scenarios testing end-to-end selection behavior:

| Scenario | Description |
|----------|-------------|
| `modify_math_utils` | Change shared utility, verify dependent tests selected |
| `modify_string_utils` | Change string utility, verify correct selection |
| `modify_calculator_only` | Change class, verify only class tests selected |
| `modify_test_only` | Change test file, verify only that test selected |
| `no_changes` | No changes, verify no tests selected |
| `add_new_test` | Add new test, verify it runs |
| `multiple_modifications` | Multiple file changes, verify all affected selected |
| `modify_nested_class_method` | Nested class changes |
| `modify_static_method` | Static method changes |
| `modify_generator` | Generator function changes |
| `modify_decorator` | Decorator changes |
| `modify_context_manager` | Context manager changes |
| `modify_config_file` | Non-Python file changes |
| `modify_uncalled_method` | Unused method changes |
| `modify_globals` | Global variable changes |

### Parallel Execution Tests (test_parallel_execution.py)

5 tests verifying pytest-xdist integration:

- `test_sequential_baseline` - Sequential execution works
- `test_parallel_small_subset` - Parallel execution with few workers
- `test_parallel_coverage_saved` - Coverage data saved correctly in parallel
- `test_parallel_netdb_basic` - Parallel + NetDB works
- `test_parallel_netdb_coverage_collection` - Coverage collected in parallel NetDB mode

### File Dependency Test (test_file_dependency_indirect.py)

Tests collection-time dependency tracking:

1. Creates project where `__init__.py` reads config file at import time
2. Verifies config file is recorded as dependency
3. Verifies changing config file triggers test re-runs

### NetDB Mode

NetDB mode tests the network database functionality where test data is stored on a remote server instead of locally.

```bash
# Run integration tests in NetDB mode
python integration_tests/run_integration_tests.py --netdb
```

## Python Version Compatibility

Tests are designed to work with:
- Python 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
- pytest 5.x, 6.x, 7.x, 8.x, 9.x

## Documentation

- `docs/file-dependency-tracking.md` - Comprehensive documentation on dependency tracking
- `docs/dependency-graph.md` - Dependency graph visualization feature
