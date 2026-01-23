#!/usr/bin/env python
"""
Test all integration scenarios across all supported Python versions.

This script discovers available Python interpreters and runs the full
integration test suite against each one.

Usage:
    python test_all_versions.py [OPTIONS]

Examples:
    # Run all scenarios on all available Python versions
    python test_all_versions.py

    # Run only on specific versions
    python test_all_versions.py --versions 3.7,3.10,3.11

    # Run specific scenario on all versions
    python test_all_versions.py --scenario modify_math_utils

    # Verbose output
    python test_all_versions.py -v

    # List available Python versions without running tests
    python test_all_versions.py --list-versions
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()

# Supported Python versions (major.minor)
SUPPORTED_VERSIONS = [
    (3, 7),
    (3, 8),
    (3, 9),
    (3, 10),
    (3, 11),
    (3, 12),
    (3, 13),
]


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


def find_python_executable(version: Tuple[int, int]) -> Optional[str]:
    """
    Find a Python executable for the given version.
    Returns the path to the executable or None if not found.
    """
    major, minor = version
    candidates = [
        f"python{major}.{minor}",
        f"python{major}{minor}",
    ]

    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            # Verify it's actually the right version
            try:
                result = subprocess.run(
                    [path, "-c", f"import sys; assert sys.version_info[:2] == ({major}, {minor})"],
                    capture_output=True,
                )
                if result.returncode == 0:
                    return path
            except Exception:
                pass

    return None


def get_actual_version(python_path: str) -> str:
    """Get the full version string from a Python executable."""
    try:
        result = subprocess.run(
            [python_path, "--version"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or result.stderr.strip()
    except Exception:
        return "Unknown"


def discover_available_pythons(versions: List[Tuple[int, int]]) -> List[Tuple[Tuple[int, int], str]]:
    """
    Discover which Python versions are available.
    Returns list of ((major, minor), executable_path) tuples.
    """
    available = []
    for version in versions:
        path = find_python_executable(version)
        if path:
            available.append((version, path))
    return available


def run_integration_tests(
    python_path: str,
    expected_version: Tuple[int, int],
    scenario: Optional[str] = None,
    verbose: bool = False,
) -> Tuple[bool, str]:
    """
    Run integration tests with a specific Python version.
    Returns (success, output_summary).
    """
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "run_integration_tests.py"),
        "--python", python_path,
        "--expect-version", f"{expected_version[0]}.{expected_version[1]}",
    ]

    if scenario:
        cmd.extend(["--scenario", scenario])

    if verbose:
        cmd.append("--verbose")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    # Extract summary from output
    output = result.stdout + result.stderr
    if "All" in output and "passed" in output:
        # Find the summary line
        for line in output.split('\n'):
            if "passed" in line or "failed" in line:
                summary = line.strip()
                break
        else:
            summary = "Completed"
    else:
        summary = output.split('\n')[-2] if output else "No output"

    return result.returncode == 0, summary


def main():
    parser = argparse.ArgumentParser(
        description="Test all integration scenarios across all supported Python versions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--versions",
        help="Comma-separated list of Python versions to test (e.g., '3.7,3.10,3.11'). "
             "Default: all supported versions that are available.",
    )
    parser.add_argument(
        "--scenario", "-s",
        help="Run only this scenario on each Python version",
    )
    parser.add_argument(
        "--list-versions",
        action="store_true",
        help="List available Python versions and exit",
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

    # Parse requested versions or use all supported
    if args.versions:
        requested_versions = []
        for v in args.versions.split(','):
            parts = v.strip().split('.')
            if len(parts) >= 2:
                requested_versions.append((int(parts[0]), int(parts[1])))
    else:
        requested_versions = SUPPORTED_VERSIONS

    # Discover available Python interpreters
    available = discover_available_pythons(requested_versions)

    if args.list_versions:
        print(f"\n{Colors.BOLD}Supported Python versions:{Colors.END}\n")
        for version in SUPPORTED_VERSIONS:
            version_str = f"{version[0]}.{version[1]}"
            path = find_python_executable(version)
            if path:
                actual = get_actual_version(path)
                print(f"  {Colors.GREEN}+{Colors.END} {version_str}: {path} ({actual})")
            else:
                print(f"  {Colors.RED}x{Colors.END} {version_str}: not found")
        print()
        return 0

    if not available:
        print(f"{Colors.RED}Error: No supported Python versions found!{Colors.END}")
        print(f"Looked for: {', '.join(f'{v[0]}.{v[1]}' for v in requested_versions)}")
        return 1

    # Print header
    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.END}")
    print(f"{Colors.BOLD}Testing ezmon integration across {len(available)} Python version(s){Colors.END}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.END}\n")

    print(f"{Colors.CYAN}Available Python versions:{Colors.END}")
    for version, path in available:
        actual = get_actual_version(path)
        print(f"  - {version[0]}.{version[1]}: {path} ({actual})")
    print()

    if args.scenario:
        print(f"{Colors.CYAN}Running scenario:{Colors.END} {args.scenario}\n")

    # Run tests for each version
    results = []
    for version, path in available:
        version_str = f"{version[0]}.{version[1]}"
        print(f"{Colors.BOLD}{'─' * 70}{Colors.END}")
        print(f"{Colors.BLUE}Testing Python {version_str}{Colors.END}")
        print(f"{Colors.BOLD}{'─' * 70}{Colors.END}")

        success, summary = run_integration_tests(
            python_path=path,
            expected_version=version,
            scenario=args.scenario,
            verbose=args.verbose,
        )

        results.append((version, success, summary))

        if success:
            print(f"{Colors.GREEN}+ Python {version_str}: PASSED{Colors.END}")
        else:
            print(f"{Colors.RED}x Python {version_str}: FAILED{Colors.END}")
            if args.verbose:
                print(f"  {summary}")
        print()

    # Print summary
    print(f"{Colors.BOLD}{'=' * 70}{Colors.END}")
    print(f"{Colors.BOLD}SUMMARY{Colors.END}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.END}\n")

    passed = sum(1 for _, success, _ in results if success)
    failed = len(results) - passed

    for version, success, summary in results:
        version_str = f"{version[0]}.{version[1]}"
        if success:
            print(f"  {Colors.GREEN}+{Colors.END} Python {version_str}: PASSED")
        else:
            print(f"  {Colors.RED}x{Colors.END} Python {version_str}: FAILED")

    print()
    if failed == 0:
        print(f"{Colors.GREEN}{Colors.BOLD}All {passed} Python version(s) passed!{Colors.END}")
        return 0
    else:
        print(f"{Colors.RED}{Colors.BOLD}{failed}/{len(results)} Python version(s) failed{Colors.END}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
