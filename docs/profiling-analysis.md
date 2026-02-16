# Performance Profiling Analysis - ezmon-nocov Plugin

## Test Environment
- **Test Subset**: `pandas/tests/frame` (~3000 tests)
- **Total Time**: 208.6 seconds
- **Profile Date**: 2026-02-01

## Top Bottlenecks by Own Time

| Rank | Function | Own Time | % | Calls | Description |
|------|----------|----------|---|-------|-------------|
| 1 | `sqlite3.executemany` | 31.1s | 15% | 15,027 | Batch DB inserts for dependencies |
| 2 | `db.py:determine_tests` | 29.4s | 14% | 1 | Finding which tests to run |
| 3 | `str.startswith` | 21.3s | 10% | 329M | Module name filtering |
| 4 | `dependency_tracker:553 <genexpr>` | 16.7s | 8% | 36M | Creating "before" module sets |
| 5 | `dependency_tracker:601 <genexpr>` | 16.5s | 8% | 36M | Creating "after" module sets |
| 6 | `_get_submodule_attrs` | 8.2s | 4% | 929K | dir() + getattr per module |
| 7 | `_checkpoint_import` | 5.7s | 3% | 120K | Import hook overhead |
| 8 | `db.all_test_executions` | 3.3s | 2% | 7 | Loading test history |

## Root Cause Analysis

### 1. Module Set Creation (Lines 553/601) - 33s total

**Problem**: For EVERY checkpoint import (120K calls), we iterate ALL ~1000 sys.modules entries twice:

```python
# Line 553
before = set(k for k in sys.modules if k.startswith(pkg_root + '.') or k == pkg_root)
# ... import happens ...
# Line 601
after = set(k for k in sys.modules if k.startswith(pkg_root + '.') or k == pkg_root)
```

**Impact**: 120K calls × 1000+ modules × 2 iterations × 2 startswith calls = 329M+ startswith calls

**Solution Ideas**:
- Maintain an incremental set of local package modules instead of rebuilding each time
- Only check modules that could have changed (newly added)
- Use a prefix tree/trie for faster prefix matching

### 2. restore_to_per_file_checkpoint (Line 921-926) - 21.5s cumulative

**Problem**: Same pattern, iterating all sys.modules for each test:

```python
for pkg_root in self._local_package_roots:
    current_local_mods = [
        k for k in sys.modules
        if k == pkg_root or k.startswith(pkg_root + '.')
    ]
```

**Impact**: Called 3004 times (once per test), iterating 1000+ modules each time

**Solution Ideas**:
- Cache the set of local modules and update incrementally
- Track module additions/removals rather than rescanning

### 3. _get_submodule_attrs - 20.2s cumulative (8.2s own)

**Problem**: Called 929K times, each call does expensive operations:

```python
for attr in dir(module):  # Expensive!
    if attr.startswith('_'):
        continue
    val = getattr(module, attr)  # Expensive!
    if isinstance(val, types.ModuleType):
        ...
```

**Solution Ideas**:
- Cache results (submodule attrs rarely change)
- Only recompute when module is modified
- Use `__dict__` instead of `dir()` where possible

### 4. Database Operations - 60s+ total

**Problems**:
- `executemany` called 15,027 times (31.1s) - batch size too small
- `determine_tests` takes 29.4s - complex SQL queries
- `fetch_or_create_file_fp` called 899K times (3.5s) - could be cached better

**Solution Ideas**:
- Larger batch sizes for dependency inserts
- Cache file fingerprints in memory
- Optimize SQL queries in determine_tests
- Consider using WAL mode for better write performance

## Optimization Priority

1. **High Impact, Medium Effort**: Cache local module set, update incrementally
2. **High Impact, Low Effort**: Cache `_get_submodule_attrs` results
3. **Medium Impact, Medium Effort**: Increase database batch sizes
4. **Medium Impact, High Effort**: Optimize `determine_tests` SQL

## Comparison with testmon

Based on run2 metrics:
- **testmon**: 17s (all tests deselected)
- **ezmon-nocov**: 38s (all tests deselected)

The 2x slowdown is likely due to:
1. Import tracking overhead (checkpoint/restore operations)
2. More fine-grained dependency tracking
3. Submodule attribute tracking for accurate restoration

## Next Steps

1. Implement local module set caching
2. Cache `_get_submodule_attrs` with invalidation
3. Profile database operations separately
4. Consider lazy initialization where possible
