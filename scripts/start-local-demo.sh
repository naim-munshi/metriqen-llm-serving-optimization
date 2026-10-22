#!/usr/bin/env bash
# Local development demo only. No production credentials, public listeners or background agents.
set -euo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
RUN_DIR="$ROOT/reports/local/demo-run"
TOKEN_FILE="$ROOT/reports/local/.model-operator-token"
MODEL_DB="$ROOT/reports/local/models.sqlite3"
PYTHON="$ROOT/.venv/bin/python3"
MODEL="llama3.2:1b"
OLLAMA_URL="http://127.0.0.1:11434"
GATEWAY_URL="http://127.0.0.1:8081"
CONTROL_URL="http://127.0.0.1:8000"

die() { printf 'Metriqen startup blocked: %s\n' "$*" >&2; exit 1; }
healthy() { curl --silent --show-error --fail --max-time 3 "$1" >/dev/null 2>&1; }
listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
wait_for() {
  local url="$1" i
  for ((i=0; i<30; i++)); do
    if healthy "$url"; then return 0; fi
    sleep 1
  done
  return 1
}

[[ "$(uname -s)" == "Darwin" ]] || die "This launcher is restricted to the owner's macOS machine."
[[ -x "$PYTHON" ]] || die "Missing local .venv; install control API dev dependencies before launching."
for executable in curl go ollama open lsof nohup; do
  command -v "$executable" >/dev/null 2>&1 || die "Missing command: $executable"
done
mkdir -p "$RUN_DIR"
chmod 700 "$ROOT/reports/local" "$RUN_DIR"

# Retain the existing credential; never print or silently rotate it.
"$PYTHON" - "$TOKEN_FILE" <<'PY'
import os
from pathlib import Path
import secrets
import stat
import sys

path = Path(sys.argv[1])
if path.is_symlink():
    raise SystemExit("Operator token path must not be a symlink")
if path.exists():
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise SystemExit("Existing operator token must be a private regular file (0600)")
    value = path.read_text(encoding="ascii").strip()
    if not 32 <= len(value) <= 256 or not value.isascii() or not value.isprintable() or " " in value:
        raise SystemExit("Existing operator token is invalid; do not overwrite it")
else:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as output:
        output.write(secrets.token_urlsafe(40))
PY

if healthy "$OLLAMA_URL/api/version"; then
  echo "Ollama: reusing the existing local server."
else
  listening 11434 && die "Port 11434 is occupied but Ollama is unhealthy. Inspect the existing process."
  echo "Ollama: starting a local server."
  if open -Ra Ollama >/dev/null 2>&1; then
    open -a Ollama || die "Unable to start Ollama.app"
  else
    (nohup ollama serve >>"$RUN_DIR/ollama.log" 2>&1 < /dev/null & echo $! >"$RUN_DIR/ollama.pid")
  fi
  wait_for "$OLLAMA_URL/api/version" || die "Ollama startup failed; inspect reports/local/demo-run/ollama.log."
fi

ollama show "$MODEL" >/dev/null 2>&1 || die "Model not installed. Run 'ollama pull llama3.2:1b' over a trusted connection."

if healthy "$GATEWAY_URL/console"; then
  healthy "$GATEWAY_URL/health/upstream" || die "Existing gateway cannot reach Ollama; inspect its configuration."
  echo "Gateway: reusing the existing inference server."
else
  listening 8081 && die "Port 8081 occupied by another or unhealthy process. Do not start a duplicate gateway."
  echo "Gateway: compiling and starting on 127.0.0.1:8081."
  (cd "$ROOT/services/inference-gateway" && go build -o "$RUN_DIR/metriqen-gateway" ./cmd/server)
  (nohup env -i PATH="$PATH" HOME="$HOME" TMPDIR="/tmp" \
    METRIQEN_UPSTREAM_URL="$OLLAMA_URL" \
    METRIQEN_ALLOWED_MODELS="$MODEL" METRIQEN_QUEUE_DEPTH=0 \
    "$RUN_DIR/metriqen-gateway" >>"$RUN_DIR/gateway.log" 2>&1 < /dev/null & echo $! >"$RUN_DIR/gateway.pid")
  wait_for "$GATEWAY_URL/console" || die "Gateway startup failed; inspect reports/local/demo-run/gateway.log."
  healthy "$GATEWAY_URL/health/upstream" || die "Gateway is running but Ollama upstream probe failed."
fi

if healthy "$CONTROL_URL/console/models"; then
  echo "Model Manager: reusing the existing FastAPI server."
else
  listening 8000 && die "Port 8000 occupied or model manager disabled. Inspect the existing FastAPI process."
  echo "Model Manager: starting on 127.0.0.1:8000."
  (
    cd "$ROOT/services/control-api"
    exec nohup env -i PATH="$PATH" HOME="$HOME" TMPDIR="/tmp" \
      METRIQEN_LOCAL_MODEL_TOKEN="$(cat "$TOKEN_FILE")" \
      METRIQEN_LOCAL_MODEL_DB="$MODEL_DB" \
      "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ) >>"$RUN_DIR/control-api.log" 2>&1 < /dev/null &
  echo $! >"$RUN_DIR/control-api.pid"
  wait_for "$CONTROL_URL/console/models" || die "FastAPI startup failed; inspect reports/local/demo-run/control-api.log."
fi

# The anonymous model registry API must exist and deny unauthenticated access.
code="$(curl --silent --output /dev/null --max-time 4 --write-out '%{http_code}' "$CONTROL_URL/api/v1/local/models")"
[[ "$code" == 401 ]] || die "Model Manager API is not protected/available (HTTP $code). Check port 8000."

echo "Ollama: preloading existing $MODEL for local demo (no model download)."
curl --silent --show-error --fail --max-time 120 \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:1b","keep_alive":"10m"}' \
  "$OLLAMA_URL/api/generate" >/dev/null || die "Existing model preload failed."

# A bounded real request checks Go -> Ollama; it is not a benchmark or P1 sign-off.
echo "Gateway: performing a bounded real non-streaming chat smoke."
curl --silent --show-error --fail --max-time 125 \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Reply ready"}],"temperature":0,"max_tokens":16,"stream":false}' \
  "$GATEWAY_URL/v1/chat/completions" | "$PYTHON" -c 'import json,sys; j=json.load(sys.stdin); assert isinstance(j["choices"][0]["message"]["content"],str)' \
  || die "Real Go/Ollama smoke failed. Check gateway settings (including any existing bearer guard)."

echo "Local demo endpoints:"
echo "  Operations:    $GATEWAY_URL/console"
echo "  Model Manager: $CONTROL_URL/console/models"
echo "Model approval and deployment changes must be performed explicitly in the UI."
echo "Operator token stays private: reports/local/.model-operator-token"
echo "Account/OIDC Console is NOT enabled by this local-only launcher."
open "$GATEWAY_URL/console" >/dev/null 2>&1 || true
open "$CONTROL_URL/console/models" >/dev/null 2>&1 || true
echo "Local demo started and real chat smoke completed. No release gates auto-approved."
