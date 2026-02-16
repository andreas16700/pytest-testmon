#!/usr/bin/env python3
"""
Systematic Blind Spot Testing Framework for ezmon-nocov

This script provides infrastructure for testing bypass patterns:
1. Creates a fresh test project with configurable dependencies
2. Establishes baseline .testmondata
3. Applies specified changes to untracked dependencies
4. Runs ezmon and captures results
5. Verifies actual test behavior (with/without ezmon)

Usage:
    # First time setup:
    python blind_spot_tester.py setup

    # Test a specific pattern:
    python blind_spot_tester.py test --pattern np.load --change double

    # List available patterns:
    python blind_spot_tester.py list

    # Run all patterns:
    python blind_spot_tester.py test-all

Results are saved to: /tmp/ezmon_blind_spot_results/
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configuration
# IMPORTANT: On macOS, /tmp is symlinked to /private/tmp which causes path
# resolution issues with ezmon's _is_in_project() check. Use /var/tmp instead.
VENV_PATH = Path("/tmp/ezmon_demo_env")
PROJECT_PATH = Path("/var/tmp/ezmon_blind_spot_project")  # Use /var/tmp to avoid symlink issues
RESULTS_PATH = Path("/var/tmp/ezmon_blind_spot_results")
BASELINE_DB_PATH = RESULTS_PATH / "baseline.testmondata"
EZMON_SOURCE = Path("/Users/andrew_yos/pytest-super/pytest-testmon-nocov")


# =============================================================================
# Pattern Definitions
# =============================================================================

PATTERNS = {
    "np.load": {
        "description": "NumPy np.load() for .npy/.npz files",
        "data_file": "data/reference.npy",
        "test_code": '''
import numpy as np
from pathlib import Path

# Use relative path from test file to avoid absolute path issues
DATA_FILE = Path(__file__).parent.parent / "data" / "reference.npy"

def compute(x):
    return np.sin(x)

def test_matches_reference():
    """Test uses np.load() - check if tracked"""
    data = np.load(DATA_FILE)
    x = data[0]
    expected = data[1]
    actual = compute(x)
    np.testing.assert_allclose(actual, expected, rtol=1e-12)
''',
        "create_data": '''
import numpy as np
x = np.linspace(0, 10, 100)
expected = np.sin(x)
np.save("{data_path}", np.array([x, expected]))
''',
        "changes": {
            "double": '''
import numpy as np
data = np.load("{data_path}")
data[1] = data[1] * 2.0
np.save("{data_path}", data)
''',
            "corrupt": '''
import numpy as np
data = np.load("{data_path}")
data[1] = np.random.rand(*data[1].shape)
np.save("{data_path}", data)
''',
            "zero": '''
import numpy as np
data = np.load("{data_path}")
data[1] = np.zeros_like(data[1])
np.save("{data_path}", data)
''',
        }
    },

    "np.fromfile": {
        "description": "NumPy np.fromfile() for raw binary files",
        "data_file": "data/raw_data.bin",
        "test_code": '''
import numpy as np
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "raw_data.bin"

def test_raw_data():
    """Test uses np.fromfile() - check if tracked"""
    data = np.fromfile(DATA_FILE, dtype=np.float64)
    expected_sum = 450.0  # sum of 0..9 * 10
    np.testing.assert_allclose(data.sum(), expected_sum, rtol=1e-12)
''',
        "create_data": '''
import numpy as np
data = np.arange(10, dtype=np.float64) * 10.0
data.tofile("{data_path}")
''',
        "changes": {
            "double": '''
import numpy as np
data = np.fromfile("{data_path}", dtype=np.float64)
data = data * 2.0
data.tofile("{data_path}")
''',
        }
    },

    "np.memmap": {
        "description": "NumPy memory-mapped files",
        "data_file": "data/memmap_data.dat",
        "test_code": '''
import numpy as np
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "memmap_data.dat"

def test_memmap_data():
    """Test uses np.memmap() - check if tracked"""
    data = np.memmap(DATA_FILE, dtype=np.float64, mode='r', shape=(10,))
    expected_sum = 450.0
    np.testing.assert_allclose(data.sum(), expected_sum, rtol=1e-12)
''',
        "create_data": '''
import numpy as np
fp = np.memmap("{data_path}", dtype=np.float64, mode='w+', shape=(10,))
fp[:] = np.arange(10, dtype=np.float64) * 10.0
del fp  # flush to disk
''',
        "changes": {
            "double": '''
import numpy as np
fp = np.memmap("{data_path}", dtype=np.float64, mode='r+', shape=(10,))
fp[:] = fp[:] * 2.0
del fp
''',
        }
    },

    "json_file": {
        "description": "JSON config file (uses builtins.open - SHOULD be tracked)",
        "data_file": "data/config.json",
        "test_code": '''
import json
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "config.json"

def test_config():
    """Test uses open() for JSON - SHOULD be tracked"""
    with open(DATA_FILE) as f:
        config = json.load(f)
    assert config["threshold"] == 0.5
''',
        "create_data": '''
import json
config = {{"threshold": 0.5, "name": "test"}}
with open("{data_path}", "w") as f:
    json.dump(config, f)
''',
        "changes": {
            "change_value": '''
import json
with open("{data_path}") as f:
    config = json.load(f)
config["threshold"] = 0.9
with open("{data_path}", "w") as f:
    json.dump(config, f)
''',
        }
    },
}


# =============================================================================
# Core Functions
# =============================================================================

def get_python():
    """Get Python executable from venv."""
    python = VENV_PATH / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"Virtualenv not found at {VENV_PATH}. Run 'setup' first.")
    return str(python)


def get_pytest():
    """Get pytest executable from venv."""
    return str(VENV_PATH / "bin" / "pytest")


def run_python(code: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run Python code in the venv."""
    return subprocess.run(
        [get_python(), "-c", code],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60
    )


