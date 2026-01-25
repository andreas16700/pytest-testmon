import hashlib
import os
import random
import sys
import sysconfig
import textwrap
from functools import lru_cache
from collections import defaultdict
from xmlrpc.client import Fault, ProtocolError
from socket import gaierror

from typing import TypeVar

try:
    from pytest_cov.plugin import CovPlugin
except ImportError:
    pass

import pytest
from coverage import Coverage, CoverageData

from ezmon import db
from ezmon.net_db import create_net_db_from_env, NetDB
from ezmon import TESTMON_VERSION as TM_CLIENT_VERSION
from ezmon.common import (
    get_logger,
    get_system_packages,
    drop_patch_version,
    git_current_head,
)

from ezmon.process_code import (
    match_fingerprint,
    create_fingerprint,
    methods_to_checksums,
    get_source_sha,
    Module,
)

from ezmon.common import DepsNOutcomes, TestExecutions
from ezmon.dependency_tracker import DependencyTracker, file_sha_to_checksum

T = TypeVar("T")

TEST_BATCH_SIZE = 250

CHECKUMS_ARRAY_TYPE = "I"
DB_FILENAME = ".testmondata"

logger = get_logger(__name__)


def create_database(rootdir, readonly=False):
    """
    Factory function to create the appropriate database backend.

    If TESTMON_NET_ENABLED=true and required env vars are set,
    returns a NetDB instance for remote server communication.
    Otherwise, returns a local SQLite DB instance.

    Args:
        rootdir: Project root directory
        readonly: Whether to open in readonly mode

    Returns:
        Either NetDB or db.DB instance
    """
    net_db = create_net_db_from_env()
    if net_db is not None:
        logger.info("Using NetDB for remote server communication")
        return net_db

    # Fall back to local SQLite database
    import os
    datafile = os.path.join(rootdir, get_data_file_path())
    return db.DB(datafile, readonly=readonly)


def get_data_file_path():
    return os.environ.get("TESTMON_DATAFILE", DB_FILENAME)


def home_file(test_execution_name):
    return test_execution_name.split("::", 1)[0]


def is_python_file(file_path):
    return file_path[-3:] == ".py"


class TestmonException(Exception):
    pass


class SourceTree:
    """
    - reads files from file_system and caches them in memory
      (if the file was once read, let's use the on memory
      copy for further operations. Also caches the mtime.)
    - store rootdir and convert between relative and absolute paths
    - implement the check if the file is changed based on mtime, fsha.
    - implement the ckeck if the node is stable based on fingerprint
    - mockability (unit tests without filesystem, check if the file is changed
      based on mtime, fsha)
    """

    def __init__(self, rootdir, packages=None):
        self.rootdir = rootdir
        self.packages = packages
        self.cache: dict = {}

    def get_file(self, filename):
        if filename not in self.cache:
            code, fsha = get_source_sha(directory=self.rootdir, filename=filename)
            if fsha:
                try:
                    fs_mtime = os.path.getmtime(os.path.join(self.rootdir, filename))
                    self.cache[filename] = Module(
                        source_code=code,
                        mtime=fs_mtime,
                        ext=filename.rsplit(".", 1)[1],
                        fs_fsha=fsha,
                        filename=filename,
                        rootdir=self.rootdir,
                    )
                except FileNotFoundError:
                    self.cache[filename] = None
            else:
                self.cache[filename] = None
        return self.cache[filename]


def check_mtime(file_system: SourceTree, record):
    absfilename = os.path.join(file_system.rootdir, record["filename"])

    cache_module = file_system.cache.get(record["filename"], None)
    try:
        fs_mtime = cache_module.mtime if cache_module else os.path.getmtime(absfilename)
    except OSError:
        return False
    return record["mtime"] == fs_mtime


def check_fsha(file_system, record):
    cache_module = file_system.get_file(record["filename"])
    fs_fsha = cache_module.fs_fsha if cache_module else None

    return record["fsha"] == fs_fsha


def check_fingerprint(
    disk, record: db.ChangedFileData
):  # filename name method_fshas id failed
    file = record[0]
    fingerprint = record[2]

    module = disk.get_file(file)
    return module and match_fingerprint(module, fingerprint)


def split_filter(disk, function, records: [T]) -> ([T], [T]):
    first = []
    second = []
    for record in records:
        if function(disk, record):
            first.append(record)
        else:
            second.append(record)
    return first, second


