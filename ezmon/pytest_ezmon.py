# -*- coding: utf-8 -*-
"""
Main module of ezmon pytest plugin.
"""
import time
import xmlrpc.client
import os

from collections import defaultdict
from datetime import date, timedelta

from pathlib import Path
import pytest

from ezmon.server_sync import download_testmon_data, upload_testmon_data, should_sync
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
        "--ezmon-graph",
        action="store_true",
        dest="ezmon_graph",
        help="Generate an interactive dependency graph (dependency_graph.html) of the project."
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



def get_testmon_file(config: Config) -> Path:
    return Path(config.rootdir.strpath) / ".testmondata"


def init_testmon_data(config: Config):
    running_as = get_running_as(config)

    # Workers receive pre-computed stability data from the controller via workerinput
    # This prevents race conditions where workers independently compute different stable_test_names
    if running_as == "worker" and hasattr(config, "workerinput"):
        workerinput = config.workerinput
        if "testmon_exec_id" in workerinput:
            # Create TestmonData for worker using controller's pre-computed data
            testmon_data = TestmonData.for_worker(
                rootdir=config.rootdir.strpath,
                exec_id=workerinput["testmon_exec_id"],
                stable_test_names=workerinput.get("testmon_stable_test_names", set()),
                files_of_interest=workerinput.get("testmon_files_of_interest", []),
                changed_packages=workerinput.get("testmon_changed_packages", set()),
            )
            config.testmon_data = testmon_data
            return

    # Controller or single process: compute stability normally
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
    testmon_data.determine_stable(bool(None))
    config.testmon_data = testmon_data


def get_running_as(config):
    if hasattr(config, "workerinput"):
        return "worker"

    if getattr(config.option, "dist", "no") == "no":
        return "single"

    return "controller"


def register_plugins(config, should_select, should_collect, cov_plugin):
    if should_select or should_collect:
        config.pluginmanager.register(
            TestmonSelect(config, config.testmon_data), "TestmonSelect"
        )

    if should_collect:
        config.pluginmanager.register(
            TestmonCollect(
                TestmonCollector(
                    config.rootdir.strpath,
                    testmon_labels=testmon_options(config),
                    cov_plugin=cov_plugin,
                ),
                config.testmon_data,
                running_as=get_running_as(config),
            ),
            "TestmonCollect",
        )
        if config.pluginmanager.hasplugin("xdist"):
            config.pluginmanager.register(TestmonXdistSync())


def pytest_configure(config):
    # Initialize defaults
    config.always_run_files = []
    config.prioritized_files = []

    if should_sync():
        # 1. Fetch preferences (Always Run Tests and Prioritized Tests)
        prefs = get_test_preferences()
        config.always_run_files = list(prefs.get("always_run_tests", []))
        config.prioritized_files = list(prefs.get("prioritized_tests", []))
        
        # 2. Download testmon data
        testmon_file = get_testmon_file(config)
        downloaded = download_testmon_data(testmon_file)
        
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

    tm_conf = configure.header_collect_select(
        config, coverage_stack, cov_plugin=cov_plugin
    )
    config.testmon_config: TmConf = tm_conf
    if tm_conf.select or tm_conf.collect:
        try:
            init_testmon_data(config)
            register_plugins(config, tm_conf.select, tm_conf.collect, cov_plugin)
        except TestmonException as error:
            pytest.exit(str(error))

