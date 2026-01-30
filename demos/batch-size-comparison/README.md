# Batch Size Comparison Demo

This demo illustrates a critical difference between the original `pytest-testmon` and our `ezmon` fork regarding how test dependencies are tracked.

## The Problem: Coverage Context Limitation

Both plugins use `coverage.py` to track which lines of code each test executes. Coverage.py supports "dynamic contexts" which allow attributing executed lines to specific tests via `switch_context()`.

**However, coverage.py has a fundamental limitation**: within a single coverage session, each line is only attributed to the **first** context (test) that executes it. Subsequent tests that execute the same line do not get that line recorded in their coverage data.

### How Batching Works

The `TEST_BATCH_SIZE` constant controls how many tests share a single coverage session:

```
TEST_BATCH_SIZE = 250 (original testmon)
─────────────────────────────────────────
Test 1 runs → coverage records lines under "test_1" context
Test 2 runs → coverage records ONLY NEW lines under "test_2" context
             (lines already recorded are NOT re-attributed!)
...
Test 250 runs → ...
→ Batch processed: extract data, ERASE coverage, restart fresh
Test 251 runs → new coverage session begins
```

```
TEST_BATCH_SIZE = 1 (ezmon)
─────────────────────────────────────────
Test 1 runs → coverage records lines
→ Batch processed: extract data, ERASE coverage, restart fresh
Test 2 runs → fresh session, gets its OWN complete coverage
→ Batch processed: extract data, ERASE coverage, restart fresh
...
```

### The Bug in Original Testmon

Consider this scenario:
- `src/math_utils.py` contains an `add(a, b)` function
- `test_add_positive` calls `add(2, 3)`
- `test_add_negative` also calls `add(-1, -2)`

With batch size 250:
1. `test_add_positive` runs first, coverage records line 4 (`return a + b`) for this test
2. `test_add_negative` runs second, also executes line 4, but coverage.py **doesn't record it again**
3. When `add()` body changes, only `test_add_positive` is selected
4. `test_add_negative` is **incorrectly skipped** even though it depends on `add()`!

### The Fix in Ezmon

With batch size 1:
1. `test_add_positive` runs, coverage records line 4, coverage is **erased**
2. `test_add_negative` runs with fresh coverage, records line 4 for itself
3. When `add()` body changes, **both tests are correctly selected**

## Demo Results

Running the demo produces:

**Original pytest-testmon (v2.2.0, batch size 250):**
```
collected 2 items / 1 deselected / 1 selected
tests/test_math.py::test_add_positive PASSED
```
❌ Only one test selected - `test_add_negative` incorrectly skipped!

**Ezmon (batch size 1):**
```
tests/test_math.py::test_add_negative PASSED
tests/test_math.py::test_add_positive PASSED
```
✅ Both tests correctly selected!

## Running the Demo

```bash
# Run the complete demo (creates isolated venvs, installs both plugins)
./run_demo.sh
```

## Trade-offs

| Aspect | Batch Size 250 | Batch Size 1 |
|--------|---------------|--------------|
| Correctness | May miss dependencies | Correct per-test tracking |
| Performance | Fewer coverage start/stop cycles | More overhead per test |
| Memory | Accumulates coverage data | Fresh state each test |

For most test suites, the overhead of batch size 1 is negligible compared to test execution time, and correctness is more important than micro-optimizations.

## Technical Details

The relevant code is in `ezmon/testmon_core.py`:

```python
TEST_BATCH_SIZE = 1  # Process coverage after each test

def get_batch_coverage_data(self):
    # ...
    if self.cov and (
        len(self.batched_test_names) >= TEST_BATCH_SIZE  # Triggers after each test
        or self._next_test_name is None
        or self._interrupted_at
    ):
        self.cov.stop()
        nodes_files_lines, lines_data = self.get_nodes_files_lines(...)
        self.cov.erase()   # Clear coverage data
        self.cov.start()   # Fresh session for next test
        self.batched_test_names = set()
    return nodes_files_lines
```

## See Also

- [FINGERPRINTING.md](../../docs/FINGERPRINTING.md) - How block-based fingerprinting works
- [Coverage.py Dynamic Contexts](https://coverage.readthedocs.io/en/latest/contexts.html) - Official docs
