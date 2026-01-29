"""
Dependency tracking for ezmon.

This module provides comprehensive dependency tracking to capture:
1. File dependencies - non-Python files read during test execution
2. Import dependencies - all Python modules imported during test execution

This complements coverage.py's line tracking by ensuring dependencies are
captured even when:
- Non-Python files (JSON, YAML, etc.) are read
- Python modules are imported from cache (sys.modules)
- Modules are imported but no code is executed from them
"""

import ast
import builtins
import hashlib
import importlib
import importlib.util
import os
import subprocess
import sys
import threading
from functools import lru_cache
from typing import Dict, Optional, Set, Tuple, NamedTuple

from ezmon.common import get_logger

logger = get_logger(__name__)


class TrackedFile(NamedTuple):
    """A tracked file dependency."""
    path: str  # Relative path from rootdir
    sha: str   # SHA hash of file contents


class TrackedImport(NamedTuple):
    """A tracked import dependency."""
    name: str      # Module name
    path: str      # Relative path (for local) or None (for external)
    is_local: bool # True if in project, False if external


class DependencyTracker:
    """
    Tracks file and import dependencies during test execution.

    This class hooks into Python's file I/O and import system to capture
    all dependencies, including those that coverage.py might miss.

    Usage:
        tracker = DependencyTracker(rootdir="/path/to/project")

        # Collection-time tracking (captures import-time file reads):
        tracker.start_collection_tracking()
        tracker.set_collection_context("tests/test_a.py")
        # ... test file imported, modules load, file reads happen ...
        collection_deps = tracker.stop_collection_tracking()

        # Test execution tracking:
        tracker.start("test_name")
        # ... test runs ...
        files, local_imports, external_imports = tracker.stop()
    """

    def __init__(self, rootdir: str):
        self.rootdir = os.path.abspath(rootdir)
        self._lock = threading.RLock()

        # Per-context (test) tracking
        self._current_context: Optional[str] = None
        self._tracked_files: Dict[str, Set[TrackedFile]] = {}
        self._tracked_local_imports: Dict[str, Set[str]] = {}
        self._tracked_external_imports: Dict[str, Set[str]] = {}

        # Original functions to restore
        self._original_open: Optional[callable] = None
        self._original_import: Optional[callable] = None
        self._original_import_module: Optional[callable] = None

        # State
        self._active = False

        # Collection-time tracking state
        # Tracks file reads that happen during test collection (import time)
        self._collection_mode = False
        self._collection_context: Optional[str] = None  # Current test file being collected
        self._collection_file_deps: Dict[str, Set[TrackedFile]] = {}  # {test_file: set of TrackedFile}

        # Cache for path checks
        self._path_cache: Dict[str, Optional[str]] = {}

        # Directories to skip
        self._skip_dirs = frozenset([
            '.venv', 'venv', '.tox', 'site-packages', '.git',
            '__pycache__', '.pytest_cache', '.mypy_cache',
            'node_modules', '.eggs', '*.egg-info',
        ])

        # Cache for local package detection
        self._local_packages_cache: Dict[str, bool] = {}

    def _is_in_project(self, filepath: str) -> Optional[str]:
        """
        Check if a file is within the project directory.
        Returns the relative path if in project, None otherwise.
        """
        if filepath in self._path_cache:
            return self._path_cache[filepath]

        try:
            abspath = os.path.abspath(filepath)
            relpath = os.path.relpath(abspath, self.rootdir)

            # Skip if outside rootdir
            if relpath.startswith('..') or os.path.isabs(relpath):
                self._path_cache[filepath] = None
                return None

            # Skip common non-project directories
            parts = relpath.replace(os.sep, '/').split('/')
            for skip_dir in self._skip_dirs:
                if any(part == skip_dir or part.endswith('.egg-info') for part in parts):
                    self._path_cache[filepath] = None
                    return None

            # Normalize path separators
            relpath = relpath.replace(os.sep, '/')
            self._path_cache[filepath] = relpath
            return relpath

        except (ValueError, TypeError):
            self._path_cache[filepath] = None
            return None

    def _is_local_package(self, pkg_name: str) -> bool:
        """
        Check if a package name corresponds to a local project package.

        This handles the case where a package is installed (e.g., pip install -e .)
        but is actually the project being tested. For example, when testing
        matplotlib, 'import matplotlib.pyplot' should NOT be treated as an
        external dependency because matplotlib IS the local project.

        We detect this by checking if there's a Python package directory
        in the project that matches the package name.
        """
        if pkg_name in self._local_packages_cache:
            return self._local_packages_cache[pkg_name]

        # Look for a package directory matching this name anywhere in the project
        # Common patterns:
        # - src/packagename/
        # - lib/packagename/
        # - packagename/
        search_dirs = ['', 'src', 'lib', 'source', 'packages']

        for search_dir in search_dirs:
            if search_dir:
                pkg_path = os.path.join(self.rootdir, search_dir, pkg_name)
            else:
                pkg_path = os.path.join(self.rootdir, pkg_name)

            # Check if it's a package (directory with __init__.py or just a .py file)
            if os.path.isdir(pkg_path):
                init_file = os.path.join(pkg_path, '__init__.py')
                if os.path.exists(init_file):
                    self._local_packages_cache[pkg_name] = True
                    return True

            # Also check for single-file modules
            py_file = pkg_path + '.py'
            if os.path.exists(py_file):
                self._local_packages_cache[pkg_name] = True
                return True

        self._local_packages_cache[pkg_name] = False
        return False

    def _compute_file_sha(self, filepath: str) -> Optional[str]:
        """Compute SHA1 hash of file contents."""
        try:
            with self._original_open(filepath, 'rb') as f:
                content = f.read()
            return hashlib.sha1(content).hexdigest()
        except (OSError, IOError):
            return None

    def _get_committed_file_sha(self, relpath: str) -> Optional[str]:
        """
        Get the git blob hash for the committed version of a file.

        This only returns a SHA for files that exist in the committed HEAD.
        This ensures:
        1. Ephemeral/generated files (not in git) are NOT tracked
        2. Files modified during workflow are tracked with their committed state

        Returns None if the file is not committed in HEAD.
        """
        # Check cache first
        cache_key = f"git_sha:{relpath}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]

        try:
            result = subprocess.run(
                ['git', 'ls-tree', 'HEAD', '--', relpath],
                cwd=self.rootdir,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "100644 blob <sha>\t<filename>"
                parts = result.stdout.strip().split()
                if len(parts) >= 3 and parts[1] == 'blob':
                    sha = parts[2]
                    self._path_cache[cache_key] = sha
                    return sha
        except Exception:
            pass

        self._path_cache[cache_key] = None
        return None

    def _get_module_file(self, module) -> Optional[str]:
        """Get the file path for a module, if it's a local file."""
        if module is None:
            return None

        filepath = getattr(module, '__file__', None)
        if not filepath:
            return None

        # Handle compiled files
        if filepath.endswith(('.pyc', '.pyo')):
            try:
                source_path = importlib.util.source_from_cache(filepath)
                if source_path and os.path.exists(source_path):
                    filepath = source_path
            except (ImportError, ValueError, TypeError):
                # Try simple .pyc -> .py conversion
                source_path = filepath[:-1]
                if os.path.exists(source_path):
                    filepath = source_path

        return self._is_in_project(filepath)

    def _get_package_name(self, module_name: str) -> str:
        """Extract the top-level package name from a module name."""
        return module_name.split('.')[0]

    def _is_stdlib_module(self, module_name: str) -> bool:
        """Check if a module is from the Python standard library."""
        try:
            module = sys.modules.get(module_name)
            if module is None:
                return False

            filepath = getattr(module, '__file__', None)
            if filepath is None:
                # Built-in modules have no __file__
                return True

            # Check if it's in the stdlib location
            stdlib_paths = [
                os.path.dirname(os.__file__),  # stdlib
                os.path.dirname(os.path.dirname(os.__file__)),  # lib-dynload
            ]

            filepath = os.path.abspath(filepath)
            for stdlib_path in stdlib_paths:
                if filepath.startswith(stdlib_path):
                    return True

            return False
        except Exception:
            return False

    def _track_file(self, filepath: str, mode: str) -> None:
        """Track a file read operation.

        Handles two modes:
        1. Collection mode: File reads during import time are associated with
           the test file being collected.
        2. Test execution mode: File reads during test run are associated with
           the specific test.

        Only tracks files that are committed in git (exist in HEAD).
        This ensures:
        1. Ephemeral/generated files (like result_images/) are NOT tracked
        2. Files modified during workflow are tracked with their committed state
        """
        # Check if we should track this read
        in_collection_mode = self._collection_mode and self._collection_context
        in_test_mode = self._active and self._current_context

        if not in_collection_mode and not in_test_mode:
            return

        # Only track read operations
        if 'r' not in mode and '+' not in mode:
            return

        # Check if file is in project
        relpath = self._is_in_project(filepath)
        if not relpath:
            return

        # Skip Python files (handled by import tracking)
        if relpath.endswith('.py'):
            return

        # Get the committed SHA from git HEAD
        # This returns None for files not in git (ephemeral/generated)
        # and returns the committed state for files modified during workflow
        sha = self._get_committed_file_sha(relpath)
        if not sha:
            return

        tracked_file = TrackedFile(path=relpath, sha=sha)

        with self._lock:
            # Collection mode: associate with test file being collected
            if in_collection_mode:
                if self._collection_context in self._collection_file_deps:
                    self._collection_file_deps[self._collection_context].add(tracked_file)

            # Test execution mode: associate with current test
            if in_test_mode:
                if self._current_context in self._tracked_files:
                    self._tracked_files[self._current_context].add(tracked_file)

    def _track_import(self, module, name: str) -> None:
        """Track a module import."""
        if not self._active or not self._current_context:
            return

        # Get module file path
        relpath = self._get_module_file(module)

        with self._lock:
            context = self._current_context

            if relpath:
                # Local module
                if context in self._tracked_local_imports:
                    self._tracked_local_imports[context].add(relpath)
            else:
                # External module (or built-in)
                # Get the actual module name from the module object for accuracy
                actual_name = getattr(module, '__name__', name) if module else name
                if not self._is_stdlib_module(actual_name):
                    pkg_name = self._get_package_name(actual_name)
                    # Only track non-empty, valid package names that aren't local packages
                    # This handles the case where the project being tested is pip-installed
                    # (e.g., matplotlib tests importing matplotlib.pyplot)
                    if pkg_name and not pkg_name.startswith('_') and not self._is_local_package(pkg_name):
                        if context in self._tracked_external_imports:
                            self._tracked_external_imports[context].add(pkg_name)

    def _tracking_open(self, file, mode='r', *args, **kwargs):
        """Replacement for builtins.open that tracks file access."""
        # Call original open first
        result = self._original_open(file, mode, *args, **kwargs)

        # Track in either collection mode or test execution mode
        should_track = self._active or (self._collection_mode and self._collection_context)
        if should_track:
            # Handle both str and Path objects (os.PathLike)
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
                    pass  # Don't let tracking errors affect the test

        return result

    def _tracking_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        """Replacement for builtins.__import__ that tracks imports."""
        result = self._original_import(name, globals, locals, fromlist, level)

        if self._active:
            try:
                # Track the main module
                self._track_import(result, name)

                # For 'from X import Y', also check if Y is a submodule
                if fromlist:
                    for attr_name in fromlist:
                        if attr_name == '*':
                            continue
                        try:
                            submodule = getattr(result, attr_name, None)
                            if submodule is not None and hasattr(submodule, '__file__'):
                                self._track_import(submodule, f"{name}.{attr_name}")
                        except Exception:
                            pass

                # Handle package imports - track parent packages too
                if '.' in name:
                    parts = name.split('.')
                    for i in range(1, len(parts)):
                        parent_name = '.'.join(parts[:i])
                        parent_module = sys.modules.get(parent_name)
                        if parent_module:
                            self._track_import(parent_module, parent_name)
            except Exception:
                pass  # Don't let tracking errors affect the test

        return result

    def _tracking_import_module(self, name, package=None):
        """Replacement for importlib.import_module that tracks imports."""
        result = self._original_import_module(name, package)

        if self._active:
            try:
                self._track_import(result, name)
            except Exception:
                pass

        return result

    def get_test_file_imports(self, test_file: Optional[str] = None) -> Set[str]:
        """
        Get all project modules imported by a test file.

        This is called AFTER coverage runs to check which imports from the
        test file weren't captured by coverage (because the module was already
        loaded before the test ran).

        Uses AST parsing to find import statements directly.

        Args:
            test_file: Relative path to the test file

        Returns:
            Set of relative paths to project modules imported by the test file
        """
        if not test_file:
            return set()

        # Use AST-based import detection (same as get_module_imports)
        return self.get_module_imports(test_file)

    def get_module_imports(self, module_path: str) -> Set[str]:
        """
        Get all project modules imported by a given module.

        This enables tracking transitive dependencies:
        - Test imports module_a
        - module_a imports module_b (e.g., a globals file)
        - Therefore, test depends on module_b

        Uses AST parsing to find import statements, which catches imports of
        primitive values (like global constants) that namespace inspection misses.

        Args:
            module_path: Relative path to the module (e.g., 'src/globals_consumer.py')

        Returns:
            Set of relative paths to project modules imported by this module
        """
        imports = set()

        # Read and parse the module's source code
        abs_path = os.path.join(self.rootdir, module_path)
        if not os.path.exists(abs_path):
            return imports

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            return imports

        # Extract module names from import statements
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)

        # Resolve each imported module to a file path
        for module_name in imported_modules:
            # Try to find the module file
            module_file = self._resolve_module_to_file(module_name)
            if module_file:
                relpath = self._is_in_project(module_file)
                if relpath and relpath != module_path:
                    imports.add(relpath)

        return imports

    def get_module_external_imports(self, module_path: str) -> Set[str]:
        """
        Get all external package names imported by a given module.

        This enables tracking external dependencies from module-level imports:
        - Test imports src/external_deps.py
        - external_deps.py imports 'requests', 'numpy' at module level
        - Therefore, test depends on 'requests' and 'numpy' packages

        Uses AST parsing to find import statements, which catches imports
        that happen at module load time (before test-specific tracking starts).

        Args:
            module_path: Relative path to the module (e.g., 'src/external_deps.py')

        Returns:
            Set of external package names (top-level, e.g., 'requests', 'numpy')
        """
        external_imports = set()

        # Read and parse the module's source code
        abs_path = os.path.join(self.rootdir, module_path)
        if not os.path.exists(abs_path):
            return external_imports

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            return external_imports

        # Extract module names from import statements
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)

        # Filter for external packages (not local, not stdlib)
        for module_name in imported_modules:
            # Get top-level package name
            pkg_name = self._get_package_name(module_name)

            # Skip private modules
            if pkg_name.startswith('_'):
                continue

            # Check if the top-level package is a local project package
            # This handles pip-installed projects being tested (e.g., matplotlib tests)
            if self._is_local_package(pkg_name):
                continue

            # Check if it's a local project module (file-based check)
            module_file = self._resolve_module_to_file(module_name)
            if module_file:
                relpath = self._is_in_project(module_file)
                if relpath:
                    # It's a local module, skip
                    continue

            # Check if it's stdlib
            if self._is_stdlib_module(module_name):
                continue

            # Check if the top-level package is installed (exists in sys.modules or can be found)
            if pkg_name in sys.modules or self._is_installed_package(pkg_name):
                external_imports.add(pkg_name)

        return external_imports

    @lru_cache(maxsize=256)
    def _is_installed_package(self, pkg_name: str) -> bool:
        """Check if a package is installed (can be imported)."""
        try:
            spec = importlib.util.find_spec(pkg_name)
            return spec is not None
        except (ModuleNotFoundError, ImportError, ValueError):
            return False

    def _resolve_module_to_file(self, module_name: str) -> Optional[str]:
        """Resolve a module name to its file path."""
        # First check sys.modules (already loaded)
        module = sys.modules.get(module_name)
        if module is not None:
            filepath = getattr(module, '__file__', None)
            if filepath:
                return os.path.abspath(filepath)

        # Try to find the module spec
        try:
            spec = importlib.util.find_spec(module_name)
            if spec and spec.origin and spec.origin != 'built-in':
                return os.path.abspath(spec.origin)
        except (ModuleNotFoundError, ImportError, ValueError):
            pass

        return None

    def _install_hooks(self) -> None:
        """Install tracking hooks for open() and import."""
        if self._original_open is None:
            self._original_open = builtins.open
            builtins.open = self._tracking_open

        if self._original_import is None:
            self._original_import = builtins.__import__
            builtins.__import__ = self._tracking_import

        if self._original_import_module is None:
            self._original_import_module = importlib.import_module
            importlib.import_module = self._tracking_import_module

    # =========================================================================
    # Collection-time tracking methods
    # These capture file reads that happen during test collection (import time)
    # =========================================================================

    def start_collection_tracking(self) -> None:
        """
        Start tracking file reads during test collection phase.

        This should be called early in pytest_configure, before test collection
        begins. File reads that happen during module imports will be captured
        and associated with the test file being collected.
        """
        with self._lock:
            self._collection_mode = True
            self._collection_file_deps = {}
            self._install_hooks()

    def set_collection_context(self, test_file: str) -> None:
        """
        Set the current test file being collected.

        Called when pytest starts collecting a test module. Any file reads
        that happen while collecting this module (including imports) will
        be associated with this test file.

        Args:
            test_file: Relative path to the test file (e.g., "tests/test_a.py")
        """
        with self._lock:
            self._collection_context = test_file
            if test_file not in self._collection_file_deps:
                self._collection_file_deps[test_file] = set()

    def clear_collection_context(self) -> None:
        """Clear the collection context after a test file is done being collected."""
        with self._lock:
            self._collection_context = None

    def stop_collection_tracking(self) -> Dict[str, Set[TrackedFile]]:
        """
        Stop collection tracking and return collected file dependencies.

        Returns:
            Dict mapping test file paths to sets of TrackedFile dependencies
            that were read during that test file's collection/import.
        """
        with self._lock:
            self._collection_mode = False
            self._collection_context = None
            result = self._collection_file_deps.copy()
            self._collection_file_deps = {}

            # Only restore hooks if no test-time tracking is active
            if not self._active and not self._tracked_files:
                self._restore_hooks()

            return result

    def get_collection_file_deps(self) -> Dict[str, Set[TrackedFile]]:
        """Get the current collection file dependencies without stopping tracking."""
        with self._lock:
            return {k: v.copy() for k, v in self._collection_file_deps.items()}

    # =========================================================================
    # Test execution tracking methods
    # =========================================================================

    def start(self, context: str, test_file: Optional[str] = None) -> None:
        """
        Start tracking dependencies for a test context.

        Args:
            context: The test nodeid or identifier
            test_file: Optional relative path to the test file
        """
        with self._lock:
            self._current_context = context
            self._tracked_files[context] = set()
            self._tracked_local_imports[context] = set()
            self._tracked_external_imports[context] = set()
            # Store test file for later use in get_test_file_imports
            if test_file:
                self._test_files = getattr(self, '_test_files', {})
                self._test_files[context] = test_file

            if not self._active:
                self._install_hooks()
                self._active = True

    def stop(self) -> Tuple[Set[TrackedFile], Set[str], Set[str], Optional[str]]:
        """
        Stop tracking and return the tracked dependencies.

        Returns:
            Tuple of (tracked_files, local_imports, external_imports, test_file)
        """
        with self._lock:
            context = self._current_context

            files = self._tracked_files.pop(context, set()) if context else set()
            local_imports = self._tracked_local_imports.pop(context, set()) if context else set()
            external_imports = self._tracked_external_imports.pop(context, set()) if context else set()
            test_file = self._test_files.pop(context, None) if hasattr(self, '_test_files') and context else None

            # If no more contexts being tracked, restore original functions
            if not self._tracked_files:
                self._restore_hooks()

            self._current_context = None
            return files, local_imports, external_imports, test_file

    def switch_context(self, new_context: str) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        """
        Switch to a new test context, returning dependencies from the previous context.
        """
        with self._lock:
            old_context = self._current_context

            files = self._tracked_files.pop(old_context, set()) if old_context else set()
            local_imports = self._tracked_local_imports.pop(old_context, set()) if old_context else set()
            external_imports = self._tracked_external_imports.pop(old_context, set()) if old_context else set()

            self._current_context = new_context
            self._tracked_files[new_context] = set()
            self._tracked_local_imports[new_context] = set()
            self._tracked_external_imports[new_context] = set()

            return files, local_imports, external_imports

    def get_current(self) -> Tuple[Set[TrackedFile], Set[str], Set[str]]:
        """Get current tracked dependencies without stopping."""
        with self._lock:
            context = self._current_context
            if not context:
                return set(), set(), set()
            return (
                self._tracked_files.get(context, set()).copy(),
                self._tracked_local_imports.get(context, set()).copy(),
                self._tracked_external_imports.get(context, set()).copy(),
            )

    def _restore_hooks(self) -> None:
        """Restore all original functions."""
        if self._original_open:
            builtins.open = self._original_open
            self._original_open = None
        if self._original_import:
            builtins.__import__ = self._original_import
            self._original_import = None
        if self._original_import_module:
            importlib.import_module = self._original_import_module
            self._original_import_module = None
        self._active = False

    def close(self) -> None:
        """Fully close the tracker and restore all hooks."""
        with self._lock:
            self._restore_hooks()
            self._current_context = None
            self._tracked_files.clear()
            self._tracked_local_imports.clear()
            self._tracked_external_imports.clear()
            self._path_cache.clear()


# Special checksum marker for file dependencies
FILE_DEPENDENCY_MARKER = "__file_dep__"

def file_sha_to_checksum(sha: str) -> int:
    """Convert a file SHA to a checksum integer for fingerprinting."""
    # Use first 8 hex chars (32 bits) of SHA
    return int(sha[:8], 16) & 0x7FFFFFFF  # Keep it positive
