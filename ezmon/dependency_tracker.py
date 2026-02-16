"""
Dependency tracking for ezmon.

This tracker captures dependencies at three levels without sys.modules checkpointing:
1. Global: dependencies seen before test-file collection starts (e.g. conftest imports)
2. Test-file: dependencies newly seen while collecting each test file
3. Test: dependencies newly seen while running each individual test
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

from ezmon.common import get_logger
from ezmon.file_cache import FileInfoCache
from ezmon.trie import get_encoder

logger = get_logger(__name__)


class TrackedFile(NamedTuple):
    """A tracked file dependency."""

    path: str  # Relative path from rootdir or encoded path
    sha: Optional[str]  # Git blob SHA (None if deferred)


class TrackedImport(NamedTuple):
    """A tracked import dependency."""

    name: str
    path: str
    is_local: bool


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
    """Tracks file and import dependencies during collection and test execution."""

    def __init__(self, rootdir: str):
        self.rootdir = os.path.realpath(rootdir)
        self._root_prefix = self.rootdir + os.sep
        self._stdlib_prefix = _get_stdlib_prefix()
        self._site_dirs = _get_site_dirs()

        self._lock = threading.RLock()
        self._file_cache = FileInfoCache(self.rootdir)

        # Original functions to restore
        self._original_open = None
        self._original_io_open = None
        self._original_import = None
        self._original_import_module = None

        # Activity state
        self._active = False  # Test-level tracking active
        self._collection_mode = False
        self._global_tracking_active = False
        self._collection_context: Optional[str] = None
        self._current_context: Optional[str] = None
        self._current_test_file: Optional[str] = None

        # Per-test tracked deltas (returned by stop/end_test)
        self._tracked_files: Dict[str, Set[TrackedFile]] = {}
        self._tracked_local_imports: Dict[str, Set[str]] = {}
        self._tracked_external_imports: Dict[str, Set[str]] = {}
        self._test_files: Dict[str, str] = {}

        # Global-level dependencies (before per-file collection context is set)
        self._global_file_deps: Set[TrackedFile] = set()
        self._global_local_imports: Set[str] = set()
        self._global_external_imports: Set[str] = set()

        # Backwards-compatible aliases consumed by existing code
        self._checkpoint_file_deps = self._global_file_deps
        self._checkpoint_local_imports = self._global_local_imports

        # Per-test-file collection-time deltas (new after global)
        self._collection_file_deps: Dict[str, Set[TrackedFile]] = {}
        self._collection_local_imports: Dict[str, Set[str]] = {}
        self._collection_external_imports: Dict[str, Set[str]] = {}

        # Caches
        self._path_cache: Dict[str, Optional[str]] = {}
        self._module_kind_cache: Dict[str, str] = {}
        self._local_packages_cache: Dict[str, bool] = {}
        self._local_pkgs = self._scan_local_packages()

        # Directories to skip
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

        # Expected tracking filters (None means track all)
        self._expected_imports: Optional[Set[str]] = None
        self._expected_reads: Optional[Set[str]] = None
        self._expected_packages: Optional[Set[str]] = None
        self._compute_shas = True
        self._expected_package_index: Optional[Dict[str, int]] = None

    def _scan_local_packages(self) -> Set[str]:
        """Best-effort scan of local package/module roots under project paths."""
        pkgs: Set[str] = set()
        search_dirs = ["", "src", "lib", "source", "packages"]
        for search_dir in search_dirs:
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
        """Return normalized relpath when filepath belongs to project; else None."""
        if filepath in self._path_cache:
            return self._path_cache[filepath]

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
        except (ValueError, TypeError):
            self._path_cache[filepath] = None
            return None

    def _get_committed_file_sha(self, relpath: str) -> Optional[str]:
        """Return git blob SHA for relpath tracked in repo, else None."""
        cache_key = f"git_sha:{relpath}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]
        sha = self._file_cache.get_tracked_sha(relpath)
        self._path_cache[cache_key] = sha
        return sha

    def _get_module_filepath(self, module) -> Optional[str]:
        """Resolve module file path from spec/file attributes."""
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
            except (ImportError, ValueError, TypeError):
                source_path = filepath[:-1]
                if os.path.exists(source_path):
                    filepath = source_path

        return os.path.realpath(filepath)

    def _module_kind(self, mod_name: str, module=None) -> str:
        """Classify module as local|external|stdlib."""
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
        """Map module name to local .py source under root, if present."""
        parts = mod_name.split(".")
        pkg_init = os.path.join(self.rootdir, *parts, "__init__.py")
        if os.path.isfile(pkg_init):
            return pkg_init
        module_py = os.path.join(self.rootdir, *parts) + ".py"
        if os.path.isfile(module_py):
            return module_py

        for prefix in ("src", "lib", "source", "packages"):
            pkg_init = os.path.join(self.rootdir, prefix, *parts, "__init__.py")
            if os.path.isfile(pkg_init):
                return pkg_init
            module_py = os.path.join(self.rootdir, prefix, *parts) + ".py"
            if os.path.isfile(module_py):
                return module_py

        return None

    def _get_module_file(self, module) -> Optional[Tuple[str, bool]]:
        """Resolve module to tracked project file.

        Returns (path, is_python_file) where path is encoded if expected filters are active.
        """
        if module is None:
            return None

        filepath = self._get_module_filepath(module)
        relpath = self._is_in_project(filepath) if filepath else None

        # Local namespace/builtins without __file__: map via module name when local.
        if not relpath:
            mod_name = getattr(module, "__name__", None)
            if mod_name and mod_name.split(".")[0] in self._local_pkgs:
                local_source = self._local_source(mod_name)
                if local_source:
                    relpath = self._is_in_project(local_source)

        if not relpath:
            return None

        encoder = get_encoder(self.rootdir)

        if relpath.endswith(".py"):
            if self._expected_imports is not None:
                encoded = encoder.encode(relpath)
                if encoded not in self._expected_imports:
                    return None
                return encoded, True
            if self._file_cache.is_tracked(relpath):
                return relpath, True
            return None

        if self._expected_reads is not None:
            encoded = encoder.encode(relpath)
            if encoded not in self._expected_reads:
                return None
            return encoded, False

        sha = self._get_committed_file_sha(relpath)
        if sha:
            return relpath, False
        return None

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

    def _record_file_dep(self, tracked_file: TrackedFile) -> None:
        """Route a tracked file dependency to global/file/test scopes."""
        in_test_mode = self._active and self._current_context

        if self._collection_mode:
            if self._collection_context:
                if tracked_file not in self._global_file_deps:
                    self._collection_file_deps.setdefault(self._collection_context, set()).add(tracked_file)
            elif self._global_tracking_active:
                self._global_file_deps.add(tracked_file)

        if in_test_mode:
            ctx = self._current_context
            self._tracked_files.setdefault(ctx, set()).add(tracked_file)

    def _record_local_import(self, relpath: str) -> None:
        """Route a local python import to global/file/test scopes."""
        in_test_mode = self._active and self._current_context

        if self._collection_mode:
            if self._collection_context:
                if relpath not in self._global_local_imports:
                    self._collection_local_imports.setdefault(self._collection_context, set()).add(relpath)
            elif self._global_tracking_active:
                self._global_local_imports.add(relpath)

        if in_test_mode:
            ctx = self._current_context
            self._tracked_local_imports.setdefault(ctx, set()).add(relpath)

    def _record_external_import(self, pkg_name: str) -> None:
        """Route an external package dependency to global/file/test scopes."""
        in_test_mode = self._active and self._current_context

        if self._expected_packages is not None:
            encoded_pkg = (
                self._expected_package_index.get(pkg_name, pkg_name)
                if self._expected_package_index is not None
                else pkg_name
            )
            if encoded_pkg not in self._expected_packages:
                return
            pkg_name = encoded_pkg
        elif self._expected_package_index is not None:
            pkg_name = self._expected_package_index.get(pkg_name, pkg_name)

        if self._collection_mode:
            if self._collection_context:
                if pkg_name not in self._global_external_imports:
                    self._collection_external_imports.setdefault(self._collection_context, set()).add(pkg_name)
            elif self._global_tracking_active:
                self._global_external_imports.add(pkg_name)

        if in_test_mode:
            ctx = self._current_context
            self._tracked_external_imports.setdefault(ctx, set()).add(pkg_name)

    def _record_import_name(self, mod_name: str) -> None:
        """Record a single module name dependency from sys.modules."""
        if not mod_name:
            return

        module = sys.modules.get(mod_name)
        if module is None:
            return

        module_info = self._get_module_file(module)
        if module_info:
            path, is_python = module_info
            if is_python:
                self._record_local_import(path)
            else:
                # For encoded paths we need original relpath to resolve git sha.
                relpath_for_sha = path
                if self._expected_reads is not None:
                    try:
                        decoded = get_encoder(self.rootdir).decode(path)
                        if str(decoded).startswith(str(self.rootdir)):
                            relpath_for_sha = str(decoded.relative_to(self.rootdir))
                        else:
                            relpath_for_sha = str(decoded)
                    except Exception:
                        relpath_for_sha = path

                sha = None
                if self._compute_shas:
                    sha = self._get_committed_file_sha(relpath_for_sha)
                    if not sha:
                        return

                self._record_file_dep(TrackedFile(path=path, sha=sha))
            return

        kind = self._module_kind(mod_name, module)
        if kind == "stdlib":
            return
        if kind == "local":
            # Local module not resolved from __file__; map by name.
            local_source = self._local_source(mod_name)
            if local_source:
                relpath = self._is_in_project(local_source)
                if relpath and relpath.endswith(".py"):
                    if self._expected_imports is not None:
                        encoded = get_encoder(self.rootdir).encode(relpath)
                        if encoded not in self._expected_imports:
                            return
                        relpath = encoded
                    self._record_local_import(relpath)
            return

        pkg_name = mod_name.split(".")[0]
        if not pkg_name or pkg_name.startswith("_"):
            return
        if pkg_name in _STDLIB_NAMES:
            return
        self._record_external_import(pkg_name)

    def _track_file(self, filepath: str, mode: str) -> None:
        """Track a file read operation."""
        should_track = self._collection_mode or (self._active and self._current_context)
        if not should_track:
            return
        if "r" not in mode and "+" not in mode:
            return

        relpath = self._is_in_project(filepath)
        if not relpath or relpath.endswith(".py"):
            return

        original_relpath = relpath
        if self._expected_reads is not None:
            encoded = get_encoder(self.rootdir).encode(relpath)
            if encoded not in self._expected_reads:
                return
            relpath = encoded

        sha = None
        if self._compute_shas:
            sha = self._get_committed_file_sha(original_relpath)
            if not sha:
                return

        self._record_file_dep(TrackedFile(path=relpath, sha=sha))

    def _tracking_open(self, file, mode="r", *args, **kwargs):
        """Replacement for builtins.open that tracks file access."""
        result = self._original_open(file, mode, *args, **kwargs)
        if self._collection_mode or self._active:
            if isinstance(file, str):
                filepath = file
            elif isinstance(file, os.PathLike):
                filepath = os.fspath(file)
            else:
                filepath = None
            if filepath:
                try:
                    self._track_file(filepath, mode)
                except Exception:
                    pass
        return result

    def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        """Replacement for builtins.__import__ that tracks import dependencies."""
        should_track = self._collection_mode or (self._active and self._current_context)

        resolved_name = self._resolve_name(name, globals, level)
        names = []
        if resolved_name:
            parts = resolved_name.split(".")
            names.extend(".".join(parts[: i + 1]) for i in range(len(parts)))
            if fromlist:
                for attr in fromlist:
                    if attr != "*":
                        names.append(f"{resolved_name}.{attr}")

        result = self._original_import(name, globals, locals, fromlist, level)

        if should_track:
            try:
                # Import path hierarchy + fromlist candidates.
                for mod_name in names:
                    self._record_import_name(mod_name)

                # Track returned module itself.
                result_name = getattr(result, "__name__", None)
                if result_name:
                    self._record_import_name(result_name)
                    # For local package imports, also include already-loaded
                    # local submodules under that package. This keeps dependency
                    # attribution stable whether submodules were loaded earlier
                    # or loaded fresh in this import.
                    should_expand_package = bool(fromlist) or (name == result_name)
                    if (
                        should_expand_package
                        and hasattr(result, "__path__")
                        and self._module_kind(result_name, result) == "local"
                    ):
                        prefix = result_name + "."
                        for dep_name in list(sys.modules.keys()):
                            if dep_name.startswith(prefix):
                                self._record_import_name(dep_name)

                # For `from X import Y`, Y may be class/function; track defining module.
                for attr_name in fromlist or ():
                    if attr_name == "*":
                        continue
                    imported_obj = getattr(result, attr_name, None)
                    if imported_obj is None:
                        continue
                    if isinstance(imported_obj, types.ModuleType):
                        self._record_import_name(getattr(imported_obj, "__name__", ""))
                    else:
                        defining_mod = getattr(imported_obj, "__module__", None)
                        if defining_mod:
                            self._record_import_name(defining_mod)
            except Exception:
                pass

        return result

    def _tracking_import_module(self, name, package=None):
        """Replacement for importlib.import_module that tracks dependencies."""
        result = self._original_import_module(name, package)

        should_track = self._collection_mode or (self._active and self._current_context)
        if not should_track:
            return result

        try:
            if name.startswith("."):
                if package is None:
                    return result
                abs_name = importlib.util.resolve_name(name, package)
            else:
                abs_name = name
            self._record_import_name(abs_name)
            module_name = getattr(result, "__name__", None)
            if module_name:
                self._record_import_name(module_name)
        except Exception:
            pass

        return result

    @lru_cache(maxsize=256)
    def _is_installed_package(self, pkg_name: str) -> bool:
        try:
            spec = importlib.util.find_spec(pkg_name)
            return spec is not None
        except (ModuleNotFoundError, ImportError, ValueError):
            return False

    def _install_hooks(self) -> None:
        """Install tracking hooks for open() and imports."""
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
        """Restore all original functions."""
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
        self._active = False

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

    # =========================================================================
    # Collection-time tracking methods
    # =========================================================================

    def start_collection_tracking(self) -> None:
        """Start tracking dependencies during collection phase."""
        with self._lock:
            self._collection_mode = True
            self._global_tracking_active = True
            self._collection_context = None
            self._global_file_deps.clear()
            self._global_local_imports.clear()
            self._global_external_imports.clear()
            self._collection_file_deps = {}
            self._collection_local_imports = {}
            self._collection_external_imports = {}
            self._module_kind_cache.clear()
            self._install_hooks()

    def stop_global_tracking(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        """Freeze global collection phase and return global dependencies."""
        with self._lock:
            self._global_tracking_active = False
            return (
                self._global_file_deps.copy(),
                self._global_local_imports.copy(),
                self._global_external_imports.copy(),
            )

    def start_file_tracking(self, test_file: str) -> None:
        """Start collection tracking for a specific test file."""
        self.set_collection_context(test_file)

    def stop_file_tracking(self, test_file: Optional[str] = None) -> None:
        """Stop collection tracking for the current test file context."""
        with self._lock:
            if test_file is None or self._collection_context == test_file:
                self._collection_context = None

    def set_collection_context(self, test_file: str) -> None:
        """Set current test file being collected."""
        with self._lock:
            if self._global_tracking_active:
                self._global_tracking_active = False
            self._collection_context = test_file
            self._collection_file_deps.setdefault(test_file, set())
            self._collection_local_imports.setdefault(test_file, set())
            self._collection_external_imports.setdefault(test_file, set())

    def clear_collection_context(self) -> None:
        """Clear current collection context."""
        with self._lock:
            self._collection_context = None

    def stop_collection_tracking(self) -> tuple:
        """Stop collection tracking and return per-file collection dependencies."""
        with self._lock:
            self._collection_mode = False
            self._global_tracking_active = False
            self._collection_context = None
            return (
                {k: v.copy() for k, v in self._collection_file_deps.items()},
                {k: v.copy() for k, v in self._collection_local_imports.items()},
                {k: v.copy() for k, v in self._collection_external_imports.items()},
            )

    def get_collection_file_deps(self) -> Dict[str, Set[TrackedFile]]:
        with self._lock:
            return {k: v.copy() for k, v in self._collection_file_deps.items()}

    def get_collection_imports(self) -> tuple:
        with self._lock:
            return (
                {k: v.copy() for k, v in self._collection_local_imports.items()},
                {k: v.copy() for k, v in self._collection_external_imports.items()},
            )

    # =========================================================================
    # Test execution tracking methods
    # =========================================================================

    def start_test_file(self, test_file: str) -> None:
        """Notify tracker that a test file is starting execution."""
        if not test_file:
            return
        with self._lock:
            self._current_test_file = test_file

    def end_test_file(self, test_file: str) -> None:
        """Notify tracker that a test file finished execution."""
        with self._lock:
            if self._current_test_file == test_file:
                self._current_test_file = None

    def start_test(self, context: str, test_file: Optional[str] = None) -> None:
        self.start(context, test_file=test_file)

    def end_test(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        return self.stop()

    def start(self, context: str, test_file: Optional[str] = None) -> None:
        """Start tracking dependencies for a test context."""
        with self._lock:
            self._current_context = context
            self._tracked_files[context] = set()
            self._tracked_local_imports[context] = set()
            self._tracked_external_imports[context] = set()
            if test_file:
                self._test_files[context] = test_file
                self.start_test_file(test_file)
            elif self._current_test_file:
                self._test_files[context] = self._current_test_file

            self._install_hooks()
            self._active = True

    def stop(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        """Stop current test tracking and return test-level deltas."""
        with self._lock:
            context = self._current_context
            files = self._tracked_files.pop(context, set()) if context else set()
            local_imports = self._tracked_local_imports.pop(context, set()) if context else set()
            external_imports = self._tracked_external_imports.pop(context, set()) if context else set()
            test_file = self._test_files.pop(context, None) if context else None
            self._current_context = None
            return files, local_imports, external_imports, test_file

    def switch_context(self, new_context: str) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            old_context = self._current_context
            files = self._tracked_files.pop(old_context, set()) if old_context else set()
            local_imports = self._tracked_local_imports.pop(old_context, set()) if old_context else set()
            external_imports = self._tracked_external_imports.pop(old_context, set()) if old_context else set()

            self._current_context = new_context
            self._tracked_files[new_context] = set()
            self._tracked_local_imports[new_context] = set()
            self._tracked_external_imports[new_context] = set()
            if self._current_test_file:
                self._test_files[new_context] = self._current_test_file

            return files, local_imports, external_imports

    def get_current(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        with self._lock:
            context = self._current_context
            if not context:
                return set(), set(), set()
            return (
                self._tracked_files.get(context, set()).copy(),
                self._tracked_local_imports.get(context, set()).copy(),
                self._tracked_external_imports.get(context, set()).copy(),
            )

    # Compatibility no-ops retained for older call sites.
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
        """Fully close tracker and restore hooks."""
        with self._lock:
            self._restore_hooks()
            self._current_context = None
            self._current_test_file = None
            self._tracked_files.clear()
            self._tracked_local_imports.clear()
            self._tracked_external_imports.clear()
            self._test_files.clear()
            self._collection_file_deps.clear()
            self._collection_local_imports.clear()
            self._collection_external_imports.clear()
            self._path_cache.clear()
            self._module_kind_cache.clear()


# Special checksum marker for file dependencies
FILE_DEPENDENCY_MARKER = "__file_dep__"



def file_sha_to_checksum(sha: str) -> int:
    """Convert a file SHA to a checksum integer for fingerprinting."""
    return int(sha[:8], 16) & 0x7FFFFFFF
