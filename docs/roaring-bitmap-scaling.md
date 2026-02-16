# Roaring Bitmap Scaling & Inverted Index Benchmark

This doc summarizes a controlled benchmark to estimate bitmap storage size and affected‑test query cost at large scale, and compares **forward** vs **inverted** bitmap models. The scale parameters are chosen to be **pandas‑like** (thousands of tracked files and hundreds of thousands of tests). We also assume a **high overlap of dependencies** because our safe selection strategy treats a file as a dependency for a test if it would be imported or read when that test is run in isolation.

## Experiment Setup

We used `scripts/estimate_bitmap_size.py` with the following parameters:

- **Tests:** 200,000
- **Files:** 5,000
- **Deps/test:** 70% of files (≈ 3,500 file IDs per test)
- **Roaring:** `pyroaring` enabled (same serializer as the plugin)
- **Compression:** zstd level 3 (same as `ezmon/bitmap_deps.py`)
- **Benchmark sweep:** changed files from **1 file**, then **+20% steps**, up to **all but one file**
- **Repeats:** 3 per sweep point

Commands used:

```bash
# Size + forward benchmark
PYTHONPATH=/Users/andrew_yos/pytest-super/nocov-refactor \
  .venv/bin/python scripts/estimate_bitmap_size.py \
  --tests 200000 --files 5000 --dep-ratio 0.7 --samples 200 \
  --bench --bench-tests 200000 --bench-repeats 3 --percent-step 20 \
  --csv /tmp/ezmon_benchmark.csv --graph /tmp/ezmon_benchmark.svg

# Size + inverted benchmark
PYTHONPATH=/Users/andrew_yos/pytest-super/nocov-refactor \
  .venv/bin/python scripts/estimate_bitmap_size.py \
  --tests 200000 --files 5000 --dep-ratio 0.7 --samples 200 \
  --bench --bench-tests 200000 --bench-repeats 3 --percent-step 20 \
  --bench-mode inverted \
  --csv /tmp/ezmon_benchmark_inverted.csv --graph /tmp/ezmon_benchmark_inverted.svg
```

Outputs:

- Forward: `/tmp/ezmon_benchmark.csv`, `/tmp/ezmon_benchmark.svg`
- Inverted: `/tmp/ezmon_benchmark_inverted.csv`, `/tmp/ezmon_benchmark_inverted.svg`

## Storage Size Results

Using **real Roaring serialization** (pyroaring) and **zstd level 3**:

- **Avg raw bitmap size**: ~4,216 bytes/test
- **Avg zstd bitmap size**: ~2,782 bytes/test

Estimated total for 200,000 tests:

- **Raw**: ~0.79 GiB
- **Zstd**: **~0.52 GiB**

These sizes match the plugin’s storage path.

## Query Cost Benchmark (Affected Tests)

We measured `find_affected_tests` time as the **affected file ratio** increases:

Sweep points (changed file count):

- 1 file
- 20% of files (1,000)
- 40% (2,000)
- 60% (3,000)
- 80% (4,000)
- all but one file (4,999)

The benchmark outputs:

- `changed_file_count`
- `affected_ratio`
- `seconds` (mean of 3 runs)

## Results (DB End‑to‑End)

**Forward model (tests → file bitmap, current schema)**  
DB query: `find_affected_tests_bitmap` over `test_deps`

- changed files **1** → affected ratio **0.70** → **1.82s**
- changed files **1,000** (20%) → affected ratio **1.00** → **6.53s**
- changed files **2,000** (40%) → affected ratio **1.00** → **6.83s**
- changed files **3,000** (60%) → affected ratio **1.00** → **9.89s**
- changed files **4,000** (80%) → affected ratio **1.00** → **9.57s**
- changed files **4,999** (all‑but‑one) → affected ratio **1.00** → **10.67s**

**Inverted model (files → test bitmap)**  
DB query: OR bitmaps from `file_tests`

- changed files **1** → affected ratio **0.70** → **~0.0000s** (below timer resolution)
- changed files **1,000** (20%) → affected ratio **1.00** → **0.027s**
- changed files **2,000** (40%) → affected ratio **1.00** → **0.058s**
- changed files **3,000** (60%) → affected ratio **1.00** → **0.085s**
- changed files **4,000** (80%) → affected ratio **1.00** → **0.110s**
- changed files **4,999** (all‑but‑one) → affected ratio **1.00** → **0.138s**

**Speedup (forward ÷ inverted)**  
Ranges from **~239×** at 20% changed files to **~78×** at all‑but‑one.

> Note: with a 70% dependency ratio, even a single changed file impacts ~70% of tests. That’s why the affected ratio is already 0.70 at “1 file”.

## Storage Growth (SQLite DB size)

Measured DB file size with both models:

- **50k tests / 5k files**: forward **0.199 GiB**, inverted **0.029 GiB**
- **100k tests / 5k files**: forward **0.397 GiB**, inverted **0.057 GiB**
- **200k tests / 5k files**: forward **0.795 GiB**, inverted **0.117 GiB**

Inverted storage is **~6–7× smaller** in this synthetic high‑overlap scenario.

## Forward vs Inverted Models

**Forward model (current)**:

- Store **test → file bitmap**
- Query: for each test, check bitmap intersection with changed files
- Cost grows with number of tests (`O(#tests)`)

**Inverted model**:

- Store **file → test bitmap**
- Query: OR bitmaps for changed files
- Cost grows with number of changed files (`O(#changed_files)`)

In practice (with the same parameters), the inverted model is significantly faster when:

- Many tests exist
- The set of changed files is small to moderate

The SVG graphs and CSVs above quantify this difference.

## Implication

At large scale (hundreds of thousands of tests), **inverting the bitmaps** provides a clear path to faster selection:

- It trades a large scan over tests for a smaller OR over changed files.
- With sparse changes, query time is dominated by a handful of bitmap ORs.

If we pursue this, we’ll need:

- A `file_tests` table (file_id → test bitmap)
- Optional retention of per‑test deps only if needed for rebuilds/debugging
