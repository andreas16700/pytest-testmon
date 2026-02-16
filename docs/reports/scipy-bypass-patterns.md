# SciPy: Import/File Tracking Bypass Patterns

## Executive Summary

**Total bypass patterns found: ~347**
- Library code: ~35
- Test code: ~170
- Tools/Build: ~61
- Benchmarks: ~28

**Most critical for test tracking:**
1. `np.load()` / `np.fromfile()` - 47 occurrences, heavy test data usage
2. C extension file I/O - SuperLU, fast_matrix_market use `fopen()`/`fread()` directly
3. `exec()` in stats module - Dynamic method generation
4. Binary format I/O - loadmat, FortranFile, WAV files

---

## 1. NumPy File I/O (47 occurrences) - HIGHEST IMPACT

### 1.1 Library Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/io/_fortran.py` | 272 | `np.fromfile(self._fp, dtype=dtype, count=num_blocks)` | Fortran record reading |
| `scipy/io/wavfile.py` | 516 | `np.fromfile(fid, dtype=dtype, count=count)` | WAV file reading |
| `scipy/io/wavfile.py` | 534 | `np.memmap(fid, dtype=dtype, mode='c', ...)` | WAV memory mapping |
| `scipy/sparse/_matrix_io.py` | 138 | `np.load(file, **PICKLE_KWARGS)` | Sparse matrix NPZ |
| `scipy/stats/_sobol.pyx` | 150 | `dns = np.load(f)` | Sobol sequence data |

### 1.2 Test Code (30+ occurrences)

| File | Line | Code |
|------|------|------|
| `scipy/special/tests/test_data.py` | 32, 36, 40 | `DATASETS_BOOST = np.load(f)` (3 datasets) |
| `scipy/stats/tests/test_distributions.py` | 4245 | `np.load(Path(__file__).parent / "data/...")` |
| `scipy/stats/tests/test_distributions.py` | 5579 | `np.load(...)` |
| `scipy/stats/tests/test_distributions.py` | 5623 | `np.load(...)` |
| `scipy/stats/tests/test_distributions.py` | 5639 | `np.load(...)` |
| `scipy/stats/tests/test_distributions.py` | 10189 | `np.load(...)` |
| `scipy/fft/_pocketfft/tests/test_real_transforms.py` | 33 | `MDATA = np.load(...)` |
| `scipy/fft/_pocketfft/tests/test_real_transforms.py` | 42-48 | `FFTWDATA_* = np.load(...)` (4 files) |
| `scipy/fftpack/tests/test_real_transforms.py` | 13, 22, 23 | `np.load(...)` (3 files) |
| `scipy/linalg/tests/test_solvers.py` | 27 | `np.load(filename)` |
| `scipy/interpolate/tests/test_interpnd.py` | 248 | `np.load(data_file(...))` |
| `scipy/interpolate/tests/test_fitpack.py` | 387 | `np.load(data_file(...))` |
| `scipy/interpolate/tests/test_bsplines.py` | 2273 | `np.load(data_file(...))` |
| `scipy/spatial/tests/test_qhull.py` | 334 | `np.load(os.path.join(...))` |
| `scipy/sparse/linalg/tests/test_propack.py` | 106 | `np.load(filename, allow_pickle=True)` |
| `scipy/io/tests/test_fortran.py` | 147, 176, 179, 206 | `np.fromfile(f, dtype=..., count=...)` (4x) |

### 1.3 Benchmark Code

| File | Line | Code |
|------|------|------|
| `benchmarks/benchmarks/sparse_linalg_svds.py` | 47 | `np.load(datafile, allow_pickle=True)` |
| `benchmarks/benchmarks/optimize_linprog.py` | 165, 209 | `np.load(datafile, ...)` (2x) |
| `benchmarks/benchmarks/optimize_qap.py` | 45, 47 | `np.load(...)` (2x) |
| `benchmarks/benchmarks/optimize_milp.py` | 32 | `np.load(datafile, ...)` |

---

## 2. exec() Calls (7 occurrences)

### 2.1 Stats Module - Dynamic Method Generation

**File:** `scipy/stats/_distn_infrastructure.py`

| Line | Code | Purpose |
|------|------|---------|
| 369 | `exec('del ' + obj)` | Cleanup _doc_* variables |
| 748 | `exec(self._parse_arg_template, ns)` | Generate argument parser methods |

