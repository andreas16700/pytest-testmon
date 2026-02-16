# Blind Spot Testing Process Report

**Date:** 2026-01-31
**Status:** Investigation Complete
**Plugin:** pytest-ezmon-nocov (v2.1.4+nocov)

---

## Executive Summary

**CORRECTED FINDINGS**: After thorough investigation, **ezmon-nocov's file dependency tracking DOES work correctly** for all tested patterns, including `np.load()`, `np.fromfile()`, `np.memmap()`, and `builtins.open()`.

The earlier "blind spots" were caused by **test setup issues**, not actual tracking failures:

1. **macOS symlink issue**: `/tmp` → `/private/tmp` causes path resolution failures
2. **Absolute paths**: Using hardcoded absolute paths instead of relative paths
3. **Non-git projects**: Files not committed to git are intentionally not tracked

---

## Test Results (All Patterns PASS)

| Pattern | Description | Detected by ezmon? |
|---------|-------------|-------------------|
| `np.load` (double) | NumPy .npy files | ✓ YES |
| `np.load` (corrupt) | NumPy .npy files | ✓ YES |
| `np.load` (zero) | NumPy .npy files | ✓ YES |
| `np.fromfile` | Raw binary files | ✓ YES |
| `np.memmap` | Memory-mapped files | ✓ YES |
| `json_file` | JSON via builtins.open | ✓ YES |

**Result: 0 blind spots confirmed, 6/6 patterns correctly tracked**

---

## How File Dependency Tracking Works

### The Tracking Pipeline

1. **Hook Installation** (`_install_hooks`):
   - `builtins.open` → `_tracking_open`
   - `builtins.__import__` → `_tracking_import`

2. **File Read Tracking** (`_track_file`):
   ```python
   def _track_file(self, filepath, mode):
       # Only track read operations
       if 'r' not in mode:
           return

       # Check if file is in project
       relpath = self._is_in_project(filepath)
       if not relpath:
           return

       # Skip Python files (handled by import tracking)
       if relpath.endswith('.py'):
           return

       # Only track git-committed files
       sha = self._get_committed_file_sha(relpath)
       if not sha:
           return

       # Record the dependency
       self._tracked_files[context].add(TrackedFile(path=relpath, sha=sha))
   ```

3. **Requirements for Tracking**:
   - File must be within project directory
   - File must be committed to git
   - File must not be a `.py` file (handled separately)
   - File must be opened with read mode

### Why NumPy Functions Are Tracked

All numpy file I/O functions (`np.load`, `np.fromfile`, `np.memmap`) call `builtins.open()` internally:

```python
# Verification:
>>> import builtins
>>> calls = []
>>> original = builtins.open
>>> builtins.open = lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1]
>>> np.load('data.npy')
>>> calls
['/path/to/data.npy']  # np.load DOES call builtins.open
```

---

## Root Causes of Earlier Failures

### 1. macOS Symlink Issue

On macOS, `/tmp` is symlinked to `/private/tmp`:
```bash
$ ls -la /tmp
lrwxr-xr-x  1 root  wheel  11 Nov 22 14:49 /tmp -> private/tmp
```

This causes path resolution issues:
```python
# If rootdir = "/tmp/project" and filepath = "/private/tmp/project/data.npy"
relpath = os.path.relpath(filepath, rootdir)
# Returns: "../../private/tmp/project/data.npy" (starts with ".." = REJECTED)
```

**Fix**: Use `/var/tmp` instead of `/tmp` on macOS.

### 2. Absolute vs Relative Paths

Using absolute paths in test code:
```python
# BAD - absolute path may not resolve correctly
DATA_FILE = Path("/var/tmp/project/data/file.npy")

# GOOD - relative path from test file
DATA_FILE = Path(__file__).parent.parent / "data" / "file.npy"
```

### 3. Files Must Be in Git

The `_get_committed_file_sha()` check intentionally filters out:
- Files not in the git repository
- Ephemeral/generated files
- Files modified but not committed

This is by design to avoid tracking temporary test artifacts.

---

## Test Infrastructure

### Files

| File | Purpose |
|------|---------|
| `blind_spot_tester.py` | Systematic testing framework (FIXED) |
| `BLIND_SPOT_TESTING_PROCESS.md` | This report |

### Requirements

1. **Virtual environment** with numpy, pytest, ezmon-nocov
2. **Project must be a git repo** with committed files
3. **Use /var/tmp** instead of /tmp on macOS
4. **Use relative paths** in test code

### Running Tests

```bash
# Setup (one time)
python3.11 -m venv /tmp/ezmon_demo_env
/tmp/ezmon_demo_env/bin/pip install numpy pytest requests
/tmp/ezmon_demo_env/bin/pip install -e /path/to/pytest-testmon-nocov

# Run all pattern tests
cd /path/to/pytest-testmon-nocov/docs/reports
/tmp/ezmon_demo_env/bin/python blind_spot_tester.py test-all

# Run single pattern
/tmp/ezmon_demo_env/bin/python blind_spot_tester.py test --pattern np.load --change double
```

---

## Implications for scipy/matplotlib/pandas

### Previous Concern (Incorrect)

We previously believed that `np.load()` bypassed tracking because it uses "C-level I/O". This was incorrect.

### Actual Situation

**scipy/matplotlib/pandas data files WILL be tracked IF**:
1. The files are committed to git (they are)
2. The test code uses proper path resolution
3. No symlink issues in the test environment

**Potential real blind spots** (to investigate further):
1. Files opened via truly C-level code (C extensions calling `fopen()` directly)
2. Files accessed via subprocess (separate Python process)
3. Files using `sqlite3.connect()` (SQLite's C library)

---

## Verification Steps for Real Projects

To verify tracking works for a real project like scipy:

```bash
# 1. Clone/use existing scipy checkout
cd /path/to/scipy

# 2. Create venv with ezmon
python -m venv .venv
.venv/bin/pip install pytest pytest-ezmon-nocov numpy

# 3. Run a specific test that loads .npy data
.venv/bin/pytest --ezmon -v scipy/stats/tests/test_distributions.py::TestJFSkewT::test_compare_with_gamlss_r

# 4. Check if data file was tracked
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('.testmondata')
cursor = conn.cursor()
cursor.execute('SELECT filename FROM file_dependency')
for row in cursor.fetchall():
    print(row[0])
"

# Expected: scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy should appear
```

---

## Summary

| Issue | Status | Notes |
|-------|--------|-------|
| `np.load()` blind spot | ✗ NOT a blind spot | Works correctly |
| `np.fromfile()` blind spot | ✗ NOT a blind spot | Works correctly |
| `np.memmap()` blind spot | ✗ NOT a blind spot | Works correctly |
| `builtins.open()` for data | ✗ NOT a blind spot | Works correctly |
| macOS /tmp symlink | ✓ REAL issue | Use /var/tmp |
| Absolute path resolution | ✓ REAL issue | Use relative paths |
| Non-git files | ✓ By design | Files must be committed |

**Conclusion**: ezmon-nocov's file dependency tracking is more robust than initially believed. The "blind spots" were test setup issues, not actual tracking failures.
