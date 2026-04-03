# -*- coding: utf-8 -*-
"""
Main module of ezmon pytest plugin.
"""
import time
import sqlite3
import xmlrpc.client
import os
import json
import shutil

from collections import defaultdict, deque
from datetime import date, timedelta

from pathlib import Path
import pytest

from ezmon.net_db import get_net_db_config, upload_db_to_server
from ezmon.server_sync import get_test_preferences
from _pytest.config import ExitCode, Config
from _pytest.terminal import TerminalReporter

from ezmon.configure import TmConf

from ezmon.testmon_core import (
    TestmonCollector,
    eval_environment,
    TestmonData,
    home_file,
    TestmonException,
    get_test_execution_class_name,
    get_test_execution_module_name,
    cached_relpath,
)
from ezmon.dependency_tracker import DependencyTracker
from ezmon import configure
from ezmon.common import get_logger, get_system_packages

SURVEY_NOTIFICATION_INTERVAL = timedelta(days=28)

logger = get_logger(__name__)


def pytest_addoption(parser):
    group = parser.getgroup(
        "automatically select tests affected by changes (pytest-ezmon)"
    )
    group.addoption(
        "--ezmon",
        action="store_true",
        dest="ezmon",
        help=(
            "Select tests affected by changes (based on previously collected data) "
            "and collect + write new data (.testmondata file). "
            "Either collection or selection might be deactivated "
            "(sometimes automatically). See below."
        ),
    )

    group.addoption(
        "--ezmon-noselect",
        action="store_true",
        dest="testmon_noselect",
        help=(
            "Reorder and prioritize the tests most likely to fail first, but don't deselect anything. "
            "Forced if you use -m, -k, -l, -lf, test_file.py::test_name"
        ),
    )

    group.addoption(
        "--ezmon-nocollect",
        action="store_true",
        dest="testmon_nocollect",
        help=(
            "Run ezmon but deactivate the collection and writing of ezmon data. "
            "Forced if you run under debugger or coverage."
        ),
    )

    group.addoption(
        "--ezmon-forceselect",
        action="store_true",
        dest="testmon_forceselect",
        help=(
            "Run ezmon and select only tests affected by changes "
            "and satisfying pytest selectors at the same time."
        ),
    )

    group.addoption(
        "--no-ezmon",
        action="store_true",
        dest="no-ezmon",
        help=(
            "Turn off (even if activated from config by default).\n"
            "Forced if neither read nor write is possible "
            "(debugger plus test selector)."
        ),
    )

    group.addoption(
        "--ezmon-env",
        action="store",
        type=str,
        dest="environment_expression",
        default="",
        help=(
            "This allows you to have separate coverage data within one"
            " .testmondata file, e.g. when using the same source"
            " code serving different endpoints or Django settings."
        ),
    )

    group.addoption(
        "--tmnet",
        action="store_true",
        dest="tmnet",
        help=(
            "This is used for internal beta. Please don't use. You can go to https://www.testmon.net/ to register."
        ),
    )

    group.addoption(
        "--ezmon-no-reorder",
        action="store_true",
        dest="ezmon_no_reorder",
        help=(
            "Disable duration-based test reordering. "
            "Tests will run in their original collection order. "
            "Useful when test suites have inter-test dependencies or state pollution issues."
        ),
    )

    group.addoption(
        "--ezmon-coverage-lines",
        action="store_true",
        dest="ezmon_coverage_lines",
        help=(
            "Store per-test, per-file coverage line data in the database. "
            "This is disabled by default as it significantly increases database size. "
            "Enable for debugging or detailed coverage analysis."
        ),
    )

    parser.addini("environment_expression", "environment expression", default="")
    parser.addini(
        "testmon_ignore_dependencies",
        "ignore dependencies",
        type="args",
        default=[],
    )
    parser.addini("tmnet_url", "URL of the ezmon.net api server.")
    parser.addini("tmnet_api_key", "ezmon api key")


def pytest_load_initial_conftests(early_config, parser, args):
    # Start dependency tracking before conftest/package imports.
    if "--ezmon" not in args:
        return
    rootpath = getattr(early_config, "rootpath", None)
    rootdir = str(rootpath) if rootpath else os.getcwd()
    tracker = DependencyTracker(rootdir)
    tracker.start_collection_tracking()
    early_config._ezmon_early_tracker = tracker


def testmon_options(config):
    result = []
    for label in [
        "ezmon",
        "no-ezmon",
        "environment_expression",
    ]:
        if config.getoption(label):
            result.append(label.replace("testmon_", ""))
    return result


_TIMING_BUFFER = defaultdict(list)


