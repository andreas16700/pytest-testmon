# Class Import Tracking Fix

> **Historical context (2026-03-14):** This document describes the Phase 1 and Phase 2 fixes applied when the tracker used `sys.modules` diffing. The underlying problem — tracking re-exported classes to their defining modules — is now handled by the reconciliation-time `__module__` tracing in the pure import hook approach. The Phase 2 cache-restoration workaround is no longer needed because reconciliation reads `__module__` from live objects after all imports have completed. See `docs/checkpoint-import-tracking.md` for the current design.

## Problem

When a test file imports a class via a package re-export, the dependency tracker was not tracking the module where the class is defined.

### Example Pattern

```python
# src/models/__init__.py
from src.models.user import User  # Re-export User from submodule

# test_user.py
from src.models import User  # Import User via package

class TestUser:
    def test_create_user(self):
        user = User("Alice", "alice@example.com")
        assert user.name == "Alice"
```

This pattern is common in libraries like:
- `from pandas import Series` (Series is defined in pandas.core.series)
- `from django.db import models` (models is defined in django.db.models.base)

## The Bug (Phase 1)

In `dependency_tracker.py`, the `_tracking_import` method handles `from X import Y` statements:

```python
# Old code (buggy)
if fromlist:
    for attr_name in fromlist:
        submodule = getattr(result, attr_name, None)
        if submodule is not None and hasattr(submodule, '__file__'):
            self._track_import(submodule, f"{name}.{attr_name}")
```

The problem: `User` is a CLASS, not a module. Classes don't have `__file__`, so the check `hasattr(submodule, '__file__')` returns False, and the dependency is not tracked.

### Fix 1: Check `__module__` Attribute

Check for `__module__` attribute on classes/functions to find their defining module:

```python
if hasattr(imported_obj, '__file__'):
    # It's a module - track it directly
    self._track_import(imported_obj, f"{name}.{attr_name}")
elif hasattr(imported_obj, '__module__'):
    # It's a class, function, or other object with __module__
    # Track the module where it's defined
    defining_module_name = imported_obj.__module__
    if defining_module_name and defining_module_name in sys.modules:
        defining_module = sys.modules[defining_module_name]
        self._track_import(defining_module, defining_module_name)
```

## The Bug (Phase 2) - Checkpoint Interaction

After fixing the `__module__` check, tests still failed when running **all tests together** but passed when running a **single test file**.

### Root Cause

When multiple test files are collected:
1. `test_models.py` collects first, imports `src.models` → loads `user.py` into `sys.modules`
2. Global checkpoint is saved (includes `src.models.user`)
3. `test_user_only.py` collects, checkpoint restores to global state
4. `from src.models import User` executes
5. `src.models` is restored from cache (module object still has `User` attribute)
6. BUT `src.models.user` is NOT in `sys.modules` (removed by checkpoint restore)
7. `User.__module__` = `'src.models.user'` → check `in sys.modules` → **False**
8. Dependency not tracked!

The key insight: **The class still exists as an attribute on the cached parent module**, but its defining module has been removed from `sys.modules`.

### Fix 2: Restore Defining Module from Cache

```python
elif hasattr(imported_obj, '__module__'):
    # It's a class, function, or other object with __module__
    defining_module_name = imported_obj.__module__
    if defining_module_name:
        # The defining module might not be in sys.modules if:
        # 1. It was removed by checkpoint restore
        # 2. But the class still exists as an attribute on cached parent
        # In this case, restore from cache if available
        if defining_module_name not in sys.modules:
            if defining_module_name in self._real_cache:
                self._restore_module_with_parent_attr(defining_module_name)

        if defining_module_name in sys.modules:
            defining_module = sys.modules[defining_module_name]
            self._track_import(defining_module, defining_module_name)
```

## How It Works

1. When `from src.models import User` is executed
2. `imported_obj` = the User class
3. `User.__module__` = `'src.models.user'`
4. Check if `src.models.user` is in `sys.modules` → **No** (removed by checkpoint)
5. Check if `src.models.user` is in `_real_cache` → **Yes** (was imported earlier)
6. Restore `src.models.user` from cache to `sys.modules`
7. Track `src/models/user.py` as a dependency

Now when `user.py` changes, all tests that import `User` (regardless of how they import it) will be re-run.

## Test Coverage

Added integration test scenarios:
- `from_package_import_class` - Verifies User tests run when user.py changes
- `from_package_import_class_product` - Verifies Product tests run when product.py changes

Both scenarios verify that:
1. Tests importing the changed class ARE selected
2. Tests NOT importing the changed class are NOT selected

## Debugging This Issue

### Check if module is in cache vs sys.modules
```python
tracker = dependency_tracker
mod_name = 'src.models.user'
print(f"In sys.modules: {mod_name in sys.modules}")
print(f"In _real_cache: {mod_name in tracker._real_cache}")
```

### Trace module restoration
```python
# In _restore_module_with_parent_attr
print(f"Restoring {mod_name} from cache")
```

### Check what gets tracked for a test
```sql
SELECT fp.filename
FROM test_execution te
JOIN test_execution_file_fp tefp ON te.id = tefp.test_execution_id
JOIN file_fp fp ON tefp.fingerprint_id = fp.id
WHERE te.test_name LIKE '%test_user_only%'
```

## Impact

This fix improves test selection accuracy for codebases that use package re-exports (a common Python pattern). Previously, changes to submodules might not trigger dependent tests if:
1. Those tests imported classes via the package level
2. Another test file collected first and loaded the submodule
3. Checkpoint restore removed the submodule from `sys.modules`

## Timeline

- **Phase 1 fix**: Added `__module__` check for classes/functions
- **Phase 2 fix**: Restore defining module from cache when missing from `sys.modules`
- **Phase 3 (current)**: Pure import hook with deferred reconciliation. `__module__` tracing happens at reconciliation time, after all imports have completed, so the defining module is always in `sys.modules`. The Phase 2 cache-restoration workaround is no longer needed.
- **Version**: 3.0.0-nocov
