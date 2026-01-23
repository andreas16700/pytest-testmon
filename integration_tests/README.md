# Integration Tests for pytest-ezmon

This directory contains integration tests that verify ezmon's test selection behavior using realistic scenarios.

## Structure

```
integration_tests/
├── run_integration_tests.py  # Test runner script
├── test_all_versions.py      # Multi-version test script
├── scenarios/
│   └── __init__.py           # Scenario definitions
└── sample_project/           # Example project with clear dependencies
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

## Dependency Graph

```
math_utils.py ──> calculator.py
                        │
                        ▼
              test_calculator.py (also tests math_utils indirectly)

math_utils.py ──> test_math_utils.py

string_utils.py ──> formatter.py
                        │
                        ▼
              test_formatter.py (also tests string_utils indirectly)

string_utils.py ──> test_string_utils.py
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

## How It Works

1. **Setup**: Creates a fresh temp directory with a copy of `sample_project/`
2. **Initialize Git**: Initializes a git repo (ezmon uses git for optimization)
3. **Create venv**: Creates a virtual environment and installs ezmon
4. **Version verification**: Verifies Python version matches expected (if `--expect-version` used)
5. **Baseline run**: Runs `pytest --ezmon` to build the dependency database
6. **Apply modifications**: Makes the code changes defined in the scenario
7. **Test run**: Runs `pytest --ezmon` again
8. **Verify**: Parses output to verify expected tests were selected/deselected

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
    expected_selected=["test_file.py"],
    expected_deselected=["other_test.py"],
))
```

**Important**: Ezmon uses AST-based fingerprinting, so modifications must change actual code structure, not just comments!

## Supported Python Versions

- Python 3.7
- Python 3.8
- Python 3.9
- Python 3.10
- Python 3.11
- Python 3.12
- Python 3.13
