# Checkpoint-Based Import Tracking (Historical)

> This document describes a previous checkpoint-based approach.
> Current behavior uses explicit dependency phases (global -> file -> test)
> without restoring `sys.modules` checkpoints. See `docs/file-dependency-tracking.md`.

## Fundamental Principle

**No code in Python outside the current module can execute unless it is imported.**

This is the core observation that enables import-based test dependency tracking. If a test depends on code in `module_x.py`, then at some point during the test's lifecycle, `module_x` must be imported. By tracking imports, we can identify all file dependencies.

**Note:** Imports are tracked only for the expected file set computed for the run (from git diff between the last run commit and HEAD, or full HEAD on first run). This keeps overhead low and avoids unnecessary tracking.

## Checkpoint Model

### Overview

The checkpoint system tracks which modules are in `sys.modules` at key points during pytest's lifecycle. By saving and restoring these checkpoints, we ensure our import hook fires for each test file's imports, allowing precise per-file dependency tracking.

### Checkpoints

1. **Base Checkpoint (Checkpoint 0)**
   - Saved when collection STARTS (before any test file is collected)
   - Contains modules imported by `conftest.py` and pytest infrastructure
   - These are "base dependencies" for ALL tests

2. **Per-File Checkpoint**
   - Saved after each test file's collection completes
   - Contains: base checkpoint modules + modules imported during this file's collection
   - Used to restore state before each test in this file runs

### Lifecycle

```
pytest starts
    |
    v
conftest.py loads (imports pandas, etc.)
    |
    v
[SAVE BASE CHECKPOINT] -- modules in sys.modules become base dependencies
    |
    v
For each test file:
    |
    +-- [RESTORE TO BASE CHECKPOINT] -- clean state for this file
    |
    +-- Collect test file (imports happen, tracked for this file)
    |
    +-- [SAVE PER-FILE CHECKPOINT] -- state after collection
    |
    v
Collection complete
    |
    v
For each test:
    |
    +-- [RESTORE TO PER-FILE CHECKPOINT] -- state from file's collection
    |
    +-- Run test (more imports may happen, tracked for this test)
    |
    +-- Test dependencies = base + collection + execution
    |
    v
Done
```

### Dependency Attribution

For a test in `tests/test_foo.py::test_bar`:
- **Base deps**: Modules in sys.modules when collection started (conftest.py imports)
- **Collection deps**: Modules imported when `test_foo.py` was collected
- **Execution deps**: Modules imported when `test_bar` ran
- **Total deps**: Union of all three

## Key Implementation Details

### Real Cache (`_real_cache`)
- Stores actual module objects (never re-import, no re-execution)
- When restoring a module, we use the cached object

### Module Dependencies (`_module_deps`)
- Tracks which modules were loaded when a specific module was first imported
- Used to restore transitive dependencies when a cached module is restored
- Deps are recorded relative to the **checkpoint** (not the `before` snapshot), so
  that modules loaded earlier in the same test file are included
- When restoring a cached module, its `_module_deps` entries are also restored to
  `sys.modules` and as parent attributes — this replays the side effects of the
  module's import-time code without re-executing it

### Checkpoint Local Imports (`_checkpoint_local_imports`)
- File paths of modules in the base checkpoint
- Added to every test file's dependencies

### Per-File Checkpoints (`_sysmodules_per_file_checkpoint`)
- Module names that were in sys.modules after each test file's collection
- Key: test file path, Value: set of module names

## Comparison with Coverage-Based Tracking

| Aspect | Import-Based (our approach) | Coverage-Based (testmon) |
|--------|----------------------------|-------------------------|
| Tracks | Import statements | Code execution |
| Per-file isolation | Yes (restore between files) | No (imports accumulate) |
| Precision | More precise | More conservative |
| Cross-file deps | Only if imported | All collected modules |

### Example: `@register_extension_dtype`

When `test_json_table_schema_ext_dtype.py` is collected:
1. It imports `pandas.tests.extension.date`
2. `@register_extension_dtype` runs, registering the dtype globally

**Coverage-based (testmon):**
- Sees module-level code execution during collection
- Attributes to ALL tests (less precise, more conservative)

**Import-based (our approach):**
- Tracks import for `test_json_table_schema_ext_dtype.py` tests only
- Other test files don't get this dependency (more precise)

## Debugging Tips

### Check if a module is in the checkpoint
```python
tracker = dependency_tracker
print(f"pandas.core.internals.blocks in checkpoint: {'pandas.core.internals.blocks' in tracker._sysmodules_checkpoint}")
```

### Check a test's dependencies
```sql
SELECT fp.filename
FROM test_execution te
JOIN test_execution_file_fp tefp ON te.id = tefp.test_execution_id
JOIN file_fp fp ON tefp.fingerprint_id = fp.id
WHERE te.test_name LIKE '%test_name%'
```

