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

from ezmon.server_sync import download_testmon_data, upload_testmon_data, should_sync, upload_dependency_graph
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
from ezmon import graph as ezmon_graph

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
    environment = config.getoption("environment_expression") or eval_environment(
        config.getini("environment_expression")
    )
    ignore_dependencies = config.getini("testmon_ignore_dependencies")

    system_packages = get_system_packages(ignore=ignore_dependencies)

    
    testmon_data = TestmonData(
        rootdir=config.rootdir.strpath,
        database=None,
        environment=environment,
        system_packages=system_packages,
        readonly=get_running_as(config) == "worker",
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
    # Initialize default
    config.always_run_files = []

    if should_sync():
        # 1. Fetch preferences (Always Run Tests)
        prefs = get_test_preferences()
        config.always_run_files = list(prefs.get("always_run_tests", []))
        
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
            message += (
                "The packages installed in your Python environment have been changed. "
                "All tests have to be re-executed. "
                if packages_change
                else f"changed files: {changed_files_msg}, unchanged files: {len(stable_files)}, "
            )
    if config.testmon_data.environment:
        message += f"environment: {environment}"
    return message

def pytest_unconfigure(config):
    # Close and commit database FIRST (flush all changes to disk)
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
        graph_file = testmon_file.parent / "dependency_graph.html"
        # Give file system time to flush
        time.sleep(0.2)
        
        if testmon_file.exists() and testmon_file.stat().st_size > 0:
            logger.info(f"📦 Uploading: {testmon_file.stat().st_size:,} bytes")
            repo_name = os.getenv("GITHUB_REPOSITORY") or os.getenv("REPO_ID") or "unknown"
            upload_testmon_data(testmon_file, repo_name)
        else:
            logger.info("No testmon data to upload")

        if config.getoption("--ezmon-graph"):
            if graph_file.exists() and graph_file.stat().st_size > 0:
                logger.info(f"Uploading Graph: {graph_file.stat().st_size:,} bytes")
                upload_dependency_graph(graph_file)
            else:
                logger.warning(f"--ezmon-graph was set, but {graph_file.name} was not found or is empty.")


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
        self.testmon.close()


class TestmonXdistSync:
    def __init__(self):
        self.await_nodes = 0

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
        print("always run files are", always_run_files)

        # normalized versions (for comparison)
        normalized_always = [f.replace("\\", "/").lower() for f in always_run_files]

        forced_by_file = {f: [] for f in always_run_files}
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

            matched_forced = None
            # preserve server order from always_run_files
            for original_f, norm_f in zip(always_run_files, normalized_always):
                if item_path_norm.endswith(norm_f):
                    matched_forced = original_f
                    break

            if matched_forced:
                forced_by_file[matched_forced].append(item)
                if item.nodeid in self.deselected_tests:
                    forced_count += 1
                continue

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

        # 2) Normal selected: duration priority (your existing testmon behavior)
        sort_items_by_duration(normal_selected, self.testmon_data.avg_durations)

        selected = forced + normal_selected

        if self.config.testmon_config.select:
            items[:] = selected
            session.config.hook.pytest_deselected(
                items=([FakeItemFromTestmon(session.config)] * len(deselected))
            )
            if forced_count > 0:
                logger.info(f" Forced {forced_count} tests from always_run list")
        else:
            # 3) In noselect mode: also prioritize deselected by duration
            sort_items_by_duration(deselected, self.testmon_data.avg_durations)
            items[:] = selected + deselected
    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if len(self.deselected_tests) and exitstatus == ExitCode.NO_TESTS_COLLECTED:
            session.exitstatus = ExitCode.OK

        if self.config.getoption("ezmon_graph"):
            root_dir = self.config.rootdir.strpath
            logger.info("Generating dependency graph...")
            ezmon_graph.generate_graph(root_dir)

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
