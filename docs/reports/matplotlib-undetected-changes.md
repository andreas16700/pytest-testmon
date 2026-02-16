# Matplotlib: Concrete Undetected Dependency Examples

## Overview

These are **concrete, reproducible examples** where changing a file would cause a test to fail, but the import tracker would NOT detect the dependency, resulting in the test being incorrectly skipped.

---

## Example 1: Subprocess Test Function Dependencies

### The Problem

Tests using `subprocess_run_helper()` execute code in a separate Python process. The parent process's import tracker cannot see what the subprocess imports or reads.

### Concrete Scenario

**Test:** `lib/matplotlib/tests/test_pickle.py::test_pickle_load_from_subprocess`

**The subprocess function (lines 126-135):**
```python
def _pickle_load_subprocess():
    import os
    import pickle
    path = os.environ['PICKLE_FILE_PATH']
    with open(path, 'rb') as blob:
        fig = pickle.load(blob)
    print(str(pickle.dumps(fig)))
```

**The test (lines 140-171):**
```python
def test_pickle_load_from_subprocess(tmp_path):
    fig = plt.figure()
    pickle_path = tmp_path / 'fig.pickle'
    fig.savefig(pickle_path, format='pickle')

    proc = subprocess_run_helper(
        _pickle_load_subprocess,
        timeout=60,
        extra_env={'PICKLE_FILE_PATH': str(pickle_path)}
    )
```

### The Undetected Change

**File to modify:** `lib/matplotlib/figure.py`

**Change:** Modify the `Figure.__reduce_ex__` method (pickle serialization) around line 3200:

```python
# Original
def __reduce_ex__(self, protocol):
    return (self.__class__._reconstruct, (self.__class__,), self.__getstate__())

# Modified (breaks backward compatibility)
def __reduce_ex__(self, protocol):
    # Add a new required field that old unpicklers don't know about
    state = self.__getstate__()
    state['_new_required_field'] = 'must_exist'
    return (self.__class__._reconstruct, (self.__class__,), state)
```

### Why Tracker Misses It

1. The tracker sees `test_pickle_load_from_subprocess` imports `matplotlib.testing` (for `subprocess_run_helper`)
2. The tracker does NOT see that the subprocess function `_pickle_load_subprocess`:
   - Imports `pickle`
   - Reads a file via `open()`
   - Depends on `Figure.__reduce_ex__` for deserialization
3. When `figure.py` changes, the tracker doesn't know `test_pickle_load_from_subprocess` depends on it

### Impact

- **Test result:** FAIL (pickle format incompatibility)
- **Tracker decision:** SKIP (no detected dependency on `figure.py`)
- **Consequence:** Broken pickle compatibility ships undetected

---

## Example 2: Style Files via importlib.resources

### The Problem

Style files (`.mplstyle`) are loaded via `importlib.resources`, which bypasses the `builtins.open()` hook.

### Concrete Scenario

**Test:** `lib/matplotlib/tests/test_style.py::test_use`

**Style loading code in `lib/matplotlib/style/core.py` (lines 116-119):**
```python
elif "." in style:
    pkg, _, name = style.rpartition(".")
    try:
        path = importlib.resources.files(pkg) / f"{name}.{_STYLE_EXTENSION}"
        style = rc_params_from_file(path, use_default_template=False)
```

### The Undetected Change

**File to modify:** `lib/matplotlib/mpl-data/stylelib/classic.mplstyle`

**Change:** Add an invalid parameter:

```diff
# classic.mplstyle
+ lines.linewidth: INVALID_NOT_A_NUMBER
```

Or change a valid value that tests assert on:

```diff
# classic.mplstyle
- lines.linewidth: 1.0
+ lines.linewidth: 5.0
```

### Why Tracker Misses It

1. `importlib.resources.files()` is NOT hooked by the tracker
2. The `.mplstyle` file is accessed through package resource API
3. Tracker only sees the test imports `matplotlib.style`
4. Changes to `.mplstyle` files are invisible to the tracker

### Impact

- **Test result:** FAIL (invalid RC param or assertion failure)
- **Tracker decision:** SKIP (no detected dependency on `.mplstyle` files)
- **Consequence:** Broken style files ship undetected

---

## Example 3: Baseline Images for Visual Tests

### The Problem

Image comparison tests depend on PNG baseline images that are read during test execution, but this dependency isn't tracked through imports.

### Concrete Scenario

**Test:** `lib/matplotlib/tests/test_image.py::test_alpha_interp`

**Test code (lines 26-36):**
```python
@image_comparison(['interp_alpha.png'], remove_text=True)
def test_alpha_interp():
    fig, ax = plt.subplots()
    # ... creates a plot ...
```

**Baseline image:** `lib/matplotlib/tests/baseline_images/test_image/interp_alpha.png`

### The Undetected Change

**File to modify:** `lib/matplotlib/image.py` (the image rendering code)

**Change:** Modify interpolation behavior around line 450:

