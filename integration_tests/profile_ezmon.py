#!/usr/bin/env python
"""
Profile pytest-ezmon execution to identify performance bottlenecks.

This script runs pytest with ezmon on the sample project while profiling,
then reports where time is spent.

Usage:
    python profile_ezmon.py                    # Profile with cProfile
    python profile_ezmon.py --runs 3           # Multiple runs for averaging
    python profile_ezmon.py --compare          # Compare all versions
"""

import argparse
import cProfile
import os
import pstats
import re
import shutil
import subprocess
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"

# Original repo for comparison (the fork with coverage)
ORIGINAL_REPO = REPO_ROOT.parent / "pytest-testmon"


def setup_workspace(temp_dir: Path) -> Path:
    """Copy sample project to temp workspace."""
    workspace = temp_dir / "workspace"
    shutil.copytree(SAMPLE_PROJECT, workspace)

    # Remove any existing .testmondata
    testmondata = workspace / ".testmondata"
    if testmondata.exists():
        testmondata.unlink()

    return workspace


def setup_venv_bare(temp_dir: Path) -> Path:
    """Create a venv with just pytest (no testmon/ezmon)."""
    venv_dir = temp_dir / "venv"

    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )

    if sys.platform == "win32":
        python = venv_dir / "Scripts" / "python.exe"
        pip = venv_dir / "Scripts" / "pip.exe"
    else:
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"

    subprocess.run(
        [str(pip), "install", "--upgrade", "pip"],
        capture_output=True,
    )
    subprocess.run(
        [str(pip), "install", "pytest"],
        check=True,
        capture_output=True,
    )

    return python


def setup_venv_pypi_testmon(temp_dir: Path) -> Path:
    """Create a venv with pytest-testmon from PyPI."""
    venv_dir = temp_dir / "venv"

    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )

    if sys.platform == "win32":
        python = venv_dir / "Scripts" / "python.exe"
        pip = venv_dir / "Scripts" / "pip.exe"
    else:
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"

    subprocess.run(
        [str(pip), "install", "--upgrade", "pip"],
        capture_output=True,
    )
    subprocess.run(
        [str(pip), "install", "pytest-testmon"],
        check=True,
        capture_output=True,
    )

    return python


def setup_venv(temp_dir: Path, repo_root: Path) -> Path:
    """Create a venv and install ezmon from the given repo."""
    venv_dir = temp_dir / "venv"

    # Create venv
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )

    # Get paths
    if sys.platform == "win32":
        python = venv_dir / "Scripts" / "python.exe"
        pip = venv_dir / "Scripts" / "pip.exe"
    else:
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"

    # Install ezmon
    subprocess.run(
        [str(pip), "install", "--upgrade", "pip"],
        capture_output=True,
    )
    subprocess.run(
        [str(pip), "install", str(repo_root)],
        check=True,
        capture_output=True,
    )

    return python


def parse_pytest_output(stdout: str) -> dict:
    """Parse pytest output to extract test selection details."""
    info = {
        "collected": 0,
        "selected": 0,
        "deselected": 0,
        "passed": 0,
        "failed": 0,
        "selected_tests": [],
        "deselected_tests": [],
    }

    lines = stdout.split("\n")

    # Parse collection line: "collected X items" or "collected X items / Y deselected"
    for line in lines:
        if "collected" in line.lower():
            m = re.search(r"collected (\d+) items?", line)
            if m:
                info["collected"] = int(m.group(1))
            m = re.search(r"(\d+) deselected", line)
            if m:
                info["deselected"] = int(m.group(1))

    # Parse test results
    for line in lines:
        # Match lines like "tests/test_math.py::test_add PASSED"
        m = re.match(r"(tests/\S+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)", line)
        if m:
            test_name = m.group(1)
            result = m.group(2)
            info["selected_tests"].append(test_name)
            if result == "PASSED":
                info["passed"] += 1
            elif result == "FAILED":
                info["failed"] += 1

    info["selected"] = len(info["selected_tests"])

    # Parse summary line like "====== 5 passed in 0.12s ======"
    for line in lines:
        m = re.search(r"(\d+) passed", line)
        if m and info["passed"] == 0:
            info["passed"] = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m and info["failed"] == 0:
            info["failed"] = int(m.group(1))

    return info


