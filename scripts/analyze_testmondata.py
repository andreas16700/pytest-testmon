#!/usr/bin/env python
"""Analyze a .testmondata SQLite database.

Decodes zstd-compressed Roaring bitmaps from test_deps to produce
dependency statistics: per-test file counts, overlap analysis,
universally-shared files, and storage efficiency metrics.

Usage:
    python scripts/analyze_testmondata.py .testmondata
    python scripts/analyze_testmondata.py /path/to/.testmondata --top 20
    python scripts/analyze_testmondata.py .testmondata --show-universal
    python scripts/analyze_testmondata.py .testmondata --show-isolation
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path

# --- Bitmap decoding (standalone, no ezmon imports) -------------------------

try:
    import zstandard as zstd
    HAVE_ZSTD = True
except ImportError:
    HAVE_ZSTD = False
    import gzip

try:
    from pyroaring import BitMap
    HAVE_PYROARING = True
except ImportError:
    HAVE_PYROARING = False


def _decode_bitmap_fallback(data: bytes) -> set[int]:
    """Pure-Python Roaring bitmap deserializer (matches BitMap.serialize format)."""
    if not data or len(data) < 4:
        return set()
    count = struct.unpack("<I", data[:4])[0]
    if count == 0:
        return set()
    return set(struct.unpack(f"<{count}I", data[4 : 4 + count * 4]))


def decode_blob(blob: bytes) -> set[int]:
    """Decompress + deserialize a file_bitmap BLOB → set of file IDs."""
    if HAVE_ZSTD:
        try:
            raw = zstd.ZstdDecompressor().decompress(blob)
        except Exception:
            raw = gzip.decompress(blob)
    else:
        try:
            raw = gzip.decompress(blob)
        except Exception:
            raw = blob

    if HAVE_PYROARING:
        return set(BitMap.deserialize(raw))
    return _decode_bitmap_fallback(raw)


# --- Analysis ----------------------------------------------------------------

def load_data(db_path: str):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # file ID → path
    files = {}
    for row in con.execute("SELECT id, path FROM files"):
        files[row["id"]] = row["path"]

    # test name → set of file IDs
    tests: dict[str, set[int]] = {}
    test_files: dict[str, str | None] = {}
    rows = con.execute(
        """SELECT t.name, t.test_file, td.file_bitmap
           FROM tests t JOIN test_deps td ON t.id = td.test_id"""
    )
    for row in rows:
        ids = decode_blob(row["file_bitmap"])
        tests[row["name"]] = ids
        test_files[row["name"]] = row["test_file"]

    con.close()
    return files, tests, test_files


def analyze(files, tests, test_files, *, top_n=10, show_universal=False, show_isolation=False):
    n_tests = len(tests)
    n_files = len(files)
    if n_tests == 0:
        print("No tests found in database.")
        return

    dep_counts = [len(ids) for ids in tests.values()]
    all_dep_sets = list(tests.values())

    # --- Summary ---
    print(f"{'='*70}")
    print(f"  .testmondata analysis")
    print(f"{'='*70}")
    print(f"  Tests:          {n_tests:,}")
    print(f"  Tracked files:  {n_files:,}")
    print()

    # --- Per-test dep counts ---
    print(f"  File deps per test:")
    print(f"    min     {min(dep_counts):,}")
    print(f"    max     {max(dep_counts):,}")
    print(f"    mean    {sum(dep_counts)/n_tests:.1f}")
    print(f"    median  {sorted(dep_counts)[n_tests//2]:,}")
    print()

    # --- Distribution histogram ---
    buckets = [0, 1, 5, 10, 20, 50, 100, 200, 500, 1000, float("inf")]
    hist: dict[str, int] = {}
    for c in dep_counts:
        for i in range(len(buckets) - 1):
            if buckets[i] <= c < buckets[i + 1]:
                if buckets[i + 1] == float("inf"):
                    label = f"{int(buckets[i])}+"
                else:
                    label = f"{int(buckets[i])}-{int(buckets[i+1])-1}"
                hist[label] = hist.get(label, 0) + 1
                break
    print(f"  Distribution (file deps per test):")
    max_bar = max(hist.values()) if hist else 1
    for label, count in hist.items():
        bar = "█" * int(40 * count / max_bar)
        print(f"    {label:>8s}  {count:>6,}  {bar}")
    print()

    # --- Overlap analysis ---
    # Count how many tests reference each file
    file_refcount: Counter = Counter()
    for ids in all_dep_sets:
        for fid in ids:
            file_refcount[fid] += 1

    universal = {fid for fid, c in file_refcount.items() if c == n_tests}
    near_universal = {fid for fid, c in file_refcount.items() if c >= n_tests * 0.9}
    wide = {fid for fid, c in file_refcount.items() if c >= n_tests * 0.5}
    unique_to_one = {fid for fid, c in file_refcount.items() if c == 1}

    referenced_files = set(file_refcount.keys())
    unreferenced = set(files.keys()) - referenced_files

    print(f"  File overlap:")
    print(f"    Universal (100% of tests):  {len(universal):,} files")
    print(f"    Near-universal (>=90%):      {len(near_universal):,} files")
    print(f"    Wide (>=50%):                {len(wide):,} files")
    print(f"    Unique to 1 test:            {len(unique_to_one):,} files")
    print(f"    Unreferenced:                {len(unreferenced):,} files")
    print()

    # --- Per-test unique deps (after removing universal) ---
    unique_counts = [len(ids - universal) for ids in all_dep_sets]
    print(f"  Per-test unique deps (excluding {len(universal)} universal files):")
    print(f"    min     {min(unique_counts):,}")
    print(f"    max     {max(unique_counts):,}")
    print(f"    mean    {sum(unique_counts)/n_tests:.1f}")
    print(f"    median  {sorted(unique_counts)[n_tests//2]:,}")
    print()

    # --- By test file grouping ---
    by_file: dict[str, list[set[int]]] = {}
    for name, ids in tests.items():
        tf = test_files.get(name) or name.split("::")[0]
        by_file.setdefault(tf, []).append(ids)

    print(f"  Test files: {len(by_file):,}")
    file_unique_deps: dict[str, set[int]] = {}
    for tf, dep_sets in by_file.items():
        union = set()
        for ds in dep_sets:
            union |= ds
        file_unique_deps[tf] = union - universal

    file_dep_counts = [(tf, len(deps)) for tf, deps in file_unique_deps.items()]
    file_dep_counts.sort(key=lambda x: -x[1])

    print(f"\n  Top {top_n} test files by unique dep count (excl. universal):")
    for tf, count in file_dep_counts[:top_n]:
        n = len(by_file[tf])
        print(f"    {count:>4} deps  ({n:>4} tests)  {tf}")
    if len(file_dep_counts) > top_n:
        bottom = file_dep_counts[-1]
        print(f"    {'...':>4}")
        print(f"    {bottom[1]:>4} deps  ({len(by_file[bottom[0]]):>4} tests)  {bottom[0]}")
    print()

    # --- Most-referenced files ---
    top_files = file_refcount.most_common(top_n)
    print(f"  Top {top_n} most-depended-on files:")
    for fid, count in top_files:
        pct = 100 * count / n_tests
        path = files.get(fid, f"<id={fid}>")
        print(f"    {count:>6,} tests ({pct:5.1f}%)  {path}")
    print()

    # --- Least-referenced files (excluding unreferenced) ---
    bottom_files = file_refcount.most_common()
    bottom_files = [(fid, c) for fid, c in reversed(bottom_files) if c > 0][:top_n]
    print(f"  Bottom {top_n} least-depended-on files:")
    for fid, count in bottom_files:
        pct = 100 * count / n_tests
        path = files.get(fid, f"<id={fid}>")
        print(f"    {count:>6,} tests ({pct:5.1f}%)  {path}")
    print()

    # --- Storage efficiency ---
    total_edges = sum(dep_counts)
    naive_bytes = total_edges * 8  # 2x int32 per (test_id, file_id) row
    print(f"  Storage:")
    print(f"    Total test→file edges:  {total_edges:,}")
    print(f"    Naive junction table:   {naive_bytes/1024:.0f} KB ({total_edges:,} rows × 8 bytes)")
    print(f"    Bitmap (zstd+roaring):  see DB blob sizes")
    print()

    # --- Optional: show universal files ---
    if show_universal and universal:
        print(f"  Universal files ({len(universal)}):")
        for fid in sorted(universal):
            print(f"    {files.get(fid, f'<id={fid}>')}")
        print()

    # --- Optional: isolation analysis ---
    if show_isolation:
        print(f"  Test isolation (pairwise overlap sample):")
        test_names = list(tests.keys())
        import random
        random.seed(42)
        pairs = []
        sample_size = min(1000, n_tests * (n_tests - 1) // 2)
        seen = set()
        while len(pairs) < sample_size:
            i = random.randint(0, n_tests - 1)
            j = random.randint(0, n_tests - 1)
            if i == j or (i, j) in seen:
                continue
            seen.add((i, j))
            seen.add((j, i))
            a = all_dep_sets[i]
            b = all_dep_sets[j]
            if not a or not b:
                continue
            overlap = len(a & b)
            union = len(a | b)
            pairs.append(overlap / union if union else 0)

        if pairs:
            pairs.sort()
            print(f"    Sampled {len(pairs):,} random test pairs (Jaccard similarity):")
            print(f"      min     {pairs[0]:.3f}")
            print(f"      p25     {pairs[len(pairs)//4]:.3f}")
            print(f"      median  {pairs[len(pairs)//2]:.3f}")
            print(f"      p75     {pairs[3*len(pairs)//4]:.3f}")
            print(f"      max     {pairs[-1]:.3f}")
            high_overlap = sum(1 for p in pairs if p > 0.8)
            print(f"      >80% overlap: {high_overlap}/{len(pairs)} ({100*high_overlap/len(pairs):.1f}%)")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a .testmondata database: dep counts, overlap, storage."
    )
    parser.add_argument("db", help="Path to .testmondata SQLite file")
    parser.add_argument("--top", type=int, default=10, help="Number of top/bottom items to show")
    parser.add_argument("--show-universal", action="store_true", help="List all universal files")
    parser.add_argument("--show-isolation", action="store_true", help="Pairwise overlap analysis")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"Error: {args.db} not found", file=sys.stderr)
        return 1

    files, tests, test_files = load_data(args.db)
    analyze(files, tests, test_files, top_n=args.top,
            show_universal=args.show_universal, show_isolation=args.show_isolation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
