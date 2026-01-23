# pytest-ezmon Test Suite

This directory contains unit tests for pytest-ezmon, verifying the core fingerprinting and code processing logic.

## Test Structure

```
tests/
├── conftest.py             # Shared fixtures
├── test_process_code.py    # Unit tests for fingerprint generation (24 tests)
└── README.md               # This file

integration_tests/          # Separate directory for integration tests
├── run_integration_tests.py    # Main integration test runner
├── test_all_versions.py        # Multi-version test script
├── scenarios/                  # Declarative test scenarios
│   └── __init__.py
├── sample_project/             # Example project with clear dependencies
└── README.md                   # Integration test documentation
```

## Running Unit Tests

### Quick Start

```bash
# Run all unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=ezmon --cov-report=term-missing
```

### Specific Tests

```bash
# Run fingerprint tests only
pytest tests/test_process_code.py -v

# Run a specific test
pytest tests/test_process_code.py::test_module_blocks -v
```

## Unit Tests (test_process_code.py)

These tests verify the core fingerprinting logic:

| Test | Description |
|------|-------------|
| `test_module_blocks` | Module creates correct block structure |
| `test_function_blocks` | Function bodies extracted as separate blocks |
| `test_class_method_blocks` | Class methods tracked correctly |
| `test_nested_function_blocks` | Nested functions handled properly |
| `test_comment_stripping` | Comments stripped before checksum |
| `test_fingerprint_matching` | Fingerprints match/mismatch correctly |
| `test_changed_function_body` | Function body changes detected |
| `test_changed_module_level` | Module-level changes detected |
| `test_added_function` | New functions don't break existing fingerprints |
| `test_removed_function` | Removed functions properly invalidate fingerprints |

## Integration Tests

For integration tests that verify end-to-end selection behavior, see `integration_tests/README.md`.

Integration tests use:
- A sample project with clear dependency chains
- Declarative scenarios that modify code and verify selection
- Multi-version Python testing (3.7-3.13)

```bash
# Run all integration scenarios
python integration_tests/run_integration_tests.py

# Run across all Python versions
python integration_tests/test_all_versions.py

# List available scenarios
python integration_tests/run_integration_tests.py --list
```

## Python Version Compatibility

Tests are designed to work with:
- Python 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
- pytest 5.x, 6.x, 7.x, 8.x, 9.x

Some edge cases may behave differently across versions due to AST changes.
