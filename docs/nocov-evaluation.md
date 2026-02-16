# No-Coverage Experiment Evaluation

This document evaluates the impact of removing coverage.py from ezmon and relying solely on the import tracker for test dependency detection.

## Overview

The experiment replaces:
- **Per-function checksums** (coverage.py line tracking) → **Single file checksums** (AST-based)
- **Coverage.py integration** → **Import tracker only**

## What Triggers Test Re-runs

The nocov version uses **AST-based checksums** with docstring stripping. This means:

### Changes that DO NOT trigger re-runs:
- **Comments** - AST parsing ignores comments entirely
- **Docstrings** - Explicitly stripped before checksum calculation
- **Whitespace changes** - AST normalizes whitespace

### Changes that DO trigger re-runs:
- **Function/method body changes** - Any code modification
- **Import statement changes** - Adding/removing/modifying imports
- **Class/function signatures** - Parameter changes, decorators
- **Module-level code** - Constants, global variables
- **Non-Python files** - Any content change (uses raw content hash)

### Granularity
When a file changes, **ALL tests that import that file** re-run (file-level granularity).
This is less precise than coverage-based tracking but faster and never misses affected tests.

## Performance Results

### Timing Comparison

| Version | First Run | Warm Run | After Modification | Notes |
|---------|-----------|----------|-------------------|-------|
| **bare** (no plugin) | - | - | 0.264s | Baseline pytest |
| **pypi** (upstream testmon) | 0.393s (+49%) | 0.250s | 0.251s | Original testmon |
| **ezmon** (fork + coverage) | 1.240s (+370%) | 0.244s | 0.306s | Current fork |
| **nocov** (this experiment) | 0.415s (+58%) | 0.233s | 0.237s | No coverage |

### Key Findings

1. **First run speedup: 3x faster**
   - ezmon with coverage: 1.240s
   - nocov without coverage: 0.415s
   - Savings: 0.825s (67% reduction)

2. **Warm run (no changes)**: Similar performance across all versions (~0.23-0.25s)

3. **After modification**: nocov slightly faster (0.237s vs 0.306s)

## Test Selection Accuracy

After modifying `math_utils.py` (changed the `add()` function):

| Version | Tests Selected | Granularity |
|---------|---------------|-------------|
| **pypi** | 2 tests | Function-level |
| **ezmon** | 8 tests | Function-level |
| **nocov** | 20 tests | **File-level** |

### Detailed Test Selection

**ezmon (8 tests)** - Only tests that executed `add()`:
- `test_calculator.py::TestCalculator::test_add`
- `test_calculator.py::TestCalculatorHistory::test_clear_history`
- `test_calculator.py::TestCalculatorHistory::test_history_recording`
- `test_math_utils.py::TestAdd::test_positive_numbers`
- `test_math_utils.py::TestAdd::test_mixed_numbers`
- `test_math_utils.py::TestAdd::test_negative_numbers`
- `test_dynamic_loader.py::TestDynamicMathImport::test_dynamic_add`
- `test_dynamic_loader.py::TestDynamicMathImport::test_compute_with_dynamic_import`

**nocov (20 tests)** - ALL tests that import `math_utils.py`:
- All of the above, PLUS:
- `test_math_utils.py::TestMultiply::*` (3 tests)
- `test_math_utils.py::TestSubtract::*` (2 tests)
- `test_math_utils.py::TestDivide::*` (3 tests)
- `test_calculator.py::TestCalculator::test_multiply`
- `test_calculator.py::TestCalculator::test_subtract`
- `test_calculator.py::TestCalculator::test_divide`
- `test_calculator.py::TestCalculator::test_divide_by_zero`
- `test_calculator.py::TestCalculator::test_unknown_operator`

## Trade-off Analysis

### Pros of No-Coverage Approach

1. **Significantly faster first run** (3x improvement)
2. **Simpler implementation** (no coverage.py integration)
3. **No coverage overhead during test execution**
4. **Never misses affected tests** (conservative, always correct)

