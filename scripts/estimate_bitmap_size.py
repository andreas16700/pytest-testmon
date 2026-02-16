#!/usr/bin/env python3
import argparse
import random
import statistics
import time
from pathlib import Path

try:
    from pyroaring import BitMap
    HAVE_PYROARING = True
except Exception:
    HAVE_PYROARING = False

try:
    import zstandard as zstd
    HAVE_ZSTD = True
except Exception:
    HAVE_ZSTD = False

from ezmon.bitmap_deps import TestDeps, find_affected_tests
from ezmon import db as ezdb
import sqlite3
import struct

ZSTD_COMPRESSION_LEVEL = 3


def build_bitmap(ids):
    if HAVE_PYROARING:
        return BitMap(ids)
    return set(ids)


def serialized_size(bm):
    if HAVE_PYROARING:
        return len(bm.serialize())
    return 4 + 4 * len(bm)


def compressed_size(bm):
    raw = bm.serialize() if HAVE_PYROARING else b""
    if not HAVE_ZSTD:
        return None
    compressor = zstd.ZstdCompressor(level=ZSTD_COMPRESSION_LEVEL)
    return len(compressor.compress(raw))


def estimate_sizes(total_files, dep_ratio, samples, seed):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    sizes = []
    comp_sizes = []
    for _ in range(samples):
        ids = rng.sample(range(total_files), dep_count)
        bm = build_bitmap(ids)
        sizes.append(serialized_size(bm))
        if HAVE_ZSTD and HAVE_PYROARING:
            comp_sizes.append(compressed_size(bm))
    return sizes, comp_sizes


def build_deps(test_count, total_files, dep_ratio, overlap_ratio, seed):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    common_count = int(dep_count * overlap_ratio)
    common_files = set(rng.sample(range(1, total_files + 1), common_count))
    deps_list = []
    for test_id in range(test_count):
        remaining = dep_count - common_count
        pool = [i for i in range(1, total_files + 1) if i not in common_files]
        unique = set(rng.sample(pool, remaining)) if remaining > 0 else set()
        ids = common_files | unique
        deps_list.append((test_id, ids))
    return deps_list


def build_inverted_index(test_count, total_files, dep_ratio, overlap_ratio, seed):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    common_count = int(dep_count * overlap_ratio)
    common_files = set(rng.sample(range(1, total_files + 1), common_count))
    file_to_tests = [set() for _ in range(total_files)]
    for test_id in range(test_count):
        remaining = dep_count - common_count
        pool = [i for i in range(1, total_files + 1) if i not in common_files]
        unique = set(rng.sample(pool, remaining)) if remaining > 0 else set()
        ids = common_files | unique
        for fid in ids:
            file_to_tests[fid - 1].add(test_id)
    if HAVE_PYROARING:
        return [BitMap(tests) for tests in file_to_tests]
    return file_to_tests


def benchmark_affected_tests(
    deps_list,
    total_files,
    percent_step,
    repeats,
    seed,
):
    rng = random.Random(seed)
    sizes = [1]
    pct = percent_step
    while pct < 100:
        sizes.append(max(1, int(total_files * (pct / 100.0))))
        pct += percent_step
    sizes.append(max(1, total_files - 1))
    sizes = sorted(set(sizes))

    rows = []
    for size in sizes:
        changed = set(rng.sample(range(total_files), min(size, total_files)))
        find_affected_tests(deps_list, changed, set())
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            affected = find_affected_tests(deps_list, changed, set())
            times.append(time.perf_counter() - start)
        affected_ratio = len(affected) / len(deps_list) if deps_list else 0.0
        rows.append((size, affected_ratio, statistics.mean(times)))
    return rows


