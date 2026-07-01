#!/usr/bin/env bash
# Test Ollama reachability (e.g. over Tailscale). Usage:
#   ./scripts/check_ollama.sh
#   ./scripts/check_ollama.sh http://100.92.208.65:11434
#   OLLAMA_BASE_URL=http://100.92.208.65:11434 ./scripts/check_ollama.sh

set -euo pipefail

BASE="${1:-${OLLAMA_BASE_URL:-http://127.0.0.1:11434}}"
BASE="${BASE%/}"
TAGS_URL="${BASE}/api/tags"
GEN_URL="${BASE}/api/generate"

echo "Checking Ollama at ${BASE} ..."

if ! curl -sf --connect-timeout 5 "${TAGS_URL}" -o /tmp/beatit_ollama_tags.json; then
  echo "FAIL: cannot reach ${TAGS_URL}"
  echo "  - Is Ollama running on the VM?"
  echo "  - Is OLLAMA_HOST=0.0.0.0 set and Ollama restarted?"
  echo "  - Is Tailscale up and firewall allowing TCP 11434?"
  exit 1
fi

echo "OK: /api/tags"
python3 - <<'PY'
import json
with open("/tmp/beatit_ollama_tags.json") as f:
    data = json.load(f)
models = [m.get("name") for m in data.get("models", [])]
if models:
    print("Models:", ", ".join(models))
else:
    print("No models pulled yet. On the VM run: ollama pull qwen2.5:7b-instruct")
PY

MODEL="${OLLAMA_MODEL:-}"
if [[ -n "${MODEL}" ]]; then
  if python3 - <<PY
import json
with open("/tmp/beatit_ollama_tags.json") as f:
    models = [m.get("name","") for m in json.load(f).get("models",[])]
target = "${MODEL}"
ok = any(target in (n or "") for n in models)
print("yes" if ok else "no")
PY
  then
    echo "OK: OLLAMA_MODEL=${MODEL} is available"
  else
    echo "WARN: OLLAMA_MODEL=${MODEL} not in tag list — run ollama pull ${MODEL} on the VM"
  fi
fi

echo "Done."