def pytest_report_header(config):
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
    # Close and commit database FIRST (flush all changes to disk)
    logger.info("pytest_configure function!")
    if hasattr(config, "testmon_data"):
        try:
            if hasattr(config.testmon_data, 'db') and hasattr(config.testmon_data.db, 'con'):
                # 1. Commit changes
                config.testmon_data.db.con.commit()
                logger.info("💾 Database committed")
                
                # 2. CRITICAL: Close the connection to force WAL checkpoint
                # This merges .testmondata-wal into .testmondata
                config.testmon_data.db.con.close()
                logger.info("🔒 SQLite connection closed (WAL checkpointed)")
                
                # Prevent double closing
                config.testmon_data.db.con = None
            
            # Call the class method (even if empty)
            config.testmon_data.close_connection()
            
        except Exception as e:
            logger.warning(f"Failed to close testmon database: {e}")

    # Only upload from main process (not xdist workers)
    if get_running_as(config) not in ("single", "controller"):
        return

    # Upload if sync is enabled - AFTER database is committed and closed
    if should_sync():
        testmon_file = get_testmon_file(config)
        logger.info(f"Testmon file path: {testmon_file}")
        # Give file system time to flush
        time.sleep(0.2)

        if testmon_file.exists() and testmon_file.stat().st_size > 0:
            logger.info(f"📦 Uploading: {testmon_file.stat().st_size:,} bytes")
            repo_name = os.getenv("GITHUB_REPOSITORY") or os.getenv("REPO_ID") or "unknown"
            upload_testmon_data(testmon_file, repo_name)
        else:
            logger.info("No testmon data to upload")

        # Note: Dependency graph data is now collected automatically during
        # test execution and stored in the database (no separate upload needed).


