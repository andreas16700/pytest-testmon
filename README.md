# pytest-ezmon-nocov

A pytest plugin that automatically selects and re-executes only tests affected by recent changes.

**This is a fork of [pytest-testmon](https://github.com/tarpas/pytest-testmon)** that uses **import-based tracking** instead of coverage.py for faster execution and more reliable dependency detection.

## Key Differences from Original testmon

| Feature | pytest-testmon | pytest-ezmon-nocov |
|---------|----------------|-------------------|
| Dependency tracking | coverage.py contexts | Import hooks |
| Granularity | Line/method-level | File-level |
| First-run speed | Slower (coverage overhead) | ~3x faster |
| False negatives | Possible (coverage limitations) | Never (conservative) |
| Transitive imports | Not fully tracked | Fully tracked |
| Storage | Junction tables | Roaring bitmaps + zstd |
| Python support | 3.10+ | 3.7+ |

## Installation

```bash
pip install pytest-ezmon-nocov
```

## Quickstart

```bash
# Build the dependency database and save it to .testmondata
pytest --ezmon

# Change some of your code

# Only run tests affected by recent changes
pytest --ezmon
```

## How It Works

**Core Principle**: No code in Python outside the current module can execute unless it is imported.

By hooking Python's import system, ezmon-nocov tracks which files each test imports (directly or transitively). On subsequent runs, it compares **the last recorded commit** to **HEAD** and selects only tests affected by those committed changes.

### Import-Based Tracking

```python
# When your test does:
from src.calculator import Calculator

# Ezmon tracks:
# - src/calculator.py (direct import)
# - src/math_utils.py (if calculator imports it)
# - Any other transitive dependencies
```

### File Dependency Tracking

Ezmon also tracks non-Python file dependencies (git-tracked files only):

```python
def test_config():
    with open("config.json") as f:
        config = json.load(f)
    # Ezmon tracks config.json as a dependency
```

### AST-Based Fingerprinting (Committed Files Only)

Changes are detected via AST (Abstract Syntax Tree) checksums:
- **Triggers tests**: Code changes, function signatures, import statements
- **Ignored**: Comments, docstrings (stripped from AST before checksum)
Note: Selection is based on **committed** changes between the previous run’s commit and `HEAD`, not on a dirty working tree.

### External Package Dependencies

External packages are tracked by **package name + version** when available. Package changes are compared between runs and only tests that used changed/removed packages are selected.

## Command Line Options

| Option | Description |
|--------|-------------|
| `--ezmon` | Enable test selection and dependency collection |
| `--ezmon-noselect` | Collect dependencies but don't deselect tests |
| `--ezmon-nocollect` | Selection only, no dependency collection |
| `--ezmon-forceselect` | Force selection even with pytest selectors (-k, -m) |
| `--no-ezmon` | Disable ezmon completely |
| `--ezmon-env` | Separate dependency data for different environments |
| `--ezmon-graph` | Generate interactive dependency graph |
| `--ezmon-no-reorder` | Disable duration-based test reordering |

## Storage

Test dependencies are stored in `.testmondata` (SQLite database):
- **Roaring bitmaps** for compact dependency storage (~50-200 bytes per test)
- **zstd compression** for minimal disk usage
- Pure Python fallbacks for maximum compatibility

## Parallel Execution

Fully supports pytest-xdist for parallel test execution. The controller computes selection once and workers reuse that decision without reloading the database. Workers only collect dependency data, while the controller computes checksums and writes to the database. Collection is pruned to only the selected test files.

```bash
pytest --ezmon -n auto
```

## Server Mode (CI Integration)

For CI environments, ezmon supports direct server communication:

```bash
export TESTMON_NET_ENABLED=true
export TESTMON_SERVER=https://your-server.com
export REPO_ID=owner/repo
export JOB_ID=test-py311
pytest --ezmon
```

If `TESTMON_SERVER` points at the ezmon public backend, the plugin stays local and does not make network calls.

## Trade-offs

**Pros of Import-Based Tracking:**
- Faster test execution (no coverage.py overhead)
- No false negatives - if code changes, dependent tests run
- Reliable transitive import tracking
- Simpler mental model

**Cons of Import-Based Tracking:**
- Less precise: changes to any code in a file trigger all tests importing that file
- May run more tests than strictly necessary (conservative approach)
 - Only git-tracked non-Python files are tracked

## Version History (opt-in)

Enable change-history tracking to debug test selection decisions and analyze fingerprint churn:

```bash
# Via environment variable
EZMON_VERSIONING=1 pytest --ezmon

# Or in pytest.ini / pyproject.toml [tool.pytest.ini_options]
# ezmon_versioning = true
```

The env var takes precedence over the ini setting. Default is **off** — zero runtime cost when disabled.

When enabled, every file checksum change, test failure-flag flip, and dependency bitmap change is recorded in append-only history tables alongside the current-state data. Query the history programmatically:

```python
from ezmon.db import DB
from ezmon.history import explain_selection, file_churn

db = DB(".testmondata", readonly=True)

# "Why was test_foo selected in run 5?"
exp = explain_selection(db, "tests/test_foo.py::test_case", run_id=5)
print(exp.triggering_files)  # ['src/utils.py']

# "Which files change most often?"
for entry in file_churn(db):
    print(f"{entry['path']}: {entry['versions']} versions")

db.close()
```

### Pruning

History grows with each run. Prune old entries to control DB size:

```python
from ezmon.db import DB
from ezmon.history import prune_history_before_run

db = DB(".testmondata")
stats = prune_history_before_run(db, keep_from_run_id=10)
print(f"Deleted: {stats.files_deleted} file versions, "
      f"{stats.test_deps_deleted} dep versions")
db.con.commit()
db.close()
```

Recommended retention policies:
- **CI benchmark DBs**: keep full history (for analysis)
- **Developer DBs**: prune to last 20 runs periodically

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Detailed technical documentation
- [CHANGELOG.md](CHANGELOG.md) - Version history and changes
- [integration_tests/README.md](integration_tests/README.md) - Integration test documentation

## Python Version Support

- Python 3.7+
- Tested through Python 3.14

## License

MIT License - see [LICENSE](LICENSE) for details.

## Credits

This project is a fork of [pytest-testmon](https://github.com/tarpas/pytest-testmon) by Tibor Arpas.
