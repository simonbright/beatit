#!/usr/bin/env bash
# Launch the BeatIt server fully detached so it survives the parent shell.
set -euo pipefail
cd "$(dirname "$0")/.."
pkill -f "run.py" 2>/dev/null || true
sleep 1
LOG="/tmp/beatit_server.log"
nohup .venv/bin/python run.py >"$LOG" 2>&1 </dev/null &
disown || true
echo "Launched BeatIt server; logging to $LOG"