def _timing_log_for_actor(actor, event, **fields):
    timing_dir = os.environ.get("EZMON_XDIST_TIMING_LOG_DIR")
    if not timing_dir:
        return
    payload = {
        "event": event,
        "actor": actor,
        "ts": time.time(),
        "mono": time.monotonic(),
    }
    if fields:
        payload.update(fields)
    # For diagnosing hangs, allow immediate flush of timing events.
    if (
        os.environ.get("EZMON_XDIST_TIMING_FLUSH_ALL")
        or (actor == "controller" and os.environ.get("EZMON_XDIST_TIMING_FLUSH_CONTROLLER"))
    ):
        try:
            os.makedirs(timing_dir, exist_ok=True)
            path = os.path.join(timing_dir, f"{actor}.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except Exception:
            return
        return
    _TIMING_BUFFER[str(actor)].append(payload)


def _flush_timing_logs(timing_dir: str):
    if not timing_dir:
        return
    try:
        os.makedirs(timing_dir, exist_ok=True)
        # Ensure the timeline viewer is available alongside timing logs.
        src = os.path.join(os.path.dirname(__file__), "..", "scripts", "timing_timeline.html")
        src = os.path.abspath(src)
        dest = os.path.join(timing_dir, "timing_timeline.html")
        if os.path.exists(src):
            try:
                if (not os.path.exists(dest)) or (os.path.getmtime(src) > os.path.getmtime(dest)):
                    shutil.copyfile(src, dest)
            except Exception:
                pass
        for actor, events in _TIMING_BUFFER.items():
            path = os.path.join(timing_dir, f"{actor}.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                for payload in events:
                    handle.write(json.dumps(payload) + "\n")
    except Exception:
        return


def _timing_log(config, event, **fields):
    if config is None:
        return
    actor = getattr(config, "workerid", None)
    if not actor and hasattr(config, "workerinput"):
        try:
            actor = config.workerinput.get("workerid")  # xdist sets this in workerinput
        except Exception:
            actor = None
    if not actor:
        running_as = get_running_as(config)
        actor = "controller" if running_as == "controller" else "single"
    _timing_log_for_actor(actor, event, **fields)



def get_testmon_file(config: Config) -> Path:
    return Path(config.rootdir.strpath) / ".testmondata"


def init_testmon_data(config: Config):
    running_as = get_running_as(config)

    # Workers receive pre-computed stability data from the controller via workerinput
    # This prevents race conditions where workers independently compute different stable_test_names
    if running_as == "worker" and hasattr(config, "workerinput"):
        _timing_log(config, "worker_init_testmon_start")
        _timing_log(config, "worker_start")
        workerinput = config.workerinput
        _timing_log(config, "worker_received_start")
        payload_dir = os.environ.get("EZMON_WORKER_PAYLOAD_DIR")
        if payload_dir:
            worker_id = getattr(config, "workerid", "worker")
            worker_path = os.path.join(payload_dir, str(worker_id))
            os.makedirs(worker_path, exist_ok=True)
            def _jsonable(value):
                if isinstance(value, dict):
                    return {str(k): _jsonable(v) for k, v in value.items()}
                if isinstance(value, set):
                    return [_jsonable(v) for v in value]
                if isinstance(value, tuple):
                    return [_jsonable(v) for v in value]
                if isinstance(value, list):
                    return [_jsonable(v) for v in value]
                return value
            try:
                with open(os.path.join(worker_path, "received.json"), "w", encoding="utf-8") as f:
                    json.dump(_jsonable(workerinput), f)
            except Exception as exc:
                logger.warning(f"Failed to write workerinput for {worker_id}: {exc}")
        if "testmon_run_id" in workerinput:
            # Create TestmonData for worker using controller's pre-computed data
            # Workers receive unstable_test_names (tests to RUN) - much smaller than stable
            _timing_log(config, "worker_apply_input_start")
            testmon_data = TestmonData.for_worker(
                rootdir=config.rootdir.strpath,
                run_id=workerinput["testmon_run_id"],
                unstable_test_names=workerinput.get("testmon_unstable_test_names", set()),
                files_of_interest=workerinput.get("testmon_files_of_interest", []),
                changed_packages=workerinput.get("testmon_changed_packages", set()),
                explicitly_nocollect_files=workerinput.get("testmon_explicitly_nocollect_files", []),
                min_collected_files=workerinput.get("testmon_min_collected_files", []),
                expected_imports=workerinput.get("testmon_expected_imports", []),
                expected_reads=workerinput.get("testmon_expected_reads", []),
                expected_packages=workerinput.get("testmon_expected_packages", []),
                expected_files_list=workerinput.get("testmon_expected_files_list", []),
                expected_packages_list=workerinput.get("testmon_expected_packages_list", []),
            )
            config.testmon_data = testmon_data
            _timing_log(config, "worker_apply_input_end")
            _timing_log(config, "worker_received_end")
            _timing_log(config, "worker_init_testmon_end")
            return

    # Controller or single process: compute stability normally
    _timing_log(config, "controller_init_start")
    environment = config.getoption("environment_expression") or eval_environment(
        config.getini("environment_expression")
    )
    ignore_dependencies = config.getini("testmon_ignore_dependencies")

    # Pass rootdir to auto-detect and exclude local packages (the project being tested)
    system_packages = get_system_packages(
        ignore=ignore_dependencies,
        rootdir=config.rootdir.strpath
    )


    testmon_data = TestmonData(
        rootdir=config.rootdir.strpath,
        database=None,
        environment=environment,
        system_packages=system_packages,
        readonly=False,  # Controller/single always writes
    )
    testmon_data.determine_stable()

    config.testmon_data = testmon_data
    _timing_log(config, "controller_init_end")


def get_running_as(config):
    if hasattr(config, "workerinput"):
        return "worker"

    if getattr(config.option, "dist", "no") == "no":
        return "single"

    return "controller"


def register_plugins(config, should_select, should_collect, cov_plugin):
    if should_select or should_collect:
        config.pluginmanager.register(
            TestmonSelect(config, config.testmon_data, running_as=get_running_as(config)), "TestmonSelect"
        )

    if should_select or should_collect:
        config.pluginmanager.register(
            TestmonCollect(
                TestmonCollector(
                    config.rootdir.strpath,
                    testmon_labels=testmon_options(config),
                    cov_plugin=cov_plugin,
                    expected_imports=getattr(config.testmon_data, "expected_imports", None),
                    expected_reads=getattr(config.testmon_data, "expected_reads", None),
                    expected_packages=getattr(config.testmon_data, "expected_packages", None),
                    expected_files_list=getattr(config.testmon_data, "expected_files_list", None),
                    expected_packages_list=getattr(config.testmon_data, "expected_packages_list", None),
                    package_index=getattr(config.testmon_data, "package_code_map", None),
                    dependency_tracker=getattr(config, "_ezmon_early_tracker", None),
                ),
                config.testmon_data,
                running_as=get_running_as(config),
                config=config,
            ),
            "TestmonCollect",
        )
        if config.pluginmanager.hasplugin("xdist"):
            config.pluginmanager.register(TestmonXdistSync())


def pytest_configure(config):
    _timing_log(config, "worker_configure_start")
    # Initialize defaults
    config.always_run_files = []
    config.prioritized_files = []

    if get_net_db_config() is not None:
        prefs = get_test_preferences()
        config.always_run_files = list(prefs.get("always_run_tests", []))
        config.prioritized_files = list(prefs.get("prioritized_tests", []))

    coverage_stack = None
    try:
        from tmnet.testmon_core import (  # pylint: disable=import-outside-toplevel
            Testmon as UberTestmon,
        )

        coverage_stack = UberTestmon.coverage_stack
    except ImportError:
        pass

    cov_plugin = None
    cov_plugin = config.pluginmanager.get_plugin("_cov")

    _timing_log(config, "worker_header_collect_select_start")
    tm_conf = configure.header_collect_select(
        config, coverage_stack, cov_plugin=cov_plugin
    )
    _timing_log(
        config,
        "worker_header_collect_select_end",
        select=tm_conf.select,
        collect=tm_conf.collect,
    )
    config.testmon_config: TmConf = tm_conf
    if tm_conf.select or tm_conf.collect:
        try:
            init_testmon_data(config)
            _timing_log(config, "worker_register_plugins_start")
            register_plugins(config, tm_conf.select, tm_conf.collect, cov_plugin)
            _timing_log(config, "worker_register_plugins_end")
        except TestmonException as error:
            pytest.exit(str(error))

    _timing_log(config, "worker_configure_end")


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    """Cap xdist auto workers for very large test suites."""
    if not config.pluginmanager.hasplugin("xdist"):
        return None
    testmon_data = getattr(config, "testmon_data", None)
    if not testmon_data:
        return None
    try:
        total_tests = len(testmon_data.all_tests or [])
    except Exception:
        return None
    max_workers = int(os.environ.get("EZMON_XDIST_MAX_WORKERS", "8"))
    if total_tests > 50000:
        return min(os.cpu_count() or 4, max_workers)
    return os.cpu_count() or 4

def pytest_report_header(config):
    if get_running_as(config) == "worker":
        return ""
    tm_conf = config.testmon_config

    if tm_conf.collect or tm_conf.select:
        unstable_files = getattr(config.testmon_data, "unstable_files", set())
        stable_files = getattr(config.testmon_data, "stable_files", set())
        environment = config.testmon_data.environment

        tm_conf.message += changed_message(
            config,
            environment,
            config.testmon_data.system_packages_change,
            tm_conf.select,
            stable_files,
            unstable_files,
        )

        show_survey_notification = True
        last_notification_date = config.testmon_data.db.fetch_attribute(
            "last_survey_notification_date"
        )
        if last_notification_date:
            last_notification_date = date.fromisoformat(last_notification_date)
            if date.today() - last_notification_date < SURVEY_NOTIFICATION_INTERVAL:
                show_survey_notification = False
            else:
                config.testmon_data.db.write_attribute(
                    "last_survey_notification_date", date.today().isoformat()
                )
        else:
            config.testmon_data.db.write_attribute(
                "last_survey_notification_date", date.today().isoformat()
            )

        if show_survey_notification:
            tm_conf.message += (
                "\nWe'd like to hear from ezmon users! "
                "Please go to https://testmon.org/survey to leave feedback."
            )
    return tm_conf.message


def pytest_sessionstart(session):
    _timing_log(session.config, "worker_sessionstart_start")
    _timing_log(session.config, "worker_sessionstart_end")


def pytest_collectstart(collector):
    _timing_log(collector.session.config, "worker_collectstart", nodeid=getattr(collector, "nodeid", None))


def pytest_collectreport(report):
    try:
        count = len(report.result)
    except Exception:
        count = None
    cfg = getattr(report, "config", None)
    if cfg is None:
        cfg = getattr(report, "session", None).config if getattr(report, "session", None) else None
    _timing_log(cfg, "worker_collectreport", count=count, nodeid=getattr(report, "nodeid", None))


def changed_message(
    config,
    environment,
    packages_change,
    should_select,
    stable_files,
    unstable_files,
):
    message = ""
    if should_select:
        changed_files_msg = ", ".join(unstable_files)
        if changed_files_msg == "" or len(changed_files_msg) > 100:
            changed_files_msg = str(len(config.testmon_data.unstable_files))

        if config.testmon_data.new_db:
            message += "new DB, "
        else:
            # Check for granular package changes
            changed_packages = getattr(config.testmon_data, 'changed_packages', set())
            if changed_packages:
                if "__python_version_changed__" in changed_packages:
                    message += "Python version changed - all tests re-running, "
                else:
                    pkg_list = ", ".join(sorted(changed_packages)[:5])
                    if len(changed_packages) > 5:
                        pkg_list += f" (+{len(changed_packages) - 5} more)"
                    message += f"changed packages: {pkg_list}, "
            message += f"changed files: {changed_files_msg}, unchanged files: {len(stable_files)}, "
    if config.testmon_data.environment:
        message += f"environment: {environment}"
    return message

def pytest_unconfigure(config):
    running_as = get_running_as(config)
    if running_as in ("single", "controller"):
        _timing_log(config, "controller_unconfigure_start")
    # Close and commit database FIRST (flush all changes to disk)
    logger.info("pytest_unconfigure function!")
    if hasattr(config, "testmon_data"):
        try:
            if running_as in ("single", "controller"):
                if hasattr(config.testmon_data, "db"):
                    _timing_log(config, "controller_db_close_start")
                    config.testmon_data.db.close()
                    _timing_log(config, "controller_db_close_end")
                    logger.info("💾 Database committed and closed (WAL checkpointed)")
        except Exception as exc:
            logger.warning(f"Failed to close testmon database: {exc}")

    # Only upload from main process (not xdist workers)
    if running_as not in ("single", "controller"):
        return

    # Upload DB to server if NetDB mode is active
    if hasattr(config, "testmon_data") and hasattr(config.testmon_data, "db"):
        net_config = getattr(config.testmon_data.db, "_net_config", None)
        if net_config is not None:
            _timing_log(config, "controller_upload_start")
            testmon_file = get_testmon_file(config)
            if testmon_file.exists() and testmon_file.stat().st_size > 0:
                upload_db_to_server(
                    net_config["server_url"],
                    net_config["repo_id"],
                    net_config["job_id"],
                    net_config.get("auth_token"),
                    net_config.get("run_id"),
                    str(testmon_file),
                )
            _timing_log(config, "controller_upload_end")
    timing_dir = os.environ.get("EZMON_XDIST_TIMING_LOG_DIR")
    if timing_dir:
        _timing_log(config, "controller_flush_timing_start")
        _flush_timing_logs(timing_dir)
        _timing_log(config, "controller_flush_timing_end")
    if running_as in ("single", "controller"):
        _timing_log(config, "controller_unconfigure_end")


class TestmonCollect:
    """Collects test dependencies during test execution.

    With the no-coverage model, we rely solely on the DependencyTracker
    to identify file dependencies via import hooks.
    """

    def __init__(self, testmon, testmon_data, running_as="single", cov_plugin=None, config=None):
        self.testmon_data: TestmonData = testmon_data
        self.testmon: TestmonCollector = testmon
        self._running_as = running_as
        self._config = config

        self._outcomes = {}
        self.raw_test_names = []
        self._sessionstarttime = time.time()
        self._file_nodes = defaultdict(dict)  # {test_file: {test_name: deps}}
        self._file_tests = defaultdict(set)  # {test_file: set(test_name)}
        self._worker_aggregate_files = {}
        self._worker_batches = []
        self._worker_batch_size = 5
        self._file_index = {}
        self._package_index = {}
        # Aggregate hook timings (per worker/single)
        self._timing_totals = defaultdict(float)
        self._timing_counts = defaultdict(int)
        self._write_queue = deque()

        # Collection-time dependency tracking
        # Maps test files to dependencies captured during import
        self._collection_file_deps = {}  # {test_file: set of TrackedFile}
        self._collection_local_imports = {}  # {test_file: set of module paths}
        self._collection_external_imports = {}  # {test_file: set of package names}
        self._active_collection_file = None

        # Start collection-time tracking to capture import-time dependencies
        if self.testmon.dependency_tracker._scope == "idle":
            self.testmon.dependency_tracker.start_collection_tracking()

        # Worker-specific tuning.
        if running_as == "worker":
            if config is not None:
                worker_id = getattr(config, "workerid", "")
                if isinstance(worker_id, str) and worker_id.startswith("gw"):
                    try:
                        worker_num = int(worker_id[2:])
                        self._worker_batch_size = 3 + (worker_num % 5)
                    except ValueError:
                        pass
            # Workers should not compute git SHAs; controller will fill them
            self.testmon.dependency_tracker.set_compute_shas(False)
            if getattr(self.testmon_data, "package_code_map", None):
                self._package_index = dict(self.testmon_data.package_code_map)
            # Workers still execute collection hooks; global and per-file
            # baselines are captured directly by the dependency tracker.

    def _enqueue_sync(self, retain):
        if self._running_as != "controller":
            return
        self._write_queue.append(("sync", retain, None))

    def _drain_write_queue(self):
        if self._running_as != "controller":
            return
        if self._config is not None:
            _timing_log(self._config, "controller_drain_queue_start", size=len(self._write_queue))

        # Merge all queued items into batches to minimize DB transactions
        merged_deps = {}
        sync_retains = []
        while self._write_queue:
            kind, payload, _unused = self._write_queue.popleft()
            if kind == "deps":
                if payload:
                    merged_deps.update(payload)
            elif kind == "sync":
                sync_retains.append(payload)

        try:
            if merged_deps:
                if self._config is not None:
                    _timing_log(self._config, "controller_save_bitmap_start")
                self.testmon_data.save_test_deps_bitmap(merged_deps)
                if self._config is not None:
                    _timing_log(self._config, "controller_save_bitmap_end")
            for retain in sync_retains:
                self.testmon_data.sync_db_fs_tests(retain=set(retain))
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                # Re-enqueue everything and retry later
                if merged_deps:
                    self._write_queue.appendleft(("deps", merged_deps, None))
                for retain in sync_retains:
                    self._write_queue.appendleft(("sync", retain, None))
            else:
                raise

        if self._config is not None:
            _timing_log(self._config, "controller_drain_queue_end")

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_file(self, file_path, parent):  # pylint: disable=unused-argument
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_collectstart(self, collector):
        self.testmon.dependency_tracker.mark_collection_started()
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
            test_file = cached_relpath(path_str, str(collector.config.rootdir))
            if self._active_collection_file == test_file:
                return
            self.testmon.dependency_tracker.begin_test_file_collection(test_file)
            self._active_collection_file = test_file
        except Exception:
            return

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_pycollect_makeitem(
        self, collector, name, obj
    ):  # pylint: disable=unused-argument
        makeitem_result = yield
        items = makeitem_result.get_result() or []
        try:
            self.raw_test_names.extend(
                [item.nodeid for item in items if isinstance(item, pytest.Item)]
            )
        except TypeError:  # 'Class' object is not iterable
            pass

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, session, config, items
    ):  # pylint: disable=unused-argument
        _timing_log(config, "collection_start", item_count=len(items))
        # Stop collection tracking and get collected dependencies
        (
            self._collection_file_deps,
            self._collection_local_imports,
            self._collection_external_imports,
        ) = self.testmon.dependency_tracker.stop_collection_tracking()
        self._active_collection_file = None

        should_sync = not session.testsfailed and self._running_as in (
            "single",
            "controller",
        )
        if should_sync:
            if self._running_as == "controller":
                self._enqueue_sync(set(self.raw_test_names))
                self._drain_write_queue()
            else:
                config.testmon_data.sync_db_fs_tests(retain=set(self.raw_test_names))
        _timing_log(
            config,
            "collection_end",
            item_count=len(items),
            raw_count=len(self.raw_test_names),
        )

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(
        self, item, nextitem
    ):  # pylint: disable=unused-argument
        current_file = item.nodeid.split("::")[0] if "::" in item.nodeid else item.nodeid
        if getattr(self, "_current_test_file", None) != current_file:
            if getattr(self, "_current_test_file", None):
                self.testmon.dependency_tracker.end_test_file(self._current_test_file)
            self._current_test_file = current_file
            self.testmon.dependency_tracker.start_test_file(current_file)
            _timing_log(
                item.session.config,
                "worker_file_exec_start",
                test_file=current_file,
            )
        if not getattr(self, "_seen_first_test", False):
            self._seen_first_test = True
            _timing_log(item.session.config, "worker_first_test_start")
        self._file_tests[current_file].add(item.nodeid)
        t0 = time.monotonic()
        self.testmon.start_testmon(item.nodeid, nextitem.nodeid if nextitem else None)
        self._timing_totals["start_testmon"] += time.monotonic() - t0
        self._timing_counts["start_testmon"] += 1
        result = yield
        if result.excinfo and issubclass(result.excinfo[0], BaseException):
            self.testmon.discard_current()
        deps_payload = self.testmon.stop_testmon()
        self._file_nodes[current_file][item.nodeid] = deps_payload
        # Finalize when leaving a test file
        if self._running_as in ("single", "controller"):
            next_file = None
            if nextitem is not None:
                next_file = nextitem.nodeid.split("::")[0] if "::" in nextitem.nodeid else nextitem.nodeid
            if nextitem is None or current_file != next_file:
                t1 = time.monotonic()
                self._finalize_test_file(current_file)
                self._timing_totals["finalize_file"] += time.monotonic() - t1
                self._timing_counts["finalize_file"] += 1
        elif self._running_as == "worker":
            next_file = None
            if nextitem is not None:
                next_file = nextitem.nodeid.split("::")[0] if "::" in nextitem.nodeid else nextitem.nodeid
            if nextitem is None or current_file != next_file:
                test_count = len(self._file_tests.get(current_file, []))
                t1 = time.monotonic()
                self._finalize_worker_test_file(current_file)
                self._timing_totals["finalize_worker_file"] += time.monotonic() - t1
                self._timing_counts["finalize_worker_file"] += 1
                _timing_log(
                    item.session.config,
                    "worker_file_exec_end",
                    test_file=current_file,
                    test_count=test_count,
                )
        if getattr(self, "_seen_first_test", False) and not getattr(self, "_marked_first_test_end", False):
            self._marked_first_test_end = True
            _timing_log(item.session.config, "worker_first_test_end")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):  # pylint: disable=unused-argument
        result = yield

        if call.when == "teardown":
            report = result.get_result()
            if self._running_as == "worker":
                pass
            else:
                pass

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report):
        outcome = self._outcomes.get(report.nodeid)
        if outcome is None:
            outcome = {"failed": False, "duration": 0.0}
            self._outcomes[report.nodeid] = outcome
        outcome["duration"] += getattr(report, "duration", 0.0) or 0.0
        if report.outcome == "failed":
            outcome["failed"] = True
        if "forced" not in outcome:
            forced_nodeids = getattr(self, "_forced_nodeids", set())
            outcome["forced"] = 1 if report.nodeid in forced_nodeids else 0

        if self._running_as == "worker":
            if report.when == "call" and self._worker_batches:
                batch = self._worker_batches.pop(0)
                props = getattr(report, "user_properties", None)
                if props is None:
                    report.user_properties = []
                    props = report.user_properties
                props.append(("ezmon_batch", batch))
            return
        if self._running_as == "controller":
            props = getattr(report, "user_properties", None) or []
            for key, value in props:
                if key != "ezmon_batch":
                    continue
                worker_id = (
                    getattr(report, "worker_id", None)
                    or getattr(report, "workerid", None)
                    or "worker"
                )
                self._handle_worker_output(
                    {
                        "testmon_nodes_files_lines": {
                            "__format__": "file_common_unique_v2",
                            "batches": [value],
                        }
                    },
                    worker_id,
                )
            return
        # Per-test deps are captured in pytest_runtest_protocol via stop_testmon.

    def _merge_collection_deps(self, nodes_files_lines):
        """Merge collection-time dependencies into test data."""
        t0 = time.monotonic()
        has_file_deps = bool(self._collection_file_deps)
        has_local_imports = bool(self._collection_local_imports)
        has_external_imports = bool(self._collection_external_imports)
        global_file_deps, global_base_deps, global_external_deps = self.testmon.dependency_tracker.get_global_import_deps()
        has_file_checkpoint_imports = False

        has_global_deps = bool(global_base_deps) or bool(global_file_deps)
        has_global_external = bool(global_external_deps)


        if not (
            has_file_deps
            or has_local_imports
            or has_external_imports
            or has_global_deps
            or has_file_checkpoint_imports
            or has_global_external
        ):
            self._timing_totals["merge_collection_deps"] += time.monotonic() - t0
            self._timing_counts["merge_collection_deps"] += 1
            return nodes_files_lines

        for test_nodeid, data in nodes_files_lines.items():
            if "deps" not in data:
                data["deps"] = set()
            if "file_deps" not in data:
                data["file_deps"] = set()
            if "external_deps" not in data:
                data["external_deps"] = set()

            test_file = test_nodeid.split("::")[0] if "::" in test_nodeid else test_nodeid

            if has_file_deps:
                collection_file_deps = self._collection_file_deps.get(test_file, set())
                for tracked_file in collection_file_deps:
                    data["file_deps"].add((tracked_file.path, tracked_file.sha))

            file_checkpoint_file_deps, file_checkpoint_imports, file_checkpoint_external = self.testmon.dependency_tracker.get_file_import_deps(test_file)
            has_file_checkpoint_imports = bool(file_checkpoint_imports)
            if has_local_imports:
                imported_modules = self._collection_local_imports.get(test_file, set())
                if imported_modules:
                    data["deps"].update(imported_modules)
            if has_file_checkpoint_imports:
                data["deps"].update(file_checkpoint_imports)
            if file_checkpoint_file_deps:
                for tracked_file in file_checkpoint_file_deps:
                    data["file_deps"].add((tracked_file.path, tracked_file.sha))

            if global_base_deps:
                data["deps"].update(global_base_deps)

            if global_file_deps:
                for tracked_file in global_file_deps:
                    data["file_deps"].add((tracked_file.path, tracked_file.sha))

            if has_external_imports:
                collection_external_imports = self._collection_external_imports.get(test_file, set())
                if collection_external_imports:
                    data["external_deps"].update(collection_external_imports)
            if file_checkpoint_external:
                data["external_deps"].update(file_checkpoint_external)
            if has_global_external:
                data["external_deps"].update(global_external_deps)

        self._timing_totals["merge_collection_deps"] += time.monotonic() - t0
        self._timing_counts["merge_collection_deps"] += 1
        return nodes_files_lines

    def _finalize_test_file(self, test_file: str) -> None:
        """Compute common/unique deps for a test file and persist results."""
        if self._config is not None:
            _timing_log(self._config, "controller_finalize_file_start")
        t0 = time.monotonic()
        nodes = self._file_nodes.pop(test_file, {})
        tests_in_file = self._file_tests.pop(test_file, set())
        if not nodes:
            nodes = {}

        succeeded = {}
        for test_name in tests_in_file or nodes.keys():
            deps = nodes.get(test_name)
            if deps is None:
                deps = {"deps": {test_file}, "file_deps": set(), "external_deps": set()}
            outcome = self._outcomes.get(test_name, {"failed": False, "duration": 0.0})
            if outcome.get("failed"):
                # Mark failed tests but do not update deps
                ds = self.testmon_data.dep_store
                if ds:
                    ds.ensure_tests_batch(
                        self.testmon_data.run_id,
                        [(test_name, test_file, outcome.get("duration"), True, None)],
                    )
                else:
                    self.testmon_data.db.get_or_create_test_id(
                        test_name,
                        duration=outcome.get("duration"),
                        failed=True,
                        test_file=test_file,
                        run_id=self.testmon_data.run_id,
                    )
            else:
                succeeded[test_name] = deps

        if not succeeded:
            self._timing_totals["finalize_test_file_body"] += time.monotonic() - t0
            self._timing_counts["finalize_test_file_body"] += 1
            if self._config is not None:
                _timing_log(self._config, "controller_finalize_file_end")
            return

        common, unique = self._compute_common_unique(succeeded)
        merged_nodes = {}
        for test_name, deps in succeeded.items():
            merged = {}
            for key, value in common.items():
                merged[key] = set(value)
            for key, value in unique.get(test_name, {}).items():
                if key in merged:
                    merged[key].update(value)
                else:
                    merged[key] = set(value)
            merged_nodes[test_name] = merged

        merged_nodes = self._merge_collection_deps(merged_nodes)

        test_executions_fingerprints = self.testmon_data.get_tests_fingerprints(
            merged_nodes, self._outcomes
        )
        self.testmon_data.save_test_deps_bitmap(test_executions_fingerprints)
        self._timing_totals["finalize_test_file_body"] += time.monotonic() - t0
        self._timing_counts["finalize_test_file_body"] += 1
        if self._config is not None:
            _timing_log(self._config, "controller_finalize_file_end")

    def _compute_common_unique(self, nodes):
        """Return (common_deps, unique_deps_by_test) for simplified payloads."""
        common = None
        for deps in nodes.values():
            normalized = {
                "deps": set(deps.get("deps", set())),
                "file_deps": set(deps.get("file_deps", set())),
                "external_deps": set(deps.get("external_deps", set())),
            }
            if common is None:
                common = normalized
                continue
            for key in ("deps", "file_deps", "external_deps"):
                common[key] &= normalized[key]
        if common is None:
            common = {"deps": set(), "file_deps": set(), "external_deps": set()}

        unique = {}
        for test_name, deps in nodes.items():
            norm = {
                "deps": set(deps.get("deps", set())),
                "file_deps": set(deps.get("file_deps", set())),
                "external_deps": set(deps.get("external_deps", set())),
            }
            unique_deps = {}
            for key in ("deps", "file_deps", "external_deps"):
                diff = norm[key] - common[key]
                if diff:
                    unique_deps[key] = diff
            unique[test_name] = unique_deps
        return common, unique

    def _finalize_worker_test_file(self, test_file: str) -> None:
        if self._config is not None:
            _timing_log(self._config, "worker_finalize_file_start", test_file=test_file)
        t0 = time.monotonic()
        nodes = self._file_nodes.pop(test_file, {})
        tests_in_file = self._file_tests.pop(test_file, set())
        if not nodes:
            nodes = {}

        succeeded = {}
        for test_name in tests_in_file or nodes.keys():
            deps = nodes.get(test_name)
            if deps is None:
                deps = {"deps": {test_file}, "file_deps": set(), "external_deps": set()}
            outcome = self._outcomes.get(test_name, {"failed": False, "duration": 0.0})
            if not outcome.get("failed"):
                succeeded[test_name] = deps

        if not succeeded:
            return

        # Workers must merge collection-time deps before sending, because in
        # xdist the controller doesn't import test modules and has no
        # collection deps to add back.
        merged_succeeded = {
            name: {k: set(v) for k, v in deps.items()}
            for name, deps in succeeded.items()
        }
        merged_succeeded = self._merge_collection_deps(merged_succeeded)

        common, unique = self._compute_common_unique(merged_succeeded)

        def _deps_to_payload(deps):
            payload = {}
            files = deps.get("file_deps", set())
            if files:
                payload["f"] = [path for path, _sha in files]
            py_deps = deps.get("deps", set())
            if py_deps:
                payload["p"] = list(py_deps)
            ext = deps.get("external_deps", set())
            if ext:
                payload["e"] = list(ext)
            return payload

        def _suffix_for(name):
            if name.startswith(test_file + "::"):
                return name[len(test_file) + 2 :]
            if name == test_file:
                return ""
            return name

        suffixes = [_suffix_for(name) for name in unique.keys()]

        prefixes = []
        prefix_index = {}
        for suffix in suffixes:
            if "::" in suffix:
                prefix = "::".join(suffix.split("::")[:-1])
                if prefix not in prefix_index:
                    prefix_index[prefix] = len(prefixes) + 1
                    prefixes.append(prefix)

        def _encode_name(suffix):
            if "::" not in suffix:
                return "0|" + suffix
            parts = suffix.split("::")
            prefix = "::".join(parts[:-1])
            last = parts[-1]
            return f"{prefix_index[prefix]}|{last}"

        t_names = []
        for suffix in suffixes:
            t_names.append(_encode_name(suffix))
        name_to_index = {name: idx for idx, name in enumerate(t_names)}

        file_payload = {}
        common_payload = _deps_to_payload(common)
        if common_payload:
            file_payload["com"] = common_payload

        etc = []
        dur = [0.0] * len(t_names)
        for (test_name, deps), suffix in zip(unique.items(), suffixes):
            test_payload = _deps_to_payload(deps)
            encoded_name = _encode_name(suffix)
            idx = name_to_index.get(encoded_name)
            if idx is None:
                continue
            outcome = self._outcomes.get(test_name, {"failed": False, "duration": 0.0})
            dur[idx] = outcome.get("duration", 0.0) or 0.0
            if test_payload:
                file_payload[str(idx)] = test_payload
            else:
                etc.append(idx)

        file_payload["t_names"] = t_names
        if etc:
            file_payload["etc"] = etc
        file_payload["dur"] = dur

        if prefixes:
            file_payload["pm"] = prefixes

        self._worker_aggregate_files[test_file] = file_payload
        if len(self._worker_aggregate_files) >= self._worker_batch_size:
            self._worker_batches.append({"files": dict(self._worker_aggregate_files)})
            self._worker_aggregate_files.clear()
        self._timing_totals["finalize_worker_file_body"] += time.monotonic() - t0
        self._timing_counts["finalize_worker_file_body"] += 1
        if self._config is not None:
            _timing_log(self._config, "worker_finalize_file_end", test_file=test_file)

    def pytest_keyboard_interrupt(self, excinfo):  # pylint: disable=unused-argument
        if self._running_as == "single":
            nodes_files_lines = self.testmon.get_batch_coverage_data()

            test_executions_fingerprints = self.testmon_data.get_tests_fingerprints(
                nodes_files_lines, self._outcomes
            )
            self.testmon_data.save_test_deps_bitmap(test_executions_fingerprints)
            self.testmon.close()

    def pytest_sessionfinish(self, session):  # pylint: disable=unused-argument
        if self._running_as == "worker":
            workeroutput = getattr(session.config, "workeroutput", None)
            if workeroutput is not None:
                _timing_log(session.config, "worker_send_start")
                files_items = list(self._worker_aggregate_files.items())
                _timing_log(session.config, "worker_batch_build_start", file_count=len(files_items))
                batches = list(self._worker_batches)
                if self._worker_aggregate_files:
                    batches.append({"files": dict(self._worker_aggregate_files)})
                    self._worker_aggregate_files.clear()
                for idx, batch in enumerate(batches):
                    _timing_log(
                        session.config,
                        "worker_batch_start",
                        batch_index=idx,
                        file_count=len(batch.get("files") or {}),
                    )
                    _timing_log(
                        session.config,
                        "worker_batch_end",
                        batch_index=idx,
                        file_count=len(batch.get("files") or {}),
                    )
                _timing_log(session.config, "worker_batch_build_end", batch_count=len(batches))
                workeroutput["testmon_nodes_files_lines"] = {
                    "__format__": "file_common_unique_v2",
                    "batches": batches,
                }
                _timing_log(
                    session.config,
                    "worker_send_end",
                    batch_count=len(batches),
                    file_count=len(files_items),
                )
            _timing_log(session.config, "worker_end")
            _timing_log(
                session.config,
                "worker_hook_totals",
                totals=dict(self._timing_totals),
                counts=dict(self._timing_counts),
            )
        if self._running_as in ("single", "controller"):
            if self._running_as == "controller":
                self._drain_write_queue()
            # Flush failed tests to DB — workers don't send deps for failed
            # tests, so the controller must mark them explicitly.
            failed_tests_for_db = [
                (name,
                 name.split("::")[0] if "::" in name else name,
                 outcome.get("duration", 0.0),
                 True,
                 None)
                for name, outcome in self._outcomes.items()
                if outcome.get("failed")
            ]
            if failed_tests_for_db:
                ds = self.testmon_data.dep_store
                if ds:
                    ds.ensure_tests_batch(
                        self.testmon_data.run_id, failed_tests_for_db
                    )
                    with self.testmon_data.db.con:
                        ds.save_batch([])  # flush dirty test metadata, no deps to write
                else:
                    for name, test_file, dur, _failed in failed_tests_for_db:
                        self.testmon_data.db.get_or_create_test_id(
                            name, duration=dur, failed=True,
                            test_file=test_file,
                            run_id=self.testmon_data.run_id,
                        )
            _timing_log(session.config, "controller_save_deps_start")
            duration = time.time() - self._sessionstarttime
            run_stats = self.testmon_data.db.fetch_current_run_stats(self.testmon_data.run_id)
            run_saved_time, run_all_time, run_saved_tests, run_all_tests = run_stats
            self.testmon_data.db.finish_run(
                self.testmon_data.run_id,
                duration=duration,
                tests_selected=run_saved_tests or 0,
                tests_deselected=(run_all_tests or 0) - (run_saved_tests or 0),
                tests_all=run_all_tests or 0,
                time_saved=run_saved_time or 0,
                time_all=run_all_time or 0,
            )
            self.testmon_data.db.finish_execution(
                duration=duration,
                select=session.config.testmon_config.select,
            )
            _timing_log(session.config, "controller_save_deps_end")
            _timing_log(
                session.config,
                "controller_hook_totals",
                totals=dict(self._timing_totals),
                counts=dict(self._timing_counts),
            )
        timing_dir = os.environ.get("EZMON_XDIST_TIMING_LOG_DIR")
        if timing_dir:
            _flush_timing_logs(timing_dir)
        self.testmon.close()

    def _handle_worker_output(self, workeroutput, worker_id):
        _timing_log_for_actor("controller", "controller_receive_start", worker_id=worker_id)
        payload_dir = os.environ.get("EZMON_WORKER_PAYLOAD_DIR")
        if payload_dir:
            worker_path = os.path.join(payload_dir, str(worker_id))
            os.makedirs(worker_path, exist_ok=True)
            counter_path = os.path.join(worker_path, "sent_index.txt")
            try:
                with open(counter_path, "r", encoding="utf-8") as f:
                    idx = int(f.read().strip() or "0")
            except Exception:
                idx = 0
            def _jsonable(value):
                if isinstance(value, dict):
                    return {str(k): _jsonable(v) for k, v in value.items()}
                if isinstance(value, set):
                    return [_jsonable(v) for v in value]
                if isinstance(value, tuple):
                    return [_jsonable(v) for v in value]
                if isinstance(value, list):
                    return [_jsonable(v) for v in value]
                return value
            try:
                nodes_payload = workeroutput.get("testmon_nodes_files_lines")
                if isinstance(nodes_payload, dict) and "batches" in nodes_payload:
                    for batch in nodes_payload.get("batches") or []:
                        batch_payload = dict(workeroutput)
                        batch_payload["testmon_nodes_files_lines"] = {
                            "__format__": nodes_payload.get("__format__"),
                            "batches": [batch],
                        }
                        with open(os.path.join(worker_path, f"sent_{idx}.json"), "w", encoding="utf-8") as f:
                            json.dump(_jsonable(batch_payload), f)
                        idx += 1
                else:
                    with open(os.path.join(worker_path, f"sent_{idx}.json"), "w", encoding="utf-8") as f:
                        json.dump(_jsonable(workeroutput), f)
                    idx += 1
                with open(counter_path, "w", encoding="utf-8") as f:
                    f.write(str(idx))
            except Exception as exc:
                logger.warning(f"Failed to write workeroutput for {worker_id}: {exc}")
        nodes_files_lines = workeroutput.get("testmon_nodes_files_lines") or {}
        expanded = {}
        if not (
            isinstance(nodes_files_lines, dict)
            and nodes_files_lines.get("__format__") == "file_common_unique_v2"
        ):
            _timing_log_for_actor("controller", "controller_receive_end", worker_id=worker_id, batch_count=0)
            return
        batches = nodes_files_lines.get("batches") or []
        for batch_index, batch in enumerate(batches):
            _timing_log_for_actor(
                "controller",
                "controller_batch_start",
                worker_id=worker_id,
                batch_index=batch_index,
                file_count=len(batch.get("files") or {}),
            )
            files_payload = batch.get("files") or {}
            for test_file, payload in files_payload.items():
                prefix_map = payload.get("pm", []) or []
                t_names = payload.get("t_names") or []
                durations = payload.get("dur") or []
                common_payload = payload.get("com") or {}
                common = {}
                if "p" in common_payload:
                    common["deps"] = set(common_payload["p"])
                if "f" in common_payload:
                    common["file_deps"] = set((path, None) for path in common_payload["f"])
                if "e" in common_payload:
                    common["external_deps"] = set(common_payload["e"])

                def _merge_unique(unique_payload):
                    merged = {
                        "deps": set(common.get("deps", set())),
                        "file_deps": set(common.get("file_deps", set())),
                        "external_deps": set(common.get("external_deps", set())),
                    }
                    if "p" in unique_payload:
                        merged.setdefault("deps", set()).update(unique_payload["p"])
                    if "f" in unique_payload:
                        merged.setdefault("file_deps", set()).update(
                            (path, None) for path in unique_payload["f"]
                        )
                    if "e" in unique_payload:
                        merged.setdefault("external_deps", set()).update(unique_payload["e"])
                    return merged

                def _decode_name(encoded_name):
                    if "|" not in encoded_name:
                        return encoded_name
                    prefix_id, last = encoded_name.split("|", 1)
                    try:
                        prefix_id = int(prefix_id)
                    except ValueError:
                        return encoded_name
                    if prefix_id == 0:
                        return last
                    if 0 < prefix_id <= len(prefix_map):
                        return f"{prefix_map[prefix_id - 1]}::{last}"
                    return encoded_name

                for suffix, deps_payload in payload.items():
                    if suffix in ("com", "etc", "pm", "t_names", "dur"):
                        continue
                    try:
                        idx = int(suffix)
                    except ValueError:
                        continue
                    if idx < 0 or idx >= len(t_names):
                        continue
                    decoded = _decode_name(t_names[idx])
                    if decoded and not decoded.startswith(test_file):
                        test_name = f"{test_file}::{decoded}"
                    else:
                        test_name = decoded or test_file
                    expanded[test_name] = _merge_unique(deps_payload or {})
                    if idx < len(durations):
                        outcome = self._outcomes.get(test_name)
                        if outcome is None:
                            outcome = {"failed": False, "duration": 0.0}
                            self._outcomes[test_name] = outcome
                        outcome["duration"] = durations[idx]

                for idx in payload.get("etc", []) or []:
                    if idx < 0 or idx >= len(t_names):
                        continue
                    decoded = _decode_name(t_names[idx])
                    if decoded and not decoded.startswith(test_file):
                        test_name = f"{test_file}::{decoded}"
                    else:
                        test_name = decoded or test_file
                    expanded[test_name] = {
                        "deps": set(common.get("deps", set())),
                        "file_deps": set(common.get("file_deps", set())),
                        "external_deps": set(common.get("external_deps", set())),
                    }
                    if idx < len(durations):
                        outcome = self._outcomes.get(test_name)
                        if outcome is None:
                            outcome = {"failed": False, "duration": 0.0}
                            self._outcomes[test_name] = outcome
                        outcome["duration"] = durations[idx]
            _timing_log_for_actor(
                "controller",
                "controller_batch_end",
                worker_id=worker_id,
                batch_index=batch_index,
                file_count=len(batch.get("files") or {}),
            )
        _timing_log_for_actor("controller", "controller_receive_end", worker_id=worker_id, batch_count=len(batches))
        nodes_files_lines = expanded

        if self._running_as == "controller":
            # Save raw deps directly as each batch arrives — no queuing.
            # save_test_deps_raw Phase 1 handles test ID creation + failure marking.
            if nodes_files_lines:
                batch_outcomes = {
                    name: self._outcomes.get(name, {"failed": False, "duration": 0.0})
                    for name in nodes_files_lines
                }
                self.testmon_data.save_test_deps_raw(nodes_files_lines, batch_outcomes)
        else:
            # Single-process fallback: fingerprint then save
            if nodes_files_lines:
                _timing_log_for_actor("controller", "controller_fingerprint_start", worker_id=worker_id)
                test_executions_fingerprints = self.testmon_data.get_tests_fingerprints(
                    nodes_files_lines, self._outcomes
                )
                _timing_log_for_actor("controller", "controller_fingerprint_end", worker_id=worker_id)
                self.testmon_data.save_test_deps_bitmap(test_executions_fingerprints)
            failed_tests = [
                name
                for name, outcome in self._outcomes.items()
                if outcome.get("failed")
            ]
            if failed_tests:
                failed_tests_for_db = [
                    (name,
                     name.split("::")[0] if "::" in name else name,
                     self._outcomes.get(name, {}).get("duration", 0.0),
                     True,
                     None)
                    for name in failed_tests
                ]
                ds = self.testmon_data.dep_store
                if ds:
                    ds.ensure_tests_batch(
                        self.testmon_data.run_id, failed_tests_for_db
                    )
                else:
                    self.testmon_data.db.get_or_create_test_ids_batch(
                        self.testmon_data.run_id, failed_tests_for_db
                    )

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_down(self, node, error):  # pylint: disable=unused-argument
        if self._running_as != "controller":
            return
        workeroutput = getattr(node, "workeroutput", None) or {}
        worker_id = (
            getattr(node, "workerid", None)
            or getattr(node, "name", None)
            or getattr(getattr(node, "gateway", None), "id", None)
            or "worker"
        )
        self._handle_worker_output(workeroutput, worker_id)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):  # pylint: disable=unused-argument
        if self._running_as != "controller":
            return
        workeroutput = getattr(node, "workeroutput", None) or {}
        worker_id = (
            getattr(node, "workerid", None)
            or getattr(node, "name", None)
            or getattr(getattr(node, "gateway", None), "id", None)
            or "worker"
        )
        self._handle_worker_output(workeroutput, worker_id)


