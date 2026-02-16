# SciPy: Concrete Undetected Dependency Examples

## Overview

SciPy has the **most significant tracking gaps** due to heavy reliance on:
- NumPy binary file I/O (`np.load()`, `np.fromfile()`)
- Binary scientific formats (MATLAB `.mat`, Fortran unformatted)
- Dynamic code generation (`exec()`)
- Package resources (`importlib.resources`)

These are **concrete, reproducible examples** where changing a file would cause a test to fail, but the import tracker would NOT detect the dependency.

---

## Example 1: NumPy Test Data Files (CRITICAL - 47 occurrences)

### The Problem

`np.load()` uses C-level file I/O, completely bypassing `builtins.open()`.

### Concrete Scenario 1a: Special Function Test Data

**Test:** `scipy/special/tests/test_data.py::test_boost`

**Test code (lines 624-626):**
```python
@pytest.mark.parametrize("test", BOOST_TESTS)
def test_boost(test):
    _test_factory(test, DATASETS_BOOST)
```

**Data loading (lines 28-33):**
```python
_datadir = importlib.resources.files('scipy.special.tests.data')

_boost_npz = _datadir.joinpath('boost.npz')
with importlib.resources.as_file(_boost_npz) as f:
    DATASETS_BOOST = np.load(f)
```

**Data file:** `scipy/special/tests/data/boost.npz`

### The Undetected Change

**File to modify:** `scipy/special/tests/data/boost.npz`

**Change:** Corrupt the file or modify the expected values:

```python
# Script to modify the data
import numpy as np

data = np.load('scipy/special/tests/data/boost.npz')
# Modify one of the arrays
modified = {k: v * 1.001 for k, v in data.items()}  # 0.1% error
np.savez('scipy/special/tests/data/boost.npz', **modified)
```

### Why Tracker Misses It

1. `importlib.resources.files()` is NOT hooked
2. `np.load()` is NOT hooked (C extension)
3. The tracker only sees the test imports `scipy.special`
4. NO dependency is recorded on `boost.npz`

### Impact

- **Test result:** FAIL (numerical tolerance exceeded)
- **Tracker decision:** SKIP (no detected dependency on `.npz`)
- **Consequence:** Test data corruption goes undetected

---

### Concrete Scenario 1b: Statistical Distribution Test Data

**Test:** `scipy/stats/tests/test_distributions.py::TestJFSkewT::test_compare_with_gamlss_r`

**Test code (lines 4245-4260):**
```python
class TestJFSkewT:
    @pytest.fixture
    def gamlss_pdf_data(self):
        data = np.load(
            Path(__file__).parent / "data/jf_skew_t_gamlss_pdf_data.npy"
        )
        return np.rec.fromarrays(data, names="x,pdf,a,b")

    @pytest.mark.parametrize("a,b", [(2, 3), (8, 4), (12, 13)])
    def test_compare_with_gamlss_r(self, gamlss_pdf_data, a, b):
        data = gamlss_pdf_data[
            (gamlss_pdf_data["a"] == a) & (gamlss_pdf_data["b"] == b)
        ]
        x, pdf = data["x"], data["pdf"]
        assert_allclose(pdf, stats.jf_skew_t(a, b).pdf(x), rtol=1e-12)
```

**Data file:** `scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy`

### The Undetected Change

**Option A - Modify data file:**
```python
# Modify the reference data
import numpy as np
data = np.load('scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy')
data[0, 1] *= 2  # Double the first PDF value
np.save('scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy', data)
```

**Option B - Modify the distribution implementation:**

**File:** `scipy/stats/_continuous_distns.py` (line 5732)

```python
# Original (around line 5732 in jf_skew_t_gen class)
def _pdf(self, x, a, b):
    c = 2 ** (a + b - 1) * sc.beta(a, b) * np.sqrt(a + b)
    # ...

# Modified
def _pdf(self, x, a, b):
    c = 2 ** (a + b) * sc.beta(a, b) * np.sqrt(a + b)  # Wrong: +1 error
    # ...
```

### Why Tracker Misses It

1. `np.load()` with `Path` object bypasses `builtins.open()`
2. Data file change → test fails, but tracker says "no dependency"
3. Implementation change → tracker DOES catch it (import dependency)
4. **The gap:** Data file dependencies are invisible

---

### Concrete Scenario 1c: Levy Stable Distribution Tests

**Test:** `scipy/stats/tests/test_distributions.py::TestLevyStable::test_pdf`

**Data files (3 total):**
- `scipy/stats/tests/data/levy_stable/stable-Z1-pdf-sample-data.npy`
- `scipy/stats/tests/data/levy_stable/stable-Z1-cdf-sample-data.npy`
- `scipy/stats/tests/data/levy_stable/stable-loc-scale-sample-data.npy`

