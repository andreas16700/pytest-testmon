# Import Tracking: Approaches, Evolution, and Current Design

This document traces the journey through three import tracking approaches, documenting what failed, why, and how the current design addresses each failure. It is the canonical reference for the import tracking subsystem.

## The fundamental principle

No code in Python outside the current module can execute unless it is imported. If a test depends on code in `module_x.py`, then at some point during the test's lifecycle, `module_x` must be imported. By tracking imports, we identify all file dependencies — without the overhead of coverage.py line tracing.

---

## Approach 1: coverage.py contexts

### How it worked

The original testmon uses coverage.py's dynamic context API to trace which lines each test executes. When a test runs, coverage records every line hit and tags it with the test's node ID. After the test session, the per-test line data maps back to files.

### Why it failed

**Context attribution bug.** Coverage.py's dynamic contexts attribute lines to the **first test that executes them** within a coverage session. When tests are batched (e.g., `TEST_BATCH_SIZE=250`, where multiple tests share one coverage session), the second test to hit a shared line gets no attribution for that line. This caused missed dependencies — tests that genuinely depended on a module would not be selected when that module changed.

Setting `TEST_BATCH_SIZE=1` (one coverage session per test) fixed the attribution bug but introduced ~15ms of overhead per test for starting/stopping coverage contexts. At pandas scale (230K tests), that alone added ~58 minutes of pure overhead.

**Performance.** Even with batch sizes, coverage.py adds measurable overhead to every line executed. First-run benchmarks showed ~3x slower than no-coverage on the sample project (1.240s vs 0.415s).

These structural limitations — a correctness/performance tradeoff with no good operating point — motivated the switch to import-based tracking.

---

## Approach 2: sys.modules diffing

### How it worked

The tracker snapshotted `sys.modules` keys before and after each test. New keys in the "after" set were treated as the test's imports. A `CheckpointManager` maintained:

- `real_cache`: permanent module object store (objects never evicted)
- `global_checkpoint` and `per_file_checkpoints`: sets of module name strings at each phase
- `_restore_to(checkpoint)`: removed non-checkpoint keys from `sys.modules` and rehydrated missing ones from `real_cache`

To classify imports, the tracker scanned all `sys.modules` entries against local package roots, building sets of local vs stdlib vs external modules.

### Why it failed

Three categories of failure, in order of severity:

#### 1. The fromlist resolution gap ("the critical case")

```python
# mypkg/models/__init__.py
from .product import Product   # re-exports Product

# test file
from mypkg.models import Product
```

`Product` is a class, not a module. It does not appear in `sys.modules`. The only entries added to `sys.modules` are `mypkg.models` (the package init) and `mypkg.models.product` (loaded when `__init__.py` runs). If `mypkg.models.product` was already loaded by a prior test, the `sys.modules` diff is **empty** — yet the test genuinely depends on `product.py`.

Pure `sys.modules` diffing fundamentally cannot see attribute-level imports. The `from X import Y` statement where `Y` is a class/function/constant leaves no trace in `sys.modules`. This was the single most important failure case and the primary motivation for the hook-based approach.

**Intermediate fix (Phase 1).** Added a `__module__` check in the import hook: when a fromlist item has `__module__`, look up the defining module in `sys.modules` and track it. This worked for the simple case but exposed Phase 2.

**Intermediate fix (Phase 2).** After checkpoint restore removed `mypkg.models.product` from `sys.modules`, `Product.__module__` still pointed to `"mypkg.models.product"` — but the `in sys.modules` check failed. Fix: restore the defining module from `_real_cache` before checking. This worked but added complexity to the hot path and required careful ordering of restore operations. See `docs/class-import-tracking.md` for the full investigation.

#### 2. Checkpoint absorption (submodule removal ineffectiveness)

When a test imports `mypkg.utils`, the checkpoint restore removes the key `"mypkg.utils"` from `sys.modules`. But the **module object** for `mypkg.utils` survives as an attribute on the `mypkg` parent module object (Python sets `mypkg.utils = <module>` during initial import). The next test can access `mypkg.utils` through the parent without triggering a new import — Python finds the attribute on the parent module and returns it. The `sys.modules` diff sees nothing.

This meant that after checkpoint restore, modules that should have been "forgotten" were still silently accessible, and subsequent tests that used them left no trace in the diff.

#### 3. Performance at scale

Checkpoint computation required:
- Two O(N) scans of all `sys.modules` keys per import (before/after sets)
- Linear scans per local package root (500–2000 entries, mostly irrelevant stdlib/external)
- `str.startswith` called 329 million times in a pandas run (21.3s, 10% of total runtime)
- `_get_submodule_attrs` consuming 8.2s for parent attribute enumeration
- Generator expressions for `sys.modules` scanning totaling ~33s

These costs occurred on every import and every checkpoint transition. At pandas scale (230K tests, ~300 deps each), the approach was unacceptably slow. See `docs/profiling-analysis.md` for the full breakdown (note: those bottlenecks are from this old approach and have been eliminated).