class TestmonXdistSync:
    """Synchronizes testmon data between xdist controller and workers.

    This class ensures all workers receive the same pre-computed stability data
    from the controller, preventing race conditions where workers independently
    compute different stable_test_names from varying database states.
    """
    def __init__(self):
        self.await_nodes = 0

    def pytest_configure_node(self, node):
        """Pass stability data from controller to workers during xdist initialization.

        Called by the controller for each worker node before it starts.
        We pass the pre-computed run_id, stable_test_names, and other data
        so workers all use the same deselection criteria.
        """
        running_as = get_running_as(node.config)
        if running_as != "controller":
            return

        if hasattr(node.config, "testmon_data") and hasattr(node, "workerinput"):
            worker_id = (
                getattr(node, "workerid", None)
                or getattr(node, "name", None)
                or getattr(getattr(node, "gateway", None), "id", None)
                or "worker"
            )
            _timing_log_for_actor("controller", "controller_send_start", worker_id=worker_id)
            testmon_data = node.config.testmon_data
            # Pass all data workers need to avoid recomputing stability
            # Pass unstable_test_names (tests to RUN) instead of stable_test_names
            # This is ~750x smaller (255 tests vs 230k) for large test suites
            node.workerinput["testmon_run_id"] = testmon_data.run_id
            node.workerinput["testmon_unstable_test_names"] = (
                list(testmon_data.unstable_test_names)
                if testmon_data.unstable_test_names is not None
                else None
            )
            node.workerinput["testmon_files_of_interest"] = list(
                testmon_data.files_of_interest or []
            )
            node.workerinput["testmon_changed_packages"] = list(
                getattr(testmon_data, "changed_packages", set()) or set()
            )
            node.workerinput["testmon_explicitly_nocollect_files"] = list(
                getattr(testmon_data, "explicitly_nocollect_files", set()) or set()
            )
            node.workerinput["testmon_min_collected_files"] = list(
                getattr(testmon_data, "min_collected_files", set()) or set()
            )
            node.workerinput["testmon_expected_imports"] = list(
                getattr(testmon_data, "expected_imports", set()) or set()
            )
            node.workerinput["testmon_expected_reads"] = list(
                getattr(testmon_data, "expected_reads", set()) or set()
            )
            node.workerinput["testmon_expected_packages"] = list(
                getattr(testmon_data, "expected_packages", set()) or set()
            )
            node.workerinput["testmon_expected_files_list"] = list(
                getattr(testmon_data, "expected_files_list", []) or []
            )
            node.workerinput["testmon_expected_packages_list"] = list(
                getattr(testmon_data, "expected_packages_list", []) or []
            )
            _timing_log_for_actor("controller", "controller_send_end", worker_id=worker_id)


    def pytest_testnodeready(self, node):  # pylint: disable=unused-argument
        self.await_nodes += 1

    def pytest_xdist_node_collection_finished(
        self, node, ids
    ):  # pylint: disable=invalid-name
        self.await_nodes += -1
        if self.await_nodes != 0:
            return
        if get_running_as(node.config) != "controller":
            return
        collect_plugin = node.config.pluginmanager.get_plugin("TestmonCollect")
        if not collect_plugin:
            return
        collect_plugin._enqueue_sync(set(ids))
        collect_plugin._drain_write_queue()


