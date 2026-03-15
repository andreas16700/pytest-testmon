#!/usr/bin/env python3
"""
Historical commit benchmark pipeline.

Simulates running the ezmon plugin across cached CI commit history to measure
overhead, savings, and accuracy. The .testmondata DB persists across plugin runs
(oldest→newest), so each subsequent commit benefits from accumulated dep data.

Usage:
    python scripts/benchmark_pipeline.py <repo> [--start-from SHA] [--no-plugin-only]
                                                 [--plugin-only] [--dry-run]

Supported repos: matplotlib, pandas
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_PATH = Path("/Users/andrew_yos/pytest-super/nocov-refactor")
TW_DB = Path.home() / ".cache" / "twcli" / "cache.db"

REPO_CONFIGS = {
    "matplotlib": {
        "repo_path": Path("/Users/andrew_yos/tw/matplotlib"),
        "venv_bin": Path("/Users/andrew_yos/tw/matplotlib/.venv313/bin"),
        "build_dir": "build/cp313",
        "job_name": "Python 3.13 on macos-15",
        "db_repo": "matplotlib",
        "results_file": PLUGIN_PATH / "profile" / "matplotlib" / "pipeline_results.json",
        "clean_excludes": [".testmondata", ".venv313", "ezmon-timing*"],
        "has_subprojects": True,
        "pytest_extra": [
            "-rfEsXR", "-n", "auto",
            "--maxfail=50", "--timeout=300", "--durations=25",
            "--color=no", "-q",
        ],
        "test_env": {},
    },
    "pandas": {
        "repo_path": Path("/Users/andrew_yos/tw/pandas"),
        "venv_bin": Path("/Users/andrew_yos/tw/pandas/.conda-env/bin"),
        "build_dir": "build/cp314",
        "job_name": "macos-15 actions-314.yaml",
        "db_repo": "pandas",
        "results_file": PLUGIN_PATH / "profile" / "pandas" / "pipeline_results.json",
        "clean_excludes": [".testmondata", ".venv", ".conda-env", "ezmon-timing*"],
        "has_subprojects": False,
        "pytest_extra": [
            "-r", "fE", "-n", "auto", "--dist=worksteal",
            "-m", "not slow and not db and not network and not single_cpu",
            "--color=no", "-q",
            "pandas",
        ],
        "test_env": {
            "PYTHONDEVMODE": "1",
            "PYTHONWARNDEFAULTENCODING": "1",
            "PANDAS_CI": "1",
            "MESONPY_EDITABLE_VERBOSE": "1",
        },
    },
}

PYTEST_TIMEOUT = 1800  # 30 minutes per run

# Regex for pytest summary line (flexible order)
SUMMARY_RE = re.compile(
    r"(?:=+ )?"
    r"(?P<counts>[\d]+ (?:passed|failed|error).*?)"
    r" in "
    r"(?P<duration>[\d.]+)s"
)

COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|error)")


def query_jobs(cfg):
    """Query non-cancelled jobs from tw DB, deduplicated by SHA."""
    conn = sqlite3.connect(str(TW_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT j.job_id, r.head_sha, r.created_at, j.duration_secs, j.conclusion
        FROM jobs j JOIN runs r ON j.run_id = r.run_id
        WHERE j.name = ?
          AND r.repo = ?
          AND j.conclusion <> 'cancelled'
        ORDER BY r.created_at ASC
    """, (cfg["job_name"], cfg["db_repo"]))
    rows = cur.fetchall()

    # Deduplicate by SHA (keep first occurrence)
    seen = set()
    jobs = []
    for row in rows:
        sha = row["head_sha"]
        if sha not in seen:
            seen.add(sha)
            jobs.append(dict(row))

    # Fetch CI test results for each job
    ci_results = {}
    cur.execute("""
        SELECT tr.job_id, tr.passed, tr.failed, tr.skipped,
               tr.xfailed, tr.xpassed, tr.errors, tr.duration_secs
        FROM test_results tr
        JOIN jobs j ON tr.job_id = j.job_id
        JOIN runs r ON j.run_id = r.run_id
        WHERE j.name = ?
          AND r.repo = ?
          AND j.conclusion <> 'cancelled'
    """, (cfg["job_name"], cfg["db_repo"]))
    for tr in cur.fetchall():
        ci_results[tr["job_id"]] = {
            "passed": tr["passed"],
            "failed": tr["failed"],
            "skipped": tr["skipped"],
            "xfailed": tr["xfailed"],
            "xpassed": tr["xpassed"],
            "errors": tr["errors"],
            "duration": tr["duration_secs"],
        }

    conn.close()

    for job in jobs:
        job["ci"] = ci_results.get(job["job_id"])

    return jobs