---

## Approach 3: Pure import hook with deferred reconciliation (current)

### Design principle

Record raw import event data with **zero processing** in the hot path. Defer all classification, path resolution, and deduplication to a reconciliation step that runs once per test after all imports have completed. The hook itself is 5 lines.

### The hook

```python
def _hook(self, name, globals=None, locals=None, fromlist=(), level=0):
    result = self._original_import(name, globals, locals, fromlist, level)
    fl = tuple(fromlist) if fromlist is not None else None
    self._current[name].add(fl)
    self._current[result.__name__].add(fl)
    return result
```

The hook replaces `builtins.__import__` and records exactly two things per import call:

- **`name`**: the raw dotted string passed to `__import__` by CPython
- **`result.__name__`**: the actual module name that Python resolved and returned

Both are stored as keys in a `defaultdict(set)`, with the value being the set of `fromlist` tuples observed for that key. No path lookups, no `sys.modules` probing, no file classification.

**CPython detail.** CPython sometimes passes `fromlist` as a `list` (unhashable). The `tuple()` conversion is necessary to store fromlists in a set. `tuple()` on a tuple returns the same object, so there is no cost for the common case.

### Why record both `name` and `result.__name__`?

They diverge in two important cases:

**Relative imports.** `from .sibling_b import shared_fn` inside `mypkg/sibling_a.py` calls `__import__("sibling_b", ..., level=1)`. The `name` parameter is the raw relative string `"sibling_b"` — useless for resolving to a file path. But `result.__name__` is `"mypkg.sibling_b"` — the absolute module name. Recording both ensures relative imports are always captured under their absolute name. The garbage relative name harmlessly fails resolution and is ignored.

**Plain `import X.Y` (no fromlist).** `import mypkg.utils` calls `__import__("mypkg.utils", ..., fromlist=())`. Without a fromlist, CPython returns the **top-level package** (`mypkg`), so `result.__name__` is `"mypkg"`. But `name` is `"mypkg.utils"` — the full dotted path we need. Recording both ensures the full path is captured regardless of CPython's return semantics.

### Reconciliation

All processing is deferred to the `reconcile()` method, which runs once after a test completes:

**Step 1: Prefix expansion.** For each recorded key, split on `.` and resolve every prefix against `sys.modules`. For key `"mypkg.sub.deep"`, resolve:
- `mypkg` → `mypkg/__init__.py`
- `mypkg.sub` → `mypkg/sub/__init__.py`
- `mypkg.sub.deep` → `mypkg/sub/deep.py`

This automatically captures all intermediate `__init__.py` files along any import chain.

**Step 2: Fromlist expansion.** For each `(key, fromlist_tuple)` pair, try each fromlist item as a submodule first (`self._resolve(f"{key}.{item}")`). If that fails (the item is an attribute, not a submodule), fall back to `getattr(module, item).__module__` to trace the attribute back to its defining module file.

This is how `from mypkg.models import Product` correctly resolves to **both** `mypkg/models/__init__.py` (the package where it was imported from) **and** `mypkg/models/product.py` (where `Product` is actually defined). The `Product.__module__` attribute is `"mypkg.models.product"`, which resolves to `product.py`.

**Step 3: Project root filtering.** `_resolve()` converts a module name to a file path via `sys.modules[name].__spec__.origin` (falling back to `__file__`), rejects anything outside the project root, and caches the result. Stdlib and third-party packages resolve to `None` and are ignored.

### How this solves each failure of sys.modules diffing

| sys.modules failure | Hook approach solution |
|---|---|
| Fromlist gap (re-exported class not in `sys.modules`) | Fromlist expansion + `__module__` tracing finds the defining file |
| Checkpoint absorption (module survives on parent attribute) | Hook fires on every `import` statement, even for cached `sys.modules` hits |
| O(N) scans per import at scale | Hook is O(1) per call; reconciliation is O(recorded_keys) once per test |

---

## Production integration

### Three-level checkpoint model

The production `DependencyTracker` in `ezmon/dependency_tracker.py` uses the hook approach within a three-level checkpoint hierarchy:

```
global_module_keys          ← set once at first collect-file-start
file_module_keys[file]      ← set once per file from delta over global
test_unique_module_keys     ← computed at test-end as delta over global+file
```

**collect-file-start:** Lazily establishes the global checkpoint (all imports before any test file was collected). For subsequent files, restores `sys.modules` to global so each file's collection imports are isolated.

**test-start:** Lazily establishes the file checkpoint (imports from collecting this test file). For subsequent tests in the same file, restores `sys.modules` to `global + file`.

**test-end:** Computes the test's unique dependencies as `current - global - file - stdlib_keep`, supplemented by `seen_module_keys` from the hook.

Final persisted deps for a test: `global + file + test_unique`.

See `OPTIMIZATION_REFACTOR.md` for the formal checkpoint model and `docs/file-dependency-tracking.md` for the complete tracker event reference.

