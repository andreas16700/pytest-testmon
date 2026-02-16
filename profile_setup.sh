#!/bin/bash
# Setup script for profiling nocov-refactor "0 tests selected" case
#
# This creates a ./profile directory with:
# - pandas repo at run 3 commit (7affab288b)

set -e

PROFILE_DIR="$(pwd)/profile"
RUN3_COMMIT="7affab288bcbd2eeada128f55fc8800d2688f112"

echo "=== Setting up profile directory ==="

# Clean and create profile dir
rm -rf "$PROFILE_DIR"
mkdir -p "$PROFILE_DIR"

# Clone pandas repo
echo "Cloning pandas repo..."
git clone --no-checkout /Users/andrew_yos/pandas/pandas-repo "$PROFILE_DIR/pandas"

# Checkout the specific commit
echo "Checking out commit $RUN3_COMMIT..."
cd "$PROFILE_DIR/pandas"
git checkout "$RUN3_COMMIT"

echo ""
echo "=== Setup complete ==="
echo "Profile dir: $PROFILE_DIR"
echo "Pandas commit: $RUN3_COMMIT"
echo ""
echo "Next: run ./profile_run.sh to execute the profiled test run"
