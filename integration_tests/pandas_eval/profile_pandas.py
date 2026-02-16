#!/usr/bin/env python3
"""
Pandas Profiling Script for ezmon no-coverage experiment.

This script evaluates the performance of the nocov approach on pandas,
comparing it with bare pytest, upstream testmon, and ezmon with coverage.

Usage (must run in pandas-dev conda environment):
    micromamba activate pandas-dev
    python profile_pandas.py [--subset SUBSET] [--full]

Options:
    --subset SUBSET  Run a subset of tests (default: pandas/tests/computation)
    --full          Run the full test suite (WARNING: takes 10-20+ minutes)
    --runs N        Number of runs for averaging (default: 1)

The script:
1. Installs each plugin variant
2. Runs pytest with timing and profiling
3. Measures import tracker overhead
4. Compares test selection after code modification
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Paths
SCRIPT_DIR = Path(__file__).parent.absolute()
EZMON_REPO = SCRIPT_DIR.parent.parent  # pytest-testmon-nocov
PANDAS_REPO = Path.home() / "pandas" / "pandas-repo"

# Test patterns (matching pandas CI)
PYTEST_MARKERS = "not slow and not db and not network and not single_cpu"

# Default subset for quick profiling
DEFAULT_SUBSET = "pandas/tests/computation"


def run_command(cmd: List[str], cwd: Optional[Path] = None,
                capture_output: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"+ {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )
        return result
    except subprocess.TimeoutExpired:
        print(f"Command timed out after {timeout}s")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")


def install_plugin(plugin: str) -> bool:
    """Install a specific plugin variant."""
    # First uninstall any existing testmon variants
    run_command(["pip", "uninstall", "-y", "pytest-testmon"], capture_output=True)
    run_command(["pip", "uninstall", "-y", "pytest-ezmon"], capture_output=True)
    run_command(["pip", "uninstall", "-y", "ezmon"], capture_output=True)

    if plugin == "bare":
        return True
    elif plugin == "testmon":
        result = run_command(["pip", "install", "pytest-testmon"])
        return result.returncode == 0
    elif plugin == "ezmon":
        # Install ezmon from GitHub (main branch with coverage)
        result = run_command([
            "pip", "install",
            "git+https://github.com/andreas16700/pytest-testmon.git@main"
        ])
        return result.returncode == 0
    elif plugin == "nocov":
        # Install from local nocov branch
        result = run_command(["pip", "install", "-e", str(EZMON_REPO)])
        return result.returncode == 0
    else:
        print(f"Unknown plugin: {plugin}")
        return False


def get_pytest_args(plugin: str) -> List[str]:
    """Get pytest arguments for a specific plugin."""
    if plugin == "bare":
        return []
    elif plugin == "testmon":
        return ["--testmon", "--testmon-forceselect"]
    elif plugin == "ezmon":
        return ["--ezmon", "--ezmon-forceselect"]
    elif plugin == "nocov":
        return ["--ezmon", "--ezmon-forceselect"]
    return []


def run_pytest_with_timing(
    plugin: str,
    subset: str,
    work_dir: Path,
    first_run: bool = True
) -> Dict:
    """Run pytest with timing and return metrics."""

    # Clean testmondata for first run
    testmondata = work_dir / ".testmondata"
    if first_run and testmondata.exists():
        testmondata.unlink()

    # Clean pytest cache
    pytest_cache = work_dir / ".pytest_cache"
    if pytest_cache.exists():
        shutil.rmtree(pytest_cache)

    # Build pytest command
    cmd = [
        "pytest",
        "-m", PYTEST_MARKERS,
        "--tb=no",
        "-q",
    ]
    cmd.extend(get_pytest_args(plugin))
    cmd.append(subset)

    # Run with timing
    start = time.perf_counter()
    result = run_command(cmd, cwd=work_dir, timeout=1800)
    elapsed = time.perf_counter() - start

    # Parse output
    output = result.stdout + result.stderr
    metrics = {
        "plugin": plugin,
        "first_run": first_run,
        "wall_time": elapsed,
        "exit_code": result.returncode,
        "output": output[:5000],  # Truncate for storage
    }

    # Parse test counts
    import re
    collected = re.search(r"(\d+) items", output)
    deselected = re.search(r"(\d+) deselected", output)
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    skipped = re.search(r"(\d+) skipped", output)

    metrics["tests_collected"] = int(collected.group(1)) if collected else 0
    metrics["tests_deselected"] = int(deselected.group(1)) if deselected else 0
    metrics["tests_passed"] = int(passed.group(1)) if passed else 0
    metrics["tests_failed"] = int(failed.group(1)) if failed else 0
    metrics["tests_skipped"] = int(skipped.group(1)) if skipped else 0
    metrics["tests_selected"] = metrics["tests_collected"] - metrics["tests_deselected"]

    # Get testmondata size
    if testmondata.exists():
        metrics["testmondata_kb"] = testmondata.stat().st_size // 1024
    else:
        metrics["testmondata_kb"] = 0

    return metrics


def modify_pandas_file(pandas_repo: Path) -> Tuple[Path, str, str]:
    """Modify a pandas source file for testing change detection.

    Returns: (file_path, original_content, modified_content)
    """
    # Modify a core file used by computation tests
    target_file = pandas_repo / "pandas" / "core" / "computation" / "ops.py"

    if not target_file.exists():
        # Fallback to another common file
        target_file = pandas_repo / "pandas" / "core" / "common.py"

    original = target_file.read_text()

    # Add a harmless modification that changes the AST
    modified = original.replace(
        "import operator",
        "import operator  # ezmon-test-marker\n_EZMON_TEST_CONST = 42"
    )

    if modified == original:
        # Try another modification
        modified = original + "\n_EZMON_TEST_CONST = 42\n"

    return target_file, original, modified


def run_comparison(subset: str, runs: int = 1) -> Dict:
    """Run full comparison of all plugin variants."""

    print("=" * 60)
    print("Pandas Plugin Comparison")
    print(f"Subset: {subset}")
    print(f"Runs: {runs}")
    print("=" * 60)

    results = {"subset": subset, "runs": runs, "plugins": {}}

    plugins = ["bare", "testmon", "ezmon", "nocov"]

    for plugin in plugins:
        print(f"\n{'='*60}")
        print(f"Testing: {plugin}")
        print("=" * 60)

        # Install plugin
        if not install_plugin(plugin):
            print(f"Failed to install {plugin}, skipping")
            continue

        plugin_results = {"first_runs": [], "warm_runs": []}

        for run_num in range(runs):
            print(f"\n--- Run {run_num + 1}/{runs} ---")

            # First run (cold)
            print("\nFirst run (cold)...")
            metrics = run_pytest_with_timing(plugin, subset, PANDAS_REPO, first_run=True)
            plugin_results["first_runs"].append(metrics)
            print(f"  Time: {metrics['wall_time']:.2f}s")
            print(f"  Tests: collected={metrics['tests_collected']}, "
                  f"selected={metrics['tests_selected']}")

            if plugin != "bare":
                # Warm run (no changes)
                print("\nWarm run (no changes)...")
                metrics = run_pytest_with_timing(plugin, subset, PANDAS_REPO, first_run=False)
                plugin_results["warm_runs"].append(metrics)
                print(f"  Time: {metrics['wall_time']:.2f}s")
                print(f"  Tests: selected={metrics['tests_selected']}, "
                      f"deselected={metrics['tests_deselected']}")

        results["plugins"][plugin] = plugin_results

    return results


def measure_import_tracker_overhead(subset: str) -> Dict:
    """Measure specifically the import tracker overhead on pandas."""

    print("\n" + "=" * 60)
    print("Import Tracker Overhead Analysis")
    print("=" * 60)

    results = {"subset": subset, "measurements": []}

    # Install nocov for profiling
    install_plugin("nocov")

    # Profile with cProfile
    import cProfile
    import pstats
    import io

    # Clean state
    testmondata = PANDAS_REPO / ".testmondata"
    if testmondata.exists():
        testmondata.unlink()

    # Create profiling script
    profile_script = f'''
import subprocess
import sys
subprocess.run([
    sys.executable, "-m", "pytest",
    "-m", "{PYTEST_MARKERS}",
    "--ezmon", "--ezmon-forceselect",
    "--tb=no", "-q",
    "{subset}"
], cwd="{PANDAS_REPO}")
'''

    # Run with profiling
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(profile_script)
        script_path = f.name

    try:
        start = time.perf_counter()
        result = run_command([
            sys.executable, "-m", "cProfile", "-o", "/tmp/pandas_profile.prof",
            script_path
        ], timeout=1800)
        elapsed = time.perf_counter() - start

        results["total_time"] = elapsed

        # Analyze profile
        if os.path.exists("/tmp/pandas_profile.prof"):
            stats = pstats.Stats("/tmp/pandas_profile.prof")

            # Get tracker-specific function stats
            tracker_funcs = [
                "_tracking_import",
                "_tracking_import_module",
                "_tracking_open",
                "_track_import",
                "_track_file",
                "_is_stdlib_module",
                "_get_module_file",
            ]

            output = io.StringIO()
            stats.stream = output
            stats.sort_stats('cumulative')
            stats.print_stats(50)

            profile_output = output.getvalue()
            results["profile_summary"] = profile_output[:10000]

            # Parse tracker function times
            tracker_times = {}
            for func_name in tracker_funcs:
                for line in profile_output.split('\n'):
                    if func_name in line and 'dependency_tracker' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            try:
                                cumtime = float(parts[3])
                                ncalls = int(parts[0].split('/')[0])
                                tracker_times[func_name] = {
                                    "calls": ncalls,
                                    "cumulative_time": cumtime
                                }
                            except (ValueError, IndexError):
                                pass

            results["tracker_functions"] = tracker_times

    finally:
        os.unlink(script_path)

    return results


def print_summary(results: Dict):
    """Print a summary of the comparison results."""

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print("\n### Timing Comparison")
    print(f"\n| Plugin | First Run | Warm Run | Tests Selected |")
    print("|--------|-----------|----------|----------------|")

    baseline_time = None
    for plugin, data in results.get("plugins", {}).items():
        if data.get("first_runs"):
            first = data["first_runs"][0]
            first_time = first["wall_time"]

            if plugin == "bare":
                baseline_time = first_time
                overhead = ""
            else:
                if baseline_time:
                    overhead_pct = ((first_time - baseline_time) / baseline_time) * 100
                    overhead = f" (+{overhead_pct:.0f}%)"
                else:
                    overhead = ""

            warm_time = "-"
            if data.get("warm_runs"):
                warm_time = f"{data['warm_runs'][0]['wall_time']:.2f}s"

            print(f"| {plugin:6} | {first_time:.2f}s{overhead:10} | {warm_time:8} | "
                  f"{first['tests_selected']:14} |")

    print("\n### Key Findings")

    plugins = results.get("plugins", {})
    if "ezmon" in plugins and "nocov" in plugins:
        ezmon_first = plugins["ezmon"]["first_runs"][0]["wall_time"] if plugins["ezmon"].get("first_runs") else 0
        nocov_first = plugins["nocov"]["first_runs"][0]["wall_time"] if plugins["nocov"].get("first_runs") else 0

        if ezmon_first > 0 and nocov_first > 0:
            speedup = ezmon_first / nocov_first
            savings = ezmon_first - nocov_first
            print(f"\n1. **First run speedup**: {speedup:.1f}x faster with nocov")
            print(f"   - ezmon with coverage: {ezmon_first:.2f}s")
            print(f"   - nocov without coverage: {nocov_first:.2f}s")
            print(f"   - Savings: {savings:.2f}s ({(savings/ezmon_first)*100:.0f}% reduction)")


def main():
    parser = argparse.ArgumentParser(description="Profile ezmon nocov on pandas")
    parser.add_argument("--subset", default=DEFAULT_SUBSET,
                       help=f"Test subset (default: {DEFAULT_SUBSET})")
    parser.add_argument("--full", action="store_true",
                       help="Run full pandas test suite")
    parser.add_argument("--runs", type=int, default=1,
                       help="Number of runs for averaging")
    parser.add_argument("--tracker-only", action="store_true",
                       help="Only measure import tracker overhead")
    parser.add_argument("--output", type=str, default="pandas_results.json",
                       help="Output JSON file for results")
    args = parser.parse_args()

    # Check pandas repo exists
    if not PANDAS_REPO.exists():
        print(f"Error: pandas repo not found at {PANDAS_REPO}")
        print("Please ensure pandas-repo is cloned to ~/pandas/pandas-repo")
        sys.exit(1)

    # Determine test subset
    subset = "pandas" if args.full else args.subset

    if args.tracker_only:
        results = measure_import_tracker_overhead(subset)
    else:
        results = run_comparison(subset, args.runs)
        print_summary(results)

    # Save results
    output_path = SCRIPT_DIR / args.output
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