**Loading code (lines 5579, 5623, 5639):**
```python
@pytest.fixture
def nolan_pdf_sample_data(self):
    data = np.load(
        Path(__file__).parent / 'data/levy_stable/stable-Z1-pdf-sample-data.npy'
    )
    # ...
```

### Impact

All three data files can be modified without triggering test re-runs.

---

## Example 2: Sobol Sequence Direction Numbers (importlib.resources + np.load)

### The Problem

The Sobol quasi-random number generator loads direction numbers from a `.npz` file using both `importlib.resources` AND `np.load()`.

### Concrete Scenario

**Test:** `scipy/stats/tests/test_qmc.py::TestSobol::test_random_base2`

**Test code (line 840):**
```python
class TestSobol:
    def test_random_base2(self):
        engine = qmc.Sobol(2, scramble=False)
        sample = engine.random_base2(2)
        expected = np.array([[0., 0.], [0.5, 0.5], [0.75, 0.25], [0.25, 0.75]])
        assert_array_equal(sample, expected)
```

**Data loading in Cython:** `scipy/stats/_sobol.pyx` (lines 147-150)

```python
def _initialize_direction_numbers(poly, vinit, dtype):
    _curdir = importlib.resources.files("scipy.stats")
    _npzfile = _curdir.joinpath("_sobol_direction_numbers.npz")
    with importlib.resources.as_file(_npzfile) as f:
        dns = np.load(f)
    # Use dns['poly'] and dns['vinit']
```

**Data file:** `scipy/stats/_sobol_direction_numbers.npz`

### The Undetected Change

**File to modify:** `scipy/stats/_sobol_direction_numbers.npz`

**Change:**
```python
import numpy as np

data = np.load('scipy/stats/_sobol_direction_numbers.npz')
poly = data['poly']
vinit = data['vinit']

# Corrupt the direction numbers
poly[0] = 0  # Invalid polynomial

np.savez('scipy/stats/_sobol_direction_numbers.npz', poly=poly, vinit=vinit)
```

### Why Tracker Misses It

1. `importlib.resources.files()` - NOT hooked
2. `np.load()` - NOT hooked
3. Test imports `scipy.stats.qmc` → `scipy.stats._sobol`
4. But data file `_sobol_direction_numbers.npz` is NOT tracked

### Impact

- **Test result:** FAIL (wrong Sobol sequence)
- **Tracker decision:** SKIP (no data file dependency)
- **Consequence:** Broken quasi-random generation goes undetected

---

## Example 3: Dynamic Code Generation via exec()

### The Problem

SciPy statistics distributions use `exec()` to generate argument parsing methods at runtime. Changes to the template aren't tracked as dependencies.

### Concrete Scenario

**Code generation:** `scipy/stats/_distn_infrastructure.py` (lines 669-748)

```python
# Template string (lines 669-678)
parse_arg_template = """
def _parse_args(self, %(shape_arg_str)s %(locscale_in)s):
    return (%(shape_arg_str)s), %(locscale_out)s

def _parse_args_stats(self, %(shape_arg_str)s %(locscale_in)s):
    return (%(shape_arg_str)s), %(locscale_out)s

def _parse_args_rvs(self, %(shape_arg_str)s %(locscale_in)s):
    return (%(shape_arg_str)s), %(locscale_out)s
"""

# Code generation (line 748)
def _attach_argparser_methods(self):
    ns = {}
    exec(self._parse_arg_template, ns)
    for name in ['_parse_args', '_parse_args_stats', '_parse_args_rvs']:
        setattr(self, name, types.MethodType(ns[name], self))
```

### The Undetected Change

**File to modify:** `scipy/stats/_distn_infrastructure.py`

**Change:** Modify the template string (line 670):

```python
# Original
parse_arg_template = """
def _parse_args(self, %(shape_arg_str)s %(locscale_in)s):
    return (%(shape_arg_str)s), %(locscale_out)s
...
"""

# Modified (introduces bug)
parse_arg_template = """
def _parse_args(self, %(shape_arg_str)s %(locscale_in)s):
    return (%(shape_arg_str)s), (%(locscale_out)s[0], %(locscale_out)s[1] * 2)  # Bug: scale doubled
...
"""
```

### Why This IS Detected

Actually, this IS detected because:
1. Tests import `scipy.stats`
2. `scipy.stats` imports `_distn_infrastructure.py`
3. Changes to `_distn_infrastructure.py` trigger re-runs

**The subtle gap:** The `exec()` creates methods dynamically, so static analysis tools can't understand what code paths are affected. But the tracker based on imports DOES catch file changes.

---

## Example 4: MATLAB File Loading (loadmat)

### The Problem

`loadmat()` reads MATLAB `.mat` files using binary format parsing that bypasses `builtins.open()`.

### Concrete Scenario

**Test:** `scipy/io/matlab/tests/test_mio.py::test_mat_struct_same_as_dict`

**Test data directory:** `scipy/io/matlab/tests/data/`