def benchmark_affected_tests_inverted(
    file_to_tests,
    total_files,
    percent_step,
    repeats,
    seed,
):
    rng = random.Random(seed)
    sizes = [1]
    pct = percent_step
    while pct < 100:
        sizes.append(max(1, int(total_files * (pct / 100.0))))
        pct += percent_step
    sizes.append(max(1, total_files - 1))
    sizes = sorted(set(sizes))

    rows = []
    for size in sizes:
        changed = rng.sample(range(total_files), min(size, total_files))
        # warm-up
        _ = _or_tests(file_to_tests, changed)
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            affected = _or_tests(file_to_tests, changed)
            times.append(time.perf_counter() - start)
        affected_ratio = len(affected) / _test_count_from_index(file_to_tests) if file_to_tests else 0.0
        rows.append((size, affected_ratio, statistics.mean(times)))
    return rows


def build_db(test_count, total_files, dep_ratio, overlap_ratio, seed, db_path):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    common_count = int(dep_count * overlap_ratio)
    common_files = set(rng.sample(range(1, total_files + 1), common_count))

    if db_path.exists():
        db_path.unlink()

    database = ezdb.DB(str(db_path))
    con = database.con

    # environment id 1
    con.execute(
        "INSERT INTO environment (id, environment_name, system_packages, python_version) VALUES (?, ?, ?, ?)",
        (1, "default", "", "3.14.2"),
    )

    file_rows = [(i + 1, f"file_{i}.py", None, None, "python") for i in range(total_files)]
    con.executemany(
        "INSERT INTO files (id, path, checksum, fsha, file_type) VALUES (?, ?, ?, ?, ?)",
        file_rows,
    )
    con.execute("CREATE INDEX IF NOT EXISTS files_path ON files(path)")

    test_rows = [
        (i + 1, 1, f"tests/test_{i}.py::test_{i}", f"tests/test_{i}.py", None, 0)
        for i in range(test_count)
    ]
    con.executemany(
        "INSERT INTO tests (id, environment_id, name, test_file, duration, failed) VALUES (?, ?, ?, ?, ?, ?)",
        test_rows,
    )
    con.execute("CREATE INDEX IF NOT EXISTS tests_env_name ON tests(environment_id, name)")

    deps_rows = []
    for test_id in range(1, test_count + 1):
        remaining = dep_count - common_count
        pool = [i for i in range(1, total_files + 1) if i not in common_files]
        unique = set(rng.sample(pool, remaining)) if remaining > 0 else set()
        ids = common_files | unique
        deps = TestDeps.from_file_ids(test_id, set(ids))
        deps_rows.append((test_id, deps.serialize(), deps.serialize_external_packages()))
        if len(deps_rows) >= 1000:
            con.executemany(
                "INSERT OR REPLACE INTO test_deps (test_id, file_bitmap, external_packages) VALUES (?, ?, ?)",
                deps_rows,
            )
            deps_rows.clear()
    if deps_rows:
        con.executemany(
            "INSERT OR REPLACE INTO test_deps (test_id, file_bitmap, external_packages) VALUES (?, ?, ?)",
            deps_rows,
        )

    con.commit()
    return database


def _serialize_bitmap(bm):
    raw = bm.serialize() if HAVE_PYROARING else b""
    if not HAVE_ZSTD:
        return raw
    compressor = zstd.ZstdCompressor(level=ZSTD_COMPRESSION_LEVEL)
    return compressor.compress(raw)


def _deserialize_bitmap(blob):
    data = blob
    if HAVE_ZSTD:
        try:
            data = zstd.ZstdDecompressor().decompress(blob)
        except Exception:
            data = blob
    if HAVE_PYROARING:
        return BitMap.deserialize(data)
    return set()


