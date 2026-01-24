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

        # Cache for path checks
        self._path_cache: Dict[str, Optional[str]] = {}

        # Directories to skip
        self._skip_dirs = frozenset([
            '.venv', 'venv', '.tox', 'site-packages', '.git',
            '__pycache__', '.pytest_cache', '.mypy_cache',
            'node_modules', '.eggs', '*.egg-info',
        ])

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

    def _compute_file_sha(self, filepath: str) -> Optional[str]:
        """Compute SHA1 hash of file contents."""
        try:
            with self._original_open(filepath, 'rb') as f:
                content = f.read()
            return hashlib.sha1(content).hexdigest()
        except (OSError, IOError):
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
        """Track a file read operation."""
        if not self._active or not self._current_context:
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

        # Compute file hash
        sha = self._compute_file_sha(filepath)
        if not sha:
            return

        with self._lock:
            if self._current_context in self._tracked_files:
                self._tracked_files[self._current_context].add(
                    TrackedFile(path=relpath, sha=sha)
                )

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
                if not self._is_stdlib_module(name):
                    pkg_name = self._get_package_name(name)
                    if context in self._tracked_external_imports:
                        self._tracked_external_imports[context].add(pkg_name)

    def _tracking_open(self, file, mode='r', *args, **kwargs):
        """Replacement for builtins.open that tracks file access."""
        # Call original open first
        result = self._original_open(file, mode, *args, **kwargs)

        if self._active and isinstance(file, str):
            try:
                self._track_file(file, mode)
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
                # Hook builtins.open
                self._original_open = builtins.open
                builtins.open = self._tracking_open

                # Hook builtins.__import__
                self._original_import = builtins.__import__
                builtins.__import__ = self._tracking_import

                # Hook importlib.import_module
                self._original_import_module = importlib.import_module
                importlib.import_module = self._tracking_import_module

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