**Sample test code:**
```python
def test_mat_struct_same_as_dict():
    matfile_path = pjoin(test_data_path, 'test_struct_example.mat')
    data = loadmat(matfile_path)
    # assertions on data
```

### The Undetected Change

**File to modify:** `scipy/io/matlab/tests/data/test_struct_example.mat`

**Change:** Modify the MATLAB file content (requires MATLAB or octave):
```matlab
% In MATLAB/Octave
load('test_struct_example.mat');
data.field = data.field + 1;  % Modify a field
save('test_struct_example.mat', 'data');
```

### Why Tracker Misses It

1. `loadmat()` uses C-level binary parsing
2. Does NOT call `builtins.open()` in a trackable way
3. `.mat` file changes are invisible to the tracker

---

## Example 5: Fortran Binary Files (FortranFile)

### The Problem

`FortranFile` reads unformatted Fortran binary files using direct I/O.

### Concrete Scenario

**Test:** `scipy/io/tests/test_fortran.py::test_fortranfiles_read`

**Test code (lines 28-44):**
```python
def test_fortranfiles_read(io_lock):
    for filename in iglob(path.join(DATA_PATH, "fortran-*-*x*x*.dat")):
        # Parse filename for dtype and dimensions
        with io_lock:
            f = FortranFile(filename, 'r', '<u4')
            data = f.read_record(dtype=dtype).reshape(dims, order='F')
            f.close()
        # assertions on data
```

**Data files:** `scipy/io/tests/data/fortran-*.dat`

### The Undetected Change

**File to modify:** Any `scipy/io/tests/data/fortran-*.dat` file

**Change:** Corrupt or modify the binary content

### Why Tracker Misses It

1. `FortranFile` uses `np.fromfile()` internally (line 272 in `_fortran.py`)
2. `np.fromfile()` is NOT hooked
3. Binary data file changes are invisible

---

## Example 6: FFT Reference Data

### The Problem

FFT tests compare results against reference data loaded via `np.load()`.

### Concrete Scenario

**Test:** `scipy/fft/_pocketfft/tests/test_real_transforms.py`

**Data loading (lines 33-48):**
```python
MDATA = np.load(join(fftpack_test_dir, 'test.npz'))
FFTWDATA_DOUBLE = np.load(join(fftpack_test_dir, 'fftw_double_ref.npz'))
FFTWDATA_SINGLE = np.load(join(fftpack_test_dir, 'fftw_single_ref.npz'))

try:
    FFTWDATA_LONGDOUBLE = np.load(join(fftpack_test_dir, 'fftw_longdouble_ref.npz'))
except FileNotFoundError:
    FFTWDATA_LONGDOUBLE = None
```

**Data files:**
- `scipy/fftpack/tests/test.npz`
- `scipy/fftpack/tests/fftw_double_ref.npz`
- `scipy/fftpack/tests/fftw_single_ref.npz`
- `scipy/fftpack/tests/fftw_longdouble_ref.npz`

### The Undetected Change

**File to modify:** Any of the above `.npz` files

**Change:** Modify reference FFT values

### Why Tracker Misses It

Same as above - `np.load()` bypasses tracking.

---

## Summary: High-Impact Untracked Dependencies

| Data File | Tests Affected | Impact |
|-----------|---------------|--------|
| `scipy/special/tests/data/boost.npz` | ~400 parametrized tests | Special function validation |
| `scipy/special/tests/data/gsl.npz` | ~50 parametrized tests | GSL reference tests |
| `scipy/special/tests/data/local.npz` | ~20 parametrized tests | Local reference tests |
| `scipy/stats/tests/data/*.npy` | ~10 distribution tests | Statistical distribution validation |
| `scipy/stats/_sobol_direction_numbers.npz` | All Sobol tests | Quasi-random number generation |
| `scipy/io/matlab/tests/data/*.mat` | ~50 MATLAB I/O tests | MATLAB file format |
| `scipy/io/tests/data/fortran-*.dat` | ~10 Fortran I/O tests | Fortran file format |
| `scipy/fftpack/tests/*.npz` | ~100 FFT tests | FFT reference values |

---

## Verification Script

Find all np.load() in test files:

```bash
cd ~/scipy
grep -rn "np\.load\|numpy\.load" scipy/*/tests/*.py scipy/*/*/tests/*.py | wc -l
# Returns ~47 occurrences
```

Find all .npz/.npy data files:

```bash
find scipy -name "*.npz" -o -name "*.npy" | head -20
```

Find all loadmat usage in tests:

```bash
grep -rn "loadmat\|savemat" scipy/io/matlab/tests/*.py | wc -l
```

---

## Recommended Mitigations

1. **Hook `np.load()` and `np.fromfile()`**: These are the most impactful gaps
2. **Hook `importlib.resources.files()`**: Used by scipy.stats and scipy.special
3. **Track `.npz`/`.npy`/`.mat` file changes**: Even without hooks, could scan data directories
4. **Document known gaps**: Tests depending on data files need manual re-run triggers
