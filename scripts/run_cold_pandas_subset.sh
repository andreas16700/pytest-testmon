#!/usr/bin/env bash
set -euo pipefail

# Cold run helper for pandas subset, with/without ezmon.
# Usage examples:
#   scripts/run_cold_pandas_subset.sh --subset pandas/tests/frame --mode baseline
#   scripts/run_cold_pandas_subset.sh --subset pandas/tests/frame --mode plugin --timing
#   scripts/run_cold_pandas_subset.sh --subset pandas/tests/frame --mode both --timing

subset="pandas/tests/frame"
mode="both"        # baseline | plugin | both
timing="0"         # 1 to enable EZMON timing logs
timing_dir=""      # override timing dir
python_bin="${PYTHON_BIN:-/Users/andrew_yos/mamba/envs/pandas-dev/bin/python}"
pandas_root="${PANDAS_ROOT:-/Users/andrew_yos/pandas/pandas-profile}"
cache_root="${CACHE_ROOT:-/tmp/ezmon_cold}"
xdist_args="-n auto"
mark_expr='not slow and not db and not network and not single_cpu'
purge_fs="0"       # 1 to attempt OS cache purge (requires sudo on macOS)
drop_caches_cmd="" # optional custom drop-caches command

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subset) subset="$2"; shift 2;;
    --mode) mode="$2"; shift 2;;
    --timing) timing="1"; shift;;
    --timing-dir) timing_dir="$2"; shift 2;;
    --python) python_bin="$2"; shift 2;;
    --pandas-root) pandas_root="$2"; shift 2;;
    --cache-root) cache_root="$2"; shift 2;;
    --purge-fs) purge_fs="1"; shift;;
    --drop-caches-cmd) drop_caches_cmd="$2"; shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

run_one () {
  local label="$1"
  local plugin_args=()
  local env_args=()

  rm -rf "${cache_root}/${label}" || true
  mkdir -p "${cache_root}/${label}"

  # isolate bytecode + pytest cache
  env_args+=(PYTHONPYCACHEPREFIX="${cache_root}/${label}/pycache")
  env_args+=(PYTEST_DISABLE_PLUGIN_AUTOLOAD=0)

  if [[ "${purge_fs}" == "1" ]]; then
    if [[ -n "${drop_caches_cmd}" ]]; then
      echo "Dropping OS caches via: ${drop_caches_cmd}"
      eval "${drop_caches_cmd}" || true
    elif command -v purge >/dev/null 2>&1; then
      echo "Dropping OS caches via: sudo purge"
      sudo purge || true
    else
      echo "OS cache purge requested but no purge tool found."
    fi
  fi

  if [[ "${label}" == "plugin" ]]; then
    plugin_args+=(--ezmon --ezmon-forceselect --ezmon-no-reorder)
    if [[ "${timing}" == "1" ]]; then
      local td="${timing_dir:-/tmp/ezmon_timing_${label}_$(date +%s)}"
      env_args+=(EZMON_XDIST_TIMING_LOG_DIR="${td}")
      env_args+=(EZMON_XDIST_TIMING_FLUSH_ALL=1)
      env_args+=(EZMON_CORE_TIMING=1)
      env_args+=(EZMON_CORE_TIMING_VERBOSE=1)
      echo "Timing dir: ${td}"
    fi
  else
    plugin_args+=(-p no:ezmon.pytest_ezmon)
  fi

  echo "=== ${label} (cold) ==="
  echo "subset: ${subset}"
  echo "python: ${python_bin}"
  echo "root:   ${pandas_root}"
  echo "cache:  ${cache_root}/${label}"

  # Use --cache-clear to clear pytest cache in repo
  (cd "${pandas_root}" && \
    /usr/bin/time -p env "${env_args[@]}" \
    "${python_bin}" -m pytest -r fE ${xdist_args} \
      -m "${mark_expr}" --cache-clear \
      -p xdist "${plugin_args[@]}" "${subset}")
}

case "${mode}" in
  baseline) run_one "baseline";;
  plugin) run_one "plugin";;
  both)
    run_one "baseline"
    run_one "plugin"
    ;;
  *) echo "Invalid mode: ${mode}"; exit 1;;
esac
