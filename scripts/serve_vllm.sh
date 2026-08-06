#!/usr/bin/env bash
set -euo pipefail

BASE_MODEL="${BASE_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
LORA_PATH="${LORA_PATH:-./outputs/lora_weights}"
MODEL_ALIAS="${MODEL_ALIAS:-drone-nav-lora}"
PORT="${VLLM_PORT:-8000}"
HOST="${VLLM_HOST:-127.0.0.1}"

if [[ ! -d "$LORA_PATH" ]]; then
  echo "LoRA path does not exist: $LORA_PATH" >&2
  exit 2
fi

echo "Serving model alias: $MODEL_ALIAS"
echo "Base model: $BASE_MODEL"
echo "LoRA path: $LORA_PATH"
echo "Endpoint: http://$HOST:$PORT/v1"

vllm serve "$BASE_MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --enable-lora \
  --lora-modules "${MODEL_ALIAS}=${LORA_PATH}" \
  --generation-config vllm

