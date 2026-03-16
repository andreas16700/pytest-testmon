import hashlib
import os
import random
import sys
import textwrap
import subprocess
import time
import json
from pathlib import Path
from functools import lru_cache
from collections import defaultdict
from xmlrpc.client import Fault, ProtocolError
from socket import gaierror
from typing import Dict, List, Optional, Set

import pytest

from ezmon import db
from ezmon.net_db import create_net_db_from_env, NetDB
from ezmon import TESTMON_VERSION as TM_CLIENT_VERSION
from ezmon.common import (
    get_logger,
    get_system_packages,
    drop_patch_version,
    git_current_head,
    compute_package_diff,
    compute_changed_packages,
)

from ezmon.process_code import (
    create_fingerprint,
    get_source_sha,
    Module,
    bytes_to_string_and_fsha,
    compute_file_checksum,
)
from ezmon.file_cache import FileInfoCache
from ezmon.deterministic_coding import (
    git_tracked_files,
    build_package_code_map,
    invert_map,
    encode_packages,
)

from ezmon.common import DepsNOutcomes, TestExecutions
from ezmon.dependency_tracker import DependencyTracker, file_sha_to_checksum
from ezmon.bitmap_deps import TestDeps

TEST_BATCH_SIZE = 1

DB_FILENAME = ".testmondata"

logger = get_logger(__name__)