def run_pytest_with_timing(
    python: Path,
    workspace: Path,
    label: str,
    use_testmon: bool = True,
    testmon_flag: str = "--ezmon",
) -> dict:
    """Run pytest and return timing information."""
    env = {
        **os.environ,
        "PYTHONPATH": str(workspace),
        "TESTMON_NET_ENABLED": "false",
    }

    if use_testmon:
        cmd = [str(python), "-m", "pytest", testmon_flag, "-v", "tests/"]
    else:
        cmd = [str(python), "-m", "pytest", "-v", "tests/"]

    # Time the run
    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=env,
    )
    elapsed = time.perf_counter() - start

    # Parse output
    parsed = parse_pytest_output(result.stdout)

    return {
        "label": label,
        "elapsed": elapsed,
        "collected": parsed["collected"],
        "selected": parsed["selected"],
        "deselected": parsed["deselected"],
        "passed": parsed["passed"],
        "failed": parsed["failed"],
        "selected_tests": parsed["selected_tests"],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_pytest_with_profiling(
    python: Path,
    workspace: Path,
    profile_output: Path,
) -> dict:
    """Run pytest with cProfile and return profile stats."""
    env = {
        **os.environ,
        "PYTHONPATH": str(workspace),
        "TESTMON_NET_ENABLED": "false",
    }

    cmd = [
        str(python), "-m", "cProfile",
        "-o", str(profile_output),
        "-m", "pytest", "--ezmon", "-v", "tests/",
    ]

    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=env,
    )
    elapsed = time.perf_counter() - start

    return {
        "elapsed": elapsed,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "profile_file": profile_output,
    }


def print_profile_stats(profile_file: Path, top_n: int = 30):
    """Print formatted profile statistics."""
    stats = pstats.Stats(str(profile_file))

    # Create a string buffer to capture output
    output = StringIO()
    stats.stream = output

    print("\n" + "=" * 80)
    print("TOP FUNCTIONS BY CUMULATIVE TIME")
    print("=" * 80)
    stats.sort_stats("cumtime")
    stats.print_stats(top_n)
    print(output.getvalue())

    # Filter to just ezmon functions
    output = StringIO()
    stats.stream = output
    print("\n" + "=" * 80)
    print("EZMON-SPECIFIC FUNCTIONS (by cumulative time)")
    print("=" * 80)
    stats.sort_stats("cumtime")
    stats.print_stats("ezmon", top_n)
    print(output.getvalue())

    # Show callers of expensive functions
    output = StringIO()
    stats.stream = output
    print("\n" + "=" * 80)
    print("COVERAGE.PY FUNCTIONS (if present)")
    print("=" * 80)
    stats.sort_stats("cumtime")
    stats.print_stats("coverage", 15)
    print(output.getvalue())


