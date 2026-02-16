# Matplotlib Test Selection Debugging Status

**Date**: 2026-01-25
**Current State**: Investigating test selection issues

## Summary

We're testing ezmon's test selection on the matplotlib fork (`andreas16700/matplotlib`) with CI workflow `Tests (fork, macOS + ezmon NetDB)`. The goal is to verify that when only `_docstring.py` is modified, only 1 test should be selected: `test_copy_docstring_and_deprecators`.

## Current Symptoms

1. **Expected behavior**: 1 file changed (`_docstring.py`), 1 test selected
2. **Actual behavior**: 1-56 files reported as "changed", 1506 tests selected

## Investigation Findings

### Debug Logging Added

Added debug logging to `testmon_core.py` to see which files are marked as changed by `fetch_unknown_files`:

```python
# Debug: Log changed files
if new_changed_file_data:
    logger.info(f"DEBUG: {len(new_changed_file_data)} files marked as changed")
    for f in sorted(new_changed_file_data)[:10]:
        logger.info(f"DEBUG:   changed file: {f}")
```

### Key Observation (from run 21331466182)

```
DEBUG: 1 files marked as changed
DEBUG:   changed file: lib/matplotlib/_afm.py
ezmon: changed files: 56, unchanged files: 223, environment: default
collected 5830 items / 4324 deselected / 2 skipped / 1506 selected
```

**Important distinction**:
- "1 files marked as changed" = source files with FSHA mismatch (from `fetch_unknown_files`)
- "changed files: 56" = TEST FILES containing affected tests (from `unstable_files`)

### Root Cause Analysis

1. **`_afm.py` leftover change**: Earlier testing left `_afm.py` modified (splitting `return int(float(x))` into two lines). This was NOT part of our intended test.

2. **Why `_afm.py` affects 1506 tests**: `_afm.py` is imported by many modules throughout matplotlib, causing a cascade of affected tests.

3. **Why `_docstring.py` doesn't show as changed**: The `_docstring.py` fingerprint was already recorded in a previous successful run, so its FSHA matches the baseline.

4. **Why `_afm.py` FSHA keeps mismatching**: This is the core mystery. Even after multiple runs, `_afm.py`'s fingerprint doesn't seem to be stored correctly.

### Hypotheses for FSHA Mismatch

1. **NULL fsha in database**: When fingerprints are stored, fsha might be NULL for some reason
2. **Multiple fingerprint records**: The `file_fp` table might have multiple records for the same file with different checksums
3. **Execution context mismatch**: The fingerprints are stored but not associated with the correct exec_id

### Actions Taken

1. Reverted `_afm.py` to clean state (latest commit: `08e3466376`)
2. Kept only `_docstring.py` change
3. Triggered new workflow run to verify

## Files Modified in matplotlib Fork

```
.github/workflows/tests.yml  - CI configuration
lib/matplotlib/_docstring.py - Our test change (split one line into two)
```

The `_docstring.py` change:
```python
# Original:
target.__doc__ = source.__doc__

# Modified:
doc = source.__doc__
target.__doc__ = doc
```

## Next Steps

1. **Wait for latest workflow** (run triggered by commit `08e3466376`) to see if reverting `_afm.py` fixes the issue
2. **If still failing**: Add debug logging to track what fsha values are being stored vs compared
3. **Check server-side logic**: Verify `fetch_unknown_files` and `batch_insert` are handling fsha correctly
4. **Run integration tests**: Verify the plugin works correctly in local mode

## Relevant Code Paths

### Client-side (ezmon)
- `testmon_core.py:determine_stable()` - Computes changed files and affected tests
- `testmon_core.py:collect_mhashes()` - Builds method checksums for changed files
- `net_db.py:fetch_unknown_files()` - Gets files with fsha mismatch from server
- `net_db.py:insert_test_file_fps()` - Stores test execution results with fingerprints

### Server-side (ez-viz/app.py)
- `/api/rpc/files/fetch_unknown` - Compares fsha from disk vs database
- `/api/rpc/test_execution/batch_insert` - Stores test results with fingerprints
- `/api/rpc/tests/determine` - Finds affected tests based on fingerprint changes

