#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_REPO="${HERMES_REPO:-$ROOT/.vendor/hermes-agent}"

if [[ ! -d "$HERMES_REPO/.git" ]]; then
  git clone https://github.com/NousResearch/hermes-agent.git "$HERMES_REPO"
fi

cd "$HERMES_REPO"
uv sync

echo "Hermes commit: $(git rev-parse HEAD)"
echo "export HERMES_REPO=$HERMES_REPO"

