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
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

import pytest

from ezmon.dependency_tracker import DependencyTracker


class ImportTracePlugin:
    def __init__(self, rootdir: str) -> None:
        self.rootdir = rootdir
        self.tracker = DependencyTracker(rootdir)
        self._global_stopped = False
        self.collection_imports: Dict[str, Set[str]] = {}
        self.collection_file_deps: Dict[str, Set[str]] = {}
        self.test_imports: Dict[str, Set[str]] = defaultdict(set)

    def pytest_configure(self, config):
        # Ensure only our plugin runs to avoid interference.
        os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        self.tracker.start_collection_tracking()

    def pytest_collectstart(self, collector):
        if not self._global_stopped:
            self.tracker.stop_global_tracking()
            self._global_stopped = True

        try:
            path_str = str(collector.path)
        except Exception:
            return

        if not path_str.endswith(".py"):
            return
        base = os.path.basename(path_str)
        if base == "conftest.py":
            return
        try:
            rel = os.path.relpath(path_str, self.rootdir).replace(os.sep, "/")
            self.tracker.start_file_tracking(rel)
        except (AttributeError, ValueError):
            return

    def pytest_collection_modifyitems(self, session, config, items):
        file_deps, local_imports, _external_imports = self.tracker.stop_collection_tracking()
        # Keep both Python imports and non-Python file dependencies so output
        # matches database dependency sets.
        self.collection_file_deps = {
            test_file: {tracked.path for tracked in tracked_files}
            for test_file, tracked_files in file_deps.items()
        }
        self.collection_imports = {k: set(v) for k, v in local_imports.items()}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        nodeid = item.nodeid
        test_file = nodeid.split("::")[0] if "::" in nodeid else nodeid
        self.tracker.start_test(nodeid, test_file=test_file)
        result = yield
        files, local_imports, _external_imports, test_file = self.tracker.end_test()
        # Combine execution + collection dependencies for the test file.
        merged = set(local_imports or set())
        merged.update({tracked.path for tracked in (files or set())})
        if test_file:
            merged.update(self.collection_imports.get(test_file, set()))
            merged.update(self.collection_file_deps.get(test_file, set()))
        if test_file:
            merged.add(test_file)
        self.test_imports[nodeid].update(merged)


def _as_json(data: Dict[str, Set[str]]) -> str:
    payload = {k: sorted(v) for k, v in data.items()}
    return json.dumps(payload, indent=2, sort_keys=True)


def _as_text(data: Dict[str, Set[str]]) -> str:
    lines: List[str] = []
    for nodeid in sorted(data.keys()):
        lines.append(nodeid)
        for path in sorted(data[nodeid]):
            lines.append(f"  {path}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace local Python imports for tests.")
    parser.add_argument("tests", nargs="+", help="Test nodeids or paths to run.")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--output", help="Write output to file instead of stdout.")
    parser.add_argument("--no-stdout", action="store_true", help="Suppress stdout output.")
    args = parser.parse_args()

    rootdir = str(Path.cwd())
    plugin = ImportTracePlugin(rootdir)
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    code = pytest.main(["-q", *args.tests], plugins=[plugin])
    output = _as_json(plugin.test_imports) if args.format == "json" else _as_text(plugin.test_imports)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    if not args.no_stdout:
        print(output)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