### Critical SQL Query (fetch_unknown)
```sql
SELECT DISTINCT f.filename
FROM test_execution te, test_execution_file_fp te_ffp, file_fp f
LEFT OUTER JOIN changed_files_fshas chff
ON f.filename = chff.filename AND f.fsha = chff.fsha AND chff.exec_id = ?
WHERE
    te.environment_id = ? AND
    te.id = te_ffp.test_execution_id AND
    te_ffp.fingerprint_id = f.id AND
    (f.fsha IS NULL OR chff.fsha IS NULL)
```

This query returns files where:
1. The stored fsha is NULL, OR
2. The LEFT JOIN didn't find a matching (filename, fsha) pair

## Configuration

- **Repo ID**: andreas16700/matplotlib
- **Job ID**: macos-14-py3.11
- **Environment**: default
- **NetDB Server**: https://ezmon.aloiz.ch
- **Local packages detected**: ['matplotlib'] (mpl_toolkits is namespace package)

## Integration Tests Status

**All 16 integration tests PASSED** on 2026-01-25.

The plugin works correctly in local mode (SQLite database). This confirms the core fingerprinting and test selection logic is working.

## Merge Investigation

Investigated recent merges from team commits:

### Merge History
```
* ef8e123 Debug: log which files are marked as changed
*   67e17f1 Merge remote-tracking branch 'origin/main'
|\
| * cb90803 Debug: add logging for local package detection
| * 1931837 Fix deploy.yml
| * 34c5afe Auto-detect and exclude local packages from dependency tracking
* | 804b34b log added (team)
|/
*   50e2285 Merge remote-tracking branch 'origin/main'
|\
| * 9e853ba Add --tests-for option
| * b7318d3 Add coverage analysis feature to impact tool
* | 9513409 workflow file generation flow updated (team)
```

### Findings

1. **No lost changes detected**: All key functionality is present in the current state:
   - `is_local_package()` function in `common.py`
   - Debug logging in `testmon_core.py`
   - Local package detection in `get_system_packages()`

2. **Team's additions**:
   - Added logging in `pytest_ezmon.py` (line 328: `logger.info("pytest_configure function!")`)
   - Updated workflow file generation UI
   - Minor logging improvements in `graph.py` and `server_sync.py`

3. **No merge conflicts** observed in critical paths.

## Workflow Run Results

### Run 21331782007 (Revert _afm.py)

**Result**: `_afm.py` was STILL detected as changed!

```
DEBUG: 1 files marked as changed
DEBUG:   changed file: lib/matplotlib/_afm.py
collected 5921 items / 4180 deselected / 2 skipped / 1741 selected
```

**Explanation**: The baseline in NetDB had the MODIFIED `_afm.py` fingerprints (from earlier tests). When we reverted to the original code, the fingerprint comparison correctly detected a difference - just in the opposite direction.

This confirms the fingerprint system IS working correctly!

### Key Insight

The "changed file" detection works both ways:
- Modified file → detects the modification
- Reverted file → detects the reversion (because baseline still has old fingerprints)

The workflow completed and should have stored the NEW (original) fingerprints for `_afm.py`.

### Next Run (21332072608 - empty commit after revert)

**Expected**: 0 files changed, 0 tests selected
**Actual**: STILL showing `_afm.py` as changed!

```
DEBUG: 1 files marked as changed
DEBUG:   changed file: lib/matplotlib/_afm.py
collected 5830 items / 4324 deselected / 2 skipped / 1506 selected
```

## Root Cause Hypothesis

The fingerprints ARE being stored, but there's an issue with how they're linked or compared:

1. **Multiple fingerprint records**: The `file_fp` table might have multiple records for `_afm.py`:
   - Old: (filename, checksums=MODIFIED, fsha=MODIFIED_HASH)
   - New: (filename, checksums=ORIGINAL, fsha=ORIGINAL_HASH)

2. **Test linkage**: When tests run, their fingerprints are updated. But if some tests aren't running (deselected), they still have OLD fingerprint links.

3. **Comparison query**: The `fetch_unknown_files` query checks ALL fingerprints for tests in this environment. If ANY fingerprint has a non-matching fsha, that file is considered "changed".

## Next Investigation Steps

1. Add debug logging in `fetch_unknown_files` to show:
   - What fshas are stored in the database for `_afm.py`
   - What fsha is computed from the current file
   - Which tests are linked to non-matching fingerprints

2. Check if the issue is with how old fingerprint records are retained even after tests re-run

3. Consider whether the fingerprint comparison logic needs adjustment for this edge case