@lru_cache(maxsize=1000)
def should_include(cov, filename):
    return cov._should_trace(str(filename), None).trace


def collect_mhashes(source_tree, new_changed_file_data):
    files_mhashes = {}
    for filename in new_changed_file_data:
        module = source_tree.get_file(filename)
        files_mhashes[filename] = module.method_checksums if module else None
    return files_mhashes


class TestmonData:  # pylint: disable=too-many-instance-attributes
    __test__ = False

    def __init__(  # pylint: disable=too-many-arguments
        self,
        rootdir,
        database=None,
        environment=None,
        system_packages=None,
        python_version=None,
        readonly=False,
    ):
        self.rootdir = rootdir
        self.environment = environment if environment else "default"
        self.source_tree = SourceTree(rootdir=self.rootdir)
        if system_packages is None:
            # Pass rootdir to auto-detect and exclude local packages
            system_packages = get_system_packages(rootdir=self.rootdir)
        system_packages = drop_patch_version(system_packages)
        if not python_version:
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        if database:
            self.db = database  # pylint: disable=invalid-name
        else:
            self.db = create_database(self.rootdir, readonly=readonly)  # pylint: disable=invalid-name

        try:
            result = self.db.initiate_execution(
                self.environment,
                system_packages,
                python_version,
                {
                    "tm_client_version": TM_CLIENT_VERSION,
                    "git_head_sha": git_current_head(),
                    "ci": os.environ.get("CI"),
                },
            )
        except (ConnectionRefusedError, Fault, ProtocolError, gaierror) as exc:
            logger.error(
                (
                    "%s error when communication with ezmon.net. (falling back to"
                    " .testmondata locally)"
                ),
                exc,
            )
            self.db = db.DB(
                os.path.join(self.rootdir, get_data_file_path())
            )  # pylint: disable=invalid-name
            result = self.db.initiate_execution(
                self.environment, system_packages, python_version, {}
            )
        self.exec_id = result["exec_id"]

        self.system_packages_change = result["packages_changed"]
        self.changed_packages = result.get("changed_packages", set())  # Granular package tracking
        self.files_of_interest = result["filenames"]

        self.all_files = {}
        self.unstable_test_names = None
        self.unstable_files = None
        self.stable_test_names = None
        self.stable_files = None
        self.failing_tests = None

    @property
    def new_db(self):
        return self.db.file_created

    def close_connection(self):
        if hasattr(self, 'db') and self.db:
            try:
                # Ensure all changes are committed to disk
                if hasattr(self.db, 'con') and self.db.con:
                    self.db.con.commit()
                    logger.info("✅ Database committed successfully")
            except Exception as e:
                logger.warning(f"Failed to commit database: {e}")

    @property
    def all_tests(self):
        return self.db.all_test_executions(self.exec_id)

    def get_tests_fingerprints(self, nodes_files_lines, reports) -> TestExecutions:
        test_executions_fingerprints = {}
        for context in nodes_files_lines:
            deps_n_outcomes: DepsNOutcomes = {"deps": [], "file_deps": [], "external_deps": []}

            for filename, covered in nodes_files_lines[context].items():
                # Handle special keys for file and external dependencies
                if filename.startswith("__file_deps__"):
                    # covered is actually a set of TrackedFile namedtuples
                    for tracked_file in covered:
                        deps_n_outcomes["file_deps"].append({
                            "filename": tracked_file.path,
                            "sha": tracked_file.sha,
                        })
                    continue
                elif filename.startswith("__external_deps__"):
                    # covered is a set of package names
                    deps_n_outcomes["external_deps"] = list(covered)
                    continue

                # Regular Python file dependency
                if os.path.exists(os.path.join(self.rootdir, filename)):
                    module = self.source_tree.get_file(filename)
                    if module:
                        fingerprint = create_fingerprint(module, covered)
                        deps_n_outcomes["deps"].append(
                            {
                                "filename": filename,
                                "mtime": module.mtime,
                                "fsha": module.fs_fsha,
                                "method_checksums": fingerprint,
                            }
                        )

            deps_n_outcomes.update(process_result(reports[context]))
            deps_n_outcomes["forced"] = context in self.stable_test_names and (
                context not in self.failing_tests
            )
            test_executions_fingerprints[context] = deps_n_outcomes
        return test_executions_fingerprints

    def sync_db_fs_tests(self, retain):
        collected = retain.union(set(self.stable_test_names))
        add = list(collected - set(self.all_tests))
        with self.db:
            test_execution_file_fps = {
                test_name: {
                    "deps": (
                        {
                            "filename": home_file(test_name),
                            "method_checksums": methods_to_checksums(["0match"]),
                            "mtime": None,
                            "fsha": None,
                        },
                    )
                }
                for test_name in add
                if is_python_file(home_file(test_name))
            }
            if test_execution_file_fps:
                self.save_test_execution_file_fps(test_execution_file_fps)

        to_delete = list(set(self.all_tests) - collected)
        with self.db as database:
            database.delete_test_executions(to_delete, self.exec_id)

    def determine_stable(self, assert_old=True):
        files_fshas = {}
        for filename in self.files_of_interest:
            module = self.source_tree.get_file(filename)
            if module:
                files_fshas[filename] = module.fs_fsha

        # Compare the fshas from disk to the fshas in the database and get files
        # where the fsha is not in database.
        new_changed_file_data = self.db.fetch_unknown_files(files_fshas, self.exec_id)

        # Debug: Log changed files
        if new_changed_file_data:
            from ezmon.common import logger
            logger.info(f"DEBUG: {len(new_changed_file_data)} files marked as changed")
            for f in sorted(new_changed_file_data)[:10]:  # Show first 10
                logger.info(f"DEBUG:   changed file: {f}")
            if len(new_changed_file_data) > 10:
                logger.info(f"DEBUG:   ... and {len(new_changed_file_data) - 10} more")

        # Get the mhashes for the files from above
        files_mhashes = collect_mhashes(self.source_tree, new_changed_file_data)

        # Get file dependency SHAs from disk
        file_deps_shas = self._compute_file_dependency_shas()

        # Pass changed_packages for granular external dependency tracking
        tests = self.db.determine_tests(
            self.exec_id,
            files_mhashes,
            file_deps_shas,
            changed_packages=self.changed_packages
        )
        affected_tests, self.failing_tests = tests["affected"], tests["failing"]

        if assert_old:
            self.assert_old_determin_stable(affected_tests)

        self.all_files = set(self.db.filenames(self.exec_id))
        self.unstable_test_names = set()
        self.unstable_files = set()

        for fingerprint_miss in affected_tests:
            self.unstable_test_names.add(fingerprint_miss)
            self.unstable_files.add(fingerprint_miss.split("::", 1)[0])

        self.stable_test_names = set(self.all_tests) - self.unstable_test_names
        self.stable_files = set(self.all_files) - self.unstable_files

    def _compute_file_dependency_shas(self):
        """Compute SHA hashes for all tracked file dependencies.

        Uses git blob hash from the committed version (HEAD) to match
        how dependencies were recorded. This ensures:
        1. Files not in git won't have a SHA (treated as changed)
        2. Files modified during workflow use committed state
        """
        import subprocess
        file_deps_shas = {}

        # Get list of file dependencies from database
        file_dep_filenames = self.db.get_file_dependency_filenames(self.exec_id)

        for filename in file_dep_filenames:
            try:
                # Use git ls-tree to get the committed blob hash
                # This matches how we record file dependencies
                result = subprocess.run(
                    ['git', 'ls-tree', 'HEAD', '--', filename],
                    cwd=self.rootdir,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Output format: "100644 blob <sha>\t<filename>"
                    parts = result.stdout.strip().split()
                    if len(parts) >= 3 and parts[1] == 'blob':
                        file_deps_shas[filename] = parts[2]
            except Exception:
                # File not in git or error - mark as changed (no SHA)
                pass

        return file_deps_shas

    def assert_old_determin_stable(self, new_fingerprint_misses):
        filenames_fingerprints = self.db.filenames_fingerprints(self.exec_id)

        _, fsha_misses = split_filter(
            self.source_tree, check_fsha, filenames_fingerprints
        )  # check 2. fsha vs filesystem

        # with the list of fingerprint_ids go to the database
        # and fetch all the data needed for next step

        changed_file_data = self.db.fetch_changed_file_data(
            [fsha_miss["fingerprint_id"] for fsha_miss in (fsha_misses)],
            self.exec_id,
        )

        # changed_file_data:
        # [(filename, test_name, method_fshas, fingerprint_id, failed )]
        # All the test_names in this list have a dependency on one
        # or more changed files. And we also have the fingerprints
        # of data content which they depend on. So it’s possible to
        # filter out the node_ids where the content of the changed file
        # still matches the fingerprint

        _, fingerprint_misses = split_filter(
            self.source_tree, check_fingerprint, changed_file_data
        )

        if {fingerprint_miss[1] for fingerprint_miss in fingerprint_misses} != set(
            new_fingerprint_misses
        ):
            print("ERROR: old and new fingerprint misses differ.. printing old algo")
            print(
                "\n".join(
                    sorted(
                        {fingerprint_miss[1] for fingerprint_miss in fingerprint_misses}
                    )
                )
            )
            print("printing new algo")
            print("\n".join(sorted(new_fingerprint_misses)))

    @property
    def avg_durations(self) -> dict:
        stats = defaultdict(lambda: {"test_execution": 0, "sum_duration": 0})

        for (
            test_execution_id,
            report,
        ) in self.all_tests.items():  # pylint: disable=no-member
            if report:
                class_name = get_test_execution_class_name(test_execution_id)
                module_name = get_test_execution_module_name(test_execution_id)

                stats[test_execution_id]["test_execution"] += 1
                stats[test_execution_id]["sum_duration"] = report.get("duration") or 0
                if class_name:
                    stats[class_name]["test_execution"] += 1
                    stats[class_name]["sum_duration"] += stats[test_execution_id][
                        "sum_duration"
                    ]
                stats[module_name]["test_execution"] += 1
                stats[module_name]["sum_duration"] += stats[test_execution_id][
                    "sum_duration"
                ]

        durations = defaultdict(lambda: 0)
        for key, stats in stats.items():
            durations[key] = stats["sum_duration"] / stats["test_execution"]

        return durations

    def save_test_execution_file_fps(self, test_executions_fingerprints , nodes_files_lines=None,):
        self.db.insert_test_file_fps(test_executions_fingerprints, self.exec_id)
        if nodes_files_lines:
            self.db.insert_coverage_lines(self.exec_id, nodes_files_lines)

    def fetch_saving_stats(self, select):
        return self.db.fetch_saving_stats(self.exec_id, select)


def get_new_mtimes(filesystem, hits):
    """hits: [(filename, _, _, fingerprint_id)]"""
    try:
        for hit in hits:
            module = filesystem.get_file(hit[0])
            if module:
                yield module.mtime, module.fs_fsha, hit[3]
    except KeyError:
        for hit in hits:
            module = filesystem.get_file(hit["filename"])
            if module:
                yield module.mtime, module.fs_fsha, hit["fingerprint_id"]


def get_test_execution_class_name(node_id):
    if len(node_id.split("::")) > 2:
        return node_id.split("::")[1]
    return None


def get_test_execution_module_name(node_id):
    return node_id.split("::")[0]


@lru_cache(1000)
def cached_relpath(path, basepath):
    return os.path.relpath(path, basepath).replace(os.sep, "/")


class TestmonCollector:
    coverage_stack: [Coverage] = []

    def __init__(
        self, rootdir, testmon_labels=None, cov_plugin=None
    ):  # TODO remove cov_plugin
        try:
            from ezmon.testmon_core import (  # pylint: disable=import-outside-toplevel
                Testmon as UberTestmon,
            )

            TestmonCollector.coverage_stack: [Coverage] = UberTestmon.coverage_stack
        except ImportError:
            pass
        if testmon_labels is None:
            testmon_labels = {"singleprocess"}
        self.rootdir = rootdir
        self.testmon_labels = testmon_labels
        self.cov: Coverage = None
        self.sub_cov_file = None
        self.cov_plugin: CovPlugin = cov_plugin
        self._test_name = None
        self._next_test_name = None
        self.batched_test_names = set()
        self.check_stack = []
        self.is_started = False
        self._interrupted_at = None

        # Dependency tracker for file and import tracking
        self.dependency_tracker = DependencyTracker(rootdir)
        # Store tracked dependencies per test for batch processing
        self._tracked_deps = {}  # {test_name: (files, local_imports, external_imports)}

    def start_cov(self):
        if not self.cov._started:
            TestmonCollector.coverage_stack.append(self.cov)
            self.cov.start()

    def stop_cov(self):
        if self.cov is None:
            return
        assert self.cov in TestmonCollector.coverage_stack
        if TestmonCollector.coverage_stack:
            while TestmonCollector.coverage_stack[-1] != self.cov:
                cov = TestmonCollector.coverage_stack.pop()
                cov.stop()
        if self.cov._started:
            self.cov.stop()
            TestmonCollector.coverage_stack.pop()
        if TestmonCollector.coverage_stack:
            TestmonCollector.coverage_stack[-1].start()

    def setup_coverage(self, subprocess=False):
        params = {
            "include": [os.path.join(self.rootdir, "*")],
            "omit": {
                os.path.join(value, "*")
                for key, value in sysconfig.get_paths().items()
                if key.endswith("lib")
            },
        }
        if self.cov_plugin and self.cov_plugin._started:
            cov = self.cov_plugin.cov_controller.cov
            TestmonCollector.coverage_stack.append(cov)
            if cov.config.source:
                params["include"] = list(
                    set(
                        [os.path.join(self.rootdir, "*")]
                        + [
                            os.path.join(os.path.abspath(source), "*")
                            for source in cov.config.source
                        ]
                    )
                )
            elif cov.config.run_include:
                params["include"] = list(
                    set(cov.config.run_include + params["include"])
                )
            # params["omit"] = cov.config.run_omit
            if cov.config.branch:
                raise TestmonException(
                    "ezmon doesn't support simultaneous run with pytest-cov when "
                    "branch coverage is on. Please disable branch coverage."
                )

        self.cov = Coverage(data_file=self.sub_cov_file, config_file=False, **params)
        self.cov._warn_no_data = False
        if TestmonCollector.coverage_stack:
            TestmonCollector.coverage_stack[-1].stop()

        self.start_cov()

    def start_testmon(self, test_name, next_test_name=None):
        self._next_test_name = next_test_name

        self.batched_test_names.add(test_name)
        if self.cov is None:
            self.setup_coverage()

        self.start_cov()
        self._test_name = test_name
        self.cov.switch_context(test_name)
        self.check_stack = TestmonCollector.coverage_stack.copy()

        # Start dependency tracking for this test
        # Extract test file from nodeid (format: "tests/test_foo.py::TestClass::test_method")
        test_file = test_name.split("::")[0] if "::" in test_name else None
        self.dependency_tracker.start(test_name, test_file=test_file)

    def discard_current(self):
        self._interrupted_at = self._test_name

    def get_batch_coverage_data(self):
        if self.check_stack != TestmonCollector.coverage_stack:
            pytest.exit(
                (
                    "Exiting pytest!!!! This test corrupts Testmon.coverage_stack:"
                    f" {self._test_name} {self.check_stack},"
                    f" {TestmonCollector.coverage_stack}"
                ),
                returncode=3,
            )

        # Stop dependency tracking and collect tracked dependencies
        files, local_imports, external_imports, test_file = self.dependency_tracker.stop()
        self._tracked_deps[self._test_name] = (files, local_imports, external_imports, test_file)

        nodes_files_lines = {}

        if self.cov and (
            len(self.batched_test_names) >= TEST_BATCH_SIZE
            or self._next_test_name is None
            or self._interrupted_at
        ):
            self.cov.stop()
            nodes_files_lines, lines_data = self.get_nodes_files_lines(
                dont_include=self._interrupted_at
            )

            # Merge tracked dependencies into nodes_files_lines
            nodes_files_lines = self._merge_tracked_deps(nodes_files_lines)

            if (
                len(TestmonCollector.coverage_stack) > 1
                and TestmonCollector.coverage_stack[-1] == self.cov
            ):
                filtered_lines_data = {
                    file: data
                    for file, data in lines_data.items()
                    if should_include(TestmonCollector.coverage_stack[-2], file)
                }
                TestmonCollector.coverage_stack[-2].get_data().add_lines(
                    filtered_lines_data
                )

            self.cov.erase()
            self.cov.start()
            self.batched_test_names = set()
            self._tracked_deps = {}  # Clear after processing batch
        return nodes_files_lines

    def _merge_tracked_deps(self, nodes_files_lines):
        """Merge tracked file and import dependencies into coverage data."""
        for test_name, (files, local_imports, external_imports, test_file) in self._tracked_deps.items():
            if test_name not in nodes_files_lines:
                nodes_files_lines[test_name] = {}

            # Make a mutable copy of external_imports to add discovered deps
            all_external_imports = set(external_imports) if external_imports else set()

            # Add local imports that were captured during runtime
            # These are imports that happened DURING the test execution
            for local_import in local_imports:
                if local_import not in nodes_files_lines[test_name]:
                    nodes_files_lines[test_name][local_import] = {0}

            # Track transitive module-level imports
            #
            # IMPORTANT: When Python imports a module, it executes that module's
            # top-level code, which includes any import statements in that module.
            # This means if test T imports module M1, and M1 imports M2, then
            # Python executes M2's module-level code as part of loading M1.
            #
            # Therefore, T depends on M2's module-level code even if T never
            # calls any functions from M2. Changes to M2's module-level code
            # (imports, constants, class definitions, etc.) could affect T.
            #
            # We track this by:
            # 1. Getting direct imports from the test file
            # 2. For each direct import, getting its transitive imports
            # 3. Adding module-level fingerprints (line 0) for any modules
            #    that coverage.py didn't track (because no code was called)
            # 4. Extracting external package dependencies from each module
            if test_file:
                # Get direct imports from the test file using AST parsing
                test_file_imports = self.dependency_tracker.get_test_file_imports(test_file)

                # Also get external imports from the test file itself
                test_file_external = self.dependency_tracker.get_module_external_imports(test_file)
                all_external_imports.update(test_file_external)

                for imported_module in test_file_imports:
                    # Add the directly imported module if not already tracked
                    if imported_module not in nodes_files_lines[test_name]:
                        nodes_files_lines[test_name][imported_module] = {0}

                    # Get transitive imports (modules that imported_module imports)
                    transitive_imports = self.dependency_tracker.get_module_imports(imported_module)
                    for transitive_import in transitive_imports:
                        # Add if NOT already tracked by coverage
                        if transitive_import not in nodes_files_lines[test_name]:
                            nodes_files_lines[test_name][transitive_import] = {0}

                    # Extract external package dependencies from this module
                    # This catches imports like 'import requests' at module level
                    module_external = self.dependency_tracker.get_module_external_imports(imported_module)
                    all_external_imports.update(module_external)

            # Store file dependencies in a special key
            # These will be handled specially in get_tests_fingerprints
            if files:
                file_deps_key = f"__file_deps__{test_name}"
                nodes_files_lines[test_name][file_deps_key] = files

            # Store external imports for granular package tracking
            if all_external_imports:
                ext_deps_key = f"__external_deps__{test_name}"
                nodes_files_lines[test_name][ext_deps_key] = all_external_imports

        return nodes_files_lines

    def get_nodes_files_lines(self, dont_include):
        cov_data: CoverageData = self.cov.get_data()
        files = cov_data.measured_files()
        nodes_files_lines = {}
        files_lines = {}
        for file in files:
            relfilename = cached_relpath(file, self.rootdir)

            contexts_by_lineno = cov_data.contexts_by_lineno(file)

            for lineno, contexts in contexts_by_lineno.items():
                for context in contexts:
                    nodes_files_lines.setdefault(context, {}).setdefault(
                        relfilename, set()
                    ).add(lineno)
                    files_lines.setdefault(file, set()).add(lineno)
        nodes_files_lines.pop(dont_include, None)
        self.batched_test_names.discard(dont_include)
        nodes_files_lines.pop("", None)
        for test_name in self.batched_test_names:
            if home_file(test_name) not in nodes_files_lines.setdefault(test_name, {}):
                nodes_files_lines[test_name].setdefault(home_file(test_name), {1})
        return nodes_files_lines, files_lines

    def close(self):
        # Close dependency tracker
        self.dependency_tracker.close()

        if self.cov is None:
            return
        assert self.cov in TestmonCollector.coverage_stack
        if TestmonCollector.coverage_stack:
            while TestmonCollector.coverage_stack[-1] != self.cov:
                cov = TestmonCollector.coverage_stack.pop()
                cov.stop()
        if self.cov._started:
            self.cov.stop()
            TestmonCollector.coverage_stack.pop()
        if self.sub_cov_file:
            os.remove(self.sub_cov_file + "_rc")
        os.environ.pop("COVERAGE_PROCESS_START", None)
        self.cov = None
        if TestmonCollector.coverage_stack:
            TestmonCollector.coverage_stack[-1].start()


def eval_environment(environment, **kwargs):
    if not environment:
        return ""

    def md5(string):
        return hashlib.md5(string.encode()).hexdigest()

    eval_globals = {"os": os, "sys": sys, "hashlib": hashlib, "md5": md5}
    eval_globals.update(kwargs)

    try:
        return str(eval(environment, eval_globals))
    except Exception as error:  # pylint: disable=broad-except
        return repr(error)


def process_result(result):
    failed = any(r.outcome == "failed" for r in result.values())
    duration = sum(value.duration for value in result.values())
    return {"failed": failed, "duration": duration}