### The `seen_module_keys` supplement

The hook records module names into `_current_test_seen_module_keys` as they're observed. This supplements the `sys.modules` delta because:

- A module may already be in `sys.modules` from the checkpoint (not new in the delta), but the test explicitly imported it — the hook catches this.
- After checkpoint restore, a re-imported module returns to `sys.modules` but the delta might not reflect it cleanly. The `seen` set is authoritative.

The merge: `test_unique = (current - base) | (seen_unique - base - stdlib_keep)`.

### The rehydration problem

When `sys.modules` is restored to a checkpoint between tests, modules are removed. If the next test imports a removed module, the import system would re-execute it from disk — wasteful when we have the module object cached.

The production hook pre-populates `sys.modules` before calling `_original_import`:

```python
for mod_name in requested:
    if mod_name not in sys.modules:
        module = self._real_modules.get(mod_name)
        if module is not None:
            sys.modules[mod_name] = module
```

`_real_modules` is a permanent cache that never evicts. `sys.modules` is a mutable view that gets restored to checkpoints. The hook ensures `_original_import` always finds modules it needs without disk I/O.

### The `importlib.import_module` gap

`importlib.import_module()` does not call `builtins.__import__`. It uses `importlib._bootstrap._gcd_import` directly, bypassing any override on `builtins.__import__`. This means our main hook does not fire for `importlib.import_module("mypkg.utils")` itself.

However:
- Imports triggered during module initialization (e.g., `mypkg/__init__.py` executing `from .models import Product`) **do** go through `builtins.__import__` and are captured by the main hook.
- The production tracker wraps `importlib.import_module` with a separate `_tracking_import_module` method to close the gap entirely.

### Stdlib quarantine

Stdlib modules discovered in checkpoint deltas are moved to a permanent `_stdlib_keep_keys` set and never removed during restore. This avoids accidentally evicting stdlib modules (which are expensive to re-import and can cause subtle issues if re-initialized).

---

## Edge cases and how they're handled

All edge cases below are validated by 26 tests in `tests/test_import_hook_approach.py`.

| Edge case | Mechanism | Test group |
|---|---|---|
| Relative imports (`from .x import y`) | `result.__name__` gives absolute name; raw `name` is harmless garbage | Group 1 |
| Re-exported attributes (`from pkg import Class`) | Fromlist expansion + `Class.__module__` tracing to defining file | Groups 7, 8, 10, 12 |
| Star imports (`from x import *`) | `fromlist=("*",)` recorded; `getattr(mod, "*")` returns None → graceful skip; module captured via prefix expansion | Group 2 |
| Circular imports | No infinite loop — Python's import lock handles cycles; hook records after `_original_import` returns | Group 3 |
| Already-loaded modules | `builtins.__import__` fires even for `sys.modules` cache hits | Group 12 |
| Namespace packages (no `__init__.py`) | `_resolve()` returns None for the package (`__file__` is None); only leaf module's file captured | Group 6 |
| Failed imports (`ImportError`) | `_original_import` raises before hook records — nothing pollutes the dict | Group 5 |
| `__module__ = None` on objects | `if defining:` guard before `_resolve(defining)` → graceful skip; module file still captured via prefix expansion | Group 11 |
| Multiple fromlist items (`from X import A, B`) | Each item traced independently via `__module__` | Group 7 |
| Deep re-export chains (A re-exports from B which re-exports from C) | Each link triggers its own import through `builtins.__import__`; prefix expansion captures all `__init__.py`s; `__module__` traces to the leaf | Group 10 |
| Same module imported multiple ways | All forms record into the same dict keyed by module name; reconciliation deduplicates via `set` | Group 9 |
| `importlib.import_module` | Separate `_tracking_import_module` wrapper in production; sub-imports from module init still captured by main hook | Group 4 |

---

## Known limitations

- **C-level file I/O.** Files opened via C extensions (`fopen`, `fread`, e.g., SciPy's SuperLU readers) bypass `builtins.open` and cannot be tracked. See `docs/reports/scipy-undetected-changes.md`.
- **Subprocess isolation.** Code executed in subprocesses has its own `sys.modules` and `builtins.__import__` — the parent's hook does not reach there.
- **`sys.modules` manipulation.** Code that directly inserts into or removes from `sys.modules` (e.g., mock patching, lazy importers) can create inconsistencies between what the hook recorded and what reconciliation sees at resolve time.
- **Star import member resolution.** `from X import *` records only `fromlist=("*",)`. Individual names imported via `__all__` are not traced to their defining modules. The module file itself is captured via prefix expansion, which is sufficient — changes to it trigger re-testing — but if `__all__` re-exports classes from submodules, those submodule files are only captured if the module's `__init__.py` imports them (triggering the hook).
- **`importlib.import_module` without production wrapper.** The test-only `ImportRecorder` does not wrap `importlib.import_module`. In production, this is handled by `_tracking_import_module`.