def run_pytest(with_ezmon: bool, cwd: Path) -> tuple[int, str]:
    """Run pytest and return (returncode, combined output)."""
    cmd = [get_pytest(), "-xvs"]
    if with_ezmon:
        cmd.append("--ezmon")
    cmd.append("tests/")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120
    )
    return result.returncode, result.stdout + result.stderr


def setup_venv():
    """Create and configure the virtual environment."""
    print(f"Setting up virtualenv at {VENV_PATH}...")

    if VENV_PATH.exists():
        print("  Removing existing venv...")
        shutil.rmtree(VENV_PATH)

    # Find python3.11
    python311 = shutil.which("python3.11") or "/opt/homebrew/bin/python3.11"
    if not Path(python311).exists():
        print("ERROR: python3.11 not found. Please install it first.")
        sys.exit(1)

    subprocess.run([python311, "-m", "venv", str(VENV_PATH)], check=True)

    pip = VENV_PATH / "bin" / "pip"
    print("  Installing dependencies...")
    subprocess.run([str(pip), "install", "--quiet", "numpy", "pytest", "requests"], check=True)

    print(f"  Installing ezmon-nocov from {EZMON_SOURCE}...")
    subprocess.run([str(pip), "install", "--quiet", "-e", str(EZMON_SOURCE)], check=True)

    # Verify
    result = subprocess.run([str(VENV_PATH / "bin" / "pip"), "list"], capture_output=True, text=True)
    if "pytest-ezmon-nocov" in result.stdout:
        print("  SUCCESS: ezmon-nocov installed")
    else:
        print("  WARNING: ezmon-nocov may not be installed correctly")

    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    print(f"  Results directory: {RESULTS_PATH}")


def create_project(pattern_name: str) -> Path:
    """Create a fresh test project for the given pattern.

    IMPORTANT: Initializes a git repo and commits files, because ezmon
    only tracks files that are committed in git (to avoid ephemeral files).
    """
    if PROJECT_PATH.exists():
        shutil.rmtree(PROJECT_PATH)

    PROJECT_PATH.mkdir(parents=True)
    (PROJECT_PATH / "tests").mkdir()
    (PROJECT_PATH / "data").mkdir()

    pattern = PATTERNS[pattern_name]
    data_path = PROJECT_PATH / pattern["data_file"]

    # Create data file
    create_code = pattern["create_data"].format(data_path=str(data_path))
    result = run_python(create_code)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create data: {result.stderr}")

    # Create test file
    test_code = pattern["test_code"].format(data_path=str(data_path))
    test_file = PROJECT_PATH / "tests" / f"test_{pattern_name.replace('.', '_')}.py"
    test_file.write_text(test_code)

    # Initialize git repo and commit files
    # This is REQUIRED because ezmon only tracks git-committed files
    subprocess.run(["git", "init", "-b", "main"], cwd=PROJECT_PATH, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=PROJECT_PATH, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=PROJECT_PATH, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=PROJECT_PATH, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=PROJECT_PATH, capture_output=True)

    return PROJECT_PATH


