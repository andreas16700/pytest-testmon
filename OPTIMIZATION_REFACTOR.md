# Dependency Tracker: Optimization Refactor

## Status (Implemented)

As of this refactor, we changed direction from checkpoint-based `sys.modules` restoration.
Checkpoints are now removed from behavior and replaced with explicit three-scope dependency
tracking:

1. **Global scope**: dependencies discovered before per-test-file collection context begins
   (for example, imports/file reads from conftest/bootstrap code).
2. **Test-file scope**: dependencies newly discovered while collecting a specific test file
   (delta over global scope).
3. **Test scope**: dependencies newly discovered while executing an individual test
   (delta over that file's cumulative baseline).

### What this means in code

- `DependencyTracker` no longer restores/removes local modules from `sys.modules`.
- Import tracking uses post-import module classification (`local` / `stdlib` / `external`)
  from module origin paths.
- File reads are still tracked through `open`/`io.open`.
- Non-Python file dependencies still use git blob SHAs.

Compatibility shims for old checkpoint method names remain as no-ops to avoid breaking
existing call sites during migration.

## 1. Detecting the Import Kind

### Problem

The current code classifies imports through a multi-step, scattered decision tree:

1. **Before** the import executes: `_is_local_package(pkg_root)` probes the filesystem (up to 10 `os.path.isdir`/`os.path.exists` calls across `rootdir/{,src/,lib/,source/,packages/}/pkg/__init__.py`).
2. **After** the import executes: `_get_module_file()` → `_is_in_project()` → `_is_stdlib_module()` — three functions, path cache, skip-dir scan, stdlib path comparison.
3. A **duplicate** `is_local_package()` exists in `common.py` with slightly different logic.

### Solution

Classify using the module's `__file__` path after import. Every module falls into one of three buckets based on where its file lives on disk.

**Init-time setup (once):**

```python
def _get_stdlib_prefix():
    """The single realpath'd directory where all stdlib files live."""
    return os.path.dirname(os.path.realpath(os.__file__)) + os.sep

def _get_site_packages():
    """Realpath'd site-packages directories (venv + system + user)."""
    import site
    dirs = set()
    for p in site.getsitepackages():
        dirs.add(os.path.realpath(p) + os.sep)
    dirs.add(os.path.realpath(site.getusersitepackages()) + os.sep)
    return tuple(dirs)
```

Typically 1 stdlib prefix and 3-4 site-packages prefixes.

**Per-module classification (cached after first call):**

```python
def classify(self, mod_name: str) -> str:  # 'local' | 'stdlib' | 'external'
    cached = self._classify_cache.get(mod_name)
    if cached is not None:
        return cached

    f = getattr(sys.modules.get(mod_name), '__file__', None)
    if f is None:
        result = 'stdlib'  # built-in (sys, builtins, _abc, ...)
    else:
        f = os.path.realpath(f)
        if f.startswith(self._stdlib_prefix):
            result = 'stdlib'
        elif any(f.startswith(d) for d in self._site_packages):
            result = 'external'
        elif f.startswith(self._rootdir_prefix):
            result = 'local'
        else:
            result = 'external'  # somewhere else entirely

    self._classify_cache[mod_name] = result
    return result
```

**Check order matters:** stdlib and site-packages are checked *before* rootdir because `.venv` typically lives inside `rootdir`, so `pytest.__file__` resolves to `rootdir/.venv/lib/.../site-packages/pytest/__init__.py`. Without this order, every installed package would be misclassified as local.

**Pre-import dispatch (for checkpoint-based imports):**

The current code needs to know *before* `_original_import` whether the module is local (to use `_checkpoint_import`). This simplifies to:

```python
def _is_local_root(self, pkg_root: str) -> bool:
    return pkg_root in self._local_package_roots  # O(1) set lookup
```

On the first-ever import of a package root, `_local_package_roots` won't contain it yet, so it falls through to `_original_import`. After that import completes, `classify()` identifies it as local and adds `pkg_root` to the set. This is fine — on first import there's nothing in `real_cache` to restore anyway.

**Cost comparison:**

| | Current | Simplified |
|---|---|---|
| Pre-import check | `_is_local_package`: 10+ filesystem probes per unique pkg | `pkg_root in set`: O(1) dict lookup |
| Post-import classify | `_get_module_file` + `_is_in_project` + `_is_stdlib_module`: 3 functions, path cache, skip-dir scan, stdlib path comparison | `classify()`: one `realpath` + 5-6 `startswith` checks, single cache |
| Duplicate logic | `_is_local_package` in both `dependency_tracker.py` and `common.py` | Classification lives in one place |
| Filesystem I/O per uncached pkg | ~15 OS calls | 1 `realpath` |

## 2. Checkpoint Mechanism

### What problem does it solve?

Python caches every imported module in `sys.modules`. Once `import foo.bar` runs, `foo.bar` is in `sys.modules` forever and the `__import__` hook never fires for it again. But we need the hook to fire for every test file so we can record *which* test depends on *which* module. The checkpoint mechanism makes the hook fire again by selectively removing local modules from `sys.modules` — without ever re-executing module code.

### Data structures

All live on `CheckpointManager`:

| Field | Type | Purpose |
|---|---|---|
| `real_cache` | `Dict[str, module]` | Every local module object ever imported. Grows monotonically, never shrinks. This is the source of truth — `sys.modules` is just a "view" we manipulate. |
| `global_checkpoint` | `Set[str]` | Module names present in `sys.modules` *before* any test file is collected (conftest.py-time imports). |
| `per_file_checkpoints` | `Dict[str, Set[str]]` | Module names present after each test file's collection. Key = test file path. |
| `local_package_roots` | `Set[str]` | Top-level package names identified as local (e.g., `{"ezmon", "myapp"}`). |
| `module_deps` | `Dict[str, Set[str]]` | Transitive imports recorded when a module was first imported. Key = module name, value = set of all module names that appeared in `sys.modules` as a result. |
| `package_init_imports` | `Dict[str, Set[str]]` | For package roots: which modules were loaded by `__init__.py` on first import. |

### 2a. Saving a checkpoint

#### Global checkpoint (`save_global_checkpoint`)

Called **once**, lazily on the first `set_collection_context()` call. At this point conftest.py has already been imported, so `sys.modules` contains the "baseline" local modules.

What it does:

1. Iterates every key in `sys.modules`.
2. For each, extracts `pkg_root = name.split('.')[0]` and calls `is_local_package_fn(pkg_root)`.
3. If local: adds the name to `global_checkpoint`, stashes the module object in `real_cache`, records `pkg_root` in `local_package_roots`, and records the file path as a base dependency.

Result: `global_checkpoint = {"ezmon", "ezmon.common", "ezmon.db", ...}` — a set of strings. The checkpoint is just a set of names, not module objects. Module objects are in `real_cache`.

#### Per-file checkpoint (`save_per_file_checkpoint`)

Called after each test file finishes collection (when `set_collection_context` switches to the next file, or on `clear_collection_context`/`stop_collection_tracking`).

What it does:

1. For each `pkg_root` in `local_package_roots`, scans `sys.modules` for matching names.
2. Adds them all to a new set. Stashes any new module objects into `real_cache`.
3. Stores it: `per_file_checkpoints[test_file] = checkpoint`.

This checkpoint is a **superset** of the global checkpoint — it includes everything from conftest.py plus whatever the test file's top-level imports pulled in.

### 2b. Restoring to a checkpoint

Both `restore_to_global_checkpoint` and `restore_to_per_file_checkpoint` delegate to the same `_restore_to(checkpoint: Set[str])`. It does two things per local package root:

#### Step 1: Remove modules not in the checkpoint

```
for each local pkg_root:
    current_mods = [names in sys.modules matching this pkg_root]
    for mod_name in current_mods:
        cache mod_name in real_cache (if not already)
        if mod_name NOT in checkpoint:
            del sys.modules[mod_name]
```

This is the core trick. Deleting `foo.bar` from `sys.modules` means the next `import foo.bar` will call our `__import__` hook. But the module object itself is safe in `real_cache` — no code is lost.

**What about parent attributes?** When Python does `import foo.bar`, it sets both `sys.modules['foo.bar']` and `foo.bar` as an attribute on the `foo` module object. The restore does NOT clear these attributes. The docstring says this is intentional:

- `import foo.bar` → goes through the hook (because `foo.bar` not in `sys.modules`) → we track it
- `foo.bar` attribute access → just works (attribute still on the module object) → no breakage

#### Step 2: Restore checkpoint modules that are missing

```
checkpoint_mods = sorted by depth (parents first)
for mod_name in checkpoint_mods:
    if mod_name NOT in sys.modules and mod_name in real_cache:
        sys.modules[mod_name] = real_cache[mod_name]
        set parent attribute (if submodule)
```

This is defensive — in normal flow checkpoint modules are always present at save time and only our own `_restore_to` removes things. But it handles edge cases where a checkpoint module got removed by a prior restore to a *smaller* checkpoint. The depth-sorting (parents first) ensures `foo` is in `sys.modules` before we try to set `foo.bar` as an attribute on it.

The parent-attribute restoration (`_restore_module_to_sysmodules`) has a guard: it won't overwrite a non-module attribute with a module. This handles `from .Foo import Foo` patterns where the parent's `Foo` attribute is a class, not the module.

### 2c. The full lifecycle

```
pytest_configure
  └─ start_collection_tracking()        # installs __import__ / open hooks

pytest_collectstart (for each test file):
  └─ set_collection_context(file_N)
       ├─ save_global_checkpoint()       # FIRST CALL ONLY
       ├─ save_per_file_checkpoint(file_N-1)  # if switching from previous file
       └─ restore_to_global_checkpoint() # remove post-conftest modules from sys.modules
           ... file_N is imported ...    # imports go through hook, get tracked

pytest_collectreport:
  └─ stop_collection_tracking()
       └─ save_per_file_checkpoint(last_file)  # save final file's checkpoint

pytest_runtest_protocol (for each test file):
  └─ start_test_file(file)
       └─ restore_to_per_file_checkpoint(file)  # reset to collection-time state

  For each test:
    ├─ start(nodeid)                     # begin tracking
    ├─ ... test runs ...                 # runtime imports go through hook
    └─ stop() → (files, local_imports, external_imports)
```

**Between test files during collection:** `sys.modules` is restored to the global checkpoint. This means if `test_a.py` imported `foo.utils`, that module is removed from `sys.modules` before `test_b.py` is collected. When `test_b.py` also imports `foo.utils`, the hook fires again and the dependency is recorded for `test_b.py`.

**Between tests within a file during execution:** `sys.modules` is restored to the per-file checkpoint. Any modules imported by a specific test (but not at file scope) are removed, so the hook fires again for the next test.

### 2d. What happens when the hook fires for a cached module (`_checkpoint_import`)

When our `__import__` hook fires for a local module that was previously imported (exists in `real_cache` but not in `sys.modules`):

1. **Restore from cache** — put the module object back into `sys.modules` (no code re-execution). For a package root, also restore all of its `package_init_imports`. For a submodule, restore its parents first (depth-sorted).
2. **Restore `fromlist` submodules** — if the import is `from foo import bar`, and `foo.bar` is in cache, restore it too.
3. **Restore transitive deps** (`module_deps`) — when `foo.bar` was first imported, it may have triggered `import foo.baz` as a side effect. Those transitive deps were recorded in `module_deps['foo.bar']`. Restoring `foo.bar` from cache doesn't replay its side effects, so we manually restore its deps too.
4. **Call `_original_import`** — Python finds the module in `sys.modules` (we just put it there) and returns it without re-executing.
5. **Record as dependency** — the module is tracked for the current test.

When a module is imported for the **first time** (not in `real_cache`):

1. **Call `_original_import`** — normal Python import, code executes.
2. **Detect newly loaded modules** — diff `sys.modules` before/after.
3. **Cache everything** — all new modules go into `real_cache`.
4. **Record `module_deps`** — all modules loaded vs. the current checkpoint are recorded as this module's transitive dependencies. This is recorded against the *checkpoint* (not just the before-set) so that modules loaded earlier in the same test file are included.
5. **Record `package_init_imports`** — if it's a package root, record which modules its `__init__.py` loaded.

### 2e. Complexity observations

**There are two copies of the restore-module-with-parent-attr logic:**
- `CheckpointManager._restore_module_to_sysmodules` (line 200) — used by `_restore_to`
- `DependencyTracker._restore_module_with_parent_attr` (line 1032) — used by `_checkpoint_import`

They are identical in behavior.

**The `_restore_to` loop scans sys.modules twice per package root:**
1. Once to find current modules matching the root (line 179)
2. Once to find checkpoint modules matching the root (line 191-194)

With multiple local package roots, this means multiple linear scans of `sys.modules`.

**`_checkpoint_import` builds before/after sets by scanning sys.modules:**
- Line 879: `before = set(k for k in sys.modules if k.startswith(pkg_root + '.') or k == pkg_root)`
- Line 943: same scan for `after`

This runs on every single local import. For a large project with many modules in `sys.modules`, these scans add up.

**`module_deps` can be large and overlapping.** If module A imports B which imports C, then `module_deps['A']` contains both B and C, and `module_deps['B']` contains C. When restoring A from cache, we restore its deps, which includes B, and then if something imports B later, we restore B's deps again (which includes C, already restored). This is wasteful but correct.

### 2f. New design: `LocalModuleTracker`

#### Core idea

Stop scanning `sys.modules` (500+ entries, mostly stdlib/external) to find the ~50 local modules. Maintain a parallel local-only structure that we control entirely. `sys.modules` is kept in sync as a side effect — we never read it to answer "what local modules are loaded?"

#### Structure

Two dicts, both keyed by dotted module name, both containing only local modules:

```python
class LocalModuleTracker:
    def __init__(self):
        self.cache = {}   # Dict[str, module] — every local module ever imported (monotonic)
        self.live = {}    # Dict[str, module] — local modules currently in sys.modules
```

- `cache` is equivalent to the current `real_cache`. Grows only, never shrinks.
- `live` is new. It mirrors the local subset of `sys.modules` and is the source of truth for "what local modules are loaded right now." Every mutation to `live` is accompanied by the same mutation to `sys.modules`.

A **checkpoint** is `frozenset(live.keys())` — just a set of module name strings. Module objects are always retrievable from `cache`.

#### Why not a literal tree

The dotted names *are* the tree — `"foo.bar.baz".split(".")` gives the path from root to leaf in O(depth), and depth is at most 3-4 in practice. A tree with explicit `children` dicts would add allocation overhead per node and require recursive walks for snapshot/restore. The flat dict with dotted keys gives us:
- O(1) lookup by full name
- O(depth) parent traversal via string splitting
- O(n) snapshot/diff via set operations where n = number of local modules

An explicit tree would give the same asymptotic costs with more code and more allocations.

#### Operations

**`snapshot()`** — save a checkpoint:

```python
def snapshot(self):
    return frozenset(self.live)    # O(num_local), typically ~50
```

**`restore(checkpoint)`** — make sys.modules match a saved state:

```python
def restore(self, checkpoint):
    current_keys = set(self.live)
    to_remove = current_keys - checkpoint
    to_add = checkpoint - current_keys

    for name in to_remove:
        del sys.modules[name]
        del self.live[name]

    for name in sorted(to_add, key=lambda x: x.count('.')):  # parents first
        mod = self.cache[name]
        sys.modules[name] = mod
        self.live[name] = mod
        _set_parent_attr(name, mod)
```

Cost: O(diff_size). Only touches modules that actually changed between the current state and the target checkpoint. No scanning of `sys.modules`.

**`track(name, module)`** — called when a new local module appears (first-time import):

```python
def track(self, name, module):
    self.cache[name] = module
    self.live[name] = module
```

**`remove(name)`** — remove from live (used during restore):

```python
def remove(self, name):
    del sys.modules[name]
    del self.live[name]
    # Note: does NOT remove from cache
```

#### How it integrates with the import hook

**Cached import** (module in `cache` but not in `live`):

The hook knows exactly which modules to restore — the module itself, its parents, and its `module_deps`. It restores each one by putting `cache[name]` into both `live` and `sys.modules`. No before/after scan needed. The set of "newly loaded" modules is exactly the set of modules we just restored — we built that list ourselves.

**First-time import** (module not in `cache`):

1. Snapshot `live_before = set(self.live)`.
2. Call `_original_import`. Python may load multiple modules (transitive imports). Each one that is local will itself go through our hook recursively, so `live` is updated as they load.
3. After `_original_import` returns, scan `sys.modules` for the `pkg_root` prefix to catch any modules that bypassed our hook (e.g., loaded by C extensions). Add them to `cache` and `live`.
4. `newly_loaded = set(self.live) - live_before`. This diffs ~50 entries instead of ~500+.

This scan only happens once per module for the entire session — it's the cold path.

#### Cost comparison

| Operation | Current | New |
|---|---|---|
| `snapshot` (save checkpoint) | Scan `sys.modules` per `pkg_root`: O(all_modules * num_roots) | `frozenset(live)`: O(num_local) |
| `restore` | Scan `sys.modules` per root, then scan checkpoint per root: O(all_modules * num_roots) | Set diff on `live`: O(num_local), mutations: O(diff_size) |
| Cached import (hot path) | Two full scans of `sys.modules` for before/after: O(all_modules) each | No scan. Restore known modules into `live` + `sys.modules` directly: O(num_restored) |
| First import (cold path) | Two full scans of `sys.modules` for before/after: O(all_modules) each | One scan of `sys.modules` for `pkg_root` prefix (once per module, ever): O(all_modules) |

Where `all_modules` ~ 500-2000, `num_local` ~ 50-200, `num_restored` ~ 1-10, `diff_size` ~ 5-50.