def build_db_inverted(test_count, total_files, dep_ratio, overlap_ratio, seed, db_path):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    common_count = int(dep_count * overlap_ratio)
    common_files = set(rng.sample(range(1, total_files + 1), common_count))

    if db_path.exists():
        db_path.unlink()

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS file_tests (file_id INTEGER PRIMARY KEY, test_bitmap BLOB NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS file_tests_id ON file_tests(file_id)")

    file_to_tests = [set() for _ in range(total_files)]
    for test_id in range(test_count):
        remaining = dep_count - common_count
        pool = [i for i in range(1, total_files + 1) if i not in common_files]
        unique = set(rng.sample(pool, remaining)) if remaining > 0 else set()
        ids = common_files | unique
        for fid in ids:
            file_to_tests[fid - 1].add(test_id)

    rows = []
    for fid, tests in enumerate(file_to_tests, start=1):
        bm = BitMap(tests) if HAVE_PYROARING else set(tests)
        rows.append((fid, _serialize_bitmap(bm)))
        if len(rows) >= 1000:
            con.executemany("INSERT OR REPLACE INTO file_tests (file_id, test_bitmap) VALUES (?, ?)", rows)
            rows.clear()
    if rows:
        con.executemany("INSERT OR REPLACE INTO file_tests (file_id, test_bitmap) VALUES (?, ?)", rows)
    con.commit()
    return con


def build_db_hybrid(test_count, total_files, dep_ratio, overlap_ratio, seed, db_path):
    rng = random.Random(seed)
    dep_count = int(total_files * dep_ratio)
    common_count = int(dep_count * overlap_ratio)
    common_files = set(rng.sample(range(1, total_files + 1), common_count))

    if db_path.exists():
        db_path.unlink()

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS file_tests (file_id INTEGER PRIMARY KEY, test_bitmap BLOB NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS file_tests_id ON file_tests(file_id)")
    con.execute("CREATE TABLE IF NOT EXISTS test_files (test_id INTEGER PRIMARY KEY, file_list BLOB NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS test_files_id ON test_files(test_id)")

    file_to_tests = [set() for _ in range(total_files)]
    test_rows = []
    for test_id in range(1, test_count + 1):
        remaining = dep_count - common_count
        pool = [i for i in range(1, total_files + 1) if i not in common_files]
        unique = set(rng.sample(pool, remaining)) if remaining > 0 else set()
        ids = sorted(common_files | unique)
        for fid in ids:
            file_to_tests[fid - 1].add(test_id)
        # compact list as uint32
        blob = struct.pack(f"<I{len(ids)}I", len(ids), *ids)
        test_rows.append((test_id, blob))
        if len(test_rows) >= 1000:
            con.executemany("INSERT OR REPLACE INTO test_files (test_id, file_list) VALUES (?, ?)", test_rows)
            test_rows.clear()
    if test_rows:
        con.executemany("INSERT OR REPLACE INTO test_files (test_id, file_list) VALUES (?, ?)", test_rows)

    rows = []
    for fid, tests in enumerate(file_to_tests, start=1):
        bm = BitMap(tests) if HAVE_PYROARING else set(tests)
        rows.append((fid, _serialize_bitmap(bm)))
        if len(rows) >= 1000:
            con.executemany("INSERT OR REPLACE INTO file_tests (file_id, test_bitmap) VALUES (?, ?)", rows)
            rows.clear()
    if rows:
        con.executemany("INSERT OR REPLACE INTO file_tests (file_id, test_bitmap) VALUES (?, ?)", rows)
    con.commit()
    return con


def benchmark_affected_tests_db(
    database,
    total_files,
    percent_step,
    repeats,
    seed,
):
    rng = random.Random(seed)
    sizes = [1]
    pct = percent_step
    while pct < 100:
        sizes.append(max(1, int(total_files * (pct / 100.0))))
        pct += percent_step
    sizes.append(max(1, total_files - 1))
    sizes = sorted(set(sizes))

    rows = []
    for size in sizes:
        changed = set(rng.sample(range(1, total_files + 1), min(size, total_files)))
        database.find_affected_tests_bitmap(1, changed, set())
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            affected = database.find_affected_tests_bitmap(1, changed, set())
            _ = _db_fetch_test_deps_forward(database, affected)
            times.append(time.perf_counter() - start)
        affected_ratio = len(affected) / database.con.execute(
            "SELECT count(*) FROM tests WHERE environment_id = 1"
        ).fetchone()[0]
        rows.append((size, affected_ratio, statistics.mean(times)))
    return rows


def benchmark_affected_tests_db_inverted(
    con,
    total_files,
    percent_step,
    repeats,
    seed,
):
    rng = random.Random(seed)
    sizes = [1]
    pct = percent_step
    while pct < 100:
        sizes.append(max(1, int(total_files * (pct / 100.0))))
        pct += percent_step
    sizes.append(max(1, total_files - 1))
    sizes = sorted(set(sizes))

    rows = []
    for size in sizes:
        changed = rng.sample(range(1, total_files + 1), min(size, total_files))
        _ = _db_or_tests(con, changed)
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            affected = _db_or_tests(con, changed)
            times.append(time.perf_counter() - start)
        affected_ratio = len(affected) / _estimate_test_count(con)
        rows.append((size, affected_ratio, statistics.mean(times)))
    return rows


def benchmark_affected_tests_db_hybrid(
    con,
    total_files,
    percent_step,
    repeats,
    seed,
):
    rng = random.Random(seed)
    sizes = [1]
    pct = percent_step
    while pct < 100:
        sizes.append(max(1, int(total_files * (pct / 100.0))))
        pct += percent_step
    sizes.append(max(1, total_files - 1))
    sizes = sorted(set(sizes))

    rows = []
    for size in sizes:
        changed = rng.sample(range(1, total_files + 1), min(size, total_files))
        _ = _db_or_tests(con, changed)
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            affected = _db_or_tests(con, changed)
            _ = _db_fetch_test_lists(con, affected)
            times.append(time.perf_counter() - start)
        affected_ratio = len(affected) / _estimate_test_count(con)
        rows.append((size, affected_ratio, statistics.mean(times)))
    return rows


def _estimate_test_count(con):
    # Use max test id found in any bitmap
    cur = con.execute("SELECT test_bitmap FROM file_tests")
    max_id = -1
    for (blob,) in cur:
        bm = _deserialize_bitmap(blob)
        if bm:
            try:
                max_id = max(max_id, max(bm))
            except ValueError:
                continue
    return max_id + 1 if max_id >= 0 else 0


def _db_or_tests(con, changed_files):
    out = BitMap() if HAVE_PYROARING else set()
    placeholders = ",".join("?" * len(changed_files))
    cur = con.execute(
        f"SELECT test_bitmap FROM file_tests WHERE file_id IN ({placeholders})",
        tuple(changed_files),
    )
    for (blob,) in cur:
        bm = _deserialize_bitmap(blob)
        if HAVE_PYROARING:
            out |= bm
        else:
            out |= bm
    return out


def _db_fetch_test_lists(con, test_ids):
    if not test_ids:
        return []
    if HAVE_PYROARING:
        test_ids = list(test_ids)
    placeholders = ",".join("?" * len(test_ids))
    cur = con.execute(
        f"SELECT file_list FROM test_files WHERE test_id IN ({placeholders})",
        tuple(test_ids),
    )
    lists = []
    for (blob,) in cur:
        if not blob:
            continue
        count = struct.unpack_from("<I", blob, 0)[0]
        if count:
            lists.append(struct.unpack_from(f"<{count}I", blob, 4))
    return lists


def _db_fetch_test_deps_forward(database, test_names):
    if not test_names:
        return []
    con = database.con
    placeholders = ",".join("?" * len(test_names))
    rows = con.execute(
        f"SELECT td.file_bitmap FROM tests t JOIN test_deps td ON t.id = td.test_id WHERE t.name IN ({placeholders})",
        tuple(test_names),
    ).fetchall()
    out = []
    for (blob,) in rows:
        _ = _deserialize_bitmap(blob)
        out.append(blob)
    return out


def benchmark_test_deps_db_forward(database, test_ids, repeats):
    con = database.con
    placeholders = ",".join("?" * len(test_ids))
    # warm-up
    con.execute(f"SELECT file_bitmap FROM test_deps WHERE test_id IN ({placeholders})", tuple(test_ids)).fetchall()
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        rows = con.execute(
            f"SELECT file_bitmap FROM test_deps WHERE test_id IN ({placeholders})",
            tuple(test_ids),
        ).fetchall()
        for (blob,) in rows:
            _ = _deserialize_bitmap(blob)
        times.append(time.perf_counter() - start)
    return statistics.mean(times)


def benchmark_test_deps_db_hybrid(con, test_ids, repeats):
    placeholders = ",".join("?" * len(test_ids))
    con.execute(f"SELECT file_list FROM test_files WHERE test_id IN ({placeholders})", tuple(test_ids)).fetchall()
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        rows = con.execute(
            f"SELECT file_list FROM test_files WHERE test_id IN ({placeholders})",
            tuple(test_ids),
        ).fetchall()
        for (blob,) in rows:
            if not blob:
                continue
            count = struct.unpack_from("<I", blob, 0)[0]
            if count:
                _ = struct.unpack_from(f"<{count}I", blob, 4)
        times.append(time.perf_counter() - start)
    return statistics.mean(times)


def db_file_size(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size


def _test_count_from_index(file_to_tests):
    if not file_to_tests:
        return 0
    if HAVE_PYROARING:
        # approximate: max id + 1 across bitmaps
        max_id = -1
        for bm in file_to_tests:
            if bm:
                try:
                    max_id = max(max_id, max(bm))
                except ValueError:
                    continue
        return max_id + 1 if max_id >= 0 else 0
    # set-based
    max_id = -1
    for s in file_to_tests:
        if s:
            max_id = max(max_id, max(s))
    return max_id + 1 if max_id >= 0 else 0


def _or_tests(file_to_tests, changed_files):
    if HAVE_PYROARING:
        out = BitMap()
        for fid in changed_files:
            out |= file_to_tests[fid]
        return out
    out = set()
    for fid in changed_files:
        out |= file_to_tests[fid]
    return out


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("changed_file_count,affected_ratio,seconds\n")
        for size, ratio, seconds in rows:
            f.write(f"{size},{ratio:.6f},{seconds:.6f}\n")


def plot_rows_svg(rows, path):
    xs = [r[1] for r in rows]
    ys = [r[2] for r in rows]
    if not xs or not ys:
        return False
    w, h, pad = 800, 400, 50
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1e-6
    if ymax == ymin:
        ymax = ymin + 1e-6

    def sx(x):
        return pad + (x - xmin) / (xmax - xmin) * (w - 2 * pad)

    def sy(y):
        return h - pad - (y - ymin) / (ymax - ymin) * (h - 2 * pad)

    points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{pad}" y="{pad-10}" font-size="14" font-family="Arial">Affected Test Ratio vs Query Time</text>',
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#333"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="#333"/>',
        f'<polyline fill="none" stroke="#1f77b4" stroke-width="2" points="{points}"/>',
    ]
    for x, y in zip(xs, ys):
        lines.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="#1f77b4"/>')
    lines.append('</svg>')
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="Estimate Roaring bitmap sizes and query cost.")
    parser.add_argument("--tests", type=int, default=200_000, help="Number of tests")
    parser.add_argument("--files", type=int, default=5_000, help="Total files")
    parser.add_argument("--dep-ratio", type=float, default=0.7, help="Fraction of files depended on")
    parser.add_argument("--samples", type=int, default=100, help="Sample count for per-test bitmap size")
    parser.add_argument("--seed", type=int, default=123, help="Random seed")
    parser.add_argument("--bench", action="store_true", help="Benchmark affected test lookup")
    parser.add_argument("--bench-tests", type=int, default=200_000, help="Tests to use for benchmark")
    parser.add_argument("--bench-repeats", type=int, default=5, help="Repeats per size")
    parser.add_argument("--percent-step", type=int, default=10, help="Percent step for changed file sweep")
    parser.add_argument("--bench-mode", type=str, default="forward", choices=["forward", "inverted"], help="Benchmark mode")
    parser.add_argument("--bench-db", action="store_true", help="Benchmark DB query time")
    parser.add_argument("--bench-db-path", type=str, default="/tmp/ezmon_bench.sqlite", help="SQLite path for DB benchmark")
    parser.add_argument("--bench-db-mode", type=str, default="forward", choices=["forward", "inverted", "hybrid"], help="DB benchmark mode")
    parser.add_argument("--storage-sweep", action="store_true", help="Run storage growth sweep")
    parser.add_argument("--storage-tests", type=str, default="50000,100000,200000", help="Comma list of test counts")
    parser.add_argument("--storage-files", type=str, default="5000", help="Comma list of file counts")
    parser.add_argument("--storage-csv", type=str, default="/tmp/ezmon_storage_sweep.csv", help="CSV output for storage sweep")
    parser.add_argument("--overlap-ratio", type=float, default=0.3, help="Shared dependency ratio across tests")
    parser.add_argument("--bench-test-deps", action="store_true", help="Benchmark per-test deps query time")
    parser.add_argument("--bench-test-deps-count", type=int, default=1000, help="Number of tests to fetch deps for")
    parser.add_argument("--csv", type=str, default="", help="CSV output path for benchmark")
    parser.add_argument("--graph", type=str, default="", help="PNG output path for benchmark graph")
    args = parser.parse_args()

    sizes, comp_sizes = estimate_sizes(args.files, args.dep_ratio, args.samples, args.seed)
    avg = statistics.mean(sizes)
    p50 = statistics.median(sizes)
    p90 = sorted(sizes)[int(0.9 * (len(sizes) - 1))]
    p99 = sorted(sizes)[int(0.99 * (len(sizes) - 1))]
    max_size = max(sizes)

    total_bytes = avg * args.tests
    total_gb = total_bytes / (1024 ** 3)

    print(f"pyroaring: {HAVE_PYROARING}")
    print(f"zstd: {HAVE_ZSTD}")
    print(f"files: {args.files}, dep_ratio: {args.dep_ratio:.2f}, deps/test: {int(args.files*args.dep_ratio)}")
    print(f"raw sizes (bytes): avg={avg:.1f} p50={p50} p90={p90} p99={p99} max={max_size}")
    if comp_sizes:
        cavg = statistics.mean(comp_sizes)
        cp50 = statistics.median(comp_sizes)
        cp90 = sorted(comp_sizes)[int(0.9 * (len(comp_sizes) - 1))]
        cp99 = sorted(comp_sizes)[int(0.99 * (len(comp_sizes) - 1))]
        cmax = max(comp_sizes)
        total_cbytes = cavg * args.tests
        total_cgb = total_cbytes / (1024 ** 3)
        print(f"zstd sizes (bytes): avg={cavg:.1f} p50={cp50} p90={cp90} p99={cp99} max={cmax}")
        print(f"estimated total (zstd) for {args.tests} tests: {total_cbytes:,.0f} bytes (~{total_cgb:.2f} GiB)")
    print(f"estimated total (raw) for {args.tests} tests: {total_bytes:,.0f} bytes (~{total_gb:.2f} GiB)")

    if args.bench:
        bench_tests = min(args.bench_tests, args.tests)
        if args.bench_db:
            db_path = Path(args.bench_db_path)
            if args.bench_db_mode == "forward":
                database = build_db(bench_tests, args.files, args.dep_ratio, args.overlap_ratio, args.seed, db_path)
                rows = benchmark_affected_tests_db(
                    database,
                    args.files,
                    args.percent_step,
                    args.bench_repeats,
                    args.seed + 1,
                )
                if args.bench_test_deps:
                    test_ids = list(range(1, min(bench_tests, args.bench_test_deps_count) + 1))
                    t = benchmark_test_deps_db_forward(database, test_ids, args.bench_repeats)
                    print(f"per-test deps (forward) avg: {t:.6f}s for {len(test_ids)} tests")
            else:
                if args.bench_db_mode == "inverted":
                    con = build_db_inverted(bench_tests, args.files, args.dep_ratio, args.overlap_ratio, args.seed, db_path)
                    rows = benchmark_affected_tests_db_inverted(
                        con,
                        args.files,
                        args.percent_step,
                        args.bench_repeats,
                        args.seed + 1,
                    )
                else:
                    con = build_db_hybrid(bench_tests, args.files, args.dep_ratio, args.overlap_ratio, args.seed, db_path)
                    rows = benchmark_affected_tests_db_hybrid(
                        con,
                        args.files,
                        args.percent_step,
                        args.bench_repeats,
                        args.seed + 1,
                    )
                    if args.bench_test_deps:
                        test_ids = list(range(1, min(bench_tests, args.bench_test_deps_count) + 1))
                        t = benchmark_test_deps_db_hybrid(con, test_ids, args.bench_repeats)
                        print(f"per-test deps (hybrid) avg: {t:.6f}s for {len(test_ids)} tests")
        else:
            if args.bench_mode == "forward":
                deps_list = build_deps(bench_tests, args.files, args.dep_ratio, args.overlap_ratio, args.seed)
                deps_objs = [TestDeps.from_file_ids(tid, ids) for tid, ids in deps_list]
                rows = benchmark_affected_tests(
                    deps_objs,
                    args.files,
                    args.percent_step,
                    args.bench_repeats,
                    args.seed + 1,
                )
            else:
                file_to_tests = build_inverted_index(bench_tests, args.files, args.dep_ratio, args.overlap_ratio, args.seed)
                rows = benchmark_affected_tests_inverted(
                    file_to_tests,
                    args.files,
                    args.percent_step,
                    args.bench_repeats,
                    args.seed + 1,
                )
        csv_path = args.csv or str(Path("/tmp/ezmon_benchmark.csv"))
        write_csv(rows, csv_path)
        print(f"wrote CSV: {csv_path}")
        graph_path = args.graph or str(Path("/tmp/ezmon_benchmark.svg"))
        if plot_rows_svg(rows, graph_path):
            print(f"wrote graph: {graph_path}")
        else:
            print("graph not written")

    if args.storage_sweep:
        tests_list = [int(x.strip()) for x in args.storage_tests.split(",") if x.strip()]
        files_list = [int(x.strip()) for x in args.storage_files.split(",") if x.strip()]
        out_path = Path(args.storage_csv)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("mode,tests,files,dep_ratio,db_bytes\n")
            for files in files_list:
                for tests in tests_list:
                    forward_path = Path(f"/tmp/ezmon_storage_forward_{tests}_{files}.sqlite")
                    inverted_path = Path(f"/tmp/ezmon_storage_inverted_{tests}_{files}.sqlite")
                    _ = build_db(tests, files, args.dep_ratio, args.overlap_ratio, args.seed, forward_path)
                    f.write(f"forward,{tests},{files},{args.dep_ratio},{db_file_size(forward_path)}\n")
                    con = build_db_inverted(tests, files, args.dep_ratio, args.overlap_ratio, args.seed, inverted_path)
                    con.close()
                    f.write(f"inverted,{tests},{files},{args.dep_ratio},{db_file_size(inverted_path)}\n")
                    hybrid_path = Path(f"/tmp/ezmon_storage_hybrid_{tests}_{files}.sqlite")
                    con = build_db_hybrid(tests, files, args.dep_ratio, args.overlap_ratio, args.seed, hybrid_path)
                    con.close()
                    f.write(f"hybrid,{tests},{files},{args.dep_ratio},{db_file_size(hybrid_path)}\n")
        print(f"wrote storage CSV: {out_path}")


if __name__ == "__main__":
    main()
