#!/usr/bin/env bash
# Start BeatIt locally in the background (stable — no file watcher).
# Usage: ./scripts/start_local.sh
# Stop:  ./scripts/stop_local.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${BEATIT_LOG:-/tmp/beatit-server.log}"
PIDFILE="${BEATIT_PIDFILE:-/tmp/beatit-server.pid}"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PIDFILE")) → http://localhost:8080"
  exit 0
fi

if lsof -ti :8080 >/dev/null 2>&1; then
  echo "Port 8080 in use — stopping existing process…"
  lsof -ti :8080 | xargs kill -9 2>/dev/null || true
  sleep 1
fi

: > "$LOG"
# shellcheck disable=SC2094
BEATIT_RELOAD="${BEATIT_RELOAD:-0}" nohup "$ROOT/.venv/bin/python" -u "$ROOT/run.py" >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 2
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "BeatIt started (pid $(cat "$PIDFILE")) → http://localhost:8080"
  echo "Log: $LOG"
else
  echo "Failed to start — see $LOG"
  tail -n 30 "$LOG" || true
  exit 1
fi
