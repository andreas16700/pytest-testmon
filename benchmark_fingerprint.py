#!/usr/bin/env python3
"""
Benchmark script to measure fingerprint computation cost on pandas codebase.

Compares serial vs parallel approaches for computing AST-based fingerprints
of all Python files in a pandas commit.
"""

import ast
import os
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

# Target pandas commit (v2.2.0 release)
PANDAS_REPO = "https://github.com/pandas-dev/pandas.git"
PANDAS_COMMIT = "v2.2.0"


def strip_docstrings(tree):
    """Remove docstrings from AST nodes in-place."""
    def is_docstring(node):
        if not isinstance(node, ast.Expr):
            return False
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if hasattr(node, 'body') and node.body and is_docstring(node.body[0]):
                node.body = node.body[1:]


def compute_fingerprint(source_code: str) -> int:
    """Compute AST-based fingerprint for a Python file."""
    try:
        tree = ast.parse(source_code)
        strip_docstrings(tree)
        ast_repr = ast.dump(tree, annotate_fields=False)
        return zlib.crc32(ast_repr.encode("utf-8"))
    except SyntaxError:
        # Fall back to content hash for files with syntax errors
        return zlib.crc32(source_code.encode("utf-8"))


def compute_fingerprint_for_file(filepath: str) -> Tuple[str, int, float]:
    """Read file and compute fingerprint. Returns (path, checksum, time_ms)."""
    start = time.perf_counter()
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()
        checksum = compute_fingerprint(source)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return (filepath, checksum, elapsed_ms)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return (filepath, 0, elapsed_ms)


def find_python_files(directory: Path) -> List[str]:
    """Find all .py files in directory, excluding __pycache__ and .git."""
    python_files = []
    for root, dirs, files in os.walk(directory):
        # Skip unwanted directories
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'build', 'dist', '.eggs')]
        for f in files:
            if f.endswith('.py'):
                python_files.append(os.path.join(root, f))
    return python_files


def clone_pandas(target_dir: Path) -> Path:
    """Clone pandas repo at specific commit."""
    print(f"Cloning pandas at {PANDAS_COMMIT}...")

    # Shallow clone with specific tag
    subprocess.run([
        "git", "clone",
        "--depth", "1",
        "--branch", PANDAS_COMMIT,
        PANDAS_REPO,
        str(target_dir)
    ], check=True, capture_output=True)

    return target_dir


def benchmark_serial(files: List[str]) -> Tuple[float, List[Tuple[str, int, float]]]:
    """Benchmark serial fingerprint computation."""
    print(f"\n=== Serial Processing ({len(files)} files) ===")

    start = time.perf_counter()
    results = []
    for filepath in files:
        result = compute_fingerprint_for_file(filepath)
        results.append(result)
    total_time = time.perf_counter() - start

    return total_time, results


def benchmark_parallel_threads(files: List[str], workers: int) -> Tuple[float, List[Tuple[str, int, float]]]:
    """Benchmark parallel fingerprint computation using threads."""
    print(f"\n=== Thread Pool ({workers} workers, {len(files)} files) ===")

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(compute_fingerprint_for_file, files))
    total_time = time.perf_counter() - start

    return total_time, results


def benchmark_parallel_processes(files: List[str], workers: int) -> Tuple[float, List[Tuple[str, int, float]]]:
    """Benchmark parallel fingerprint computation using processes."""
    print(f"\n=== Process Pool ({workers} workers, {len(files)} files) ===")

    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(compute_fingerprint_for_file, files))
    total_time = time.perf_counter() - start

    return total_time, results


def print_results(label: str, total_time: float, results: List[Tuple[str, int, float]], files: List[str]):
    """Print benchmark results."""
    individual_times = [r[2] for r in results]
    total_individual = sum(individual_times)

    # Find slowest files
    sorted_results = sorted(results, key=lambda x: x[2], reverse=True)

    print(f"  Total wall time: {total_time:.3f}s")
    print(f"  Sum of individual times: {total_individual:.1f}ms ({total_individual/1000:.3f}s)")
    print(f"  Average per file: {total_individual/len(files):.2f}ms")
    print(f"  Throughput: {len(files)/total_time:.1f} files/sec")

    print(f"\n  Top 10 slowest files:")
    for path, checksum, time_ms in sorted_results[:10]:
        rel_path = Path(path).name
        print(f"    {time_ms:7.2f}ms  {rel_path}")