**Context for line 748:**
```python
def _attach_argparser_methods(self):
    ns = {}
    exec(self._parse_arg_template, ns)
    for name in ['_parse_args', '_parse_args_stats', '_parse_args_rvs']:
        setattr(self, name, types.MethodType(ns[name], self))
```

This dynamically creates methods for statistical distributions at runtime.

### 2.2 Optimize Module

**File:** `scipy/optimize/_nonlin.py:1634`

```python
exec(wrapper, ns)
```

Purpose: Generate wrapper functions for nonlinear solvers.

### 2.3 Build Utilities

**File:** `scipy/_build_utils/tempita/_tempita.py`

| Line | Code |
|------|------|
| 232 | `self._exec(code[2], ns, pos)` |
| 321 | `def _exec(self, code, ns, pos):` |
| 324 | `exec(code, self.default_namespace, ns)` |

### 2.4 Library Utility

**File:** `scipy/_lib/_bunch.py:160`

```python
exec(s, namespace)
```

---

## 3. C Extension File I/O (10+ files)

### 3.1 SuperLU Matrix Readers

**Directory:** `scipy/sparse/linalg/_dsolve/SuperLU/SRC/`

| File | Function | Format |
|------|----------|--------|
| `dreadMM.c` | `dreadMM(FILE *fp, ...)` | Matrix Market (double) |
| `sreadMM.c` | `sreadMM(FILE *fp, ...)` | Matrix Market (single) |
| `zreadMM.c` | `zreadMM(FILE *fp, ...)` | Matrix Market (double complex) |
| `creadMM.c` | `creadMM(FILE *fp, ...)` | Matrix Market (single complex) |
| `dreadtriple.c` | `dreadtriple(FILE *fp, ...)` | Triple format (double) |
| `sreadtriple.c` | `sreadtriple(FILE *fp, ...)` | Triple format (single) |
| `zreadtriple.c` | `zreadtriple(FILE *fp, ...)` | Triple format (double complex) |
| `creadtriple.c` | `creadtriple(FILE *fp, ...)` | Triple format (single complex) |

**Example from dreadMM.c:56:**
```c
fgets(line, 512, fp);  // Read header
fscanf(fp, "%d%d%d", m, n, nonz);  // Parse dimensions
```

### 3.2 Fast Matrix Market (C++)

**Directory:** `scipy/io/_fast_matrix_market/src/`

| File | Purpose |
|------|---------|
| `_fmm_core.cpp` | Core Matrix Market I/O |
| `_fmm_core_read_array.cpp` | Array format reader |
| `_fmm_core_write_array.cpp` | Array format writer |

---

## 4. Memory-Mapped Files (5 occurrences)

### Library Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/io/_netcdf.py` | 42 | `import mmap as mm` | NetCDF reading |
| `scipy/io/wavfile.py` | 534 | `np.memmap(fid, dtype=dtype, mode='c', ...)` | WAV optional mmap |

### Test Code

| File | Line | Code |
|------|------|------|
| `scipy/interpolate/tests/test_bsplines.py` | 717 | `np.memmap(str(tmpdir.join(...)), mode='w+', ...)` |
| `scipy/interpolate/tests/test_bsplines.py` | 720 | `np.memmap(str(tmpdir.join(...)), mode='w+', ...)` |

---

## 5. FortranFile Binary I/O (31 occurrences)

### Library Definition

**File:** `scipy/io/_fortran.py:33-355`

```python
class FortranFile:
    """
    A file object for unformatted sequential files from Fortran code.
    """
    def read_record(self, *dtypes, **kwargs):
        # Line 272
        r = np.fromfile(self._fp, dtype=dtype, count=num_blocks)

    def write_record(self, *items):
        # Line 165-168
        nb.tofile(self._fp)
        item.tofile(self._fp)
        nb.tofile(self._fp)
```

### Test Usage

**File:** `scipy/io/tests/test_fortran.py` - 30 occurrences

| Line | Pattern |
|------|---------|
| 39, 50, 73, 100 | `FortranFile(...)` construction |
| 122, 125 | `f.read_record(...)` |
| 147, 176, 179, 206 | `np.fromfile(f, ...)` direct usage |
| 225, 230, 238, 242 | Various FortranFile operations |
| ... | (30 total) |

---

## 6. MATLAB File I/O (171 occurrences)

### Library API

**File:** `scipy/io/matlab/mio.py`

| Line | Function | Purpose |
|------|----------|---------|
| 87 | `def loadmat(file_name, ...)` | Load .mat file |
| 253 | `def savemat(file_name, mdict, ...)` | Save .mat file |
| 323 | `def whosmat(file_name, ...)` | List .mat contents |

