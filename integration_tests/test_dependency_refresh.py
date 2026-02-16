#!/usr/bin/env python
"""
Ensure deps are fully refreshed: a test remains dependent on unchanged imports,
so a later change to those imports re-selects the test.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"


def _setup_workspace() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_refresh_"))
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


def _run_pytest(workspace: Path) -> str:
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
        "tests/test_models.py",
    ]
    result = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"pytest failed: {result.stdout}\n{result.stderr}"
    return result.stdout + result.stderr


def _commit_change(workspace: Path, path: str, target: str, replacement: str, message: str) -> None:
    file_path = workspace / path
    content = file_path.read_text()
    assert target in content, f"Target not found in {path}"
    file_path.write_text(content.replace(target, replacement))
    subprocess.run(["git", "add", path], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=workspace, capture_output=True, check=True)


@pytest.mark.slow
def test_deps_refresh_include_unchanged_imports():
    workspace = _setup_workspace()
    try:
        # Run 1: baseline, populate DB
        out1 = _run_pytest(workspace)
        assert "6 passed" in out1 or "6 passed" in out1.lower()

        # Run 2: change user.py (one dependency)
        _commit_change(
            workspace,
            "src/models/user.py",
            "return self.name",
            "display = self.name\n        return display",
            "modify user.py",
        )
        out2 = _run_pytest(workspace)
        assert "6 passed" in out2 or "6 passed" in out2.lower()

        # Run 3: change product.py (previously unaffected dependency)
        _commit_change(
            workspace,
            "src/models/product.py",
            'return f"${self.price:.2f}"',
            'formatted = f"${self.price:.2f}"\n        return formatted',
            "modify product.py",
        )
        out3 = _run_pytest(workspace)
        # Must be selected due to recorded deps from Run 2.
        assert "6 passed" in out3 or "6 passed" in out3.lower(), out3
    finally:
        shutil.rmtree(workspace.parent, ignore_errors=True)