def _core_timing_log(event, **fields):
    timing_dir = os.environ.get("EZMON_XDIST_TIMING_LOG_DIR")
    if not timing_dir or not os.environ.get("EZMON_CORE_TIMING"):
        return
    payload = {
        "event": event,
        "actor": "controller",
        "ts": time.time(),
        "mono": time.monotonic(),
    }
    if fields:
        payload.update(fields)
    try:
        os.makedirs(timing_dir, exist_ok=True)
        path = os.path.join(timing_dir, "controller.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except Exception:
        return


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

    def __init__(self, rootdir, packages=None, file_cache: Optional[FileInfoCache] = None):
        self.rootdir = rootdir
        self.packages = packages
        self.cache: dict = {}
        self.file_cache = file_cache

    def get_file(self, filename):
        if os.environ.get("EZMON_CORE_TIMING_VERBOSE"):
            _core_timing_log("source_tree_get_file_start", filename=filename, cache_hit=filename in self.cache)
        if filename not in self.cache:
            if self.file_cache is not None:
                code, fsha, mtime = self.file_cache.get_source_and_fsha(filename)
            else:
                code, fsha = get_source_sha(directory=self.rootdir, filename=filename)
                mtime = None
            if fsha:
                try:
                    fs_mtime = mtime
                    if fs_mtime is None:
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
        if os.environ.get("EZMON_CORE_TIMING_VERBOSE"):
            _core_timing_log("source_tree_get_file_end", filename=filename, cache_hit=filename in self.cache)
        return self.cache[filename]


def collect_checksums(source_tree, new_changed_file_data):
    """Collect single file checksums for changed files."""
    if source_tree.file_cache is not None and new_changed_file_data:
        checksums = source_tree.file_cache.batch_get_checksums(
            new_changed_file_data,
            parallel=True,
        )
        return {path: checksum for path, checksum in checksums.items()}

    files_checksums = {}
    for filename in new_changed_file_data:
        module = source_tree.get_file(filename)
        files_checksums[filename] = module.checksum if module else None
    return files_checksums


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
        self.file_cache = FileInfoCache(self.rootdir)
        self.source_tree = SourceTree(rootdir=self.rootdir, file_cache=self.file_cache)
        if system_packages is None:
            system_packages = get_system_packages(rootdir=self.rootdir)
        system_packages = drop_patch_version(system_packages)
        if not python_version:
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        if database:
            self.db = database
        else:
            self.db = create_database(self.rootdir, readonly=readonly)

        self.commit_id = git_current_head(rootdir)
        self.current_packages = system_packages
        self.previous_packages = ""
        self.changed_packages = set()
        self.system_packages_change = False

        # NetDB path: use old initiate_execution interface
        if isinstance(self.db, NetDB):
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
                    "%s error when communication with ezmon.net. (falling back to .testmondata locally)",
                    exc,
                )
                self.db = db.DB(os.path.join(self.rootdir, get_data_file_path()))
                # Fall through to local DB path below

        # Local DB path: use new runs-based interface
        if not isinstance(self.db, NetDB):
            prev = self.db.get_previous_run_info()
            self.previous_commit_id = None
            if prev:
                self.previous_commit_id = prev.get("commit_id")
                self.previous_packages = prev.get("packages", "") or ""
                prev_python = prev.get("python_version", "") or ""
                if prev_python != python_version:
                    self.changed_packages = {"__python_version_changed__"}
                elif self.previous_packages != system_packages:
                    self.changed_packages = compute_changed_packages(
                        self.previous_packages, system_packages
                    )
            self.system_packages_change = bool(self.changed_packages)
            self.run_id = self.db.create_run(self.commit_id, system_packages, python_version)
            from ezmon.dep_store import DepStore
            self.dep_store = DepStore(self.db)
            self.files_of_interest = self.dep_store.all_filenames()
        else:
            # NetDB result handling
            self.dep_store = None
            self.run_id = result["exec_id"]
            self.system_packages_change = result["packages_changed"]
            self.changed_packages = result.get("changed_packages", set())
            self.previous_packages = result.get("previous_packages", "")
            self.current_packages = result.get("current_packages", "")
            self.files_of_interest = result["filenames"]

        self.expected_files_list = []
        self.expected_packages_list = []
        self.file_code_map = {}
        self.file_code_map_rev = {}
        self.package_code_map = {}
        self.package_code_map_rev = {}

        self.all_files = {}
        self.unstable_test_names = None
        self.unstable_files = None
        self.stable_test_names = None
        self.stable_files = None
        self.failing_tests = None

    @classmethod
    def for_worker(
        cls,
        rootdir,
        run_id,
        unstable_test_names,
        files_of_interest,
        changed_packages,
        explicitly_nocollect_files=None,
        min_collected_files=None,
        expected_imports=None,
        expected_reads=None,
        expected_packages=None,
        expected_files_list=None,
        expected_packages_list=None,
    ):
        """Create a TestmonData instance for xdist workers using pre-computed controller data.

        Workers receive stability data from the controller via workerinput to avoid
        race conditions where workers independently compute different stable_test_names
        by reading the database at different times.
        """
        instance = object.__new__(cls)
        instance.rootdir = rootdir
        instance.environment = "default"
        instance.file_cache = FileInfoCache(rootdir)
        instance.source_tree = SourceTree(rootdir=rootdir, file_cache=instance.file_cache)

        # Workers do not access the database; controller provides all data.
        instance.db = None
        instance.dep_store = None

        # Use pre-computed data from controller
        instance.run_id = run_id
        instance.commit_id = git_current_head(rootdir)
        instance.files_of_interest = list(files_of_interest) if files_of_interest else []
        instance.changed_packages = set(changed_packages) if changed_packages else set()
        instance.system_packages_change = bool(changed_packages)

        # Convert unstable_test_names from list to set (xdist serializes sets as lists)
        # None means "run all" (fresh DB); empty set means "run none affected"
        instance.unstable_test_names = set(unstable_test_names) if unstable_test_names is not None else None
        instance.stable_test_names = set()  # Workers don't need the full stable set
        instance.stable_files = set()
        instance.unstable_files = set()
        instance.explicitly_nocollect_files = set(explicitly_nocollect_files or [])
        instance.min_collected_files = set(min_collected_files or [])
        instance.expected_imports = set(expected_imports or [])
        instance.expected_reads = set(expected_reads or [])
        instance.expected_packages = set(expected_packages or [])
        instance.expected_files_list = list(expected_files_list or [])
        instance.expected_packages_list = list(expected_packages_list or [])
        instance.file_code_map = {}
        instance.file_code_map_rev = {}
        instance.package_code_map = {}
        instance.package_code_map_rev = {}
        instance.all_files = {}
        instance.failing_tests = []

        instance._init_deterministic_coding(
            tracked_files=git_tracked_files(rootdir),
            packages_str=drop_patch_version(get_system_packages(rootdir=rootdir)),
        )

        return instance

    def _init_deterministic_coding(
        self,
        tracked_files: Optional[List[str]] = None,
        packages_str: Optional[str] = None,
    ) -> None:
        if tracked_files is None:
            tracked_files = git_tracked_files(self.rootdir)
        if packages_str is None:
            packages_str = self.current_packages or drop_patch_version(
                get_system_packages(rootdir=self.rootdir)
            )
        package_names = self._package_names_from_string(packages_str)
        self.file_code_map = {}
        self.file_code_map_rev = {}
        self.package_code_map = build_package_code_map(package_names)
        self.package_code_map_rev = invert_map(self.package_code_map)
        self._fingerprint_cache = {}
        self._fp_stats = defaultdict(int)
        self._file_id_cache: Dict[str, int] = {}

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
        if self.dep_store is not None:
            return self.dep_store.all_test_executions()
        return self.db.all_test_executions()

    def get_tests_fingerprints(self, nodes_files_lines, outcomes) -> TestExecutions:
        """Create fingerprints for tests based on file dependencies.

        With the simplified model, dependency sets are explicit:
        - deps: local Python files (plain relative paths)
        - file_deps: non-Python files with (relpath, sha)
        - external_deps: external package names
        """
        test_executions_fingerprints = {}
        for context, deps_payload in nodes_files_lines.items():
            deps_n_outcomes: DepsNOutcomes = {"deps": [], "file_deps": [], "external_deps": []}

            deps_set = deps_payload.get("deps", set())
            file_deps_set = deps_payload.get("file_deps", set())
            external_deps_set = deps_payload.get("external_deps", set())
            for relpath in deps_set:
                if isinstance(relpath, int):
                    if 0 <= relpath < len(self.expected_files_list):
                        relpath = self.expected_files_list[relpath]
                    else:
                        continue
                record = self._get_cached_fingerprint(relpath)
                if record:
                    deps_n_outcomes["deps"].append(record)

            for relpath, sha in file_deps_set:
                if isinstance(relpath, int):
                    if 0 <= relpath < len(self.expected_files_list):
                        relpath = self.expected_files_list[relpath]
                    else:
                        continue
                if sha is None:
                    sha = self.file_cache.get_tracked_sha(relpath)
                    if not sha:
                        continue
                deps_n_outcomes["file_deps"].append({"filename": relpath, "sha": sha})

            resolved_packages = []
            for pkg in external_deps_set:
                if isinstance(pkg, int):
                    if 0 <= pkg < len(self.expected_packages_list):
                        pkg = self.expected_packages_list[pkg]
                    else:
                        continue
                elif isinstance(pkg, str) and self.package_code_map_rev:
                    pkg = self.package_code_map_rev.get(pkg, pkg)
                resolved_packages.append(pkg)
            deps_n_outcomes["external_deps"] = resolved_packages

            outcome = outcomes.get(context, {"failed": False, "duration": 0.0})
            deps_n_outcomes.update(outcome)
            deps_n_outcomes["forced"] = context in self.stable_test_names and (
                context not in self.failing_tests
            )
            test_executions_fingerprints[context] = deps_n_outcomes
        return test_executions_fingerprints

    def _compute_file_info(self, path: str):
        abs_path = os.path.join(self.rootdir, path)
        try:
            data = Path(abs_path).read_bytes()
        except OSError:
            return (None, None, None)
        source, fsha = bytes_to_string_and_fsha(data)
        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            mtime = None
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        checksum = compute_file_checksum(source, ext if ext else "txt")
        return (checksum, fsha, mtime)

    def _get_cached_fingerprint(self, relpath: str):
        """Get fingerprint for a relative path. Returns record with filename."""
        if not relpath:
            return None
        cached = self._fingerprint_cache.get(relpath)
        if cached:
            self._fp_stats["fingerprint_cache_hit"] += 1
            return cached
        self._fp_stats["fingerprint_cache_miss"] += 1

        # Get file info from file cache
        try:
            fingerprint, fsha, mtime = self.file_cache.get_file_info(relpath)
        except Exception:
            fingerprint = None

        if fingerprint is None:
            self._fp_stats["source_tree_miss"] += 1
            return None
        self._fp_stats["source_tree_hit"] += 1

        record = {
            "filename": relpath,
            "mtime": mtime,
            "fsha": fsha,
            "file_checksum": fingerprint,
        }
        self._fingerprint_cache[relpath] = record
        if os.environ.get("EZMON_CORE_TIMING"):
            _core_timing_log(
                "fingerprint_cache_stats",
                hits=self._fp_stats.get("fingerprint_cache_hit", 0),
                misses=self._fp_stats.get("fingerprint_cache_miss", 0),
                source_hits=self._fp_stats.get("source_tree_hit", 0),
                source_misses=self._fp_stats.get("source_tree_miss", 0),
            )
        return record

    def sync_db_fs_tests(self, retain):
        """Synchronize database tests with filesystem tests.

        Adds new tests found in collection and removes tests no longer collected.
        """
        collected = retain.union(set(self.stable_test_names))
        add = list(collected - set(self.all_tests))
        with self.db:
            test_executions_fingerprints = {
                test_name: {
                    "deps": [
                        {
                            "filename": home_file(test_name),
                            "file_checksum": 0,  # Placeholder checksum for new tests
                            "fsha": None,
                        },
                    ]
                }
                for test_name in add
                if is_python_file(home_file(test_name))
            }
            if test_executions_fingerprints:
                self.save_test_deps_bitmap(test_executions_fingerprints)

        to_delete = list(set(self.all_tests) - collected)
        with self.db as database:
            database.delete_test_executions(to_delete)

    def determine_stable(self):
        """Determine which tests are stable (unchanged) vs unstable (need to run).

        New flow:
        - Use git diff since last commit to find meaningful file changes
        - Use package diff to find affecting external package changes
        - Select tests that depend on changed files/packages + failing tests
        - Compute which test files to collect and which to ignore
        """
        self.expected_imports = set()
        self.expected_reads = set()
        self.expected_packages = set()
        self.expected_files_list = []
        self.expected_packages_list = []
        self.explicitly_nocollect_files = set()
        self.git_affected_files = set()
        self.min_collected_files = set()

        last_commit = getattr(self, "previous_commit_id", None) or self.db.get_latest_run_commit_id()
        has_existing_data = bool(last_commit) and not self.new_db and bool(self.all_tests)

        # Compute package deltas
        prev_packages = getattr(self, "previous_packages", "") or ""
        curr_packages = getattr(self, "current_packages", "") or ""
        pack_added, pack_removed, pack_changed = compute_package_diff(
            prev_packages, curr_packages
        )
        pack_affecting = pack_removed | pack_changed
        pack_expected = pack_added | pack_changed

        # If python version changed, force all tests
        if "__python_version_changed__" in (self.changed_packages or set()):
            pack_affecting = {"__python_version_changed__"}

        if not has_existing_data:
            # No prior data: track all files from HEAD, select all tests
            head_files = self._git_head_files()
            # Track all external packages for initial run
            self._init_deterministic_coding(
                tracked_files=list(head_files),
                packages_str=curr_packages,
            )
            self.expected_reads = set(head_files)
            self.expected_imports = set(p for p in head_files if p.endswith(".py"))
            self.expected_packages = set(
                encode_packages(
                    self._package_names_from_string(curr_packages),
                    self.package_code_map,
                )
            )
            self.expected_files_list = []
            self.expected_packages_list = []

            self.unstable_test_names = None
            self.stable_test_names = set()
            self.unstable_files = set()
            self.stable_files = set()
            self.failing_tests = []
            self.min_collected_files = None
            return

        git_new, git_mod, git_del = self._git_diff_files(last_commit)
        # For dependency tracking, we must include ALL git-tracked files at HEAD,
        # not just changed files, so we never miss actual imports/reads.
        head_files = set(self._git_head_files())
        self._init_deterministic_coding(
            tracked_files=list(head_files),
            packages_str=curr_packages,
        )
        self.expected_reads = set(head_files)
        self.expected_imports = set(p for p in head_files if p.endswith(".py"))
        # Track all current packages (selection still uses pack_affecting).
        self.expected_packages = set(self._package_names_from_string(curr_packages))
        self.expected_files_list = []
        self.expected_packages_list = []

        ds = self.dep_store
        tracked_files = set(ds.get_file_id_map().keys()) if ds else set(self.db.get_file_id_map().keys())
        candidate_files = (git_mod | git_del) & tracked_files
        files_for_cache = (git_new | git_mod) - git_del

        db_checksums = ds.get_file_checksums() if ds else self.db.get_file_checksums()
        head_shas = self.file_cache.batch_get_head_shas(candidate_files)

        git_affected = set()
        _update_checksum = ds.update_file_checksum if ds else self.db.update_file_checksum
        for path in sorted(candidate_files):
            is_deleted = path in git_del
            is_python = path.endswith(".py")

            if is_deleted:
                git_affected.add(path)
                continue

            if is_python:
                source = self._git_head_file_source(path)
                if source is None:
                    continue
                checksum = compute_file_checksum(source, "py")
                old_checksum = db_checksums.get(path)
                fsha = head_shas.get(path)
                # Always update checksum/fsha when computed
                _update_checksum(path, checksum, fsha=fsha)
                if old_checksum != checksum:
                    git_affected.add(path)
            else:
                # Non-Python tracked file: git_mod implies content changed
                fsha = head_shas.get(path)
                _update_checksum(path, db_checksums.get(path), fsha=fsha)
                git_affected.add(path)

        self.git_affected_files = git_affected
        changed_file_ids = ds.get_file_ids_for_paths(git_affected) if ds else self.db.get_file_ids_for_paths(git_affected)

        # Find affected tests via bitmap
        affected_tests = set(
            self.db.find_affected_tests_bitmap(changed_file_ids, pack_affecting)
        )
        self.failing_tests = set(
            ds.get_failing_tests() if ds else self.db.get_failing_tests_bitmap()
        )
        min_selected_tests = affected_tests | self.failing_tests

        self.unstable_test_names = set(min_selected_tests)
        self.stable_test_names = set(self.all_tests) - self.unstable_test_names

        # Collect only test files that contain min_selected_tests
        if ds:
            min_collected_files = ds.get_test_files_for_tests(min_selected_tests)
            known_test_files = ds.get_all_test_files()
        else:
            min_collected_files = self.db.get_test_files_for_tests(min_selected_tests)
            known_test_files = self.db.get_all_test_files()
        self.explicitly_nocollect_files = set(known_test_files) - set(min_collected_files)
        self.min_collected_files = set(min_collected_files)

        self.unstable_files = set(self.git_affected_files)
        self.stable_files = set(ds.all_filenames() if ds else self.db.filenames()) - self.unstable_files

        self.expected_packages = set(
            encode_packages(self.expected_packages, self.package_code_map)
        )

    def _git_diff_files(self, base_commit: str):
        """Return (added, modified, deleted) files between base_commit and HEAD."""
        cmd = ["git", "diff", "--name-status", "-z", f"{base_commit}..HEAD"]
        result = subprocess.run(
            cmd,
            cwd=self.rootdir,
            capture_output=True,
        )
        if result.returncode != 0:
            return set(), set(), set()
        data = result.stdout
        parts = data.split(b"\0")
        added, modified, deleted = set(), set(), set()
        idx = 0
        while idx < len(parts):
            status = parts[idx]
            if not status:
                idx += 1
                continue
            status_str = status.decode("utf-8", "replace")
            if status_str.startswith("R") or status_str.startswith("C"):
                if idx + 2 < len(parts):
                    old_path = parts[idx + 1].decode("utf-8", "replace")
                    new_path = parts[idx + 2].decode("utf-8", "replace")
                    deleted.add(old_path)
                    added.add(new_path)
                    idx += 3
                    continue
                idx += 1
                continue
            if idx + 1 >= len(parts):
                break
            path = parts[idx + 1].decode("utf-8", "replace")
            if status_str == "A":
                added.add(path)
            elif status_str == "M":
                modified.add(path)
            elif status_str == "D":
                deleted.add(path)
            idx += 2
        return added, modified, deleted

    def _git_head_files(self) -> Set[str]:
        """Return all files at HEAD (committed)."""
        cmd = ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"]
        result = subprocess.run(cmd, cwd=self.rootdir, capture_output=True)
        if result.returncode != 0:
            return set()
        return {p for p in result.stdout.decode("utf-8", "replace").split("\0") if p}

    def _git_head_file_source(self, path: str) -> Optional[str]:
        """Return file content from HEAD as text (ignores working tree)."""
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=self.rootdir,
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        source, _ = bytes_to_string_and_fsha(result.stdout)
        return source

    def _package_names_from_string(self, packages_str: str) -> Set[str]:
        if not packages_str:
            return set()
        names = set()
        for item in packages_str.split(", "):
            item = item.strip()
            if not item:
                continue
            parts = item.rsplit(" ", 1)
            names.add(parts[0])
        return names

    def _compute_file_dependency_shas(self):
        """Compute SHA hashes for all tracked file dependencies.

        Uses git blob hash from the committed version (HEAD) to match
        how dependencies were recorded. This ensures:
        1. Files not in git won't have a SHA (treated as changed)
        2. Files modified during workflow use committed state
        """
        file_deps_shas = {}

        # Get list of file dependencies from database
        file_dep_filenames = self.db.get_file_dependency_filenames()

        if not file_dep_filenames:
            return file_deps_shas

        tracked_shas = self.file_cache.batch_get_tracked_shas(file_dep_filenames)
        for filename, sha in tracked_shas.items():
            if sha:
                file_deps_shas[filename] = sha

        return file_deps_shas

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

    def save_test_deps_bitmap(self, test_executions_fingerprints: TestExecutions) -> None:
        """Save test dependencies using the new Roaring bitmap format.

        Uses DepStore when available (local DB) for O(1) lookups.
        Falls back to direct DB access for NetDB.

        Args:
            test_executions_fingerprints: Dict mapping test name to DepsNOutcomes
        """
        ds = self.dep_store

        with self.db.con:
            # Bulk-resolve test IDs
            tests_for_db = []
            for test_name, deps_n_outcomes in test_executions_fingerprints.items():
                test_file = test_name.split("::")[0] if "::" in test_name else test_name
                tests_for_db.append((
                    test_name, test_file,
                    deps_n_outcomes.get("duration"),
                    deps_n_outcomes.get("failed", False),
                ))

            if ds:
                test_id_map = ds.ensure_tests_batch(self.run_id, tests_for_db)
            else:
                test_id_map = self.db.get_or_create_test_ids_batch(self.run_id, tests_for_db)

            # Resolve file IDs and serialize deps
            pending = []  # list of (test_id, blob, packages_str)
            for test_name, deps_n_outcomes in test_executions_fingerprints.items():
                test_id = test_id_map.get(test_name)
                if test_id is None:
                    continue

                file_ids = set()
                external_packages = set()

                # Process Python file dependencies
                for dep in deps_n_outcomes.get("deps", []):
                    filename = dep["filename"]
                    checksum = dep.get("file_checksum")
                    fsha = dep.get("fsha")

                    if ds:
                        file_id = ds.get_file_id(
                            filename, checksum=checksum, fsha=fsha, file_type='python'
                        )
                    else:
                        file_id = self._file_id_cache.get(filename)
                        if file_id is None:
                            file_id = self.db.get_or_create_file_id(
                                filename, checksum=checksum, fsha=fsha, file_type='python'
                            )
                            if file_id is not None:
                                self._file_id_cache[filename] = file_id
                    if file_id is not None:
                        file_ids.add(file_id)

                # Process non-Python file dependencies
                for file_dep in deps_n_outcomes.get("file_deps", []):
                    filename = file_dep["filename"]
                    sha = file_dep.get("sha")
                    checksum = file_sha_to_checksum(sha) if sha else None

                    if ds:
                        file_id = ds.get_file_id(
                            filename, checksum=checksum, fsha=sha, file_type='data'
                        )
                    else:
                        file_id = self._file_id_cache.get(filename)
                        if file_id is None:
                            file_id = self.db.get_or_create_file_id(
                                filename, checksum=checksum, fsha=sha, file_type='data'
                            )
                            if file_id is not None:
                                self._file_id_cache[filename] = file_id
                    if file_id is not None:
                        file_ids.add(file_id)

                # Process external package dependencies
                for pkg_name in deps_n_outcomes.get("external_deps", []):
                    external_packages.add(pkg_name)

                # Serialize bitmap
                deps = TestDeps.from_file_ids(test_id, file_ids, external_packages)
                blob = deps.serialize()
                packages_str = deps.serialize_external_packages()
                pending.append((test_id, blob, packages_str))

            # Skip unchanged deps
            if ds:
                pending = [
                    (tid, blob, pkgs)
                    for tid, blob, pkgs in pending
                    if ds.get_existing_blob(tid) != blob
                ]
                ds.save_batch(pending)
            else:
                if pending and hasattr(self.db, "get_test_deps_batch"):
                    test_ids = [t[0] for t in pending]
                    existing = self.db.get_test_deps_batch(test_ids)
                    if existing:
                        pending = [
                            (tid, blob, pkgs)
                            for tid, blob, pkgs in pending
                            if existing.get(tid) != blob
                        ]
                if pending and hasattr(self.db, "save_test_deps_batch"):
                    self.db.save_test_deps_batch(pending)
                elif pending and hasattr(self.db, "save_test_deps"):
                    for test_id, blob, packages_str in pending:
                        deps_obj = TestDeps.deserialize(test_id, blob, packages_str)
                        self.db.save_test_deps(test_id, deps_obj)

    def _resolve_relpath(self, relpath):
        """Resolve an int-encoded or string relpath to a string path."""
        if isinstance(relpath, int):
            if 0 <= relpath < len(self.expected_files_list):
                return self.expected_files_list[relpath]
            return None
        return relpath or None

    def _resolve_file_dep(self, relpath_sha):
        """Resolve a file_deps entry to (relpath, sha)."""
        relpath = relpath_sha[0] if isinstance(relpath_sha, tuple) else relpath_sha
        sha = relpath_sha[1] if isinstance(relpath_sha, tuple) else None
        if isinstance(relpath, int):
            if 0 <= relpath < len(self.expected_files_list):
                return self.expected_files_list[relpath], sha
            return None, None
        return (relpath, sha) if relpath else (None, None)

    def _resolve_packages(self, external_deps):
        """Resolve a set of int-encoded or string package names."""
        result = set()
        for pkg in external_deps:
            if isinstance(pkg, int):
                if 0 <= pkg < len(self.expected_packages_list):
                    result.add(self.expected_packages_list[pkg])
            elif isinstance(pkg, str) and self.package_code_map_rev:
                result.add(self.package_code_map_rev.get(pkg, pkg))
            else:
                result.add(pkg)
        return result

    def save_test_deps_raw(self, nodes_files_lines, outcomes):
        """Save test deps directly from raw worker data using DepStore.

        This is the fast path for xdist controller. All file ID lookups are
        O(1) dict hits from the pre-loaded DepStore cache. No checksum
        computation, no serial DB queries for file IDs.
        """
        import time as _t
        _t0 = _t.monotonic()

        def _tlog(event, **fields):
            _core_timing_log(event, **fields)
            timing_dir = os.environ.get("EZMON_XDIST_TIMING_LOG_DIR")
            if timing_dir:
                import json as _json
                payload = {"event": event, "actor": "controller",
                           "ts": _t.time(), "mono": _t.monotonic()}
                payload.update(fields)
                try:
                    path = os.path.join(timing_dir, "controller.jsonl")
                    with open(path, "a") as f:
                        f.write(_json.dumps(payload) + "\n")
                except Exception:
                    pass

        ds = self.dep_store

        with self.db.con:
            _tlog("save_raw_start", n_tests=len(nodes_files_lines))

            # Bulk-resolve test IDs
            tests_for_db = []
            for test_name in nodes_files_lines:
                outcome = outcomes.get(test_name, {"failed": False, "duration": 0.0})
                test_file = test_name.split("::")[0] if "::" in test_name else test_name
                tests_for_db.append((test_name, test_file, outcome.get("duration"), outcome.get("failed", False)))

            _tlog("save_raw_bulk_test_ids_start", n_tests=len(tests_for_db))
            test_id_map = ds.ensure_tests_batch(self.run_id, tests_for_db)
            _tlog("save_raw_bulk_test_ids_end", n_resolved=len(test_id_map))

            # Build pending list — all file ID lookups are cache hits
            pending = []
            n_file_ids = 0
            for test_name, deps_payload in nodes_files_lines.items():
                test_id = test_id_map.get(test_name)
                if test_id is None:
                    continue

                file_ids = set()

                # Python deps — O(1) lookup per file
                for relpath in deps_payload.get("deps", set()):
                    relpath = self._resolve_relpath(relpath)
                    if relpath:
                        file_ids.add(ds.get_file_id(relpath))

                # Non-Python file deps
                for relpath_sha in deps_payload.get("file_deps", set()):
                    relpath, sha = self._resolve_file_dep(relpath_sha)
                    if relpath:
                        file_ids.add(ds.get_file_id(relpath, fsha=sha, file_type='data'))

                # External packages
                external_packages = self._resolve_packages(deps_payload.get("external_deps", set()))

                n_file_ids += len(file_ids)
                deps = TestDeps.from_file_ids(test_id, file_ids, external_packages)
                blob = deps.serialize()
                if blob != ds.get_existing_blob(test_id):
                    pending.append((test_id, blob, deps.serialize_external_packages()))

            _tlog("save_raw_batch_write_start", n_pending=len(pending), n_file_ids=n_file_ids)

            ds.save_batch(pending)

            _tlog("save_raw_end",
                  n_tests=len(nodes_files_lines),
                  n_file_ids=n_file_ids,
                  n_written=len(pending),
                  total_secs=round(_t.monotonic() - _t0, 3))

    def fetch_saving_stats(self, select):
        return self.db.fetch_saving_stats(select)


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
    """Collector for test dependencies.

    With the simplified model (no coverage.py), we rely solely on the
    DependencyTracker to identify file dependencies. Any change to a
    file that a test imports will trigger a re-run of that test.
    """

    def __init__(
        self,
        rootdir,
        testmon_labels=None,
        cov_plugin=None,
        expected_imports: Optional[Set[str]] = None,
        expected_reads: Optional[Set[str]] = None,
        expected_packages: Optional[Set[str]] = None,
        expected_files_list: Optional[List[str]] = None,
        expected_packages_list: Optional[List[str]] = None,
        package_index: Optional[Dict[str, str]] = None,
        dependency_tracker: Optional[DependencyTracker] = None,
    ):
        if testmon_labels is None:
            testmon_labels = {"singleprocess"}
        self.rootdir = rootdir
        self.testmon_labels = testmon_labels
        self._test_name = None
        self._next_test_name = None
        self.batched_test_names = set()
        self.is_started = False
        self._interrupted_at = None

        # Dependency tracker for file and import tracking
        self.dependency_tracker = dependency_tracker or DependencyTracker(rootdir)
        self.dependency_tracker.set_expected(
            expected_imports=expected_imports,
            expected_reads=expected_reads,
            expected_packages=expected_packages,
        )
        # Note: file_index removed - DependencyTracker uses singleton encoder directly
        self._expected_files_list = list(expected_files_list or [])
        if package_index is not None:
            expected_packages_list = list(expected_packages_list or [])
        elif expected_packages_list is not None:
            expected_packages_list = list(expected_packages_list)
            package_index = {pkg: idx for idx, pkg in enumerate(expected_packages_list)}
        elif expected_packages is not None:
            expected_packages_list = sorted(expected_packages or set())
            package_index = {pkg: idx for idx, pkg in enumerate(expected_packages_list)}
        else:
            expected_packages_list = []
            package_index = None
        self._expected_packages_list = expected_packages_list
        self.dependency_tracker.set_expected_indices(
            package_index=package_index,
        )
        # Store tracked dependencies per test for batch processing
        self._tracked_deps = {}  # {test_name: (files, local_imports, external_imports)}

    def start_testmon(self, test_name, next_test_name=None):
        """Start tracking dependencies for a test.

        With the no-coverage model, we rely solely on the DependencyTracker
        to identify which files the test imports/depends on.
        """
        self._next_test_name = next_test_name
        self.batched_test_names.add(test_name)
        self._test_name = test_name

        # Start dependency tracking for this test
        # Extract test file from nodeid (format: "tests/test_foo.py::TestClass::test_method")
        test_file = test_name.split("::")[0] if "::" in test_name else None
        self.dependency_tracker.start_test(test_name, test_file=test_file)

    def discard_current(self):
        self._interrupted_at = self._test_name

    def stop_testmon(self):
        """Stop tracking for the current test and return its deps payload."""
        files, local_imports, external_imports, test_file = self.dependency_tracker.end_test()
        deps_payload = {
            "deps": set(),
            "file_deps": set(),
            "external_deps": set(),
        }
        if local_imports:
            deps_payload["deps"].update(local_imports)
        if files:
            deps_payload["file_deps"].update({(tf.path, tf.sha) for tf in files})
        if external_imports:
            deps_payload["external_deps"].update(set(external_imports))
        return deps_payload

    def get_batch_coverage_data(self):
        """Get dependency data for the current batch of tests.

        With the no-coverage model, we rely solely on the DependencyTracker
        to identify file dependencies. Each file that a test imports
        becomes a dependency.
        """
        # Stop dependency tracking and collect tracked dependencies
        files, local_imports, external_imports, test_file = self.dependency_tracker.stop()
        self._tracked_deps[self._test_name] = (files, local_imports, external_imports, test_file)

        nodes_files_lines = {}

        if (
            len(self.batched_test_names) >= TEST_BATCH_SIZE
            or self._next_test_name is None
            or self._interrupted_at
        ):
            # Build nodes_files_lines from dependency tracker data
            nodes_files_lines = self._build_nodes_files_lines()

            # Merge tracked dependencies
            nodes_files_lines = self._merge_tracked_deps(nodes_files_lines)

            self.batched_test_names = set()
            self._tracked_deps = {}  # Clear after processing batch
        return nodes_files_lines

    def _build_nodes_files_lines(self):
        """Build nodes_files_lines from dependency tracker data."""
        nodes_files_lines = {}
        for test_name in self.batched_test_names:
            if test_name == self._interrupted_at:
                continue
            deps_payload = {
                "deps": set(),
                "file_deps": set(),
                "external_deps": set(),
            }

            if test_name in self._tracked_deps:
                _files, local_imports, _external_imports, test_file = self._tracked_deps[test_name]
                deps_payload["deps"].update(local_imports)
            nodes_files_lines[test_name] = deps_payload

        return nodes_files_lines

    def _merge_tracked_deps(self, nodes_files_lines):
        """Merge tracked file and import dependencies into coverage data.

        Note: Collection-time imports (module-level imports that happen during test
        file collection) are tracked separately by the dependency tracker and merged
        in pytest_ezmon.py via _merge_collection_deps(). This method handles:
        - Runtime imports (dynamic imports during test execution)
        - File reads during test execution
        """
        for test_name, (files, local_imports, external_imports, test_file) in self._tracked_deps.items():
            if test_name not in nodes_files_lines:
                nodes_files_lines[test_name] = {
                    "deps": set(),
                    "file_deps": set(),
                    "external_deps": set(),
                }

            if files:
                nodes_files_lines[test_name]["file_deps"].update({(tf.path, tf.sha) for tf in files})

            # Store external imports for granular package tracking
            if external_imports:
                nodes_files_lines[test_name]["external_deps"].update(set(external_imports))

        return nodes_files_lines

    def close(self):
        """Close the collector and clean up resources."""
        self.dependency_tracker.close()


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