### Implementation

| File | Purpose |
|------|---------|
| `scipy/io/matlab/_mio5.py` | MATLAB v5 format |
| `scipy/io/matlab/_mio4.py` | MATLAB v4 format |
| `scipy/io/matlab/_miobase.py` | Base classes |
| `scipy/io/matlab/_streams.pyx` | Cython stream handling |

### Test Data Directory

**Location:** `scipy/io/matlab/tests/data/`

Contains .mat files in various formats used by ~100 test cases.

---

## 7. ctypes.CDLL (5 occurrences)

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/integrate/tests/test_quadpack.py` | 45 | `self.lib = ctypes.CDLL(file)` | Load libm for tests |
| `scipy/integrate/_quadpack_py.py` | 409 | `ctypes.CDLL('/home/.../testlib.*')` | Documentation example |
| `benchmarks/benchmarks/integrate.py` | 99 | `ctypes.CDLL(clib_test.__file__)` | Benchmark integration |

**test_quadpack.py context:**
```python
def setup_method(self):
    if sys.platform == 'win32':
        files = ['api-ms-win-crt-math-l1-1-0.dll']
    elif sys.platform == 'darwin':
        files = ['libm.dylib']
    else:
        files = ['libm.so', 'libm.so.6']

    for file in files:
        try:
            self.lib = ctypes.CDLL(file)
            break
        except OSError:
            pass
```

---

## 8. importlib.resources (10 occurrences)

### Library Code

**File:** `scipy/stats/_sobol.pyx:147-150`

```python
_curdir = importlib.resources.files("scipy.stats")
_npzfile = _curdir.joinpath("_sobol_direction_numbers.npz")
with importlib.resources.as_file(_npzfile) as f:
    dns = np.load(f)
```

### Test Code

**File:** `scipy/special/tests/test_data.py:28-41`

```python
_datadir = importlib.resources.files('scipy.special.tests.data')

_boost_npz = _datadir.joinpath('boost.npz')
with importlib.resources.as_file(_boost_npz) as f:
    DATASETS_BOOST = np.load(f)

_gsl_npz = _datadir.joinpath('gsl.npz')
with importlib.resources.as_file(_gsl_npz) as f:
    DATASETS_GSL = np.load(f)

_local_npz = _datadir.joinpath('local.npz')
with importlib.resources.as_file(_local_npz) as f:
    DATASETS_LOCAL = np.load(f)
```

---

## 9. sys.modules Manipulation (11 occurrences)

### Library Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/stats/_new_distributions.py` | 538 | `sys.modules[__name__].__dict__` | Dynamic docstrings |
| `scipy/optimize/_optimize.py` | 4156 | `sys.modules[mod_name]` | Object resolution |
| `scipy/datasets/_fetchers.py` | 36 | `sys.modules['scipy'].__version__` | User-Agent header |

### Test/Tool Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/_lib/_testutils.py` | 93 | `sys.modules[self.module_name]` | Test utilities |
| `scipy/_lib/pyprima/tests/conftest.py` | 14 | `del sys.modules[m]` | Test cleanup |
| `tools/refguide_check.py` | 526 | `sys.modules[module_name]` | Doc checking |
| `doc/source/conf.py` | 485 | `sys.modules[obj.__module__]` | Sphinx config |
| `doc/source/scipyoptdoc.py` | 53 | `sys.modules[module_name]` | Doc generation |
| `tools/wheels/check_license.py` | 35 | `sys.modules[args.module]` | License check |

---

## 10. Subprocess Usage (51 occurrences)

### Test Code

| File | Line | Code | Purpose |
|------|------|------|---------|
| `scipy/_lib/tests/test_import_cycles.py` | 14 | `subprocess.Popen([sys.executable, '-c', f'import {module}'])` | Import cycle test |
| `benchmarks/benchmarks/common.py` | 338, 365 | `subprocess.Popen([sys.executable, '-c', code])` | Benchmark isolation |

### Build/Tool Code (Primary Usage - 43 occurrences)

| File | Lines | Purpose |
|------|-------|---------|
| `tools/lint.py` | 23-127 | Git and linting operations |
| `tools/generate_f2pymod.py` | 299-300 | F2PY code generation |
| `tools/pre-commit-hook.py` | 34, 48, 51-72, 87, 98 | Pre-commit operations |
| `tools/authors.py` | 194-226 | Author tracking |
| `tools/gitversion.py` | 30-33 | Version extraction |
| `scipy/_lib/_testutils.py` | 287, 309, 316, 320 | Meson build operations |
| `.spin/cmds.py` | 1042, 1049, 1062 | Spin CLI |