### Cons of No-Coverage Approach

1. **Less precise test selection**
   - Re-runs ALL tests importing a changed file
   - Doesn't distinguish which functions were used

2. **More unnecessary test runs**
   - In this example: 20 tests vs 8 tests (2.5x more)
   - Impact scales with file size and test count

### When No-Coverage Makes Sense

- **Small to medium projects** where re-running extra tests is cheap
- **CI environments** where first-run speed matters most
- **Projects with good test isolation** (tests in separate files)
- **When coverage.py conflicts** with other tools

### When Coverage-Based Makes Sense

- **Large monolithic files** with many functions
- **Long-running tests** where precision saves significant time
- **Projects where test files import many modules**

## Technical Implementation

### Changes Made

1. **`process_code.py`**:
   - Removed `Block` class and per-function tracking
   - Added `compute_file_checksum()` using AST dump
   - Simplified `Module` class to single checksum

2. **`db.py`**:
   - Schema: `file_checksum INTEGER` instead of `method_checksums BLOB`
   - DATA_VERSION bumped to 17
   - Simplified fingerprint matching (equality instead of set intersection)

3. **`testmon_core.py`**:
   - Removed coverage.py imports and integration
   - `TestmonCollector` no longer uses coverage
   - Dependencies tracked via `DependencyTracker` only

4. **`pytest_ezmon.py`**:
   - Removed `setup_collection_coverage()` calls
   - Simplified `_merge_collection_deps()`

### Checksum Algorithm

```python
def compute_file_checksum(source_code: str, ext: str = "py") -> int:
    if ext == "py":
        tree = ast.parse(source_code)
        _strip_docstrings(tree)  # Ignore docstring changes
        ast_repr = ast.dump(tree, annotate_fields=False)
        return crc32(ast_repr.encode("utf-8"))
    else:
        return crc32(source_code.encode("utf-8"))
```

Features:
- Comments don't affect checksum (AST-based)
- Docstrings stripped before hashing
- Non-Python files use content hash

## Profiling Data

### Where Time is Spent (First Run, 0.72s)

| Function | Time | % |
|----------|------|---|
| `dependency_tracker._tracking_import_module` | 0.133s | 18% |
| `pytest_ezmon` module import | 0.127s | 18% |
| `pytest_runtest_logreport` | 0.084s | 12% |
| `dependency_tracker._tracking_import` | 0.078s | 11% |
| `get_tests_fingerprints` | 0.060s | 8% |
| `compute_file_checksum` | 0.044s | 6% |

### Overhead Breakdown

- **Import tracking**: ~30% of plugin time
- **AST parsing/checksums**: ~6% of plugin time
- **Module imports**: ~18% (one-time cost)

## Import Tracker Overhead Analysis

The DependencyTracker hooks into Python's import system and file I/O to track dependencies. This section measures its overhead.

### Tracker Hooks

The DependencyTracker installs hooks for:
- `builtins.__import__` - tracks all Python imports
- `builtins.open` - tracks file reads (non-Python dependencies)
- `importlib.import_module` - tracks dynamic imports

### Overhead Measurement

| Metric | Value |
|--------|-------|
| Baseline (bare pytest) | 0.270s |
| nocov first run | 0.375s |
| nocov warm run | 0.224s |
| **First run overhead** | **0.105s (39%)** |
| Warm run overhead | -0.046s (faster than bare!) |

### Tracker Function Breakdown

| Function | Calls | Cumulative Time |
|----------|-------|-----------------|
| `_tracking_import` | 5,275 | 0.061s |
| `_track_import` | 5,111 | 0.037s |
| `_is_stdlib_module` | 5,033 | 0.025s |
| `_tracking_import_module` | 34 | 0.017s |
| `_tracking_open` | 29 | 0.008s |
| `_track_file` | 28 | 0.007s |
| **TOTAL TRACKER** | - | **0.171s** |

### Overhead Breakdown

