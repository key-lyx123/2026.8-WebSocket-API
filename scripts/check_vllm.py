"""Health-check a real OpenAI-compatible vLLM endpoint without extra packages."""

from __future__ import annotations

import argparse
import json
import urllib.request

from agent.action_parser import parse_action_id
from agent.config import SYSTEM_PROMPT


def request_json(url: str, body: dict | None = None, api_key: str = "EMPTY") -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="drone-nav-lora")
    parser.add_argument("--api-key", default="EMPTY")
    args = parser.parse_args()
    base = args.api_base.rstrip("/")
    models = request_json(base + "/models", api_key=args.api_key)
    names = [item.get("id") for item in models.get("data", [])]
    if args.model not in names:
        raise RuntimeError(f"model alias {args.model!r} not found; available={names}")
    completion = request_json(base + "/chat/completions", {
        "model": args.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "当前状态安全且目标在正前方。只输出动作编号。"},
        ],
        "temperature": 0,
        "max_tokens": 8,
    }, api_key=args.api_key)
    content = completion["choices"][0]["message"]["content"]
    action_id = parse_action_id(content)
    print(f"vLLM health check passed: model={args.model}, action_id={action_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

