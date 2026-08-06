#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python scripts/check_vllm.py \
  --api-base "${AGENT_API_BASE:-http://127.0.0.1:8000/v1}" \
  --api-key "${AGENT_API_KEY:-EMPTY}" \
  --model "${MODEL_ALIAS:-drone-nav-lora}"

python -m agent.run_agent \
  --backend hermes \
  --sim-url "${SIM_URL:-ws://127.0.0.1:8765}" \
  --api-base "${AGENT_API_BASE:-http://127.0.0.1:8000/v1}" \
  --api-key "${AGENT_API_KEY:-EMPTY}" \
  --model "${MODEL_ALIAS:-drone-nav-lora}" \
  --seeds "${SEEDS:-100}" \
  --output "${OUTPUT:-results/live_smoke.jsonl}" \
  --overwrite

