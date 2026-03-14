#!/usr/bin/env python3
"""
Trace which local Python files are actually imported during execution of tests.

Usage:
  python scripts/trace_test_imports.py tests/test_user_only.py::TestUserOnly::test_create_user
  python scripts/trace_test_imports.py tests/test_user_only.py tests/test_product_only.py --format json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Set

import pytest

from ezmon.dependency_tracker import DependencyTracker


class ImportTracePlugin:
    def __init__(self, rootdir: str) -> None:
        self.rootdir = rootdir
        self.tracker = DependencyTracker(rootdir)
        self._active_collection_file: str | None = None
        self.collection_imports: Dict[str, Set[str]] = {}
        self.collection_file_deps: Dict[str, Set[str]] = {}
        self.nodeid: str | None = None
        self.test_imports: Set[str] = set()

    def pytest_configure(self, config):
        # Ensure only our plugin runs to avoid interference.
        os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        self.tracker.start_collection_tracking()

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_file(self, file_path, parent):  # pylint: disable=unused-argument
        return None

    def pytest_collectstart(self, collector):
        self.tracker.mark_collection_started()
        try:
            path_str = str(getattr(collector, "path", ""))
        except Exception:
            return
        if not path_str.endswith(".py"):
            return
        base = os.path.basename(path_str)
        if base in {"conftest.py", "__init__.py"}:
            return
        try:
            rel = os.path.relpath(path_str, self.rootdir).replace(os.sep, "/")
            if self._active_collection_file == rel:
                return
            self.tracker.begin_test_file_collection(rel)
            self._active_collection_file = rel
        except Exception:
            return

    def pytest_collection_modifyitems(self, session, config, items):
        self._active_collection_file = None
        file_deps, local_imports, _external_imports = self.tracker.stop_collection_tracking()
        # Keep both Python imports and non-Python file dependencies so output
        # matches database dependency sets.
        self.collection_file_deps = {
            test_file: {tracked.path for tracked in tracked_files}
            for test_file, tracked_files in file_deps.items()
        }
        self.collection_imports = {k: set(v) for k, v in local_imports.items()}

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_pycollect_makeitem(self, collector, name, obj):  # pylint: disable=unused-argument
        yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        nodeid = item.nodeid
        self.nodeid = nodeid
        test_file = nodeid.split("::")[0] if "::" in nodeid else nodeid
        self.tracker.start_test(nodeid, test_file=test_file)
        result = yield
        files, local_imports, _external_imports, test_file = self.tracker.end_test()
        # Combine execution + collection dependencies for the test file.
        merged = set(local_imports or set())
        merged.update({tracked.path for tracked in (files or set())})
        global_files, global_local, _global_external = self.tracker.get_global_import_deps()
        merged.update(global_local)
        merged.update({tracked.path for tracked in global_files})
        if test_file:
            merged.update(self.collection_imports.get(test_file, set()))
            merged.update(self.collection_file_deps.get(test_file, set()))
            file_files, file_local, _file_external = self.tracker.get_file_import_deps(test_file)
            merged.update(file_local)
            merged.update({tracked.path for tracked in file_files})
        self.test_imports = merged


def _as_json(nodeid: str, deps: Set[str]) -> str:
    return json.dumps({nodeid: sorted(deps)}, indent=2, sort_keys=True)


def _as_text(nodeid: str, deps: Set[str]) -> str:
    lines: List[str] = [nodeid]
    for path in sorted(deps):
        lines.append(f"  {path}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace local Python imports for tests.")
    parser.add_argument("test", help="Single test nodeid or path to run.")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--output", help="Write output to file instead of stdout.")
    parser.add_argument("--no-stdout", action="store_true", help="Suppress stdout output.")
    args = parser.parse_args()

    rootdir = str(Path.cwd())
    plugin = ImportTracePlugin(rootdir)
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    code = pytest.main(["-q", args.test], plugins=[plugin])
    nodeid = plugin.nodeid or args.test
    output = _as_json(nodeid, plugin.test_imports) if args.format == "json" else _as_text(nodeid, plugin.test_imports)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    if not args.no_stdout:
        print(output)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
