#!/bin/bash
# Run pandas pipeline benchmark (plugin-only) from a given commit
# Usage: ./scripts/run_pandas_bench.sh [start_sha]
# Results saved to profile/pandas/pipeline_results.json
# Log written to /tmp/pandas_benchmark.log

cd /Users/andrew_yos/pytest-super/nocov-refactor

# Clean stale namespace packages
rm -rf /Users/andrew_yos/tw/pandas/.conda-env/lib/python3.14/site-packages/ezmon/ 2>/dev/null

START="${1:-d19808b3ad}"
echo "Starting pandas benchmark from $START..."
echo "Results: profile/pandas/pipeline_results.json"
echo "Log: /tmp/pandas_benchmark.log"

exec /Users/andrew_yos/tw/pandas/.conda-env/bin/python -u \
    scripts/benchmark_pipeline.py pandas --plugin-only --start-from "$START" \
    2>&1 | tee /tmp/pandas_benchmark.log