def get_file_stats(files: List[str]) -> None:
    """Print statistics about the files being processed."""
    total_size = 0
    total_lines = 0

    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                total_size += len(content)
                total_lines += content.count('\n')
        except Exception:
            pass

    print(f"\nFile statistics:")
    print(f"  Total files: {len(files)}")
    print(f"  Total size: {total_size / 1024 / 1024:.2f} MB")
    print(f"  Total lines: {total_lines:,}")
    print(f"  Average file size: {total_size / len(files) / 1024:.2f} KB")


def main():
    # Determine pandas directory
    pandas_dir = None

    # Check if pandas path is provided as argument
    if len(sys.argv) > 1:
        pandas_dir = Path(sys.argv[1])
        if not pandas_dir.exists():
            print(f"Error: {pandas_dir} does not exist")
            sys.exit(1)
        print(f"Using existing pandas directory: {pandas_dir}")
    else:
        # Clone to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            pandas_dir = Path(tmpdir) / "pandas"
            clone_pandas(pandas_dir)
            run_benchmarks(pandas_dir)
            return

    run_benchmarks(pandas_dir)


def run_benchmarks(pandas_dir: Path):
    """Run all benchmarks on the pandas directory."""
    print(f"\nFinding Python files in {pandas_dir}...")
    files = find_python_files(pandas_dir)
    print(f"Found {len(files)} Python files")

    get_file_stats(files)

    # Warmup run (helps with disk caching)
    print("\n--- Warmup run (serial) ---")
    benchmark_serial(files[:100])

    # Run benchmarks
    results = {}

    # Serial
    total_time, serial_results = benchmark_serial(files)
    results['serial'] = total_time
    print_results("Serial", total_time, serial_results, files)

    # Thread pool with various worker counts
    for workers in [2, 4, 8, os.cpu_count()]:
        if workers is None:
            continue
        total_time, thread_results = benchmark_parallel_threads(files, workers)
        results[f'threads_{workers}'] = total_time
        print_results(f"Threads ({workers})", total_time, thread_results, files)

    # Process pool with various worker counts
    for workers in [2, 4, 8, os.cpu_count()]:
        if workers is None:
            continue
        total_time, process_results = benchmark_parallel_processes(files, workers)
        results[f'processes_{workers}'] = total_time
        print_results(f"Processes ({workers})", total_time, process_results, files)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    serial_time = results['serial']
    print(f"\n{'Method':<25} {'Time (s)':<12} {'Speedup':<10}")
    print("-" * 47)

    for method, time_s in sorted(results.items(), key=lambda x: x[1]):
        speedup = serial_time / time_s
        print(f"{method:<25} {time_s:<12.3f} {speedup:<10.2f}x")

    # Calculate CPU-bound vs I/O-bound analysis
    print("\n" + "-" * 47)
    print("\nAnalysis:")

    best_thread_key = min([k for k in results if k.startswith('threads_')], key=lambda k: results[k])
    best_process_key = min([k for k in results if k.startswith('processes_')], key=lambda k: results[k])

    thread_speedup = serial_time / results[best_thread_key]
    process_speedup = serial_time / results[best_process_key]

    if thread_speedup > 1.5:
        print(f"  Threading helps ({thread_speedup:.2f}x) - workload has I/O component")
    else:
        print(f"  Threading doesn't help much ({thread_speedup:.2f}x) - workload is CPU-bound")

    if process_speedup > thread_speedup * 1.2:
        print(f"  Multiprocessing is better ({process_speedup:.2f}x) - confirms CPU-bound")
    else:
        print(f"  Multiprocessing overhead not worth it for this workload")


if __name__ == "__main__":
    main()