```python
# Original
def _interpolation_stage(self, data, ...):
    # ... interpolation algorithm ...
    return interpolated_data

# Modified (slightly different algorithm)
def _interpolation_stage(self, data, ...):
    # ... different interpolation that produces slightly different pixels ...
    return interpolated_data + 0.001  # Subtle difference
```

### Why Tracker Misses It

1. The `@image_comparison` decorator loads baseline images at test runtime
2. The PNG files are read during assertion, not during import
3. Tracker sees `test_image.py` imports `matplotlib.image`
4. BUT tracker doesn't know `test_alpha_interp` depends on `baseline_images/test_image/interp_alpha.png`
5. If you change `image.py`, tracker runs the test (correct!)
6. If you change the baseline PNG, tracker does NOT run the test (incorrect!)

### Impact

- **Test result:** FAIL (image mismatch)
- **Tracker decision:** SKIP (no detected dependency on PNG file)
- **Consequence:** Updated baselines don't trigger test re-runs

---

## Example 4: Jupyter Notebook Execution

### The Problem

Some tests execute Jupyter notebooks via subprocess, creating dependencies on notebook content that aren't tracked.

### Concrete Scenario

**Test:** `lib/matplotlib/tests/test_backend_inline.py::test_ipynb`

**Test code (lines 15-46):**
```python
def test_ipynb():
    nb_path = Path(__file__).parent / 'data/test_inline_01.ipynb'
    pytest.importorskip("nbconvert")
    pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel")

    with TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / 'out.ipynb'
        subprocess_run_for_testing(
            ["jupyter", "nbconvert", "--to", "notebook",
             "--execute", "--ExecutePreprocessor.timeout=500",
             "--output", str(out_path), str(nb_path)],
            env={**os.environ, "IPYTHONDIR": tmpdir},
            check=True)
```

**Notebook file:** `lib/matplotlib/tests/data/test_inline_01.ipynb`

### The Undetected Change

**File to modify:** `lib/matplotlib/tests/data/test_inline_01.ipynb`

**Change:** Modify a code cell in the notebook:

```json
{
  "cell_type": "code",
  "source": [
    "import matplotlib.pyplot as plt\n",
    "plt.plot([1, 2, 3], [1, 2, SYNTAX_ERROR])"  // Break the notebook
  ]
}
```

Or modify the matplotlib inline backend:

**File:** `lib/matplotlib/backends/backend_agg.py`

### Why Tracker Misses It

1. The notebook is executed via `subprocess` (jupyter nbconvert)
2. The subprocess runs a completely separate Python kernel
3. Tracker cannot follow subprocess execution
4. Changes to `.ipynb` files are invisible
5. Changes to backends used BY the notebook are also invisible

### Impact

- **Test result:** FAIL (notebook execution error)
- **Tracker decision:** SKIP (no detected dependency on notebook or backends)
- **Consequence:** Broken notebook tests go undetected

---

## Example 5: NumPy Data Files

### The Problem

Sample data loaded via `np.load()` bypasses `builtins.open()`.

### Concrete Scenario

**Library code:** `lib/matplotlib/cbook.py` (line 592)

```python
def get_sample_data(fname, asfileobj=True, *, np_load=False):
    # ...
    if np_load:
        return np.load(path)
```

**Tests using this:** Various tests that load sample data

### The Undetected Change

**File to modify:** Any `.npy` or `.npz` file in `lib/matplotlib/mpl-data/sample_data/`

**Change:** Modify the data values in a numpy file

### Why Tracker Misses It

1. `np.load()` is a C extension that doesn't call `builtins.open()`
2. Tracker never sees the file being read
3. Changes to numpy data files don't trigger test re-runs

---

## Summary: Files That Can Change Without Detection

| File Pattern | Location | Used By | Bypass Method |
|--------------|----------|---------|---------------|
| `*.mplstyle` | `mpl-data/stylelib/` | Style tests | `importlib.resources` |
| `*.png` | `tests/baseline_images/` | Image comparison tests | Runtime file read |
| `*.ipynb` | `tests/data/` | Notebook tests | Subprocess execution |
| `*.npy`, `*.npz` | `mpl-data/sample_data/` | Data loading tests | `np.load()` |
| `matplotlibrc` | `mpl-data/` | RC param tests | Config file loading |
| `*.ttf`, `*.pfb` | `mpl-data/fonts/` | Font tests | C library loading |

---

## Verification Script

Run this to find all tests that use subprocess_run_helper:

```bash
cd ~/pytest-super/matplotlib
grep -r "subprocess_run_helper" lib/matplotlib/tests/*.py --include="*.py" -l
```

Output:
```
lib/matplotlib/tests/test_pickle.py
lib/matplotlib/tests/test_backend_macosx.py
lib/matplotlib/tests/test_backend_tk.py
lib/matplotlib/tests/test_backends_interactive.py
lib/matplotlib/tests/test_font_manager.py
lib/matplotlib/tests/test_pyplot.py
lib/matplotlib/tests/test_ticker.py
```

All these tests have potential undetected dependencies.