```
Component               Time      % of First Run
-------------------------------------------------
Import hooks            0.115s    30.7%
File tracking           0.026s     6.8%
Other tracker           0.030s     8.0%
-------------------------------------------------
Total tracker           0.171s    45.5%
Non-tracker (DB, etc)   0.204s    54.5%
```

### Key Findings

1. **Import hook efficiency**: 5,275 imports tracked with only 0.061s overhead
   - Average: **0.012ms per import** (very efficient)

2. **File tracking is minimal**: Only 29 file opens tracked (0.008s)

3. **Stdlib checking is significant**: 5,033 calls to `_is_stdlib_module` (0.025s)
   - Potential optimization: better caching or faster stdlib detection

4. **Warm runs are faster than bare pytest**:
   - The overhead is offset by not running tests
   - Demonstrates the value of test selection

### Potential Optimizations

1. **Improve `_is_stdlib_module`**: Currently called for every import
   - Could use a pre-computed set of stdlib modules
   - Could cache results more aggressively

2. **Reduce `_get_module_file` calls**: 5,111 calls
   - Could batch lookups or use module.__file__ directly

3. **Skip tracking for known-irrelevant imports**:
   - pytest internal imports
   - Standard library (already filtered, but still tracked)

## Conclusion

The no-coverage approach provides a **3x speedup on first run** at the cost of **less precise test selection**. For the sample project:
- Saves 0.825s on first run
- Re-runs 2.5x more tests after modification

**Import tracker overhead is ~45% of the first run time** (0.171s), which is reasonable given it tracks 5,000+ imports. The tracker is efficient at ~0.012ms per import.

The trade-off favors no-coverage when:
- First-run performance is critical (CI pipelines)
- Test files are well-isolated (few tests per file)
- Individual tests are fast

The trade-off favors coverage when:
- Test precision is critical (slow tests)
- Files contain many functions with separate tests
- Re-running unnecessary tests is expensive

## Large Project Evaluation: Pandas

Tested on `pandas/tests/computation` (11,243 tests).

### Cold Start Results (Parallel Execution)

| Plugin | Wall Time | Overhead | DB Size |
|--------|-----------|----------|---------|
| **noplugin** | 7s | baseline | 0 KB |
| **nocov** | 10s | +43% | 7,964 KB |
| **testmon** | 23s | +229% | 15,828 KB |
| **ezmon** | 45s | +543% | 23,940 KB |

### Warm Run Results (No Code Changes)

| Plugin | Wall Time | Tests Selected |
|--------|-----------|----------------|
| **nocov** | 2.3s | 0 (all deselected) |
| **testmon** | 2.4s | 0 (all deselected) |
| **ezmon** | 2.7s | 0 (all deselected) |
| **noplugin** | 47s | 11,243 (runs all) |

### Cold Start Profiling (Single-threaded)

| Plugin | Total Time | Overhead |
|--------|------------|----------|
| **noplugin** | 38.4s | baseline |
| **nocov** | 47.0s | +22% |
| **testmon** | 127.2s | +231% |
| **ezmon** | 356.4s | +828% |

### Key Findings

1. **nocov is 2.3x faster than testmon** on cold starts
2. **nocov is 4.5x faster than ezmon** on cold starts
3. **nocov database is 50% smaller** than testmon (8MB vs 16MB)
4. **All plugins perform equally** on warm runs (~2.5s)

### Root Cause of ezmon Slowness

The ezmon fork starts **coverage.py fresh for each test** (for context isolation):
- 168s spent starting/stopping coverage.py (15ms × 11,243 tests)
- 140s spent in `inspect.py` for stack inspection

### To Reproduce

```bash
cd /Users/andrew_yos/pytest-super/pandas-eval

# Cold start comparison
./run_comparison.sh pandas/tests/computation

# Cold start profiling
./run_profiling.sh pandas/tests/computation

# Warm run comparison
./run_warm_simple.sh pandas/tests/computation/test_compat.py

# Warm run profiling
./run_warm_profile.sh pandas/tests/computation
```

Results are saved to `results/` directory.
