#!/usr/bin/env python
"""
Integration test runner for pytest-ezmon.

Usage:
    python run_integration_tests.py [OPTIONS]

Examples:
    # Run all scenarios with current Python
    python run_integration_tests.py

    # Run specific scenario
    python run_integration_tests.py --scenario modify_math_utils

    # Run with specific Python version (verifies version matches)
    python run_integration_tests.py --python python3.7

    # Install ezmon from PyPI instead of local source
    python run_integration_tests.py --ezmon-source pypi

    # List available scenarios
    python run_integration_tests.py --list

    # Verbose output
    python run_integration_tests.py -v
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Set, Tuple, Optional

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


def parse_python_version(version_string: str) -> Tuple[int, int]:
    """
    Parse a Python version string and return (major, minor).

    Examples:
        "Python 3.7.7" -> (3, 7)
        "3.10.12" -> (3, 10)
    """
    match = re.search(r'(\d+)\.(\d+)', version_string)
    if match:
        return int(match.group(1)), int(match.group(2))
    raise ValueError(f"Could not parse Python version from: {version_string}")


def get_python_version_tuple(python_executable: str) -> Tuple[int, int]:
    """Get the (major, minor) version tuple from a Python executable."""
    result = subprocess.run(
        [python_executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get Python version: {result.stderr}")
    version_str = result.stdout.strip()
    return parse_python_version(version_str)


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

    @classmethod
    def disable(cls):
        cls.GREEN = cls.RED = cls.YELLOW = cls.BLUE = cls.BOLD = cls.END = ''


class IntegrationTestRunner:
    """Runs integration tests for pytest-ezmon."""

    def __init__(
        self,
        python_executable: str = sys.executable,
        expected_version: Optional[Tuple[int, int]] = None,
        verbose: bool = False,
        ezmon_source: str = "auto",
    ):
        self.python = python_executable
        self.expected_version = expected_version
        self.verbose = verbose
        self.ezmon_source = ezmon_source
        self.temp_dir: Optional[Path] = None
        self.actual_version: Optional[Tuple[int, int]] = None

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
        elif level == "debug" and self.verbose:
            print(f"  {Colors.BOLD}->{Colors.END} {msg}")

    def verify_python_version(self) -> Tuple[bool, str]:
        """
        Verify that the Python interpreter version matches expected.
        Returns (success, message).
        """
        try:
            self.actual_version = get_python_version_tuple(self.python)
        except Exception as e:
            return False, f"Failed to get Python version: {e}"

        actual_str = f"{self.actual_version[0]}.{self.actual_version[1]}"

        if self.expected_version:
            expected_str = f"{self.expected_version[0]}.{self.expected_version[1]}"
            if self.actual_version != self.expected_version:
                return False, f"Version mismatch: expected {expected_str}, got {actual_str}"
            return True, f"Version verified: {actual_str}"

        return True, f"Version: {actual_str} (no specific version required)"

    def setup_workspace(self) -> Path:
        """Create a temporary workspace with the sample project."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_integration_"))
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

        # Verify venv Python version matches expected
        venv_version = get_python_version_tuple(str(python_venv))
        if self.expected_version and venv_version != self.expected_version:
            raise RuntimeError(
                f"Venv Python version mismatch: expected "
                f"{self.expected_version[0]}.{self.expected_version[1]}, "
                f"got {venv_version[0]}.{venv_version[1]}"
            )
        self.log(f"Venv Python version verified: {venv_version[0]}.{venv_version[1]}", "debug")

        # Upgrade pip
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip"],
            capture_output=not self.verbose,
        )

        # Determine ezmon installation source
        if self.ezmon_source == "auto":
            if is_valid_ezmon_repo(REPO_ROOT):
                install_spec = str(REPO_ROOT)
                self.log(f"Installing pytest-ezmon from local repo: {REPO_ROOT}", "debug")
            else:
                install_spec = "pytest-ezmon"
                self.log("Installing pytest-ezmon from PyPI (no local repo found)", "debug")
        elif self.ezmon_source == "pypi":
            install_spec = "pytest-ezmon"
            self.log("Installing pytest-ezmon from PyPI", "debug")
        else:
            # Assume it's a path
            install_spec = self.ezmon_source
            self.log(f"Installing pytest-ezmon from: {install_spec}", "debug")

        # Install ezmon and its dependencies
        result = subprocess.run(
            [str(pip), "install", install_spec, "requests", "networkx", "pyvis"],
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
        extra_args: List[str] = None
    ) -> Tuple[int, str, str]:
        """Run pytest with ezmon and return (returncode, stdout, stderr)."""
        cmd = [
            str(python_venv), "-m", "pytest",
            "--ezmon",
            "-v",
            "tests/",
        ]
        if extra_args:
            cmd.extend(extra_args)

        self.log(f"Running: {' '.join(cmd)}", "debug")

        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(workspace)},
        )

        if self.verbose:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

        return result.returncode, result.stdout, result.stderr

    def verify_pytest_python_version(self, stdout: str) -> Tuple[bool, str]:
        """
        Verify that pytest ran with the expected Python version.
        Parses the pytest header line like "platform darwin -- Python 3.7.7, pytest-7.4.4"
        """
        match = re.search(r'Python (\d+)\.(\d+)\.(\d+)', stdout)
        if not match:
            return False, "Could not find Python version in pytest output"

        pytest_version = (int(match.group(1)), int(match.group(2)))

        if self.expected_version and pytest_version != self.expected_version:
            return False, (
                f"Pytest Python version mismatch: expected "
                f"{self.expected_version[0]}.{self.expected_version[1]}, "
                f"got {pytest_version[0]}.{pytest_version[1]}"
            )

        return True, f"Pytest Python: {pytest_version[0]}.{pytest_version[1]}"

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
            # Debug: show what changed
            if self.verbose:
                self.log(f"  Changed '{mod.target}' to '{mod.content}'", "debug")

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

    def parse_test_results(self, stdout: str) -> Tuple[Set[str], int]:
        """
        Parse pytest output to determine which tests were selected/deselected.
        Returns (selected_files, deselected_count).
        """
        selected_files = set()
        deselected_count = 0

        # Match test results like "tests/test_math_utils.py::TestAdd::test_positive_numbers PASSED"
        for match in re.finditer(r'tests/(test_\w+\.py)::\S+\s+(PASSED|FAILED|ERROR|SKIPPED)', stdout):
            selected_files.add(match.group(1))

        # Match deselected count: "X deselected"
        deselect_match = re.search(r'(\d+) deselected', stdout)
        if deselect_match:
            deselected_count = int(deselect_match.group(1))

        return selected_files, deselected_count

    def run_scenario(self, scenario: Scenario) -> Tuple[bool, str]:
        """
        Run a single test scenario.
        Returns (success, message).
        """
        self.log(f"Running scenario: {Colors.BOLD}{scenario.name}{Colors.END}")
        self.log(f"  {scenario.description}", "debug")

        try:
            # Setup
            workspace = self.setup_workspace()
            python_venv = self.create_venv(workspace)

            # Initial run - build the ezmon database
            self.log("Running initial pytest --ezmon (building database)...", "debug")
            returncode, stdout, stderr = self.run_pytest_ezmon(workspace, python_venv)

            if returncode not in (0, 5):  # 0 = all passed, 5 = no tests collected
                return False, f"Initial test run failed: {stderr}"

            # Verify pytest used correct Python version
            version_ok, version_msg = self.verify_pytest_python_version(stdout)
            if not version_ok:
                return False, version_msg
            self.log(version_msg, "debug")

            # Apply modifications
            for mod in scenario.modifications:
                self.apply_modification(workspace, mod)

            # Run again after modifications
            self.log("Running pytest --ezmon after modifications...", "debug")
            returncode, stdout, stderr = self.run_pytest_ezmon(workspace, python_venv)

            # Parse results
            selected_files, deselected_count = self.parse_test_results(stdout)

            # Verify expectations
            errors = []

            # Check expected selected
            for expected in scenario.expected_selected:
                if expected not in selected_files:
                    errors.append(f"Expected {expected} to be SELECTED but it wasn't")

            if errors:
                self.log(f"Selected: {', '.join(sorted(selected_files)) or 'none'}", "debug")

            # Check expected deselected
            for expected in scenario.expected_deselected:
                if expected in selected_files:
                    errors.append(f"Expected {expected} to be DESELECTED but it was selected")

            # Special case: no changes scenario
            if not scenario.modifications and not scenario.expected_selected:
                if selected_files:
                    errors.append(f"Expected no tests to run but got: {selected_files}")

            if errors:
                return False, "; ".join(errors)

            return True, f"Selected: {sorted(selected_files) or 'none'}, Deselected: {deselected_count}"

        finally:
            self.cleanup_workspace()

    def get_python_version(self) -> str:
        """Get the exact Python version from the executable."""
        try:
            result = subprocess.run(
                [self.python, "--version"],
                capture_output=True,
                text=True,
            )
            # Output is like "Python 3.10.12"
            return result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return f"Unknown ({e})"

    def run_all_scenarios(self, scenario_filter: Optional[str] = None) -> bool:
        """Run all (or filtered) scenarios and report results."""
        scenarios_to_run = SCENARIOS

        if scenario_filter:
            if scenario_filter not in SCENARIOS:
                self.log(f"Unknown scenario: {scenario_filter}", "error")
                self.log(f"Available: {', '.join(SCENARIOS.keys())}")
                return False
            scenarios_to_run = {scenario_filter: SCENARIOS[scenario_filter]}

        # Verify Python version before running any scenarios
        version_ok, version_msg = self.verify_python_version()
        if not version_ok:
            self.log(version_msg, "error")
            return False

        python_version = self.get_python_version()

        print(f"\n{Colors.BOLD}Running {len(scenarios_to_run)} integration test(s){Colors.END}\n")
        print(f"Python executable: {self.python}")
        print(f"Python version: {python_version}")
        if self.expected_version:
            print(f"Expected version: {self.expected_version[0]}.{self.expected_version[1]} {Colors.GREEN}(verified){Colors.END}")
        print(f"Ezmon source: {self.ezmon_source}")
        if self.ezmon_source == "auto" and is_valid_ezmon_repo(REPO_ROOT):
            print(f"  (detected local repo: {REPO_ROOT})")
        print()
        print("-" * 60)

        results = []
        for name, scenario in scenarios_to_run.items():
            success, message = self.run_scenario(scenario)
            results.append((name, success, message))

            if success:
                self.log(f"{name}: {message}", "success")
            else:
                self.log(f"{name}: {message}", "error")
            print()

        # Summary
        print("-" * 60)
        passed = sum(1 for _, success, _ in results if success)
        failed = len(results) - passed

        if failed == 0:
            print(f"\n{Colors.GREEN}{Colors.BOLD}All {passed} scenario(s) passed!{Colors.END}")
            return True
        else:
            print(f"\n{Colors.RED}{Colors.BOLD}{failed}/{len(results)} scenario(s) failed{Colors.END}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Integration test runner for pytest-ezmon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--python", "-p",
        default=sys.executable,
        help="Python executable to use (default: current Python)",
    )
    parser.add_argument(
        "--expect-version",
        help="Expected Python version (e.g., '3.7'). Test fails if version doesn't match.",
    )
    parser.add_argument(
        "--scenario", "-s",
        help="Run only this scenario (default: all)",
    )
    parser.add_argument(
        "--ezmon-source", "-e",
        default="auto",
        help="Where to install ezmon from: 'auto' (detect local repo or use PyPI), "
             "'pypi' (always use PyPI), or a path to a local repo (default: auto)",
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

    # Parse expected version if provided
    expected_version = None
    if args.expect_version:
        try:
            expected_version = parse_python_version(args.expect_version)
        except ValueError as e:
            print(f"{Colors.RED}Error: {e}{Colors.END}")
            return 1

    runner = IntegrationTestRunner(
        python_executable=args.python,
        expected_version=expected_version,
        verbose=args.verbose,
        ezmon_source=args.ezmon_source,
    )

    success = runner.run_all_scenarios(args.scenario)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
