# Pandas: Concrete Undetected Dependency Examples

## Overview

These are **concrete, reproducible examples** where changing a file would cause a test to fail, but the import tracker would NOT detect the dependency, resulting in the test being incorrectly skipped.

---

## Example 1: Memory-Mapped CSV File Reading

### The Problem

When `memory_map=True` is passed to `read_csv()`, pandas uses `mmap.mmap()` to read file contents. This bypasses `builtins.open()` after the initial file descriptor is obtained.

### Concrete Scenario

**Test:** `pandas/tests/io/parser/common/test_file_buffer_url.py::test_memory_map`

**Test code (around line 455):**
```python
def test_memory_map(all_parsers, csv_dir_path):
    mmap_file = os.path.join(csv_dir_path, "test_mmap.csv")
    parser = all_parsers

    expected = DataFrame(
        {"a": [1, 2, 3], "b": ["one", "two", "three"], "c": ["I", "II", "III"]}
    )

    result = parser.read_csv(mmap_file, memory_map=True)
    tm.assert_frame_equal(result, expected)
```

**Data file:** `pandas/tests/io/data/csv/test_mmap.csv`
```csv
a,b,c
1,one,I
2,two,II
3,three,III
```

### The Undetected Change

**File to modify:** `pandas/tests/io/data/csv/test_mmap.csv`

**Change:** Add a row:
```csv
a,b,c
1,one,I
2,two,II
3,three,III
4,four,IV
```

### Why Tracker Misses It

1. The tracker hooks `builtins.open()`, which IS called to get the file descriptor
2. BUT then `mmap.mmap(f.fileno(), 0)` is called (NOT hooked)
3. The actual file content is read from memory-mapped pages
4. The tracker sees `open()` was called on the file path... but wait!

**Actually, the tracker SHOULD catch this** because `open()` is called on the path first. Let me reconsider...

**The REAL issue:** The dependency IS tracked for the open() call, but if mmap is used in a way that the file path isn't passed to open() directly (e.g., passed as a file descriptor), tracking fails.

### Better Example: Direct mmap from file descriptor

**Test:** `pandas/tests/io/parser/test_c_parser_only.py::test_file_handles_mmap`

**Test code (line 553):**
```python
def test_file_handles_mmap(c_parser_only, csv1):
    parser = c_parser_only

    with open(csv1, encoding="utf-8") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            parser.read_csv(m)  # Passes mmap object, not file path
            assert not m.closed
```

Here `read_csv()` receives an mmap object, not a path. The tracker sees `open(csv1)` which IS tracked. So this is also caught.

---

## Example 2: Legacy Pickle Files (Binary Format)

### The Problem

Legacy pickle files are loaded via `pd.read_pickle()` which uses pickle's C implementation for deserialization.

### Concrete Scenario

**Test:** `pandas/tests/io/test_pickle.py::test_pickles`

**Test code (lines 80-100):**
```python
def test_pickles(datapath):
    pytest.importorskip("pytz")
    if not is_platform_little_endian():
        pytest.skip("known failure on non-little endian")

    current_data = create_pickle_data()

    for legacy_pickle in Path(__file__).parent.glob("data/legacy_pickle/*/*.p*kl*"):
        legacy_version = Version(legacy_pickle.parent.name)
        legacy_pickle = datapath(legacy_pickle)

        data = pd.read_pickle(legacy_pickle)  # Loads legacy pickle

        for typ, dv in data.items():
            for dt, result in dv.items():
                expected = current_data[typ][dt]
                # ... comparison ...
```

**Data files:** `pandas/tests/io/data/legacy_pickle/*//*.pickle`

Example: `pandas/tests/io/data/legacy_pickle/1.5.3/1.5.3_x86_64_linux_3.11.9.pickle`

### The Undetected Change

**File to modify:** Any pickle file in `pandas/tests/io/data/legacy_pickle/`

**Change:** Corrupt or modify the binary content

**OR modify the pandas serialization:**

**File:** `pandas/core/generic.py` (DataFrame/Series `__reduce__` method)