def parse_summary(output):
    """Parse pytest summary line from output."""
    result = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "error": 0,
        "duration": None,
    }

    for line in output.splitlines():
        m = SUMMARY_RE.search(line)
        if m:
            result["duration"] = float(m.group("duration"))
            for count_match in COUNT_RE.finditer(m.group("counts")):
                count = int(count_match.group(1))
                kind = count_match.group(2)
                result[kind] = count
            return result

    return result


def parse_timing_telemetry(timing_dir):
    """Parse timing JSONL files for selection and DB write stats."""
    telemetry = {
        "selected": 0,
        "deselected": 0,
        "forced": 0,
        "db_write_secs": None,
        "finalize_secs": None,
    }

    if not timing_dir.exists():
        return telemetry

    # Parse worker files for selection counts
    for jsonl_file in sorted(timing_dir.glob("gw*.jsonl")):
        try:
            with open(jsonl_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") == "selection_end":
                        telemetry["selected"] += ev.get("selected_count", 0)
                        telemetry["deselected"] += ev.get("deselected_count", 0)
                        telemetry["forced"] += ev.get("forced_count", 0)
        except OSError:
            continue

    # Parse controller file for DB write timing
    controller_file = timing_dir / "controller.jsonl"
    if controller_file.exists():
        save_start = None
        finalize_start = None
        try:
            with open(controller_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_name = ev.get("event", "")
                    mono = ev.get("mono") or ev.get("ts")
                    if mono is None:
                        continue
                    if event_name in ("controller_save_bitmap_start", "controller_save_raw_start", "save_raw_collect_filenames_start"):
                        save_start = mono
                    elif event_name in ("controller_save_bitmap_end", "controller_save_raw_end", "save_raw_end") and save_start is not None:
                        elapsed_write = mono - save_start
                        if telemetry["db_write_secs"] is None:
                            telemetry["db_write_secs"] = elapsed_write
                        else:
                            telemetry["db_write_secs"] += elapsed_write
                    elif event_name == "controller_finalize_file_start":
                        finalize_start = mono
                    elif event_name == "controller_finalize_file_end" and finalize_start is not None:
                        telemetry["finalize_secs"] = mono - finalize_start
        except OSError:
            pass

    return telemetry


def run_pytest(cmd, cwd, env=None, label=""):
    """Run pytest with the given command and return (returncode, stdout, stderr, parsed_summary)."""
    merged_env = {**os.environ, **(env or {})}
    print(f"  [{label}] Running: {' '.join(cmd[-5:])}")
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT,
            cwd=str(cwd),
            env=merged_env,
        )
        elapsed = time.monotonic() - start
        output = proc.stdout + "\n" + proc.stderr
        summary = parse_summary(output)
        print(
            f"  [{label}] rc={proc.returncode} "
            f"passed={summary['passed']} failed={summary['failed']} "
            f"skipped={summary['skipped']} duration={summary['duration']}s "
            f"(wall={elapsed:.1f}s)"
        )
        return proc.returncode, proc.stdout, proc.stderr, summary
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        print(f"  [{label}] TIMEOUT after {elapsed:.1f}s")
        return -1, "", "timeout", {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "error": 0, "duration": None}


def commit_exists(sha, repo_path):
    """Check if a commit object exists locally."""
    result = subprocess.run(
        ["git", "cat-file", "-t", sha],
        cwd=str(repo_path),
        capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == b"commit"


def build_env(cfg):
    """Build an env dict with the venv bin prepended to PATH and compiler vars set."""
    env = os.environ.copy()
    env["PATH"] = str(cfg["venv_bin"]) + os.pathsep + env.get("PATH", "")
    # Ensure system compilers are found (conda envs don't ship compilers)
    for var, default in [("CC", "/usr/bin/cc"), ("CXX", "/usr/bin/c++"), ("AR", "/usr/bin/ar")]:
        if var not in env:
            env[var] = default
    return env


def fix_version_file(repo_path, build_dir):
    """Write _version_meson.py from git describe so the package version is correct."""
    version_file = repo_path / build_dir / "_version_meson.py"
    if not version_file.parent.exists():
        return
    try:
        desc = subprocess.run(
            ["git", "describe", "--tags"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if desc.returncode == 0:
            ver = desc.stdout.strip().lstrip("v").replace("-", "+", 1).replace("-", ".")
        else:
            ver = "0+unknown"
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        ).stdout.strip()
        version_file.write_text(f'__version__="{ver}"\n__git_version__="{sha}"\n')
    except Exception:
        pass


def checkout_and_build(sha, cfg):
    """Checkout the given SHA and rebuild C extensions."""
    repo_path = cfg["repo_path"]
    build_dir = cfg["build_dir"]
    venv_bin = cfg["venv_bin"]
    env = build_env(cfg)

    print(f"  Checking out {sha[:10]}...")
    subprocess.run(
        ["git", "checkout", "-f", sha],
        cwd=str(repo_path),
        capture_output=True,
        check=True,
    )

    # Clean untracked files (not gitignored — preserves build/, subprojects/*, etc.)
    clean_cmd = ["git", "clean", "-fd"]
    for exc in cfg["clean_excludes"]:
        clean_cmd.extend(["--exclude", exc])
    subprocess.run(clean_cmd, cwd=str(repo_path), capture_output=True)

    # Rebuild C extensions
    print(f"  Building C extensions...")
    ninja_result = subprocess.run(
        ["ninja", "-C", build_dir],
        cwd=str(repo_path),
        capture_output=True,
        env=env,
        timeout=300,
    )
    if ninja_result.returncode != 0:
        # Try reconfigure
        print(f"  ninja failed, reconfiguring...")
        if cfg["has_subprojects"]:
            subprojects_dir = repo_path / "subprojects"
            if subprojects_dir.exists():
                for d in subprojects_dir.iterdir():
                    if d.is_dir() and d.name not in ("packagefiles", "packagecache"):
                        shutil.rmtree(d)
        reconf = subprocess.run(
            ["meson", "setup", "--reconfigure", build_dir],
            cwd=str(repo_path),
            capture_output=True,
            env=env,
            timeout=300,
        )
        if reconf.returncode == 0:
            ninja_result = subprocess.run(
                ["ninja", "-C", build_dir],
                cwd=str(repo_path),
                capture_output=True,
                env=env,
                timeout=300,
            )
        if ninja_result.returncode != 0:
            print(f"  reconfigure failed, falling back to pip install...")
            pip_result = subprocess.run(
                [str(venv_bin / "pip"), "install", "--no-build-isolation", "--no-deps", "-e", "."],
                cwd=str(repo_path),
                capture_output=True,
                env=env,
                timeout=600,
            )
            if pip_result.returncode != 0:
                raise RuntimeError(
                    f"Build failed for {sha[:10]}: {pip_result.stderr[-500:]}"
                )

    # Fix version file for meson-python editable installs
    fix_version_file(repo_path, build_dir)


def load_results(results_file):
    """Load existing results from JSON file."""
    if results_file.exists():
        with open(results_file, "r") as f:
            return json.load(f)
    return []


def save_results(results, results_file):
    """Save results incrementally to JSON file."""
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Historical commit benchmark pipeline"
    )
    parser.add_argument(
        "repo",
        choices=list(REPO_CONFIGS.keys()),
        help="Repository to benchmark",
    )
    parser.add_argument(
        "--start-from",
        metavar="SHA",
        help="Resume from a specific commit (skip earlier ones)",
    )
    parser.add_argument(
        "--no-plugin-only",
        action="store_true",
        help="Run only the no-plugin baseline",
    )
    parser.add_argument(
        "--plugin-only",
        action="store_true",
        help="Run only the plugin run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commit list without running",
    )
    args = parser.parse_args()

    cfg = REPO_CONFIGS[args.repo]
    repo_path = cfg["repo_path"]
    venv_bin = cfg["venv_bin"]
    results_file = cfg["results_file"]
    timing_base = repo_path / "ezmon-timing-pipeline"

    pytest_base_cmd = [
        str(venv_bin / "python"), "-m", "pytest",
    ] + cfg["pytest_extra"]

    jobs = query_jobs(cfg)
    print(f"Found {len(jobs)} unique commits (deduplicated by SHA)")

    # Apply --start-from filter
    if args.start_from:
        start_sha = args.start_from.lower()
        skip = True
        filtered = []
        for job in jobs:
            if job["head_sha"].startswith(start_sha):
                skip = False
            if not skip:
                filtered.append(job)
        if not filtered:
            print(f"ERROR: SHA {args.start_from} not found in job list")
            sys.exit(1)
        print(f"Starting from {args.start_from} ({len(filtered)} commits remaining)")
        jobs = filtered

    if args.dry_run:
        print(f"\n{'#':>3}  {'SHA':10}  {'Created':20}  {'Conclusion':10}  {'CI Duration':>12}")
        print("-" * 70)
        for i, job in enumerate(jobs, 1):
            ci_dur = f"{job['duration_secs']:.0f}s" if job["duration_secs"] else "N/A"
            print(
                f"{i:3}  {job['head_sha'][:10]}  {job['created_at']:20}  "
                f"{job['conclusion']:10}  {ci_dur:>12}"
            )
        return

    # Load existing results for incremental updates
    results = load_results(results_file)

    run_no_plugin = not args.plugin_only
    run_plugin = not args.no_plugin_only

    # Build base env for all runs
    base_env = dict(cfg["test_env"])

    total = len(jobs)
    for i, job in enumerate(jobs):
        sha = job["head_sha"]
        print(f"\n{'='*60}")
        print(f"[{i+1}/{total}] {sha[:10]} ({job['created_at']}) — {job['conclusion']}")
        print(f"{'='*60}")

        # Find or create result entry
        existing = next((r for r in results if r["sha"] == sha), None)
        if existing is None:
            entry = {
                "sha": sha,
                "job_id": job["job_id"],
                "created_at": job["created_at"],
                "conclusion": job["conclusion"],
                "ci": job["ci"],
                "no_plugin": None,
                "plugin": None,
                "error": None,
            }
            results.append(entry)
        else:
            entry = existing

        # Skip if both runs already done
        if entry.get("no_plugin") and entry.get("plugin"):
            if not (run_no_plugin and not run_plugin) and not (run_plugin and not run_no_plugin):
                print(f"  Skipping (already complete)")
                continue

        if not commit_exists(sha, repo_path):
            entry["error"] = "commit not available locally"
            save_results(results, results_file)
            print(f"  Skipping (commit not available locally)")
            continue

        try:
            checkout_and_build(sha, cfg)
        except Exception as e:
            entry["error"] = str(e)
            save_results(results, results_file)
            print(f"  BUILD FAILED: {e}")
            continue

        # No-plugin run
        if run_no_plugin and not entry.get("no_plugin"):
            cmd = pytest_base_cmd + ["-p", "no:ezmon-nocov"]
            rc, stdout, stderr, summary = run_pytest(cmd, cwd=repo_path, env=base_env, label="no-plugin")
            entry["no_plugin"] = {
                "passed": summary["passed"],
                "failed": summary["failed"],
                "skipped": summary["skipped"],
                "xfailed": summary["xfailed"],
                "xpassed": summary["xpassed"],
                "error": summary["error"],
                "duration": summary["duration"],
                "returncode": rc,
            }
            if rc == -1:
                entry["no_plugin"]["timeout"] = True
            save_results(results, results_file)

        # Plugin run
        if run_plugin and not entry.get("plugin"):
            timing_dir = timing_base / sha[:10]
            timing_dir.mkdir(parents=True, exist_ok=True)

            cmd = pytest_base_cmd + ["--ezmon", "--ezmon-forceselect"]
            env = {
                **base_env,
                "EZMON_XDIST_TIMING_LOG_DIR": str(timing_dir),
                "EZMON_XDIST_TIMING_FLUSH_ALL": "1",
            }
            rc, stdout, stderr, summary = run_pytest(cmd, cwd=repo_path, env=env, label="plugin")

            telemetry = parse_timing_telemetry(timing_dir)

            entry["plugin"] = {
                "passed": summary["passed"],
                "failed": summary["failed"],
                "skipped": summary["skipped"],
                "xfailed": summary["xfailed"],
                "xpassed": summary["xpassed"],
                "error": summary["error"],
                "duration": summary["duration"],
                "returncode": rc,
                "selected": telemetry["selected"],
                "deselected": telemetry["deselected"],
                "forced": telemetry["forced"],
                "db_write_secs": telemetry["db_write_secs"],
                "finalize_secs": telemetry["finalize_secs"],
            }
            if rc == -1:
                entry["plugin"]["timeout"] = True
            save_results(results, results_file)

    # Print summary
    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE")
    print(f"{'='*60}")
    print(f"Results saved to: {results_file}")
    print(f"Timing data in: {timing_base}/")
    print(f"\nSummary:")
    print(f"{'#':>3}  {'SHA':10}  {'NP Duration':>12}  {'P Duration':>12}  {'Deselected':>10}  {'Savings':>8}")
    print("-" * 70)
    for i, r in enumerate(results, 1):
        np_dur = f"{r['no_plugin']['duration']:.1f}s" if r.get("no_plugin") and r["no_plugin"].get("duration") else "N/A"
        p_dur = f"{r['plugin']['duration']:.1f}s" if r.get("plugin") and r["plugin"].get("duration") else "N/A"
        desel = str(r["plugin"]["deselected"]) if r.get("plugin") else "N/A"

        if (r.get("no_plugin") and r["no_plugin"].get("duration")
                and r.get("plugin") and r["plugin"].get("duration")):
            savings = (1 - r["plugin"]["duration"] / r["no_plugin"]["duration"]) * 100
            savings_str = f"{savings:.0f}%"
        else:
            savings_str = "N/A"

        print(f"{i:3}  {r['sha'][:10]}  {np_dur:>12}  {p_dur:>12}  {desel:>10}  {savings_str:>8}")


if __name__ == "__main__":
    main()