def save_baseline():
    """Save a copy of the baseline .testmondata."""
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    db_file = PROJECT_PATH / ".testmondata"
    if db_file.exists():
        shutil.copy(db_file, BASELINE_DB_PATH)
        print(f"  Baseline saved: {BASELINE_DB_PATH} ({BASELINE_DB_PATH.stat().st_size} bytes)")
    else:
        print("  WARNING: No .testmondata found to save")


def restore_baseline():
    """Restore baseline .testmondata."""
    db_file = PROJECT_PATH / ".testmondata"
    if BASELINE_DB_PATH.exists():
        shutil.copy(BASELINE_DB_PATH, db_file)
        print(f"  Baseline restored from {BASELINE_DB_PATH}")
    else:
        print("  WARNING: No baseline to restore")


def apply_change(pattern_name: str, change_name: str):
    """Apply a change to the data file and commit it.

    IMPORTANT: Changes must be committed to git for ezmon to detect them,
    since ezmon tracks committed file SHAs.
    """
    pattern = PATTERNS[pattern_name]
    data_path = PROJECT_PATH / pattern["data_file"]

    if change_name not in pattern["changes"]:
        raise ValueError(f"Unknown change '{change_name}' for pattern '{pattern_name}'")

    change_code = pattern["changes"][change_name].format(data_path=str(data_path))
    result = run_python(change_code)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to apply change: {result.stderr}")

    # Commit the change to git (required for ezmon to detect it)
    subprocess.run(["git", "add", str(data_path)], cwd=PROJECT_PATH, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Apply change: {change_name}"],
        cwd=PROJECT_PATH,
        capture_output=True
    )