### Trace imports during collection
```python
import builtins
original_import = builtins.__import__
def tracing_import(name, *args, **kwargs):
    result = original_import(name, *args, **kwargs)
    if 'module_of_interest' in name:
        import traceback
        traceback.print_stack()
    return result
builtins.__import__ = tracing_import
```

## xdist Worker Checkpoint Timing

### The Problem

In xdist mode, each worker is a **separate subprocess** with its own Python interpreter.
The pytest lifecycle in a worker looks like this:

```
1. Worker subprocess starts (fresh Python — sys.modules has no local packages)
2. Plugins load (pytest_ezmon.py hooks installed)
3. pytest_sessionstart fires
4. conftest.py files execute (imports matplotlib, etc.)
5. pytest_collectstart fires for first test file
   → set_collection_context() called
```

If `save_checkpoint()` is called at step 3, the checkpoint is **empty** because no
local packages have been imported yet (conftest hasn't run). Later, when
`restore_to_checkpoint()` runs at step 5, it removes ALL local-package modules from
`sys.modules` (none are in the empty checkpoint). Python then re-imports them via
`_gcd_import` (which bypasses our `builtins.__import__` hook), creating **new module
objects**. Any code holding references to the **old** objects (e.g.
`import matplotlib as mpl` in `testing/__init__.py`) now points to stale objects
missing submodule attributes.

This manifested as 289+ failures with `AttributeError: module 'matplotlib' has no
attribute 'dviread'` — the `mpl` reference in `_has_tex_package()` was a stale object.

### The Fix: Lazy Checkpoint Save

The global checkpoint is **not** saved in `pytest_sessionstart` for workers. Instead,
it relies on the lazy save in `set_collection_context()`:

```python
def set_collection_context(self, test_file):
    if self._sysmodules_checkpoint is None:
        self.save_checkpoint()  # First call — after conftest imports
```

This fires at step 5, **after** conftest.py has imported local packages. The checkpoint
correctly captures all conftest-imported modules.

### Why Conftest Imports Are Still Tracked

The import tracking hook (`start_collection_tracking()`) is installed at step 3, before
conftest runs. During step 4, conftest imports are tracked in **collection base mode**
(`_collection_mode=True`, `_collection_context=None`). This records them as:

- `base_local_imports` — .py file paths (AST-fingerprinted)
- `base_file_deps` — non-.py files like `.so` (SHA-tracked)

When `set_collection_context()` is called for each test file, these base deps are
copied into that file's dependency set (lines 1274-1282). So every test file correctly
inherits conftest import dependencies even though the checkpoint save is deferred.

### Key Invariant

**The tracking hook must be active before conftest runs, but the checkpoint must be
saved after conftest runs.** These are two separate concerns:
- Tracking hook → captures *which files* were imported (for dependency attribution)
- Checkpoint → captures *which modules* are in sys.modules (for restore/replay cycles)

## Common Issues

### Module not tracked as dependency
1. Check if it's in the base checkpoint (conftest imports)
2. Check if it's imported during the test file's collection
3. Check if it's imported during test execution
4. If none of the above, it won't be tracked (correct behavior)

### Too many dependencies
- All conftest.py imports become base dependencies for all tests
- This is intentional - conftest sets up shared fixtures

### Slow performance
- More dependencies = more database writes
- ~300 deps/test × 200k tests = 60M+ records
- Consider optimizing database operations

### Attribute access to submodules after checkpoint restore

Checkpoint restore removes submodule attributes from parent modules and modules
from `sys.modules`. This is by design — it forces `__import__` to fire so we can
track dependencies. However, this breaks code that accesses submodules as
**attributes** rather than **imports**:

```python
# This works — goes through __import__ → our hook fires → module restored from cache
import matplotlib.style
from matplotlib import style

# This BREAKS after checkpoint restore — pure attribute access, no __import__ call
mpl.style.context(...)       # AttributeError if style was removed from mpl.__dict__
mpl._docstring.interpd(...)  # Same issue for _-prefixed submodules
```

**How this manifests with xdist:** On the controller, test files are collected
sequentially. Between files, `restore_to_checkpoint()` removes non-checkpoint
modules. If test file A imported `matplotlib.style` (as a side effect of importing
`matplotlib.testing.decorators`), and test file B accesses `mpl.style` at module
scope (e.g., via `@image_comparison` decorator), B will fail because `style` was
removed by checkpoint restore and `decorators` was restored from cache without
re-executing its `import matplotlib.style` statement.

**Solution:** Two mechanisms work together to handle this:
1. **Transitive dep restoration** (`_checkpoint_import` cached path): When a cached
   module is restored, its `_module_deps` entries are also restored to `sys.modules`
   and as parent attributes. This replays the side-effect imports.
2. **Checkpoint-relative dep recording**: `_module_deps` records deps as
   `after - checkpoint` (not `after - before`), ensuring modules loaded earlier in
   the same test file are included in the dep set.