def did_fail(reports):
    return reports["failed"]


def get_failing(all_test_executions):
    failing_files, failing_tests = set(), {}
    for test_name, result in all_test_executions.items():
        if did_fail(all_test_executions[test_name]):
            failing_files.add(home_file(test_name))
            failing_tests[test_name] = result
    return failing_files, failing_tests


def sort_items_by_duration(items, avg_durations) -> None:
    items.sort(key=lambda item: avg_durations[item.nodeid])
    items.sort(
        key=lambda item: avg_durations[get_test_execution_class_name(item.nodeid)]
    )
    items.sort(
        key=lambda item: avg_durations[get_test_execution_module_name(item.nodeid)]
    )


def format_time_saved(seconds):
    if not seconds:
        seconds = 0
    if seconds >= 3600:
        return f"{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m"
    return f"{int(seconds / 60)}m {int((seconds % 60) % 60)}s"


class TestmonSelect:
    def __init__(self, config, testmon_data, running_as: str):
        self.testmon_data: TestmonData = testmon_data
        self.config = config

        self._running_as = running_as
        failing_files, failing_test_names = set(), {}
        if running_as != "worker":
            failing_files, failing_test_names = get_failing(testmon_data.all_tests)

        # Capture the set of known tests BEFORE sync_db_fs_tests adds new ones
        # This is used to detect truly new tests that should always run
        if running_as != "worker":
            self._known_tests_at_start = set(testmon_data.all_tests.keys()) if testmon_data.all_tests else set()
        else:
            self._known_tests_at_start = set()

        # On fresh DB or no baseline data, run all tests (no deselection)
        # selected_tests = None means "select all"
        if testmon_data.unstable_test_names is None:
            self.selected_tests = None
        else:
            self.selected_tests = set(testmon_data.unstable_test_names or set())
            self.selected_tests.update(
                failing_test_names.keys() if isinstance(failing_test_names, dict) else failing_test_names
            )

        self.explicitly_nocollect_files = set(
            getattr(testmon_data, "explicitly_nocollect_files", set()) or set()
        )

        self._interrupted = False

    def pytest_ignore_collect(self, collection_path: Path, config):
        strpath = cached_relpath(str(collection_path), config.rootdir.strpath)

        # Check if this file is in the "always run" list
        always_run_files = getattr(config, "always_run_files", [])

        is_forced = any(strpath.endswith(f) for f in always_run_files)
        
        if is_forced:
            return None  # Don't ignore - force collection

        try:
            is_dir = collection_path.is_dir()
        except AttributeError:
            is_dir = collection_path.isdir()

        if not is_dir and strpath in self.explicitly_nocollect_files:
            return True
        return None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        _timing_log(config, "selection_start", item_count=len(items))
        always_run_files = getattr(config, "always_run_files", [])
        prioritized_files = getattr(config, "prioritized_files", [])

        # normalized versions (for comparison)
        normalized_always = [f.replace("\\", "/").lower() for f in always_run_files]
        normalized_prioritized = [f.replace("\\", "/").lower() for f in prioritized_files]

        forced_by_file = {f: [] for f in always_run_files}
        prioritized_by_file = {f: [] for f in prioritized_files}
        normal_selected = []
        deselected = []
        forced_count = 0

        def source_order_key(i):
            path, lineno, name = i.location
            return (path, lineno, name)

        for item in items:
            # full path from pytest
            item_path = str(item.fspath)
            # normalize to forward slashes + lowercase
            item_path_norm = item_path.replace(os.sep, "/").lower()

            # Check if it's in always_run list first
            matched_forced = None
            for original_f, norm_f in zip(always_run_files, normalized_always):
                if item_path_norm.endswith(norm_f):
                    matched_forced = original_f
                    break

            if matched_forced:
                forced_by_file[matched_forced].append(item)
                # selected_tests=None means all tests run (fresh DB)
                if self.selected_tests is not None and item.nodeid not in self.selected_tests:
                    forced_count += 1
                continue

            # Check if it's in prioritized list (but not forced)
            matched_prioritized = None
            for original_f, norm_f in zip(prioritized_files, normalized_prioritized):
                if item_path_norm.endswith(norm_f):
                    matched_prioritized = original_f
                    break

            # selected_tests=None means all tests run (fresh DB)
            if matched_prioritized and (self.selected_tests is None or item.nodeid in self.selected_tests):
                # Only prioritize if testmon would run it anyway
                prioritized_by_file[matched_prioritized].append(item)
                continue

            # Neither forced nor prioritized
            # selected_tests=None means all tests run (fresh DB)
            if self.selected_tests is not None and item.nodeid not in self.selected_tests:
                # Workers trust the controller's selected_tests unconditionally.
                # Controller checks _known_tests_at_start so new tests always run.
                if self._running_as == "worker" or item.nodeid in self._known_tests_at_start:
                    deselected.append(item)
                else:
                    # New test - run it
                    normal_selected.append(item)
            else:
                normal_selected.append(item)

        # 1) Forced: file order + source order (DO NOT duration-sort these)
        forced = []
        for f in always_run_files:
            forced_by_file[f].sort(key=source_order_key)
            forced.extend(forced_by_file[f])

        # 2) Prioritized: user's order + source order (NOT duration-sorted)
        prioritized = []
        for f in prioritized_files:
            prioritized_by_file[f].sort(key=source_order_key)
            prioritized.extend(prioritized_by_file[f])

        # 3) Normal selected: duration priority (your existing testmon behavior)
        # Skip reordering for workers (no avg_durations) or if --ezmon-no-reorder is set
        no_reorder = self._running_as == "worker" or config.getoption("ezmon_no_reorder", False)
        if not no_reorder:
            sort_items_by_duration(normal_selected, self.testmon_data.avg_durations)

        selected = forced + prioritized + normal_selected
        self._forced_nodeids = {item.nodeid for item in forced}



        if self.config.testmon_config.select:
            items[:] = selected
            session.config.hook.pytest_deselected(
                items=([FakeItemFromTestmon(session.config)] * len(deselected))
            )
            if forced_count > 0:
                logger.info(f" Forced {forced_count} tests from always_run list")
            if prioritized:
                logger.info(f" Prioritized {len(prioritized)} tests from priority list")
        else:
            # 3) In noselect mode: also prioritize deselected by duration
            # Skip reordering if --ezmon-no-reorder is set
            if not no_reorder:
                sort_items_by_duration(deselected, self.testmon_data.avg_durations)
            items[:] = selected + deselected
        _timing_log(
            config,
            "selection_end",
            selected_count=len(selected),
            deselected_count=len(deselected),
            forced_count=forced_count,
            prioritized_count=len(prioritized),
        )

    @pytest.hookimpl(trylast=True, hookwrapper=True)
    def pytest_runtestloop(self, session):  # pylint: disable=unused-argument
        _timing_log(self.config, "runtestloop_start")
        yield
        _timing_log(self.config, "runtestloop_end")
    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        # If RTS is active (selected_tests exists) and no tests were collected,
        # that's success - it means no tests were affected by changes
        if hasattr(self, 'selected_tests') and exitstatus == ExitCode.NO_TESTS_COLLECTED:
            session.exitstatus = ExitCode.OK

    @pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self):
        if self._interrupted:
            return

        if not self.config.option.verbose >= 2:
            return

        (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
            total_saved_time,
            total_all_time,
            total_saved_tests,
            total_tests_all,
        ) = self.testmon_data.fetch_saving_stats(self.config.testmon_config.select)

        terminal_reporter = TerminalReporter(self.config)
        potential_or_not = ""
        if not self.config.testmon_config.select:
            potential_or_not = "Potential t"
        else:
            potential_or_not = "T"
        terminal_reporter.section(
            f"{potential_or_not}estmon savings (deselected/no ezmon)",
            "=",
            **{"blue": True},
        )

        try:
            tests_all_ratio = f"{100.0 * total_saved_tests / total_tests_all:.0f}"
        except ZeroDivisionError:
            tests_all_ratio = "0"
        try:
            tests_current_ratio = f"{100.0 * run_saved_tests / run_all_tests:.0f}"
        except ZeroDivisionError:
            tests_current_ratio = "0"
        msg = f"this run: {run_saved_tests}/{run_all_tests} ({tests_current_ratio}%) tests, "
        msg += format_time_saved(run_saved_time) + "/" + format_time_saved(run_all_time)
        msg += f", all runs: {total_saved_tests}/{total_tests_all} ({tests_all_ratio}%) tests, "
        msg += (
            format_time_saved(total_saved_time)
            + "/"
            + format_time_saved(total_all_time)
        )
        terminal_reporter.write_line(msg)

    def pytest_keyboard_interrupt(self, excinfo):  # pylint: disable=unused-argument
        self._interrupted = True


class FakeItemFromTestmon:  # pylint: disable=too-few-public-methods
    def __init__(self, config):
        self.config = config
