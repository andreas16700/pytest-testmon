"""
Dependency tracking for ezmon.

Event-driven model:
1. on_collect_file_start(test_file): establish global checkpoint once, then keep
   per-file collection context.
2. on_test_start(test_id, test_file): establish file checkpoint once, then restore
   to global+file state for subsequent tests.
3. on_test_end(test_id): compute per-test unique module/read deltas.

Checkpoints are tracked as:
- module key sets (sys.modules keys)
- file-read sets (TrackedFile entries)
"""

import builtins
import importlib
import importlib.util
import io
import os
import site
import sys
import threading
import types
from functools import lru_cache
from typing import Dict, NamedTuple, Optional, Set, Tuple

from ezmon.file_cache import FileInfoCache
from ezmon.trie import get_encoder


class TrackedFile(NamedTuple):
    path: str
    sha: Optional[str]


def _get_stdlib_prefix() -> str:
    return os.path.dirname(os.path.realpath(os.__file__)) + os.sep


def _get_site_dirs() -> Tuple[str, ...]:
    dirs: Set[str] = set()
    try:
        for p in site.getsitepackages():
            dirs.add(os.path.realpath(p) + os.sep)
    except Exception:
        pass

    try:
        user = site.getusersitepackages()
        if isinstance(user, str):
            dirs.add(os.path.realpath(user) + os.sep)
    except Exception:
        pass

    return tuple(dirs)


_STDLIB_NAMES = getattr(sys, "stdlib_module_names", frozenset())