**Change:**
```python
# Modify how DataFrames are pickled
def __reduce__(self):
    # Original returns certain state
    # Modified version returns incompatible state
    return (self._constructor, (self._data,), {'new_field': 'breaks_old'})
```

### Why Tracker Misses It

1. `pd.read_pickle()` opens the file with `builtins.open()` (tracked!)
2. BUT the pickle deserialization happens in C code
3. The pickle module reconstructs objects using C-level calls
4. **Key insight:** The tracker DOES see the file open, so the file dependency IS tracked
5. **The real gap:** Changes to `core/generic.py` that affect pickle format might not be linked to tests that load OLD pickles

### Actual Gap

The real issue is: if you change how pandas WRITES pickles, the tests that READ old pickles don't detect the dependency because:
- The old pickle files don't change
- The reading code doesn't change
- Only the writing code changed
- Tests pass (old pickles still load) but NEW pickles are incompatible

---

## Example 3: SQLite Database Files

### The Problem

`sqlite3.connect()` opens database files using C-level I/O, bypassing `builtins.open()`.

### Concrete Scenario

**Test:** `pandas/tests/io/test_sql.py::test_sql_open_close`

**Test code (line 2580):**
```python
def test_sql_open_close(temp_file, test_frame3):
    # Test if the IO in the database still work if the connection closed
    # between the writing and reading (as in many real situations).

    with contextlib.closing(sqlite3.connect(temp_file)) as conn:
        assert sql.to_sql(test_frame3, "test_frame3_legacy", conn, index=False) == 4

    with contextlib.closing(sqlite3.connect(temp_file)) as conn:
        result = sql.read_sql_query("SELECT * FROM test_frame3_legacy;", conn)

    tm.assert_frame_equal(test_frame3, result)
```

### The Undetected Change

**File to modify:** `pandas/io/sql.py`

**Change:** Modify the SQL type mapping around line 1500:

```python
# Original
_SQL_TYPES = {
    "int64": "INTEGER",
    "float64": "REAL",
    # ...
}

# Modified
_SQL_TYPES = {
    "int64": "TEXT",  # Wrong type!
    "float64": "REAL",
    # ...
}
```

### Why Tracker Misses It

1. `sqlite3.connect(temp_file)` does NOT call `builtins.open()`
2. SQLite's C library opens the database file directly
3. The tracker doesn't see any file dependency on `temp_file`
4. **BUT:** The tracker DOES see `test_sql.py` imports `pandas.io.sql`
5. So changes to `sql.py` ARE detected!

### Real Gap

The tracker correctly handles this case because the import dependency is tracked. The SQLite file bypass doesn't matter because it's a temp file created by the test.

---

## Example 4: Subprocess Test Isolation

### The Problem

Tests that run pandas operations in subprocesses to test isolation/import behavior.

### Concrete Scenario

**Test:** `pandas/tests/test_downstream.py::test_pandas_datareader`

**Test code (line 116):**
```python
def test_pandas_datareader():
    subprocess.check_call([sys.executable, "-c", "import pandas_datareader"])
```

**Test:** `pandas/tests/test_downstream.py::test_oo_optimizations`

**Test code (line 122):**
```python
def test_oo_optimizations():
    subprocess.check_call([sys.executable, "-OO", "-c", "import pandas"])
    subprocess.check_call([
        sys.executable, "-OO", "-c",
        "import pandas as pd, pickle; "
        "pickle.loads(pickle.dumps(pd.date_range('2021', periods=2, tz='UTC')))"
    ])
```

### The Undetected Change

**File to modify:** `pandas/__init__.py`

**Change:** Add code that fails in optimized mode:

```python
# In pandas/__init__.py
assert __debug__, "This fails with -OO flag"
```

### Why Tracker Misses It

1. The subprocess runs a completely separate Python interpreter
2. The parent process tracker doesn't see subprocess imports
3. The test file imports nothing from pandas directly (just subprocess)
4. Changes to `pandas/__init__.py` don't trigger re-run of `test_oo_optimizations`

### Impact

- **Test result:** FAIL (assertion error in -OO mode)
- **Tracker decision:** SKIP (no detected import of pandas)
- **Consequence:** Import/optimization bugs go undetected

