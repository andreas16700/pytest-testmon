#!/usr/bin/env python
"""
Validate that per-test dependencies recorded by ezmon match the
dependencies observed when the same test is run in isolation.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ezmon.bitmap_deps import TestDeps

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"
TRACE_CLI = REPO_ROOT / "scripts" / "trace_test_imports.py"


def _setup_workspace() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_trace_"))
    workspace = temp_dir / "sample_project"
    shutil.copytree(SAMPLE_PROJECT, workspace)
    try:
        (workspace / ".testmondata").unlink()
    except FileNotFoundError:
        pass

    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=workspace, capture_output=True, check=True)
    return workspace


def _run_pytest_with_plugin(workspace: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT}:{workspace}"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["TESTMON_NET_ENABLED"] = "false"
    for key in ["TESTMON_SERVER", "TESTMON_AUTH_TOKEN", "REPO_ID", "JOB_ID", "RUN_ID"]:
        env.pop(key, None)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "ezmon.pytest_ezmon",
        "--ezmon",
        "-q",
    ]
    result = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"pytest failed: {result.stdout}\n{result.stderr}"


def _load_db_deps(workspace: Path) -> dict:
    db_path = workspace / ".testmondata"
    assert db_path.exists(), "Expected .testmondata to exist"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    file_map = {row["id"]: row["path"] for row in cur.execute("select id, path from files")}
    deps_by_test = {}
    for row in cur.execute("select t.name, td.file_bitmap, td.external_packages from test_deps td join tests t on t.id = td.test_id"):
        deps = TestDeps.deserialize(row["name"], row["file_bitmap"], row["external_packages"])
        deps_by_test[row["name"]] = {file_map[fid] for fid in deps.file_ids if fid in file_map}
    con.close()
    return deps_by_test


def _trace_isolated_test(workspace: Path, nodeid: str) -> set:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT}:{workspace}"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    out_file = workspace / ".trace_imports.json"
    if out_file.exists():
        out_file.unlink()

    cmd = [
        sys.executable,
        str(TRACE_CLI),
        nodeid,
        "--format",
        "json",
        "--output",
        str(out_file),
        "--no-stdout",
    ]
    result = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"trace CLI failed: {result.stdout}\n{result.stderr}"
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    return set(payload.get(nodeid, []))


@pytest.mark.slow
def test_cli_matches_recorded_deps():
    workspace = _setup_workspace()
    try:
        _run_pytest_with_plugin(workspace)
        deps_by_test = _load_db_deps(workspace)
        assert deps_by_test, "Expected deps to be recorded in DB"

        for nodeid, recorded in deps_by_test.items():
            traced = _trace_isolated_test(workspace, nodeid)
            assert traced == recorded, (
                f"Mismatch for {nodeid}\n"
                f"recorded: {sorted(recorded)}\n"
                f"traced: {sorted(traced)}"
            )
    finally:
        shutil.rmtree(workspace.parent, ignore_errors=True)