class DependencyTracker:
    def __init__(self, rootdir: str):
        self.rootdir = os.path.realpath(rootdir)
        self._root_prefix = self.rootdir + os.sep
        self._stdlib_prefix = _get_stdlib_prefix()
        self._site_dirs = _get_site_dirs()

        self._lock = threading.RLock()
        self._file_cache = FileInfoCache(self.rootdir)

        self._original_open = None
        self._original_io_open = None
        self._original_import = None
        self._original_import_module = None

        # Session/checkpoint state
        self._session_started = False
        self._collection_mode = False
        self._session_start_module_keys: Set[str] = set()
        self._global_module_keys: Optional[Set[str]] = None
        self._global_unique_module_keys: Set[str] = set()
        self._file_module_keys: Dict[str, Set[str]] = {}
        self._global_seen_module_keys: Set[str] = set()
        self._file_seen_module_keys: Dict[str, Set[str]] = {}

        self._active_collection_file: Optional[str] = None

        self._stdlib_keep_keys: Set[str] = set()
        self._stdlib_keep_tops: Set[str] = set()

        self._real_modules: Dict[str, types.ModuleType] = {}

        # Read checkpoints
        self._global_file_reads: Set[TrackedFile] = set()
        self._file_file_reads: Dict[str, Set[TrackedFile]] = {}

        # Runtime test scope
        self._active = False
        self._current_context: Optional[str] = None
        self._current_test_file: Optional[str] = None
        self._test_files: Dict[str, str] = {}
        self._current_test_reads: Set[TrackedFile] = set()
        self._current_test_seen_module_keys: Set[str] = set()

        self._tracked_files: Dict[str, Set[TrackedFile]] = {}
        self._tracked_local_imports: Dict[str, Set[str]] = {}
        self._tracked_external_imports: Dict[str, Set[str]] = {}

        # Back-compat public attributes used elsewhere
        self._checkpoint_file_deps = self._global_file_reads
        self._checkpoint_local_imports: Set[str] = set()
        self._collection_file_deps = self._file_file_reads
        self._collection_local_imports: Dict[str, Set[str]] = {}
        self._collection_external_imports: Dict[str, Set[str]] = {}

        # Resolution caches
        self._path_cache: Dict[str, Optional[str]] = {}
        self._module_kind_cache: Dict[str, str] = {}
        self._module_dep_cache: Dict[str, Tuple[Optional[str], Optional[TrackedFile], Optional[str]]] = {}
        self._local_pkgs = self._scan_local_packages()

        # Filters
        self._expected_imports: Optional[Set[str]] = None
        self._expected_reads: Optional[Set[str]] = None
        self._expected_packages: Optional[Set[str]] = None
        self._expected_package_index: Optional[Dict[str, int]] = None
        self._compute_shas = True

        self._skip_dirs = frozenset(
            [
                ".venv",
                "venv",
                ".tox",
                "site-packages",
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                "node_modules",
                ".eggs",
                "*.egg-info",
            ]
        )

    # ---------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------

    def _scan_local_packages(self) -> Set[str]:
        pkgs: Set[str] = set()
        for search_dir in ("", "src", "lib", "source", "packages"):
            root = os.path.join(self.rootdir, search_dir) if search_dir else self.rootdir
            try:
                for entry in os.scandir(root):
                    if entry.is_dir(follow_symlinks=True):
                        if os.path.isfile(os.path.join(entry.path, "__init__.py")):
                            pkgs.add(entry.name)
                    elif entry.is_file() and entry.name.endswith(".py"):
                        pkgs.add(entry.name[:-3])
            except OSError:
                continue
        return pkgs

    def _snapshot_module_keys(self) -> Set[str]:
        return set(sys.modules.keys())

    def _refresh_real_modules(self) -> None:
        for key, module in sys.modules.items():
            if isinstance(module, types.ModuleType):
                self._real_modules[key] = module

    def _is_in_project(self, filepath: str) -> Optional[str]:
        if not filepath:
            return None
        cached = self._path_cache.get(filepath)
        if cached is not None:
            return cached

        try:
            abspath = os.path.realpath(filepath)
            relpath = os.path.relpath(abspath, self.rootdir)
            if relpath.startswith("..") or os.path.isabs(relpath):
                self._path_cache[filepath] = None
                return None

            parts = relpath.replace(os.sep, "/").split("/")
            for skip_dir in self._skip_dirs:
                if any(part == skip_dir or part.endswith(".egg-info") for part in parts):
                    self._path_cache[filepath] = None
                    return None

            relpath = relpath.replace(os.sep, "/")
            self._path_cache[filepath] = relpath
            return relpath
        except Exception:
            self._path_cache[filepath] = None
            return None

    def _get_committed_file_sha(self, relpath: str) -> Optional[str]:
        cache_key = f"git_sha:{relpath}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]
        sha = self._file_cache.get_tracked_sha(relpath)
        self._path_cache[cache_key] = sha
        return sha

    def _get_module_filepath(self, module) -> Optional[str]:
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec else None
        if spec and getattr(spec, "has_location", False) and origin:
            return os.path.realpath(origin)

        filepath = getattr(module, "__file__", None)
        if not filepath:
            return None

        if filepath.endswith((".pyc", ".pyo")):
            try:
                source_path = importlib.util.source_from_cache(filepath)
                if source_path and os.path.exists(source_path):
                    filepath = source_path
            except Exception:
                source_path = filepath[:-1]
                if os.path.exists(source_path):
                    filepath = source_path

        return os.path.realpath(filepath)

    def _module_kind(self, mod_name: str, module=None) -> str:
        cached = self._module_kind_cache.get(mod_name)
        if cached is not None:
            return cached

        if module is None:
            module = sys.modules.get(mod_name) or self._real_modules.get(mod_name)

        top = mod_name.split(".")[0]
        filepath = self._get_module_filepath(module) if module else None
        if filepath:
            if filepath.startswith(self._stdlib_prefix):
                kind = "stdlib"
            elif any(filepath.startswith(d) for d in self._site_dirs):
                kind = "external"
            elif filepath.startswith(self._root_prefix):
                kind = "local"
            else:
                kind = "external"
        else:
            spec = getattr(module, "__spec__", None) if module else None
            origin = getattr(spec, "origin", None) if spec else None
            if origin in ("built-in", "frozen") or top in _STDLIB_NAMES:
                kind = "stdlib"
            elif top in self._local_pkgs:
                kind = "local"
            else:
                kind = "external"

        self._module_kind_cache[mod_name] = kind
        return kind

    def _local_source(self, mod_name: str) -> Optional[str]:
        parts = mod_name.split(".")
        for prefix in ("", "src", "lib", "source", "packages"):
            base = os.path.join(self.rootdir, prefix) if prefix else self.rootdir
            pkg_init = os.path.join(base, *parts, "__init__.py")
            if os.path.isfile(pkg_init):
                return pkg_init
            module_py = os.path.join(base, *parts) + ".py"
            if os.path.isfile(module_py):
                return module_py
        return None

    def _classify_and_capture_stdlib(self, module_keys: Set[str]) -> Set[str]:
        stdlib_keys: Set[str] = set()
        for mod_name in module_keys:
            top = mod_name.split(".")[0]
            if top in _STDLIB_NAMES:
                stdlib_keys.add(mod_name)
                self._stdlib_keep_keys.add(mod_name)
                self._stdlib_keep_tops.add(top)
                continue

            module = sys.modules.get(mod_name) or self._real_modules.get(mod_name)
            if module is None:
                continue
            if self._module_kind(mod_name, module) == "stdlib":
                stdlib_keys.add(mod_name)
                self._stdlib_keep_keys.add(mod_name)
                self._stdlib_keep_tops.add(top)
        return stdlib_keys

    def _process_checkpoint_delta(self, current_keys: Set[str], base_keys: Set[str]) -> Set[str]:
        delta = current_keys - base_keys
        stdlib_delta = self._classify_and_capture_stdlib(delta)
        return delta - stdlib_delta

    def _restore_to_module_checkpoint(self, keep_non_stdlib_keys: Set[str]) -> None:
        keep = set(keep_non_stdlib_keys) | self._stdlib_keep_keys
        current = set(sys.modules.keys())

        # remove everything not in target keep set, except stdlib tops
        to_remove = current - keep
        for key in to_remove:
            if key.split(".")[0] in self._stdlib_keep_tops:
                continue
            sys.modules.pop(key, None)

        # rehydrate missing keep keys from real module cache
        missing = keep - set(sys.modules.keys())
        for key in missing:
            module = self._real_modules.get(key)
            if module is not None:
                sys.modules[key] = module

    def _resolve_module_key(self, mod_name: str) -> Tuple[Optional[str], Optional[TrackedFile], Optional[str]]:
        cached = self._module_dep_cache.get(mod_name)
        if cached is not None:
            return cached

        local_dep: Optional[str] = None
        file_dep: Optional[TrackedFile] = None
        external_dep: Optional[str] = None

        module = sys.modules.get(mod_name) or self._real_modules.get(mod_name)
        if module is None:
            out = (None, None, None)
            self._module_dep_cache[mod_name] = out
            return out

        filepath = self._get_module_filepath(module)
        relpath = self._is_in_project(filepath) if filepath else None

        if relpath and relpath.endswith(".py"):
            if self._expected_imports is not None:
                encoded = get_encoder(self.rootdir).encode(relpath)
                if encoded in self._expected_imports:
                    local_dep = encoded
            elif self._file_cache.is_tracked(relpath):
                local_dep = relpath
            out = (local_dep, None, None)
            self._module_dep_cache[mod_name] = out
            return out

        if relpath and not relpath.endswith(".py"):
            encoded_or_rel = relpath
            if self._expected_reads is not None:
                encoded = get_encoder(self.rootdir).encode(relpath)
                if encoded not in self._expected_reads:
                    out = (None, None, None)
                    self._module_dep_cache[mod_name] = out
                    return out
                encoded_or_rel = encoded

            sha = None
            if self._compute_shas:
                sha = self._get_committed_file_sha(relpath)
                if not sha:
                    out = (None, None, None)
                    self._module_dep_cache[mod_name] = out
                    return out
            file_dep = TrackedFile(path=encoded_or_rel, sha=sha)
            out = (None, file_dep, None)
            self._module_dep_cache[mod_name] = out
            return out

        kind = self._module_kind(mod_name, module)
        if kind == "local":
            source = self._local_source(mod_name)
            rel = self._is_in_project(source) if source else None
            if rel and rel.endswith(".py"):
                if self._expected_imports is not None:
                    encoded = get_encoder(self.rootdir).encode(rel)
                    if encoded in self._expected_imports:
                        local_dep = encoded
                else:
                    local_dep = rel
        elif kind == "external":
            pkg = mod_name.split(".")[0]
            if pkg and pkg not in _STDLIB_NAMES and not pkg.startswith("_"):
                if self._expected_packages is not None:
                    encoded_pkg = (
                        self._expected_package_index.get(pkg, pkg)
                        if self._expected_package_index is not None
                        else pkg
                    )
                    if encoded_pkg in self._expected_packages:
                        external_dep = encoded_pkg
                elif self._expected_package_index is not None:
                    external_dep = self._expected_package_index.get(pkg, pkg)
                else:
                    external_dep = pkg

        out = (local_dep, file_dep, external_dep)
        self._module_dep_cache[mod_name] = out
        return out

    def _resolve_module_key_set(self, module_keys: Set[str]) -> Tuple[Set[str], Set[TrackedFile], Set[str]]:
        local_imports: Set[str] = set()
        file_deps: Set[TrackedFile] = set()
        external_imports: Set[str] = set()
        for key in module_keys:
            local_dep, file_dep, external_dep = self._resolve_module_key(key)
            if local_dep:
                local_imports.add(local_dep)
            if file_dep:
                file_deps.add(file_dep)
            if external_dep:
                external_imports.add(external_dep)
        return local_imports, file_deps, external_imports

    # ---------------------------------------------------------------------
    # Hooked functions
    # ---------------------------------------------------------------------

    def _track_file(self, filepath: str, mode: str) -> None:
        if "r" not in mode and "+" not in mode:
            return

        relpath = self._is_in_project(filepath)
        if not relpath or relpath.endswith(".py"):
            return

        path = relpath
        if self._expected_reads is not None:
            encoded = get_encoder(self.rootdir).encode(relpath)
            if encoded not in self._expected_reads:
                return
            path = encoded

        sha = None
        if self._compute_shas:
            sha = self._get_committed_file_sha(relpath)
            if not sha:
                return

        tracked = TrackedFile(path=path, sha=sha)

        if self._active and self._current_context:
            self._current_test_reads.add(tracked)
            return

        if not self._collection_mode:
            return

        if self._global_module_keys is None:
            self._global_file_reads.add(tracked)
            return

        if self._active_collection_file:
            self._file_file_reads.setdefault(self._active_collection_file, set()).add(tracked)

    def _tracking_open(self, file, mode="r", *args, **kwargs):
        result = self._original_open(file, mode, *args, **kwargs)
        if self._collection_mode or self._active:
            try:
                path = os.fspath(file)
            except Exception:
                path = None
            if path:
                try:
                    self._track_file(path, mode)
                except Exception:
                    pass
        return result

    def _resolve_name(self, name, globals_dict, level) -> str:
        if level == 0:
            return name
        package = (globals_dict or {}).get("__package__") or (globals_dict or {}).get("__name__", "")
        if not package:
            return name
        try:
            return importlib.util.resolve_name("." * level + (name or ""), package)
        except Exception:
            return name

    def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        resolved = self._resolve_name(name, globals, level)
        requested: Set[str] = set()
        if resolved:
            parts = resolved.split(".")
            requested.update(".".join(parts[: i + 1]) for i in range(len(parts)))
            for attr in fromlist or ():
                if attr != "*":
                    requested.add(f"{resolved}.{attr}")

        for mod_name in requested:
            if mod_name not in sys.modules:
                module = self._real_modules.get(mod_name)
                if module is not None:
                    sys.modules[mod_name] = module

        result = self._original_import(name, globals, locals, fromlist, level)

        known = set(requested)
        try:
            result_name = getattr(result, "__name__", None)
            if result_name:
                known.add(result_name)
            for attr in fromlist or ():
                if attr == "*":
                    continue
                obj = getattr(result, attr, None)
                if isinstance(obj, types.ModuleType):
                    n = getattr(obj, "__name__", None)
                    if n:
                        known.add(n)
        except Exception:
            pass

        for mod_name in known:
            module = sys.modules.get(mod_name)
            if isinstance(module, types.ModuleType):
                self._real_modules[mod_name] = module
        self._record_seen_module_keys(known)

        return result

    def _tracking_import_module(self, name, package=None):
        abs_name = name
        if name.startswith("."):
            if package is None:
                return self._original_import_module(name, package)
            try:
                abs_name = importlib.util.resolve_name(name, package)
            except Exception:
                abs_name = name

        if abs_name:
            parts = abs_name.split(".")
            for i in range(len(parts)):
                mod_name = ".".join(parts[: i + 1])
                if mod_name not in sys.modules:
                    module = self._real_modules.get(mod_name)
                    if module is not None:
                        sys.modules[mod_name] = module

        result = self._original_import_module(name, package)

        try:
            known: Set[str] = set()
            if abs_name:
                parts = abs_name.split(".")
                for i in range(len(parts)):
                    mod_name = ".".join(parts[: i + 1])
                    known.add(mod_name)
                    module = sys.modules.get(mod_name)
                    if isinstance(module, types.ModuleType):
                        self._real_modules[mod_name] = module
            result_name = getattr(result, "__name__", None)
            if result_name and isinstance(sys.modules.get(result_name), types.ModuleType):
                self._real_modules[result_name] = sys.modules[result_name]
                known.add(result_name)
            self._record_seen_module_keys(known)
        except Exception:
            pass

        return result

    def _record_seen_module_keys(self, names: Set[str]) -> None:
        if not names:
            return
        if self._active and self._current_context:
            self._current_test_seen_module_keys.update(names)
            return
        if not self._collection_mode:
            return
        if self._global_module_keys is None or not self._active_collection_file:
            self._global_seen_module_keys.update(names)
            return
        self._file_seen_module_keys.setdefault(self._active_collection_file, set()).update(names)

    def _install_hooks(self) -> None:
        if self._original_open is None:
            self._original_open = builtins.open
            builtins.open = self._tracking_open
        if self._original_io_open is None:
            self._original_io_open = io.open
            io.open = self._tracking_open
        if self._original_import is None:
            self._original_import = builtins.__import__
            builtins.__import__ = self._tracking_import
        if self._original_import_module is None:
            self._original_import_module = importlib.import_module
            importlib.import_module = self._tracking_import_module

    def _restore_hooks(self) -> None:
        if self._original_open is not None:
            builtins.open = self._original_open
            self._original_open = None
        if self._original_io_open is not None:
            io.open = self._original_io_open
            self._original_io_open = None
        if self._original_import is not None:
            builtins.__import__ = self._original_import
            self._original_import = None
        if self._original_import_module is not None:
            importlib.import_module = self._original_import_module
            self._original_import_module = None

    # ---------------------------------------------------------------------
    # External configuration
    # ---------------------------------------------------------------------

    def set_expected(
        self,
        expected_imports: Optional[Set[str]] = None,
        expected_reads: Optional[Set[str]] = None,
        expected_packages: Optional[Set[str]] = None,
    ) -> None:
        with self._lock:
            self._expected_imports = expected_imports
            self._expected_reads = expected_reads
            self._expected_packages = expected_packages

    def set_compute_shas(self, compute: bool) -> None:
        with self._lock:
            self._compute_shas = compute

    def set_expected_indices(
        self,
        file_index: Optional[Dict[str, int]] = None,
        package_index: Optional[Dict[str, int]] = None,
    ) -> None:
        with self._lock:
            self._expected_package_index = package_index

    # ---------------------------------------------------------------------
    # Event model
    # ---------------------------------------------------------------------

    def start_collection_tracking(self) -> None:
        with self._lock:
            self._collection_mode = True
            self._session_started = True
            self._active = False
            self._active_collection_file = None

            self._session_start_module_keys = self._snapshot_module_keys()
            self._refresh_real_modules()

            self._global_module_keys = None
            self._global_unique_module_keys = set()
            self._file_module_keys = {}
            self._global_seen_module_keys = set()
            self._file_seen_module_keys = {}

            self._stdlib_keep_keys = set()
            self._stdlib_keep_tops = set()

            self._global_file_reads.clear()
            self._file_file_reads = {}

            self._tracked_files.clear()
            self._tracked_local_imports.clear()
            self._tracked_external_imports.clear()
            self._test_files.clear()
            self._current_context = None
            self._current_test_file = None
            self._current_test_reads = set()

            self._module_kind_cache.clear()
            self._module_dep_cache.clear()
            self._path_cache.clear()

            self._install_hooks()

    def mark_collection_started(self) -> None:
        # No-op in event model; global checkpoint is established at first
        # on_collect_file_start.
        return

    def begin_test_file_collection(self, test_file: str) -> None:
        if not test_file:
            return
        with self._lock:
            if not self._session_started:
                self.start_collection_tracking()

            self._refresh_real_modules()
            current = self._snapshot_module_keys()

            # First file: establish global checkpoint once.
            if self._global_module_keys is None:
                # Process delta first to capture stdlib keys quickly.
                self._process_checkpoint_delta(current, self._session_start_module_keys)
                # Ensure stdlib keep set also includes currently loaded stdlib.
                self._classify_and_capture_stdlib(current)
                self._global_module_keys = set(current) - self._stdlib_keep_keys
                global_delta = self._process_checkpoint_delta(current, self._session_start_module_keys)
                seen_delta = set(self._global_seen_module_keys) - self._stdlib_keep_keys
                self._global_unique_module_keys = global_delta | seen_delta
                self._checkpoint_local_imports = set()
                self._active_collection_file = test_file
                self._file_file_reads.setdefault(test_file, set())
                self._file_seen_module_keys.setdefault(test_file, set())
                return

            # Subsequent files: finalize previous file from current view first.
            if self._active_collection_file and self._active_collection_file != test_file:
                self._finalize_active_file_checkpoint()

            # Then restore to global checkpoint view.
            self._restore_to_module_checkpoint(self._global_module_keys)
            self._refresh_real_modules()
            self._active_collection_file = test_file
            self._file_file_reads.setdefault(test_file, set())
            self._file_seen_module_keys.setdefault(test_file, set())

    def _finalize_active_file_checkpoint(self) -> None:
        active = self._active_collection_file
        if not active or active in self._file_module_keys or self._global_module_keys is None:
            return
        self._refresh_real_modules()
        current = self._snapshot_module_keys()
        sys_delta = self._process_checkpoint_delta(current, self._global_module_keys)
        seen_delta = (
            set(self._file_seen_module_keys.get(active, set()))
            - self._global_module_keys
            - self._stdlib_keep_keys
        )
        self._file_module_keys[active] = sys_delta | seen_delta

    def start_test(self, context: str, test_file: Optional[str] = None) -> None:
        self.start(context, test_file=test_file)

    def start(self, context: str, test_file: Optional[str] = None) -> None:
        with self._lock:
            if not self._session_started:
                self.start_collection_tracking()

            active_file = test_file or self._current_test_file
            if not active_file:
                active_file = "<unknown>"

            self._refresh_real_modules()
            current = self._snapshot_module_keys()

            if self._global_module_keys is None:
                self._process_checkpoint_delta(current, self._session_start_module_keys)
                self._classify_and_capture_stdlib(current)
                self._global_module_keys = set(current) - self._stdlib_keep_keys
                self._global_unique_module_keys = self._process_checkpoint_delta(
                    current, self._session_start_module_keys
                )

            # First test in file: create file checkpoint from current-global delta.
            if active_file not in self._file_module_keys:
                file_unique = self._process_checkpoint_delta(current, self._global_module_keys)
                self._file_module_keys[active_file] = file_unique
                self._file_file_reads.setdefault(active_file, set())
                # As requested: skip restore when checkpoint is first set.
            else:
                keep = self._global_module_keys | self._file_module_keys.get(active_file, set())
                self._restore_to_module_checkpoint(keep)
                self._refresh_real_modules()

            self._current_context = context
            self._current_test_file = active_file
            self._test_files[context] = active_file
            self._current_test_reads = set()
            self._current_test_seen_module_keys = set()
            self._tracked_files[context] = set()
            self._tracked_local_imports[context] = set()
            self._tracked_external_imports[context] = set()
            self._active = True

            self._install_hooks()

    def end_test(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        return self.stop()

    def stop(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        with self._lock:
            context = self._current_context
            if not context:
                return set(), set(), set(), None

            self._refresh_real_modules()
            current = self._snapshot_module_keys()

            test_file = self._test_files.get(context)
            base = set(self._global_module_keys or set())
            if test_file:
                base.update(self._file_module_keys.get(test_file, set()))

            test_unique_module_keys = self._process_checkpoint_delta(current, base)
            seen_unique = set(self._current_test_seen_module_keys) - base - self._stdlib_keep_keys
            test_unique_module_keys |= seen_unique
            local_imports, module_file_deps, external_imports = self._resolve_module_key_set(
                test_unique_module_keys
            )

            read_unique = set(self._current_test_reads)
            read_unique -= self._global_file_reads
            if test_file:
                read_unique -= self._file_file_reads.get(test_file, set())

            file_deps = module_file_deps | read_unique

            self._tracked_local_imports[context].update(local_imports)
            self._tracked_external_imports[context].update(external_imports)
            self._tracked_files[context].update(file_deps)

            out_files = self._tracked_files.pop(context, set())
            out_local = self._tracked_local_imports.pop(context, set())
            out_external = self._tracked_external_imports.pop(context, set())
            out_file = self._test_files.pop(context, None)

            self._current_context = None
            self._current_test_reads = set()
            self._current_test_seen_module_keys = set()
            self._active = False

            return out_files, out_local, out_external, out_file

    # ---------------------------------------------------------------------
    # Aggregated dependency access
    # ---------------------------------------------------------------------

    def get_global_import_deps(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            local, module_files, external = self._resolve_module_key_set(
                set(self._global_unique_module_keys)
            )
            return self._global_file_reads | module_files, local, external

    def get_file_import_deps(self, test_file: str) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            local, module_files, external = self._resolve_module_key_set(
                set(self._file_module_keys.get(test_file, set()))
            )
            return self._file_file_reads.get(test_file, set()) | module_files, local, external

    # ---------------------------------------------------------------------
    # Collection end + compatibility surface
    # ---------------------------------------------------------------------

    def stop_collection_tracking(self) -> tuple:
        with self._lock:
            self._finalize_active_file_checkpoint()
            self._collection_mode = False
            self._active_collection_file = None

            # Keep compatibility dictionaries for callers expecting these snapshots.
            collection_local: Dict[str, Set[str]] = {}
            collection_external: Dict[str, Set[str]] = {}
            for test_file in self._file_module_keys.keys():
                _files, local, external = self.get_file_import_deps(test_file)
                collection_local[test_file] = local
                collection_external[test_file] = external

            self._collection_local_imports = collection_local
            self._collection_external_imports = collection_external

            return (
                {k: v.copy() for k, v in self._file_file_reads.items()},
                {k: v.copy() for k, v in collection_local.items()},
                {k: v.copy() for k, v in collection_external.items()},
            )

    # Legacy API wrappers still used by some call sites.
    def stop_global_tracking(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        return self.get_global_import_deps()

    def start_file_tracking(self, test_file: str) -> None:
        self.begin_test_file_collection(test_file)

    def stop_file_tracking(self, test_file: Optional[str] = None) -> None:
        return

    def set_collection_context(self, test_file: str) -> None:
        self.begin_test_file_collection(test_file)

    def clear_collection_context(self) -> None:
        self._active_collection_file = None

    def get_collection_file_deps(self) -> Dict[str, Set[TrackedFile]]:
        with self._lock:
            return {k: v.copy() for k, v in self._file_file_reads.items()}

    def get_collection_imports(self) -> tuple:
        with self._lock:
            local = {}
            external = {}
            for test_file in self._file_module_keys:
                _files, loc, ext = self.get_file_import_deps(test_file)
                local[test_file] = loc
                external[test_file] = ext
            return local, external

    # Runtime helpers expected by pytest plugin.
    def start_test_file(self, test_file: str) -> None:
        with self._lock:
            self._current_test_file = test_file

    def end_test_file(self, test_file: str) -> None:
        with self._lock:
            if self._current_test_file == test_file:
                self._current_test_file = None

    def switch_context(self, new_context: str) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        files, local, external, _ = self.stop()
        self.start(new_context, test_file=self._current_test_file)
        return files, local, external

    def get_current(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            ctx = self._current_context
            if not ctx:
                return set(), set(), set()
            return (
                self._tracked_files.get(ctx, set()).copy(),
                self._tracked_local_imports.get(ctx, set()).copy(),
                self._tracked_external_imports.get(ctx, set()).copy(),
            )

    # Old checkpoint API retained as no-op compatibility.
    def save_checkpoint(self) -> None:
        return

    def save_per_file_checkpoint(self, test_file: str) -> None:
        return

    def restore_to_checkpoint(self) -> None:
        return

    def restore_to_per_file_checkpoint(self, test_file: str) -> None:
        return

    def clear_local_modules_from_sysmodules(self) -> None:
        return

    def close(self) -> None:
        with self._lock:
            self._restore_hooks()
            self._session_started = False
            self._collection_mode = False
            self._active = False
            self._current_context = None
            self._current_test_file = None
            self._active_collection_file = None

            self._tracked_files.clear()
            self._tracked_local_imports.clear()
            self._tracked_external_imports.clear()
            self._test_files.clear()
            self._current_test_reads = set()
            self._current_test_seen_module_keys = set()

            self._global_module_keys = None
            self._global_unique_module_keys.clear()
            self._file_module_keys.clear()
            self._global_seen_module_keys.clear()
            self._file_seen_module_keys.clear()
            self._session_start_module_keys.clear()

            self._global_file_reads.clear()
            self._file_file_reads.clear()
            self._checkpoint_local_imports = set()
            self._collection_local_imports = {}
            self._collection_external_imports = {}

            self._stdlib_keep_keys.clear()
            self._stdlib_keep_tops.clear()
            self._real_modules.clear()

            self._path_cache.clear()
            self._module_kind_cache.clear()
            self._module_dep_cache.clear()


FILE_DEPENDENCY_MARKER = "__file_dep__"


def file_sha_to_checksum(sha: str) -> int:
    return int(sha[:8], 16) & 0x7FFFFFFF