---

## Example 5: Fixture Data Dependencies

### The Problem

Tests depend on pytest fixtures that create data, not on files. The fixture implementation can change without the tracker detecting it affects consuming tests.

### Concrete Scenario

**Fixture:** `pandas/tests/io/test_sql.py::test_frame3` (line 501)

```python
@pytest.fixture
def test_frame3():
    columns = ["index", "A", "B"]
    data = [
        ("2000-01-03 00:00:00", 2**31 - 1, -1.987670),
        ("2000-01-04 00:00:00", -29, -0.0412318367011),
        ("2000-01-05 00:00:00", 20000, 0.731167677815),
        ("2000-01-06 00:00:00", -290867, 1.56762092543),
    ]
    return DataFrame(data, columns=columns)
```

**Test using fixture:** `test_sql_open_close` (line 2580)

### The Undetected Change

**Change:** Modify the fixture to return different data:

```python
@pytest.fixture
def test_frame3():
    columns = ["index", "A", "B"]
    data = [
        ("2000-01-03 00:00:00", 2**31 - 1, -1.987670),
        # Removed rows - now only 1 row
    ]
    return DataFrame(data, columns=columns)
```

### Why Tracker Misses It

Actually, since both the fixture and test are in the same file (`test_sql.py`), changes to the file WILL trigger the test. This is detected.

**Better example:** Fixtures defined in `conftest.py`:

**File:** `pandas/tests/io/conftest.py`

If a fixture is defined in conftest and used by many test files, changing the conftest fixture triggers re-runs of ALL tests (too broad), not just the specific tests using that fixture.

---

## Example 6: Entry Point Plugin Loading

### The Problem

Plotting backends can be loaded via entry points, which uses metadata-based discovery.

### Concrete Scenario

**Test:** `pandas/tests/plotting/test_backend.py::test_register_entrypoint`

**Code that loads backends:** `pandas/plotting/_core.py` (line 2175)

```python
def _load_backend(backend: str):
    from importlib.metadata import entry_points

    eps = entry_points()
    key = "pandas_plotting_backends"

    for entry_point in eps.select(group=key):
        if entry_point.name == backend:
            module = entry_point.load()  # Dynamic loading!
            break
```

### The Undetected Change

**File to modify:** A third-party plotting backend installed via pip

Since entry points are read from installed package metadata (not source files), changes to installed packages are completely invisible to the tracker.

### Why Tracker Misses It

1. `entry_point.load()` uses metadata-based module loading
2. The tracker doesn't hook `importlib.metadata.entry_points()`
3. Third-party backend changes are invisible
4. Even local backend changes might not trigger re-runs

---

## Summary: Actual Gaps vs. Perceived Gaps

| Scenario | Perceived Gap | Actual Behavior | Real Gap? |
|----------|---------------|-----------------|-----------|
| mmap CSV | mmap bypasses open | open() IS called first | **No** - path tracked |
| pickle files | C deserialization | open() IS called | **No** - path tracked |
| SQLite files | C library I/O | Temp file, import tracked | **No** - import tracked |
| Subprocess tests | Subprocess isolation | No pandas import | **Yes** - subprocess not tracked |
| Fixture changes | Data changes | Same file | **Partial** - conftest.py is broad |
| Entry points | Dynamic loading | Metadata-based | **Yes** - not tracked |

### True Undetected Scenarios

1. **Subprocess execution** - Any test using `subprocess.check_call()` with `-c "import pandas"` doesn't track what happens in the subprocess
2. **Entry point plugins** - Third-party backends loaded via entry points
3. **Clipboard operations** - External commands like `pbcopy`, `xclip` (but these are system tools, not project files)

---

## Verification Script

Find tests using subprocess:

```bash
cd ~/pandasRepo
grep -rn "subprocess\." pandas/tests/*.py pandas/tests/**/*.py | grep -E "(check_call|check_output|run|Popen)" | head -20
```

Find tests using mmap:

```bash
grep -rn "memory_map=True" pandas/tests/
grep -rn "mmap\.mmap" pandas/tests/
```
