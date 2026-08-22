#!/usr/bin/env bash
# Stop the background BeatIt server started by scripts/start_local.sh

set -euo pipefail
PIDFILE="${BEATIT_PIDFILE:-/tmp/beatit-server.pid}"

if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "Stopped pid $pid"
  else
    echo "Stale pidfile (process not running)"
  fi
  rm -f "$PIDFILE"
fi

if lsof -ti :8080 >/dev/null 2>&1; then
  lsof -ti :8080 | xargs kill -9 2>/dev/null || true
  echo "Cleared port 8080"
fi
