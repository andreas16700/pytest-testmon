# Changelog

All notable changes to pytest-ezmon-nocov will be documented in this file.

## [3.0.0-nocov] - 2026-02-01

### Major Changes

This release represents a significant refactoring of the ezmon plugin to use **import-based tracking** instead of coverage.py. The "nocov" variant is now a distinct approach with its own architecture.

### New Features

#### Roaring Bitmap Storage
- Test dependencies are now stored using **Roaring bitmaps** with zstd compression
- Dramatically reduced storage: ~50-200 bytes per test vs ~400KB with junction tables
- Faster affected test lookup: O(n) bitmap intersection vs O(n×m) row queries
- New `bitmap_deps.py` module with `TestDeps`, `FileRecord`, `TestRecord` classes
- Pure Python fallback for environments without `pyroaring` (e.g., Python 3.14+)

#### New Database Schema (v18)
- **files table**: Unified file registry with stable integer IDs
  - Supports both Python files (AST checksum) and data files (content hash)
  - `file_type` column distinguishes 'python' vs 'data' files
- **tests table**: Test records with duration and failure status
- **test_deps table**: Bitmap blobs per test (replaces junction table approach)

#### Checkpoint Manager
- New `CheckpointManager` class for cleaner checkpoint state management
- Extracted from `DependencyTracker` for better separation of concerns
- Manages global checkpoint, per-file checkpoints, and module caching

### Bug Fixes

#### Class Import Tracking from Package Re-exports
- **Fixed**: Classes imported via package re-exports (e.g., `from src.models import User`)
  are now correctly tracked to their defining module
- When a checkpoint restore removes the defining module from `sys.modules` but the class
  still exists as an attribute on a cached parent module, we now restore the defining
  module from cache before tracking
- This fixes the common pattern used by pandas, django, and many other libraries

#### Example of Fixed Behavior
```python
# src/models/__init__.py
from src.models.user import User

# test_user.py
from src.models import User  # User.__module__ = 'src.models.user'

# Previously: Changes to src/models/user.py would NOT trigger test_user.py tests
# Now: Changes correctly trigger dependent tests
```

#### FileInfoCache Checksum Computation for Unmodified Files
- **Fixed**: `batch_get_checksums()` now correctly computes checksums for files that are
  unmodified in the working tree but have different fshas compared to the database
- The bug caused test overselection when comparing against a database from an older commit:
  - If a file had a docstring-only change (same AST checksum, different fsha)
  - And the working tree was clean (disk == HEAD)
  - `get_source_and_fsha()` would return `source=None` as an optimization
  - `batch_get_checksums()` would then return `None` instead of computing the checksum
  - `None != db_checksum` → file incorrectly marked as changed → all dependent tests selected

#### Root Cause
The `get_source_and_fsha()` method has an optimization: for unmodified files, it returns
`source=None` and uses the git blob SHA directly, avoiding disk I/O. This works fine for
`batch_get_fshas()` which only needs the fsha. However, `batch_get_checksums()` needs the
actual source code to compute AST checksums.

#### The Fix
`batch_get_checksums()` now reads the file directly when `source=None`:

```python
source, fsha, mtime = self.get_source_and_fsha(norm)

# For unmodified files, get_source_and_fsha returns source=None as an
# optimization. We need the actual source to compute the checksum, so
# read the file directly. This is safe because unmodified means disk == HEAD.
if source is None:
    content = self._read_file(norm)
    source = content.source
```

#### Tests
New test file: `tests/test_file_cache_checksum.py` with 6 test cases:
- `test_checksum_computed_for_unmodified_file` - Basic checksum computation
- `test_docstring_only_change_same_checksum` - Verifies docstring changes don't affect checksum
- `test_fsha_differs_but_checksum_same` - Verifies fsha differs when content changes
- `test_batch_get_checksums_returns_value_not_none` - Core assertion: must return value, not None
- `test_simulated_change_detection_no_false_positive` - Full flow reproduces the bug scenario
- `test_get_source_and_fsha_unmodified_file` - Documents the optimization behavior

### Code Cleanup

#### Removed Legacy Code
- Removed `method_checksums` field from `FileFp` TypedDict (never populated)
- Removed `CHECKUMS_ARRAY_TYPE` constant from `testmon_core.py` and `process_code.py`
- Fixed broken `checksums_to_blob` import in `impact.py`

### Architecture

#### Import-Based Tracking (No Coverage.py)
The nocov variant uses a fundamentally different approach from upstream testmon:

| Aspect | ezmon-nocov | Original testmon |
|--------|-------------|------------------|
| Dependency detection | Import hooks | coverage.py contexts |
| Granularity | File-level | Line-level |
| First-run overhead | ~3x faster | Higher |
| False negatives | Never | Possible (coverage limitations) |

#### How It Works
1. Hook `builtins.__import__` and `importlib.import_module`
2. Track all Python files imported during test collection and execution
3. Use checkpoint system to isolate imports per test file
4. Store dependencies using Roaring bitmaps for compact storage

### Dependencies

#### Added
- `pyroaring>=0.4.0` - Roaring bitmaps for compact dependency storage
- `zstandard>=0.18.0` - zstd compression for bitmap serialization

Both have pure Python fallbacks for maximum compatibility.

### Database Migration

Database version bumped from 17 to 18. The new schema includes:
- `files` table with stable integer IDs
- `tests` table for test records
- `test_deps` table with bitmap blobs

Old schema tables are retained for backwards compatibility during transition.

### Integration Tests

All 24 integration test scenarios pass, including:
- `from_package_import_class` - Class import tracking via re-exports
- `from_package_import_class_product` - Same pattern for Product class
- Complex patterns: nested classes, generators, decorators, context managers
- File dependency tracking (config.json changes)
- Module-level constant changes

---

## [2.1.4+nocov] - Previous

Previous nocov variant with coverage.py removed but without bitmap storage optimization.