class TestmonCollect:
    def __init__(self, testmon, testmon_data, running_as="single", cov_plugin=None):
        self.testmon_data: TestmonData = testmon_data
        self.testmon: TestmonCollector = testmon
        self._running_as = running_as

        self.reports = defaultdict(lambda: {})
        self.raw_test_names = []
        self.cov_plugin = cov_plugin
        self._sessionstarttime = time.time()

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
        should_sync = not session.testsfailed and self._running_as in (
            "single",
            "controller",
        )
        if should_sync:
            config.testmon_data.sync_db_fs_tests(retain=set(self.raw_test_names))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(
        self, item, nextitem
    ):  # pylint: disable=unused-argument
        self.testmon.start_testmon(item.nodeid, nextitem.nodeid if nextitem else None)
        result = yield
        if result.excinfo and issubclass(result.excinfo[0], BaseException):
            self.testmon.discard_current()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):  # pylint: disable=unused-argument
        result = yield

        if call.when == "teardown":
            report = result.get_result()
            report.nodes_files_lines = self.testmon.get_batch_coverage_data()
            result.force_result(report)

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report):
        if self._running_as == "worker":
            return

        self.reports[report.nodeid][report.when] = report
        if report.when == "teardown" and hasattr(report, "nodes_files_lines"):
            if report.nodes_files_lines:
                test_executions_fingerprints = self.testmon_data.get_tests_fingerprints(
                    report.nodes_files_lines, self.reports
                )
                self.testmon_data.save_test_execution_file_fps(
                    test_executions_fingerprints,
                    nodes_files_lines=report.nodes_files_lines,
                )

    def pytest_keyboard_interrupt(self, excinfo):  # pylint: disable=unused-argument
        if self._running_as == "single":
            nodes_files_lines = self.testmon.get_batch_coverage_data()

            test_executions_fingerprints = self.testmon_data.get_tests_fingerprints(
                nodes_files_lines, self.reports
            )
            self.testmon_data.save_test_execution_file_fps(test_executions_fingerprints , nodes_files_lines=nodes_files_lines,)
            self.testmon.close()

    def pytest_sessionfinish(self, session):  # pylint: disable=unused-argument
        if self._running_as in ("single", "controller"):
            self.testmon_data.db.finish_execution(
                self.testmon_data.exec_id,
                time.time() - self._sessionstarttime,
                session.config.testmon_config.select,
            )
            # Save dependency graph edges after finish_execution (which creates run_uid)
            graph_edges = self.testmon.get_graph_edges()
            if graph_edges:
                self.testmon_data.save_dependency_graph(graph_edges)
        self.testmon.close()


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
        We pass the pre-computed exec_id, stable_test_names, and other data
        so workers all use the same deselection criteria.
        """
        running_as = get_running_as(node.config)
        if running_as != "controller":
            return

        if hasattr(node.config, "testmon_data") and hasattr(node, "workerinput"):
            testmon_data = node.config.testmon_data
            # Pass all data workers need to avoid recomputing stability
            node.workerinput["testmon_exec_id"] = testmon_data.exec_id
            node.workerinput["testmon_stable_test_names"] = list(
                testmon_data.stable_test_names or set()
            )
            node.workerinput["testmon_files_of_interest"] = list(
                testmon_data.files_of_interest or []
            )
            node.workerinput["testmon_changed_packages"] = list(
                getattr(testmon_data, "changed_packages", set()) or set()
            )

    def pytest_testnodeready(self, node):  # pylint: disable=unused-argument
        self.await_nodes += 1

    def pytest_xdist_node_collection_finished(
        self, node, ids
    ):  # pylint: disable=invalid-name
        self.await_nodes += -1
        if self.await_nodes == 0:
            node.config.testmon_data.sync_db_fs_tests(retain=set(ids))


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
    def __init__(self, config, testmon_data):
        self.testmon_data: TestmonData = testmon_data
        self.config = config

        failing_files, failing_test_names = get_failing(testmon_data.all_tests)

        self.deselected_files = [
            file for file in testmon_data.stable_files if file not in failing_files
        ]
        self.deselected_tests = [
            test_name
            for test_name in testmon_data.stable_test_names
            if test_name not in failing_test_names
        ]
        self._interrupted = False

    def pytest_ignore_collect(self, collection_path: Path, config):
        strpath = cached_relpath(str(collection_path), config.rootdir.strpath)
        
        # Check if this file is in the "always run" list
        always_run_files = getattr(config, "always_run_files", [])

        is_forced = any(strpath.endswith(f) for f in always_run_files)
        
        if is_forced:
            return None  # Don't ignore - force collection
        
        if strpath in self.deselected_files and self.config.testmon_config.select:
            return True
        return None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        always_run_files = getattr(config, "always_run_files", [])
        prioritized_files = getattr(config, "prioritized_files", [])
        print("always run files are", always_run_files)
        print("prioritized files are", prioritized_files)

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
                if item.nodeid in self.deselected_tests:
                    forced_count += 1
                continue

            # Check if it's in prioritized list (but not forced)
            matched_prioritized = None
            for original_f, norm_f in zip(prioritized_files, normalized_prioritized):
                if item_path_norm.endswith(norm_f):
                    matched_prioritized = original_f
                    break

            if matched_prioritized and item.nodeid not in self.deselected_tests:
                # Only prioritize if testmon would run it anyway
                prioritized_by_file[matched_prioritized].append(item)
                continue

            # Neither forced nor prioritized
            if item.nodeid in self.deselected_tests:
                deselected.append(item)
            else:
                normal_selected.append(item)

        # 1) Forced: file order + source order (DO NOT duration-sort these)
        forced = []
        for f in always_run_files:
            forced_by_file[f].sort(key=source_order_key)
            forced.extend(forced_by_file[f])

        print("Forced tests are", forced)

        # 2) Prioritized: user's order + source order (NOT duration-sorted)
        prioritized = []
        for f in prioritized_files:
            prioritized_by_file[f].sort(key=source_order_key)
            prioritized.extend(prioritized_by_file[f])

        print("Prioritized tests are", prioritized)

        # 3) Normal selected: duration priority (your existing testmon behavior)
        sort_items_by_duration(normal_selected, self.testmon_data.avg_durations)

        selected = forced + prioritized + normal_selected



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
            sort_items_by_duration(deselected, self.testmon_data.avg_durations)
            items[:] = selected + deselected
    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if len(self.deselected_tests) and exitstatus == ExitCode.NO_TESTS_COLLECTED:
            session.exitstatus = ExitCode.OK

        if self.config.getoption("ezmon_graph"):
            # The --ezmon-graph option is now deprecated.
            # Dependency graph data is automatically collected during test execution
            # and stored in the database. View it in the ez-viz interface.
            logger.info("Note: --ezmon-graph is deprecated. Dependency graph is now collected automatically during test execution.")

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