def run_comparison(runs: int = 3):
    """Compare performance between all versions."""
    print("=" * 80)
    print("PERFORMANCE COMPARISON")
    print("=" * 80)
    print("Versions:")
    print("  - bare: pytest only (no testmon plugin)")
    print("  - pypi: pytest-testmon from PyPI (original upstream)")
    print("  - ezmon: ezmon fork with coverage (local)")
    print("  - nocov: ezmon fork WITHOUT coverage (this experiment)")
    print("=" * 80)

    results = {"bare": [], "pypi": [], "ezmon": [], "nocov": []}

    # 1. Bare pytest (no testmon)
    print("\n[1/4] Setting up BARE pytest (no testmon)...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace = setup_workspace(temp_path)
        python = setup_venv_bare(temp_path)

        for run_num in range(runs):
            print(f"  Run {run_num + 1}/{runs}...")
            result = run_pytest_with_timing(
                python, workspace, "bare", use_testmon=False
            )
            results["bare"].append(("run", result))
            if run_num == 0:
                print(f"    Tests run: {result['selected']}")
                print(f"    Selected: {', '.join(result['selected_tests'][:5])}...")

    # 2. PyPI pytest-testmon
    print("\n[2/4] Setting up PYPI pytest-testmon...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace = setup_workspace(temp_path)
        python = setup_venv_pypi_testmon(temp_path)

        for run_num in range(runs):
            # Cold start
            testmondata = workspace / ".testmondata"
            if testmondata.exists():
                testmondata.unlink()

            print(f"  Run {run_num + 1}/{runs} - First (cold)...")
            first = run_pytest_with_timing(
                python, workspace, "pypi_first",
                use_testmon=True, testmon_flag="--testmon"
            )
            results["pypi"].append(("first", first))
            if run_num == 0:
                print(f"    Collected: {first['collected']}, Selected: {first['selected']}, Deselected: {first['deselected']}")
                if first['selected_tests']:
                    print(f"    Ran: {', '.join(first['selected_tests'][:3])}...")

            # Warm (no changes)
            print(f"  Run {run_num + 1}/{runs} - Second (warm)...")
            second = run_pytest_with_timing(
                python, workspace, "pypi_second",
                use_testmon=True, testmon_flag="--testmon"
            )
            results["pypi"].append(("second", second))
            if run_num == 0:
                print(f"    Collected: {second['collected']}, Selected: {second['selected']}, Deselected: {second['deselected']}")

            # After modification - make an ACTUAL code change (not just comment)
            # so both coverage-based and AST-based detection will catch it
            utils_file = workspace / "src" / "math_utils.py"
            content = utils_file.read_text()
            # Change the actual return value to trigger detection
            new_content = content.replace("return a + b", "return a + b + 0")
            utils_file.write_text(new_content)
            time.sleep(0.2)  # Ensure mtime updates

            print(f"  Run {run_num + 1}/{runs} - Third (modified)...")
            third = run_pytest_with_timing(
                python, workspace, "pypi_modified",
                use_testmon=True, testmon_flag="--testmon"
            )
            results["pypi"].append(("modified", third))
            if run_num == 0:
                print(f"    Collected: {third['collected']}, Selected: {third['selected']}, Deselected: {third['deselected']}")
                if third['selected_tests']:
                    print(f"    Ran: {', '.join(third['selected_tests'])}")

    # 3. ezmon fork (with coverage)
    if ORIGINAL_REPO.exists():
        print(f"\n[3/4] Setting up EZMON fork (with coverage) from {ORIGINAL_REPO}...")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace = setup_workspace(temp_path)
            python = setup_venv(temp_path, ORIGINAL_REPO)

            for run_num in range(runs):
                testmondata = workspace / ".testmondata"
                if testmondata.exists():
                    testmondata.unlink()

                print(f"  Run {run_num + 1}/{runs} - First (cold)...")
                first = run_pytest_with_timing(python, workspace, "ezmon_first")
                results["ezmon"].append(("first", first))
                if run_num == 0:
                    print(f"    Collected: {first['collected']}, Selected: {first['selected']}, Deselected: {first['deselected']}")

                print(f"  Run {run_num + 1}/{runs} - Second (warm)...")
                second = run_pytest_with_timing(python, workspace, "ezmon_second")
                results["ezmon"].append(("second", second))
                if run_num == 0:
                    print(f"    Collected: {second['collected']}, Selected: {second['selected']}, Deselected: {second['deselected']}")

                utils_file = workspace / "src" / "math_utils.py"
                content = utils_file.read_text()
                new_content = content.replace("return a + b", "return a + b + 0")
                utils_file.write_text(new_content)
                time.sleep(0.2)

                print(f"  Run {run_num + 1}/{runs} - Third (modified)...")
                third = run_pytest_with_timing(python, workspace, "ezmon_modified")
                results["ezmon"].append(("modified", third))
                if run_num == 0:
                    print(f"    Collected: {third['collected']}, Selected: {third['selected']}, Deselected: {third['deselected']}")
                    if third['selected_tests']:
                        print(f"    Ran: {', '.join(third['selected_tests'])}")
    else:
        print(f"\n[3/4] SKIPPING ezmon fork - not found at {ORIGINAL_REPO}")

    # 4. nocov version
    print(f"\n[4/4] Setting up NOCOV version from {REPO_ROOT}...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace = setup_workspace(temp_path)
        python = setup_venv(temp_path, REPO_ROOT)

        for run_num in range(runs):
            testmondata = workspace / ".testmondata"
            if testmondata.exists():
                testmondata.unlink()

            print(f"  Run {run_num + 1}/{runs} - First (cold)...")
            first = run_pytest_with_timing(python, workspace, "nocov_first")
            results["nocov"].append(("first", first))
            if run_num == 0:
                print(f"    Collected: {first['collected']}, Selected: {first['selected']}, Deselected: {first['deselected']}")

            print(f"  Run {run_num + 1}/{runs} - Second (warm)...")
            second = run_pytest_with_timing(python, workspace, "nocov_second")
            results["nocov"].append(("second", second))
            if run_num == 0:
                print(f"    Collected: {second['collected']}, Selected: {second['selected']}, Deselected: {second['deselected']}")

            utils_file = workspace / "src" / "math_utils.py"
            content = utils_file.read_text()
            new_content = content.replace("return a + b", "return a + b + 0")
            utils_file.write_text(new_content)
            time.sleep(0.2)

            print(f"  Run {run_num + 1}/{runs} - Third (modified)...")
            third = run_pytest_with_timing(python, workspace, "nocov_modified")
            results["nocov"].append(("modified", third))
            if run_num == 0:
                print(f"    Collected: {third['collected']}, Selected: {third['selected']}, Deselected: {third['deselected']}")
                if third['selected_tests']:
                    print(f"    Ran: {', '.join(third['selected_tests'])}")

    # Print detailed comparison
    print("\n" + "=" * 80)
    print("TIMING RESULTS (averaged over {} runs)".format(runs))
    print("=" * 80)

    # Compute averages
    def avg_times(result_list, run_type):
        times = [d["elapsed"] for t, d in result_list if t == run_type]
        return sum(times) / len(times) if times else None

    def avg_selected(result_list, run_type):
        selected = [d["selected"] for t, d in result_list if t == run_type]
        return sum(selected) / len(selected) if selected else None

    def avg_deselected(result_list, run_type):
        deselected = [d["deselected"] for t, d in result_list if t == run_type]
        return sum(deselected) / len(deselected) if deselected else None

    print("\n{:<12} {:>10} {:>10} {:>10} {:>10}".format(
        "Version", "First(s)", "Warm(s)", "Modified(s)", "Baseline"
    ))
    print("-" * 54)

    bare_time = avg_times(results["bare"], "run")
    print("{:<12} {:>10} {:>10} {:>10} {:>10}".format(
        "bare",
        "-",
        "-",
        "-",
        f"{bare_time:.3f}" if bare_time else "N/A"
    ))

    for version in ["pypi", "ezmon", "nocov"]:
        if not results[version]:
            continue
        first = avg_times(results[version], "first")
        second = avg_times(results[version], "second")
        modified = avg_times(results[version], "modified")
        print("{:<12} {:>10} {:>10} {:>10} {:>10}".format(
            version,
            f"{first:.3f}" if first else "N/A",
            f"{second:.3f}" if second else "N/A",
            f"{modified:.3f}" if modified else "N/A",
            "-"
        ))

    print("\n" + "=" * 80)
    print("TEST SELECTION (from first run of each)")
    print("=" * 80)
    print("\n{:<12} {:>10} {:>10} {:>12} {:>12} {:>12}".format(
        "Version", "Run Type", "Collected", "Selected", "Deselected", "Correct?"
    ))
    print("-" * 70)

    # Get first run data for each version
    for version in ["bare", "pypi", "ezmon", "nocov"]:
        if not results[version]:
            continue

        for run_type in ["run", "first", "second", "modified"]:
            matches = [(t, d) for t, d in results[version] if t == run_type]
            if matches:
                _, data = matches[0]  # First occurrence
                collected = data.get("collected", 0)
                selected = data.get("selected", 0)
                deselected = data.get("deselected", 0)

                # Check correctness
                if run_type in ["run", "first"]:
                    correct = "✓ (all)" if selected > 0 or deselected == 0 else "?"
                elif run_type == "second":
                    correct = "✓ (none)" if selected == 0 or deselected > 0 else "?"
                else:  # modified
                    correct = "✓ (some)" if 0 < selected < collected or deselected > 0 else "?"

                print("{:<12} {:>10} {:>10} {:>12} {:>12} {:>12}".format(
                    version if run_type in ["run", "first"] else "",
                    run_type,
                    collected,
                    selected,
                    deselected,
                    correct
                ))

    # Show which tests were selected after modification
    print("\n" + "=" * 80)
    print("TESTS SELECTED AFTER MODIFICATION (should be tests using math_utils)")
    print("=" * 80)

    for version in ["pypi", "ezmon", "nocov"]:
        if not results[version]:
            continue
        modified_runs = [(t, d) for t, d in results[version] if t == "modified"]
        if modified_runs:
            _, data = modified_runs[0]
            tests = data.get("selected_tests", [])
            print(f"\n{version}:")
            if tests:
                for t in tests:
                    print(f"  - {t}")
            else:
                print("  (no tests selected - check if deselected count is high)")
                print(f"  stdout snippet: {data['stdout'][:500]}...")

    # Speedup comparison
    print("\n" + "=" * 80)
    print("SPEEDUP vs BARE PYTEST")
    print("=" * 80)

    if bare_time:
        print(f"\nBaseline (bare pytest, no testmon): {bare_time:.3f}s")
        print("\nOverhead on FIRST run (cold start, building DB):")
        for version in ["pypi", "ezmon", "nocov"]:
            if results[version]:
                first = avg_times(results[version], "first")
                if first:
                    overhead = first - bare_time
                    pct = (overhead / bare_time) * 100
                    print(f"  {version:<8}: {first:.3f}s ({overhead:+.3f}s, {pct:+.0f}%)")

        print("\nOverhead on WARM run (no changes, should skip tests):")
        for version in ["pypi", "ezmon", "nocov"]:
            if results[version]:
                second = avg_times(results[version], "second")
                if second:
                    overhead = second - bare_time
                    pct = (overhead / bare_time) * 100
                    savings = bare_time - second
                    print(f"  {version:<8}: {second:.3f}s ({overhead:+.3f}s, {pct:+.0f}%) - saves {savings:.3f}s vs running all")

    # Compare nocov vs ezmon
    if results["ezmon"] and results["nocov"]:
        print("\n" + "=" * 80)
        print("NOCOV vs EZMON (coverage removal impact)")
        print("=" * 80)

        for run_type in ["first", "second", "modified"]:
            ezmon_time = avg_times(results["ezmon"], run_type)
            nocov_time = avg_times(results["nocov"], run_type)
            if ezmon_time and nocov_time:
                speedup = ezmon_time / nocov_time
                saved = ezmon_time - nocov_time
                print(f"  {run_type:<10}: {speedup:.2f}x faster ({saved:.3f}s saved)")


def measure_tracker_overhead(runs: int = 3):
    """Measure the overhead of the import tracker specifically."""
    print("=" * 80)
    print("IMPORT TRACKER OVERHEAD MEASUREMENT")
    print("=" * 80)
    print("""
This measures the overhead added by the DependencyTracker's hooks into:
- builtins.__import__ (tracks all Python imports)
- builtins.open (tracks file reads)
- importlib.import_module (tracks dynamic imports)
""")

    # We'll compare nocov (with tracker) against bare pytest
    # and look at the cProfile data for tracker-specific functions

    results = {}

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace = setup_workspace(temp_path)

        # 1. Bare pytest (no plugin, no tracker)
        print("\n[1/3] Bare pytest (no plugin)...")
        python_bare = setup_venv_bare(temp_path / "bare")
        bare_times = []
        for i in range(runs):
            result = run_pytest_with_timing(
                python_bare, workspace, "bare", use_testmon=False
            )
            bare_times.append(result["elapsed"])
            print(f"  Run {i+1}: {result['elapsed']:.3f}s")
        results["bare"] = sum(bare_times) / len(bare_times)

        # 2. nocov with tracker (current implementation)
        print("\n[2/3] nocov with import tracker...")
        python_nocov = setup_venv(temp_path / "nocov", REPO_ROOT)

        # Remove .testmondata for fresh start each time
        testmondata = workspace / ".testmondata"

        nocov_first_times = []
        nocov_second_times = []
        for i in range(runs):
            if testmondata.exists():
                testmondata.unlink()

            # First run (cold)
            result = run_pytest_with_timing(
                python_nocov, workspace, "nocov_first"
            )
            nocov_first_times.append(result["elapsed"])
            print(f"  Run {i+1} first: {result['elapsed']:.3f}s")

            # Second run (warm)
            result = run_pytest_with_timing(
                python_nocov, workspace, "nocov_second"
            )
            nocov_second_times.append(result["elapsed"])
            print(f"  Run {i+1} second: {result['elapsed']:.3f}s")

        results["nocov_first"] = sum(nocov_first_times) / len(nocov_first_times)
        results["nocov_second"] = sum(nocov_second_times) / len(nocov_second_times)

        # 3. Profile nocov to get detailed tracker timing
        print("\n[3/3] Detailed profiling of import tracker...")
        if testmondata.exists():
            testmondata.unlink()

        profile_file = temp_path / "tracker_profile.prof"
        env = {
            **os.environ,
            "PYTHONPATH": str(workspace),
            "TESTMON_NET_ENABLED": "false",
        }
        cmd = [
            str(python_nocov), "-m", "cProfile",
            "-o", str(profile_file),
            "-m", "pytest", "--ezmon", "-v", "tests/",
        ]
        subprocess.run(cmd, cwd=workspace, capture_output=True, env=env)

        # Analyze profile
        stats = pstats.Stats(str(profile_file))

        print("\n" + "=" * 80)
        print("DEPENDENCY TRACKER FUNCTION TIMES")
        print("=" * 80)

        # Get stats for tracker functions
        tracker_funcs = {}
        for func, (cc, nc, tt, ct, callers) in stats.stats.items():
            filename, line, name = func
            if "dependency_tracker" in filename:
                tracker_funcs[name] = {
                    "calls": nc,
                    "tottime": tt,
                    "cumtime": ct,
                }

        # Sort by cumulative time
        sorted_funcs = sorted(
            tracker_funcs.items(),
            key=lambda x: x[1]["cumtime"],
            reverse=True
        )

        total_tracker_time = sum(f["cumtime"] for _, f in sorted_funcs)

        print(f"\n{'Function':<40} {'Calls':>10} {'Total(s)':>10} {'Cum(s)':>10}")
        print("-" * 72)
        for name, data in sorted_funcs[:15]:
            print(f"{name:<40} {data['calls']:>10} {data['tottime']:>10.4f} {data['cumtime']:>10.4f}")

        print("-" * 72)
        print(f"{'TOTAL TRACKER TIME':<40} {'':<10} {'':<10} {total_tracker_time:>10.4f}")

    # Summary
    print("\n" + "=" * 80)
    print("OVERHEAD SUMMARY")
    print("=" * 80)

    bare = results["bare"]
    nocov_first = results["nocov_first"]
    nocov_second = results["nocov_second"]

    print(f"\nBaseline (bare pytest):     {bare:.3f}s")
    print(f"nocov first run:            {nocov_first:.3f}s")
    print(f"nocov second run (warm):    {nocov_second:.3f}s")

    first_overhead = nocov_first - bare
    second_overhead = nocov_second - bare

    print(f"\nFirst run overhead:         {first_overhead:.3f}s ({first_overhead/bare*100:.0f}% of bare)")
    print(f"Warm run overhead:          {second_overhead:.3f}s ({second_overhead/bare*100:.0f}% of bare)")

    print(f"\nTracker cumulative time:    {total_tracker_time:.3f}s")
    print(f"Tracker % of first run:     {total_tracker_time/nocov_first*100:.1f}%")

    # Breakdown
    print("\n" + "-" * 40)
    print("OVERHEAD BREAKDOWN (estimated):")
    print("-" * 40)

    # From the profile data, estimate components
    import_hook_time = sum(
        f["cumtime"] for name, f in tracker_funcs.items()
        if "import" in name.lower()
    )
    file_hook_time = sum(
        f["cumtime"] for name, f in tracker_funcs.items()
        if "open" in name.lower() or "file" in name.lower()
    )
    other_tracker_time = total_tracker_time - import_hook_time - file_hook_time

    print(f"  Import hooks:             {import_hook_time:.3f}s ({import_hook_time/nocov_first*100:.1f}%)")
    print(f"  File tracking:            {file_hook_time:.3f}s ({file_hook_time/nocov_first*100:.1f}%)")
    print(f"  Other tracker overhead:   {other_tracker_time:.3f}s ({other_tracker_time/nocov_first*100:.1f}%)")
    print(f"  Non-tracker overhead:     {first_overhead - total_tracker_time:.3f}s")


def main():
    parser = argparse.ArgumentParser(description="Profile pytest-ezmon execution")
    parser.add_argument("--runs", type=int, default=1, help="Number of profiling runs")
    parser.add_argument("--compare", action="store_true", help="Compare with original version")
    parser.add_argument("--tracker", action="store_true", help="Measure import tracker overhead")
    parser.add_argument("--top", type=int, default=30, help="Number of top functions to show")
    args = parser.parse_args()

    if args.compare:
        run_comparison(args.runs)
        return

    if args.tracker:
        measure_tracker_overhead(args.runs)
        return

    print("Setting up test environment...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace = setup_workspace(temp_path)
        python = setup_venv(temp_path, REPO_ROOT)
        profile_file = temp_path / "profile.prof"

        print(f"Workspace: {workspace}")
        print(f"Python: {python}")

        # Run 1: First run (cold start)
        print("\n" + "=" * 80)
        print("RUN 1: First run (cold start, building .testmondata)")
        print("=" * 80)

        result = run_pytest_with_profiling(python, workspace, profile_file)
        print(f"Elapsed: {result['elapsed']:.3f}s")
        print(f"Return code: {result['returncode']}")

        if result['returncode'] != 0:
            print("STDOUT:", result['stdout'])
            print("STDERR:", result['stderr'])

        print_profile_stats(profile_file, args.top)

        # Run 2: Second run (warm, no changes)
        print("\n" + "=" * 80)
        print("RUN 2: Second run (warm, no code changes)")
        print("=" * 80)

        profile_file2 = temp_path / "profile2.prof"
        result2 = run_pytest_with_profiling(python, workspace, profile_file2)
        print(f"Elapsed: {result2['elapsed']:.3f}s")
        print(f"Return code: {result2['returncode']}")

        print_profile_stats(profile_file2, args.top)

        # Run 3: After modification
        print("\n" + "=" * 80)
        print("RUN 3: After modifying a source file")
        print("=" * 80)

        # Modify math_utils.py
        utils_file = workspace / "src" / "math_utils.py"
        content = utils_file.read_text()
        utils_file.write_text(content.replace("return a + b", "return a + b  # modified"))

        profile_file3 = temp_path / "profile3.prof"
        result3 = run_pytest_with_profiling(python, workspace, profile_file3)
        print(f"Elapsed: {result3['elapsed']:.3f}s")
        print(f"Return code: {result3['returncode']}")

        print_profile_stats(profile_file3, args.top)

        # Summary
        print("\n" + "=" * 80)
        print("TIMING SUMMARY")
        print("=" * 80)
        print(f"First run (cold):     {result['elapsed']:.3f}s")
        print(f"Second run (warm):    {result2['elapsed']:.3f}s")
        print(f"After modification:   {result3['elapsed']:.3f}s")


if __name__ == "__main__":
    main()
