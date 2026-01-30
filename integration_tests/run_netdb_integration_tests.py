#!/usr/bin/env python
"""
NetDB Integration test runner for pytest-ezmon.

This runs the same scenarios as the local integration tests, but uses NetDB
(remote server communication) instead of local SQLite databases.

Tests are isolated using unique job_ids, and all data is cleaned up after each
test to ensure idempotency.

Usage:
    python run_netdb_integration_tests.py [OPTIONS]

Examples:
    # Run all scenarios with default server
    python run_netdb_integration_tests.py

    # Run with specific server
    python run_netdb_integration_tests.py --server https://ezmon.aloiz.ch

    # Run specific scenario
    python run_netdb_integration_tests.py --scenario modify_math_utils

    # Verbose output
    python run_netdb_integration_tests.py -v

    # List available scenarios
    python run_netdb_integration_tests.py --list
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Set, Tuple, Optional

import requests

# Add parent directory to path to import ezmon for installation
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"

# Import scenarios
sys.path.insert(0, str(SCRIPT_DIR))
from scenarios import SCENARIOS, Scenario, Modification


def is_valid_ezmon_repo(path: Path) -> bool:
    """Check if the given path looks like a valid ezmon repository."""
    required_files = [
        "pyproject.toml",
        "ezmon/__init__.py",
        "ezmon/pytest_ezmon.py",
    ]
    return all((path / f).exists() for f in required_files)


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

    @classmethod
    def disable(cls):
        cls.GREEN = cls.RED = cls.YELLOW = cls.BLUE = cls.CYAN = cls.BOLD = cls.END = ''


class NetDBClient:
    """Simple client for NetDB RPC operations."""

    def __init__(self, server_url: str, auth_token: Optional[str] = None):
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self.session = requests.Session()
        if auth_token:
            self.session.headers["Authorization"] = f"Bearer {auth_token}"

    def reset_job(self, repo_id: str, job_id: str) -> dict:
        """Reset (delete) all data for a specific job."""
        response = self.session.post(
            f"{self.server_url}/api/rpc/job/reset",
            headers={
                "X-Repo-ID": repo_id,
                "X-Job-ID": job_id,
                "Content-Type": "application/json",
            },
            json={},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def health_check(self) -> bool:
        """Check if the server is reachable."""
        try:
            response = self.session.get(
                f"{self.server_url}/health",
                timeout=10,
            )
            return response.status_code == 200
        except Exception:
            return False


class NetDBIntegrationTestRunner:
    """Runs NetDB integration tests for pytest-ezmon."""

    # Use a test-specific repo to avoid polluting real data
    REPO_ID = "integration-tests/pytest-ezmon"

    def __init__(
        self,
        python_executable: str = sys.executable,
        verbose: bool = False,
        server_url: str = "http://localhost:8004",
        auth_token: Optional[str] = None,
    ):
        self.python = python_executable
        self.verbose = verbose
        self.server_url = server_url
        # Use provided token, env var, or default local testing token
        self.auth_token = (
            auth_token
            or os.environ.get("TESTMON_AUTH_TOKEN")
            or "ezmon-ci-test-token-2024"  # Default for local testing
        )
        self.temp_dir: Optional[Path] = None
        self.client = NetDBClient(server_url, self.auth_token)

        # Generate a unique job_id for this test run to ensure isolation
        self.run_id = f"test-{uuid.uuid4().hex[:8]}"

        # Track all job_ids used during this test run for cleanup
        self._used_job_ids: Set[str] = set()

    def log(self, msg: str, level: str = "info"):
        """Print a log message."""
        if level == "info":
            print(f"{Colors.BLUE}i{Colors.END} {msg}")
        elif level == "success":
            print(f"{Colors.GREEN}+{Colors.END} {msg}")
        elif level == "error":
            print(f"{Colors.RED}x{Colors.END} {msg}")
        elif level == "warning":
            print(f"{Colors.YELLOW}!{Colors.END} {msg}")
        elif level == "netdb":
            print(f"{Colors.CYAN}@{Colors.END} {msg}")
        elif level == "debug" and self.verbose:
            print(f"  {Colors.BOLD}->{Colors.END} {msg}")

    def get_job_id(self, scenario_name: str) -> str:
        """Get a unique job_id for a scenario."""
        return f"{self.run_id}-{scenario_name}"

    def check_server_connectivity(self) -> Tuple[bool, str]:
        """Check if the NetDB server is reachable."""
        try:
            if self.client.health_check():
                return True, f"Server reachable: {self.server_url}"
            else:
                return False, f"Server health check failed: {self.server_url}"
        except Exception as e:
            return False, f"Server connectivity error: {e}"

    def reset_job_data(self, job_id: str, retries: int = 3, quiet: bool = False) -> bool:
        """Reset all data for a job. Returns True if successful.

        Args:
            job_id: The job ID to reset
            retries: Number of retry attempts on failure
            quiet: If True, don't log success (used for cleanup)
        """
        last_error = None
        for attempt in range(retries):
            try:
                result = self.client.reset_job(self.REPO_ID, job_id)
                if result.get("success"):
                    if not quiet:
                        self.log(f"Reset job data: {job_id}", "netdb")
                    return True
                else:
                    last_error = f"Server returned: {result}"
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    # Job doesn't exist yet, that's fine
                    return True
                last_error = f"HTTP error: {e}"
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
            except Exception as e:
                last_error = f"Error: {e}"

            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff

        self.log(f"Failed to reset job {job_id} after {retries} attempts: {last_error}", "error")
        return False

    def setup_workspace(self) -> Path:
        """Create a temporary workspace with the sample project."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_netdb_integration_"))
        workspace = self.temp_dir / "sample_project"

        self.log(f"Creating workspace: {workspace}", "debug")
        shutil.copytree(SAMPLE_PROJECT, workspace)

        # Initialize git (ezmon uses git for file hashing optimization)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=workspace,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=workspace,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=workspace,
            capture_output=True,
        )

        return workspace

    def cleanup_workspace(self):
        """Remove temporary workspace."""
        if self.temp_dir and self.temp_dir.exists():
            self.log(f"Cleaning up: {self.temp_dir}", "debug")
            shutil.rmtree(self.temp_dir)

    def create_venv(self, workspace: Path) -> Path:
        """Create a virtual environment and install dependencies."""
        venv_path = workspace / ".venv"

        self.log("Creating virtual environment...", "debug")
        subprocess.run(
            [self.python, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=not self.verbose,
        )

        # Determine pip executable
        if sys.platform == "win32":
            pip = venv_path / "Scripts" / "pip"
            python_venv = venv_path / "Scripts" / "python"
        else:
            pip = venv_path / "bin" / "pip"
            python_venv = venv_path / "bin" / "python"

        # Upgrade pip
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip"],
            capture_output=not self.verbose,
        )

        # Install ezmon from local repo
        install_spec = str(REPO_ROOT) if is_valid_ezmon_repo(REPO_ROOT) else "pytest-ezmon"
        self.log(f"Installing pytest-ezmon from: {install_spec}", "debug")

        # Install ezmon and its dependencies
        result = subprocess.run(
            [str(pip), "install", install_spec, "requests"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            self.log(f"Failed to install pytest-ezmon: {result.stderr}", "error")
            raise RuntimeError(f"pip install failed: {result.stderr}")

        if self.verbose and result.stdout:
            print(result.stdout)

        return python_venv

    def run_pytest_ezmon(
        self,
        workspace: Path,
        python_venv: Path,
        job_id: str,
        extra_args: List[str] = None
    ) -> Tuple[int, str, str]:
        """Run pytest with ezmon in NetDB mode."""
        cmd = [
            str(python_venv), "-m", "pytest",
            "--ezmon",
            "-v",
            "--color=no",
            "--tb=short",
            "tests/",
        ]
        if extra_args:
            cmd.extend(extra_args)

        self.log(f"Running: {' '.join(cmd)}", "debug")

        # Set up NetDB environment
        test_env = {
            **os.environ,
            "PYTHONPATH": str(workspace),
            # Enable NetDB mode
            "TESTMON_NET_ENABLED": "true",
            "TESTMON_SERVER": self.server_url,
            "REPO_ID": self.REPO_ID,
            "JOB_ID": job_id,
            "RUN_ID": self.run_id,
        }
        if self.auth_token:
            test_env["TESTMON_AUTH_TOKEN"] = self.auth_token

        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            env=test_env,
        )

        if self.verbose:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

        return result.returncode, result.stdout, result.stderr

    def apply_modification(self, workspace: Path, mod: Modification):
        """Apply a single modification to the workspace."""
        file_path = workspace / mod.file

        if mod.action == "replace":
            content = file_path.read_text()
            if mod.target not in content:
                raise ValueError(f"Target string not found in {mod.file}: {mod.target}")
            new_content = content.replace(mod.target, mod.content, 1)
            file_path.write_text(new_content)
            self.log(f"Modified: {mod.file}", "debug")
            if self.verbose:
                self.log(f"  Changed '{mod.target[:50]}...' to '{mod.content[:50]}...'", "debug")

        elif mod.action == "append":
            with open(file_path, "a") as f:
                f.write(mod.content)
            self.log(f"Appended to: {mod.file}", "debug")

        elif mod.action == "create":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(mod.content)
            self.log(f"Created: {mod.file}", "debug")

        elif mod.action == "delete":
            file_path.unlink()
            self.log(f"Deleted: {mod.file}", "debug")

        # Force filesystem sync
        os.sync()
        time.sleep(0.1)

    def parse_test_results(self, stdout: str) -> Tuple[Set[str], int]:
        """
        Parse pytest output to determine which tests were selected/deselected.
        Returns (selected_tests, deselected_count).
        """
        selected_tests = set()
        deselected_count = 0

        for match in re.finditer(r'(tests/test_\w+\.py::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)', stdout):
            selected_tests.add(match.group(1))

        deselect_match = re.search(r'(\d+) deselected', stdout)
        if deselect_match:
            deselected_count = int(deselect_match.group(1))

        return selected_tests, deselected_count

    def run_scenario(self, scenario: Scenario) -> Tuple[bool, str]:
        """
        Run a single test scenario with NetDB.
        Returns (success, message).
        """
        job_id = self.get_job_id(scenario.name)
        # Track this job_id for global cleanup
        self._used_job_ids.add(job_id)

        self.log(f"Running scenario: {Colors.BOLD}{scenario.name}{Colors.END} (job: {job_id})")
        self.log(f"  {scenario.description}", "debug")

        try:
            # Reset any existing data for this job first
            self.reset_job_data(job_id)

            # Setup
            workspace = self.setup_workspace()
            python_venv = self.create_venv(workspace)

            # Initial run - build the ezmon database
            self.log("Running initial pytest --ezmon (building database)...", "debug")
            returncode, stdout, stderr = self.run_pytest_ezmon(workspace, python_venv, job_id)

            if returncode not in (0, 5):  # 0 = all passed, 5 = no tests collected
                return False, f"Initial test run failed: {stderr}"

            # Check for NetDB confirmation in output
            if "Using NetDB" not in stdout and "NetDB" not in stderr:
                self.log("Warning: NetDB mode may not be active", "warning")

            # Apply modifications
            for mod in scenario.modifications:
                self.apply_modification(workspace, mod)

            # Commit modifications so git-based file dependency tracking can detect changes
            if scenario.modifications:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=workspace,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"Apply modifications for {scenario.name}"],
                    cwd=workspace,
                    capture_output=True,
                )

            # Run again after modifications
            self.log("Running pytest --ezmon after modifications...", "debug")
            returncode, stdout, stderr = self.run_pytest_ezmon(workspace, python_venv, job_id)

            # Parse results
            selected_tests, deselected_count = self.parse_test_results(stdout)

            # Verify expectations
            errors = []

            for expected in scenario.expected_selected:
                if expected not in selected_tests:
                    errors.append(f"Expected {expected} to be SELECTED but it wasn't")

            if errors:
                self.log(f"Selected: {', '.join(sorted(selected_tests)) or 'none'}", "debug")

            for expected in scenario.expected_deselected:
                if expected in selected_tests:
                    errors.append(f"Expected {expected} to be DESELECTED but it was selected")

            # Special case: no changes scenario
            if not scenario.modifications and not scenario.expected_selected:
                if selected_tests:
                    errors.append(f"Expected no tests to run but got: {selected_tests}")

            if errors:
                return False, "; ".join(errors)

            selected_names = [t.split("::")[-1] for t in sorted(selected_tests)]
            return True, f"Selected: {selected_names or 'none'}, Deselected: {deselected_count}"

        finally:
            # Clean up workspace and remote data
            self.cleanup_workspace()
            # Reset job data to ensure idempotency
            self.reset_job_data(job_id)

    def cleanup_all_jobs(self) -> int:
        """Clean up all job data used during this test run.

        Returns the number of jobs that failed to clean up.
        """
        failed = 0
        if not self._used_job_ids:
            return 0

        self.log(f"Final cleanup: removing {len(self._used_job_ids)} job(s) from NetDB...", "debug")
        for job_id in self._used_job_ids:
            if not self.reset_job_data(job_id, retries=3, quiet=True):
                failed += 1

        if failed > 0:
            self.log(f"Failed to clean up {failed} job(s)", "warning")
        else:
            self.log(f"Cleaned up {len(self._used_job_ids)} job(s)", "debug")

        return failed

    def run_all_scenarios(self, scenario_filter: Optional[str] = None) -> bool:
        """Run all (or filtered) scenarios and report results."""
        scenarios_to_run = SCENARIOS

        if scenario_filter:
            if scenario_filter not in SCENARIOS:
                self.log(f"Unknown scenario: {scenario_filter}", "error")
                self.log(f"Available: {', '.join(SCENARIOS.keys())}")
                return False
            scenarios_to_run = {scenario_filter: SCENARIOS[scenario_filter]}

        # Check server connectivity first
        server_ok, server_msg = self.check_server_connectivity()
        if not server_ok:
            self.log(server_msg, "error")
            return False

        print(f"\n{Colors.BOLD}Running {len(scenarios_to_run)} NetDB integration test(s){Colors.END}\n")
        print(f"Python executable: {self.python}")
        print(f"NetDB server: {self.server_url}")
        print(f"Repo ID: {self.REPO_ID}")
        print(f"Run ID: {self.run_id}")
        print(f"Auth token: {'configured' if self.auth_token else 'NOT SET (may fail)'}")
        print()
        print("-" * 60)

        results = []
        try:
            for name, scenario in scenarios_to_run.items():
                success, message = self.run_scenario(scenario)
                results.append((name, success, message))

                if success:
                    self.log(f"{name}: {message}", "success")
                else:
                    self.log(f"{name}: {message}", "error")
                print()
        finally:
            # Always perform final cleanup of all jobs, even if tests are interrupted
            self.cleanup_all_jobs()

        # Summary
        print("-" * 60)
        passed = sum(1 for _, success, _ in results if success)
        failed = len(results) - passed

        if failed == 0:
            print(f"\n{Colors.GREEN}{Colors.BOLD}All {passed} NetDB scenario(s) passed!{Colors.END}")
            return True
        else:
            print(f"\n{Colors.RED}{Colors.BOLD}{failed}/{len(results)} NetDB scenario(s) failed{Colors.END}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="NetDB integration test runner for pytest-ezmon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--python", "-p",
        default=sys.executable,
        help="Python executable to use (default: current Python)",
    )
    parser.add_argument(
        "--server", "-s",
        default="http://localhost:8004",
        help="NetDB server URL (default: http://localhost:8004 for local testing)",
    )
    parser.add_argument(
        "--token", "-t",
        default="ezmon-ci-test-token-2024",  # Default CI token for local testing
        help="Auth token (default: local testing token)",
    )
    parser.add_argument(
        "--scenario",
        help="Run only this scenario (default: all)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available scenarios and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    args = parser.parse_args()

    if args.no_color:
        Colors.disable()

    if args.list:
        print(f"\n{Colors.BOLD}Available scenarios:{Colors.END}\n")
        for name, scenario in SCENARIOS.items():
            print(f"  {Colors.BLUE}{name}{Colors.END}")
            print(f"    {scenario.description}\n")
        return 0

    runner = NetDBIntegrationTestRunner(
        python_executable=args.python,
        verbose=args.verbose,
        server_url=args.server,
        auth_token=args.token,
    )

    success = runner.run_all_scenarios(args.scenario)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