def save_result(pattern_name: str, change_name: str, result: dict):
    """Save test result to JSON file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = RESULTS_PATH / f"{pattern_name}_{change_name}_{timestamp}.json"

    with open(result_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"  Result saved: {result_file}")
    return result_file


# =============================================================================
# Test Execution
# =============================================================================

def test_pattern(pattern_name: str, change_name: str) -> dict:
    """Test a specific pattern with a specific change."""
    print(f"\n{'='*70}")
    print(f"TESTING: {pattern_name} with change '{change_name}'")
    print(f"{'='*70}")

    pattern = PATTERNS[pattern_name]
    result = {
        "pattern": pattern_name,
        "change": change_name,
        "description": pattern["description"],
        "timestamp": datetime.now().isoformat(),
        "steps": []
    }

    # Step 1: Create project
    print("\n[1/6] Creating test project...")
    create_project(pattern_name)
    result["steps"].append({"step": "create_project", "status": "ok"})

    # Step 2: Baseline run
    print("[2/6] Running baseline (--ezmon)...")
    rc, output = run_pytest(with_ezmon=True, cwd=PROJECT_PATH)
    result["steps"].append({
        "step": "baseline_run",
        "returncode": rc,
        "output": output,
        "status": "ok" if rc == 0 else "failed"
    })
    if rc != 0:
        print(f"  ERROR: Baseline failed!\n{output[-500:]}")
        return result

    # Step 3: Save baseline
    print("[3/6] Saving baseline .testmondata...")
    save_baseline()
    result["steps"].append({"step": "save_baseline", "status": "ok"})

    # Step 4: Apply change
    print(f"[4/6] Applying change: {change_name}...")
    apply_change(pattern_name, change_name)
    result["steps"].append({"step": "apply_change", "status": "ok"})

    # Step 5: Run with ezmon after change
    print("[5/6] Running with --ezmon after change...")
    rc_ezmon, output_ezmon = run_pytest(with_ezmon=True, cwd=PROJECT_PATH)

    ezmon_detected = "collected 0 items" not in output_ezmon
    result["steps"].append({
        "step": "ezmon_after_change",
        "returncode": rc_ezmon,
        "output": output_ezmon,
        "detected_change": ezmon_detected,
        "status": "detected" if ezmon_detected else "missed"
    })

    if ezmon_detected:
        print(f"  ezmon DETECTED the change (test was selected)")
    else:
        print(f"  ezmon MISSED the change (test was deselected)")

    # Step 6: Run without ezmon to verify actual state
    print("[6/6] Running WITHOUT --ezmon to verify actual test state...")
    restore_baseline()  # Restore DB so ezmon doesn't interfere
    rc_actual, output_actual = run_pytest(with_ezmon=False, cwd=PROJECT_PATH)

    result["steps"].append({
        "step": "actual_test_run",
        "returncode": rc_actual,
        "output": output_actual,
        "test_passed": rc_actual == 0,
        "status": "passed" if rc_actual == 0 else "failed"
    })

    if rc_actual == 0:
        print(f"  Actual test: PASSED")
    else:
        print(f"  Actual test: FAILED")

    # Summary
    result["summary"] = {
        "blind_spot_confirmed": not ezmon_detected and rc_actual != 0,
        "ezmon_detected": ezmon_detected,
        "actual_test_failed": rc_actual != 0
    }

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    if result["summary"]["blind_spot_confirmed"]:
        print(">>> BLIND SPOT CONFIRMED <<<")
        print(f"  - ezmon missed the change (deselected test)")
        print(f"  - But test actually FAILED when run")
    elif not ezmon_detected and rc_actual == 0:
        print(">>> Change did not affect test outcome <<<")
        print(f"  - ezmon deselected (correct)")
        print(f"  - Test still passes")
    elif ezmon_detected:
        print(">>> ezmon correctly detected the change <<<")
        print(f"  - Test was selected and run")

    # Save result
    save_result(pattern_name, change_name, result)

    return result


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_setup(args):
    """Setup the testing environment."""
    setup_venv()
    print("\nSetup complete!")


def cmd_list(args):
    """List available patterns and changes."""
    print("Available patterns:")
    print("-" * 70)
    for name, pattern in PATTERNS.items():
        print(f"\n{name}:")
        print(f"  Description: {pattern['description']}")
        print(f"  Data file: {pattern['data_file']}")
        print(f"  Changes: {', '.join(pattern['changes'].keys())}")


def cmd_test(args):
    """Test a specific pattern."""
    if args.pattern not in PATTERNS:
        print(f"Unknown pattern: {args.pattern}")
        print(f"Available: {', '.join(PATTERNS.keys())}")
        sys.exit(1)

    pattern = PATTERNS[args.pattern]
    if args.change not in pattern["changes"]:
        print(f"Unknown change: {args.change}")
        print(f"Available for {args.pattern}: {', '.join(pattern['changes'].keys())}")
        sys.exit(1)

    test_pattern(args.pattern, args.change)


def cmd_test_all(args):
    """Test all patterns with all changes."""
    results = []
    for pattern_name, pattern in PATTERNS.items():
        for change_name in pattern["changes"]:
            result = test_pattern(pattern_name, change_name)
            results.append(result)

    # Summary
    print(f"\n{'='*70}")
    print("OVERALL SUMMARY")
    print(f"{'='*70}")

    blind_spots = [r for r in results if r.get("summary", {}).get("blind_spot_confirmed")]
    detected = [r for r in results if r.get("summary", {}).get("ezmon_detected")]

    print(f"Total tests: {len(results)}")
    print(f"Blind spots confirmed: {len(blind_spots)}")
    print(f"Changes detected by ezmon: {len(detected)}")

    if blind_spots:
        print("\nConfirmed blind spots:")
        for r in blind_spots:
            print(f"  - {r['pattern']} / {r['change']}")


def main():
    parser = argparse.ArgumentParser(
        description="Systematic Blind Spot Testing Framework for ezmon-nocov"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # setup
    subparsers.add_parser("setup", help="Setup the testing environment")

    # list
    subparsers.add_parser("list", help="List available patterns and changes")

    # test
    test_parser = subparsers.add_parser("test", help="Test a specific pattern")
    test_parser.add_argument("--pattern", "-p", required=True, help="Pattern to test")
    test_parser.add_argument("--change", "-c", required=True, help="Change to apply")

    # test-all
    subparsers.add_parser("test-all", help="Test all patterns")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "test-all":
        cmd_test_all(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
