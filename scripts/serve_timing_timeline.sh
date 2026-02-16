#!/bin/bash
set -e

DIR="${1:-/tmp/ezmon_timing}"
PORT="${2:-8000}"
OPEN_BROWSER=0
if [ "${3:-}" = "--open" ]; then
  OPEN_BROWSER=1
fi

if [ ! -d "$DIR" ]; then
  echo "Directory not found: $DIR" >&2
  exit 1
fi

URL="http://localhost:$PORT/timing_timeline.html"
echo "Serving $DIR on $URL"
cd "$DIR"
if [ "$OPEN_BROWSER" -eq 1 ]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  fi
fi
python -m http.server "$PORT"