---

## Detection Script

```python
#!/usr/bin/env python3
"""
Detect scipy test modules using bypass patterns.
Run from scipy repository root.
"""

import ast
import sys
from pathlib import Path

SCIPY_ROOT = Path("scipy")

class BypassPatternVisitor(ast.NodeVisitor):
    def __init__(self, filepath):
        self.filepath = filepath
        self.findings = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            # numpy file I/O
            if node.func.attr in ('load', 'fromfile', 'memmap', 'loadtxt', 'tofile'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in ('np', 'numpy'):
                    self.findings.append({
                        'pattern': f'numpy.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })

            # subprocess
            elif node.func.attr in ('run', 'Popen', 'check_output', 'check_call', 'call'):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                    self.findings.append({
                        'pattern': f'subprocess.{node.func.attr}',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })

            # mmap
            elif node.func.attr == 'mmap':
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'mmap':
                    self.findings.append({
                        'pattern': 'mmap.mmap',
                        'line': node.lineno,
                        'file': str(self.filepath)
                    })

            # ctypes.CDLL
            elif node.func.attr == 'CDLL':
                self.findings.append({
                    'pattern': 'ctypes.CDLL',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })

            # loadmat/savemat
            elif node.func.attr in ('loadmat', 'savemat', 'whosmat'):
                self.findings.append({
                    'pattern': f'scipy.io.{node.func.attr}',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })

            # FortranFile
            elif node.func.attr == 'FortranFile':
                self.findings.append({
                    'pattern': 'FortranFile',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })

        # exec()
        if isinstance(node.func, ast.Name) and node.func.id == 'exec':
            self.findings.append({
                'pattern': 'exec()',
                'line': node.lineno,
                'file': str(self.filepath)
            })

        self.generic_visit(node)

    def visit_Subscript(self, node):
        # sys.modules[...]
        if isinstance(node.value, ast.Attribute):
            if (isinstance(node.value.value, ast.Name) and
                node.value.value.id == 'sys' and
                node.value.attr == 'modules'):
                self.findings.append({
                    'pattern': 'sys.modules',
                    'line': node.lineno,
                    'file': str(self.filepath)
                })
        self.generic_visit(node)

def scan_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=str(filepath))
        visitor = BypassPatternVisitor(filepath)
        visitor.visit(tree)
        return visitor.findings
    except (SyntaxError, UnicodeDecodeError):
        return []

def main():
    all_findings = []

    # Scan test directories
    for test_dir in SCIPY_ROOT.rglob("tests"):
        if test_dir.is_dir():
            for pyfile in test_dir.rglob("*.py"):
                findings = scan_file(pyfile)
                for f in findings:
                    f['type'] = 'test'
                all_findings.extend(findings)

    # Scan library code (excluding tests)
    for pyfile in SCIPY_ROOT.rglob("*.py"):
        if "tests" not in str(pyfile) and "benchmarks" not in str(pyfile):
            findings = scan_file(pyfile)
            for f in findings:
                f['type'] = 'library'
            all_findings.extend(findings)

    # Group by pattern
    by_pattern = {}
    for f in all_findings:
        by_pattern.setdefault(f['pattern'], []).append(f)

    print("=" * 70)
    print("SCIPY BYPASS PATTERNS SUMMARY")
    print("=" * 70)

    for pattern, findings in sorted(by_pattern.items(), key=lambda x: -len(x[1])):
        lib_count = len([f for f in findings if f.get('type') == 'library'])
        test_count = len([f for f in findings if f.get('type') == 'test'])
        print(f"\n{pattern}: {len(findings)} total ({lib_count} library, {test_count} test)")
        for f in findings[:5]:  # Show first 5
            print(f"  {f['file']}:{f['line']}")
        if len(findings) > 5:
            print(f"  ... and {len(findings) - 5} more")

    print(f"\n{'=' * 70}")
    print(f"Total: {len(all_findings)} bypass patterns")

if __name__ == "__main__":
    main()
```

---

## Test Data Directories

SciPy has extensive test data in binary formats:

| Directory | Contents |
|-----------|----------|
| `scipy/io/tests/data/` | .mat, .nc, .sav, .wav files |
| `scipy/io/matlab/tests/data/` | MATLAB test files |
| `scipy/io/arff/tests/data/` | ARFF format files |
| `scipy/ndimage/tests/data/` | Label data files |
| `scipy/spatial/tests/data/` | Qhull test data |
| `scipy/interpolate/tests/data/` | .npz interpolation data |
| `scipy/stats/tests/data/` | .npy distribution data |
| `scipy/special/tests/data/` | .npz boost/gsl/local datasets |
| `scipy/fftpack/tests/` | .npz FFT reference data |
| `scipy/fft/_pocketfft/tests/` | .npz FFT reference data |

---

## Summary by Module

| Module | Primary Bypass Patterns |
|--------|------------------------|
| `scipy.io` | FortranFile, loadmat, wavfile, netcdf, C extensions |
| `scipy.stats` | exec() for distributions, np.load for test data, importlib.resources |
| `scipy.special` | importlib.resources, np.load for test data |
| `scipy.sparse` | np.load, SuperLU C file readers |
| `scipy.interpolate` | np.load, np.memmap for test data |
| `scipy.fft/fftpack` | np.load for reference data |
| `scipy.integrate` | ctypes.CDLL for tests |
| `scipy.optimize` | exec() for wrappers, sys.modules |

---

## Key Observations

1. **NumPy I/O dominates**: 47 occurrences, mostly for loading test data (.npy, .npz files)

2. **C extensions are unavoidable**: SuperLU's matrix readers use direct `fopen()`/`fread()` - cannot be tracked from Python

3. **Binary formats need special handling**: loadmat, FortranFile, wavfile all bypass Python's open()

4. **exec() is used for code generation**: Stats distributions dynamically create methods at runtime

5. **Test data is heavily file-based**: Many tests load reference data from binary files

6. **Subprocess is mostly in tools**: 43 of 51 subprocess calls are in build/lint tools, not library code

---

## CORRECTED: File Dependency Tracking Investigation (2026-01-31)

### Initial Findings (Incorrect)

Initial testing appeared to show that `np.load()` was a blind spot. However, this was caused by **test setup issues**, not actual tracking failures.

### Root Cause Analysis

The initial "blind spot" was caused by:

1. **macOS `/tmp` symlink**: `/tmp` → `/private/tmp` caused path resolution failures
2. **Absolute paths**: Using hardcoded paths instead of relative paths
3. **Non-git test directory**: Test files weren't committed to git

### Corrected Test Results

After fixing test setup issues, **all numpy I/O patterns are correctly tracked**:

| Pattern | Description | Tracked by ezmon? |
|---------|-------------|-------------------|
| `np.load()` | NumPy .npy/.npz files | ✓ YES |
| `np.fromfile()` | Raw binary files | ✓ YES |
| `np.memmap()` | Memory-mapped files | ✓ YES |
| `builtins.open()` | JSON/text files | ✓ YES |

### Why Tracking Works

All numpy file I/O functions call `builtins.open()` internally:

```python
# Verification
>>> import builtins, numpy as np
>>> calls = []
>>> orig = builtins.open
>>> builtins.open = lambda *a, **k: (calls.append(a[0]), orig(*a, **k))[1]
>>> np.load('data.npy')
>>> print(calls)  # ['data.npy'] - np.load DOES use builtins.open
```

### Requirements for Tracking

For file dependencies to be tracked, files must:
1. Be within the project directory
2. Be committed to git (prevents tracking ephemeral files)
3. Be opened with read mode
4. Not be `.py` files (handled by import tracking)

### Implications for scipy

**scipy's .npy/.npz data files SHOULD be tracked** because:
- They are committed to git
- They are within the project directory
- `np.load()` uses `builtins.open()` internally

### Actual Blind Spots (Not np.load)

Real blind spots in scipy would be:
1. **C extension direct I/O**: SuperLU's `dreadMM.c` uses `fopen()` directly
2. **Subprocess execution**: Tests that spawn separate Python processes
3. **SQLite database files**: `sqlite3.connect()` uses C-level I/O

### Verification Command

To verify scipy data files are tracked:

```bash
cd /path/to/scipy
python -m pytest --ezmon -v scipy/stats/tests/test_distributions.py::TestJFSkewT -x
python -c "
import sqlite3
conn = sqlite3.connect('.testmondata')
cursor = conn.cursor()
cursor.execute('SELECT filename FROM file_dependency WHERE filename LIKE \"%.npy\"')
for row in cursor.fetchall(): print(row[0])
"
```
