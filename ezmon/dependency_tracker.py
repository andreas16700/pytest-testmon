"""
Dependency tracking for ezmon.

Pure import-hook model with deferred reconciliation:
1. Import hooks record raw (name, fromlist) pairs with zero processing.
2. Reconciliation at scope boundaries expands prefixes, resolves fromlists,
   and classifies modules into local/external/file deps.
3. No sys.modules restoration. No rehydration. No real_modules cache.
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
from collections import defaultdict
from typing import Dict, NamedTuple, Optional, Set, Tuple

from ezmon.file_cache import FileInfoCache


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

        # Scope model: "idle" | "global" | "file" | "test"
        self._scope: str = "idle"
        self._scope_file: Optional[str] = None

        # Import recordings (raw hook data)
        self._recording: defaultdict = defaultdict(set)
        self._global_recording: Optional[dict] = None
        self._file_recordings: Dict[str, dict] = {}

        # Reconciled deps (computed once per scope)
        self._global_deps: Optional[Tuple[Set[TrackedFile], Set[str], Set[str]]] = None
        self._file_deps: Dict[str, Tuple[Set[TrackedFile], Set[str], Set[str]]] = {}

        # File-read tracking
        self._current_reads: Set[TrackedFile] = set()
        self._global_reads: Set[TrackedFile] = set()
        self._file_reads: Dict[str, Set[TrackedFile]] = {}

        # Test scope state
        self._current_context: Optional[str] = None

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
            module = sys.modules.get(mod_name)

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

    def _resolve_module_key(self, mod_name: str) -> Tuple[Optional[str], Optional[TrackedFile], Optional[str]]:
        cached = self._module_dep_cache.get(mod_name)
        if cached is not None:
            return cached

        local_dep: Optional[str] = None
        file_dep: Optional[TrackedFile] = None
        external_dep: Optional[str] = None

        module = sys.modules.get(mod_name)
        if module is None:
            out = (None, None, None)
            self._module_dep_cache[mod_name] = out
            return out

        filepath = self._get_module_filepath(module)
        relpath = self._is_in_project(filepath) if filepath else None

        if relpath and relpath.endswith(".py"):
            if self._expected_imports is not None:
                if relpath in self._expected_imports:
                    local_dep = relpath
            elif self._file_cache.is_tracked(relpath):
                local_dep = relpath
            out = (local_dep, None, None)
            self._module_dep_cache[mod_name] = out
            return out

        if relpath and not relpath.endswith(".py"):
            if self._expected_reads is not None:
                if relpath not in self._expected_reads:
                    out = (None, None, None)
                    self._module_dep_cache[mod_name] = out
                    return out

            sha = None
            if self._compute_shas:
                sha = self._get_committed_file_sha(relpath)
                if not sha:
                    out = (None, None, None)
                    self._module_dep_cache[mod_name] = out
                    return out
            file_dep = TrackedFile(path=relpath, sha=sha)
            out = (None, file_dep, None)
            self._module_dep_cache[mod_name] = out
            return out

        kind = self._module_kind(mod_name, module)
        if kind == "local":
            source = self._local_source(mod_name)
            rel = self._is_in_project(source) if source else None
            if rel and rel.endswith(".py"):
                if self._expected_imports is not None:
                    if rel in self._expected_imports:
                        local_dep = rel
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

    # ---------------------------------------------------------------------
    # Reconciliation
    # ---------------------------------------------------------------------

    def _reconcile(self, recording: dict) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        """Resolve a raw recording into (file_deps, local_imports, external_imports)."""
        local_imports: Set[str] = set()
        file_deps: Set[TrackedFile] = set()
        external_imports: Set[str] = set()

        def _collect(mod_name: str) -> None:
            local, filed, external = self._resolve_module_key(mod_name)
            if local:
                local_imports.add(local)
            if filed:
                file_deps.add(filed)
            if external:
                external_imports.add(external)

        for key, fromlists in recording.items():
            if not key:
                continue

            # 1. Prefix expansion: "a.b.c" → resolve "a", "a.b", "a.b.c"
            parts = key.split(".")
            for i in range(len(parts)):
                _collect(".".join(parts[: i + 1]))

            # 2. Package submodule expansion for re-exporting packages.
            # When "from pkg import Attr" where Attr is re-exported (not a
            # submodule), the package's __init__.py imported submodules to
            # define Attr.  Without sys.modules restoration, __init__.py
            # only runs once, so later recordings miss these transitive
            # imports.  Expand only when a fromlist item is NOT a direct
            # submodule (i.e. the package re-exports attributes).
            has_reexport = False
            for fl in fromlists:
                if fl is None:
                    continue
                for item in fl:
                    if item == "*":
                        continue
                    if f"{key}.{item}" not in sys.modules:
                        has_reexport = True
                        break
                if has_reexport:
                    break

            if has_reexport:
                mod = sys.modules.get(key)
                if mod and hasattr(mod, "__path__") and self._module_kind(key, mod) == "local":
                    child_prefix = key + "."
                    for child_key in list(sys.modules.keys()):
                        if child_key.startswith(child_prefix):
                            _collect(child_key)

            # 3. Fromlist expansion
            for fl in fromlists:
                if fl is None:
                    continue
                for item in fl:
                    if item == "*":
                        continue
                    # Try as submodule first
                    candidate = f"{key}.{item}"
                    local, filed, external = self._resolve_module_key(candidate)
                    if local or filed or external:
                        _collect(candidate)
                    else:
                        # Attribute — trace defining module
                        mod = sys.modules.get(key)
                        if mod:
                            attr = getattr(mod, item, None)
                            defining = getattr(attr, "__module__", None)
                            if defining:
                                _collect(defining)

        return file_deps, local_imports, external_imports

    # ---------------------------------------------------------------------
    # Hooked functions
    # ---------------------------------------------------------------------

    def _track_file(self, filepath: str, mode: str) -> None:
        if "r" not in mode and "+" not in mode:
            return

        relpath = self._is_in_project(filepath)
        if not relpath or relpath.endswith(".py"):
            return

        if self._expected_reads is not None:
            if relpath not in self._expected_reads:
                return

        sha = None
        if self._compute_shas:
            sha = self._get_committed_file_sha(relpath)
            if not sha:
                return

        tracked = TrackedFile(path=relpath, sha=sha)

        scope = self._scope
        if scope == "test":
            self._current_reads.add(tracked)
        elif scope == "global":
            self._global_reads.add(tracked)
        elif scope == "file" and self._scope_file:
            self._file_reads.setdefault(self._scope_file, set()).add(tracked)

    def _tracking_open(self, file, mode="r", *args, **kwargs):
        result = self._original_open(file, mode, *args, **kwargs)
        if self._scope != "idle":
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

    def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        result = self._original_import(name, globals, locals, fromlist, level)
        fl = tuple(fromlist) if fromlist is not None else None
        self._recording[name].add(fl)
        try:
            self._recording[result.__name__].add(fl)
        except Exception:
            pass
        return result

    def _tracking_import_module(self, name, package=None):
        result = self._original_import_module(name, package)
        try:
            self._recording[result.__name__].add(None)
        except Exception:
            pass
        return result

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
    # Event model — scope transitions
    # ---------------------------------------------------------------------

    def start_collection_tracking(self) -> None:
        with self._lock:
            self._scope = "global"
            self._scope_file = None
            self._recording = defaultdict(set)
            self._global_recording = None
            self._file_recordings = {}
            self._global_deps = None
            self._file_deps = {}
            self._global_reads = set()
            self._file_reads = {}
            self._current_reads = set()
            self._current_context = None

            self._module_kind_cache.clear()
            self._module_dep_cache.clear()
            self._path_cache.clear()

            self._install_hooks()

    def mark_collection_started(self) -> None:
        return

    def begin_test_file_collection(self, test_file: str) -> None:
        if not test_file:
            return
        with self._lock:
            if self._scope == "idle":
                self.start_collection_tracking()

            if self._scope == "global":
                # First file: freeze global recording, reconcile, switch to file scope
                self._global_recording = dict(self._recording)
                self._global_deps = self._reconcile(self._global_recording)
                self._scope = "file"
                self._scope_file = test_file
                self._recording = defaultdict(set)
                self._file_reads.setdefault(test_file, set())
                return

            # Subsequent files: freeze previous file recording
            if self._scope == "file" and self._scope_file and self._scope_file != test_file:
                self._file_recordings[self._scope_file] = dict(self._recording)
                self._file_deps[self._scope_file] = self._reconcile(
                    self._file_recordings[self._scope_file]
                )

            self._scope_file = test_file
            self._recording = defaultdict(set)
            self._file_reads.setdefault(test_file, set())

    def stop_collection_tracking(self) -> tuple:
        with self._lock:
            # Freeze last file if in file scope
            if self._scope == "file" and self._scope_file:
                if self._scope_file not in self._file_recordings:
                    self._file_recordings[self._scope_file] = dict(self._recording)
                    self._file_deps[self._scope_file] = self._reconcile(
                        self._file_recordings[self._scope_file]
                    )

            self._scope = "idle"
            self._scope_file = None

            # Build return value: per-file (file_deps_reads, local_imports, external_imports)
            all_file_reads: Dict[str, Set[TrackedFile]] = {}
            all_local: Dict[str, Set[str]] = {}
            all_external: Dict[str, Set[str]] = {}

            for test_file in self._file_deps:
                module_files, local, external = self._file_deps[test_file]
                reads = self._file_reads.get(test_file, set())
                all_file_reads[test_file] = reads | module_files
                all_local[test_file] = set(local)
                all_external[test_file] = set(external)

            return (
                {k: v.copy() for k, v in all_file_reads.items()},
                {k: v.copy() for k, v in all_local.items()},
                {k: v.copy() for k, v in all_external.items()},
            )

    def start_test_file(self, test_file: str) -> None:
        with self._lock:
            self._scope_file = test_file

    def end_test_file(self, test_file: str) -> None:
        with self._lock:
            if self._scope_file == test_file:
                self._scope_file = None

    def start_test(self, context: str, test_file: Optional[str] = None) -> None:
        self.start(context, test_file=test_file)

    def start(self, context: str, test_file: Optional[str] = None) -> None:
        with self._lock:
            if self._scope == "idle" and self._global_deps is None:
                self.start_collection_tracking()

            active_file = test_file or self._scope_file or "<unknown>"

            self._scope = "test"
            self._scope_file = active_file
            self._current_context = context
            self._recording = defaultdict(set)
            self._current_reads = set()

            self._install_hooks()

    def end_test(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        return self.stop()

    def stop(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        with self._lock:
            context = self._current_context
            if not context:
                return set(), set(), set(), None

            test_file = self._scope_file

            # Reconcile test recording
            test_module_files, test_local, test_external = self._reconcile(self._recording)

            # Subtract global deps
            if self._global_deps:
                g_files, g_local, g_external = self._global_deps
                test_module_files -= g_files
                test_local -= g_local
                test_external -= g_external

            # Subtract file deps
            if test_file and test_file in self._file_deps:
                f_files, f_local, f_external = self._file_deps[test_file]
                test_module_files -= f_files
                test_local -= f_local
                test_external -= f_external

            # File reads: subtract global and file reads
            read_unique = set(self._current_reads)
            read_unique -= self._global_reads
            if test_file:
                read_unique -= self._file_reads.get(test_file, set())

            file_deps = test_module_files | read_unique

            self._current_context = None
            self._current_reads = set()
            self._scope = "idle"

            return file_deps, test_local, test_external, test_file

    # ---------------------------------------------------------------------
    # Aggregated dependency access
    # ---------------------------------------------------------------------

    def get_global_import_deps(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            if self._global_deps is None:
                return set(), set(), set()
            module_files, local, external = self._global_deps
            return self._global_reads | module_files, local, external

    def get_file_import_deps(self, test_file: str) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            if test_file not in self._file_deps:
                return set(), set(), set()
            module_files, local, external = self._file_deps[test_file]
            return self._file_reads.get(test_file, set()) | module_files, local, external

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._restore_hooks()
            self._scope = "idle"
            self._scope_file = None
            self._current_context = None
            self._recording = defaultdict(set)
            self._global_recording = None
            self._file_recordings = {}
            self._global_deps = None
            self._file_deps = {}
            self._global_reads = set()
            self._file_reads = {}
            self._current_reads = set()
            self._path_cache.clear()
            self._module_kind_cache.clear()
            self._module_dep_cache.clear()


FILE_DEPENDENCY_MARKER = "__file_dep__"


def file_sha_to_checksum(sha: str) -> int:
    return int(sha[:8], 16) & 0x7FFFFFFF
