"""Stable policy interface plus offline and OpenAI-compatible backends."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from .config import SYSTEM_PROMPT
from .schemas import AgentConfig, PolicyDecision


class PolicyBackend(Protocol):
    def reset_episode(self) -> None: ...
    def decide(self, state_text: str) -> PolicyDecision: ...


class FakePolicyBackend:
    """Deterministic backend for tests and protocol fault isolation only."""

    def __init__(self, actions: list[int | str] | None = None) -> None:
        self.actions = list(actions or [1, 9])
        if not self.actions:
            raise ValueError("fake action sequence cannot be empty")
        self.index = 0
        self.messages: list[dict[str, Any]] = []

    def reset_episode(self) -> None:
        self.index = 0
        self.messages = []

    def decide(self, state_text: str) -> PolicyDecision:
        raw = str(self.actions[min(self.index, len(self.actions) - 1)])
        self.index += 1
        self.messages.extend([
            {"role": "user", "content": state_text},
            {"role": "assistant", "content": raw},
        ])
        return PolicyDecision(raw_text=raw, reasoning_content=None, messages=list(self.messages), latency_ms=0.0)


class OpenAICompatiblePolicyBackend:
    """Small diagnostic backend; Hermes remains the production default."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.messages: list[dict[str, Any]] = []

    def reset_episode(self) -> None:
        self.messages = []

    def decide(self, state_text: str) -> PolicyDecision:
        request_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.messages, {"role": "user", "content": state_text}]
        body = json.dumps({"model": self.config.model, "messages": request_messages}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        endpoint = self.config.api_base.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key or 'EMPTY'}"}
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            message = payload["choices"][0]["message"]
            raw_text = str(message["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenAI-compatible response has no assistant content") from exc
        reasoning = message.get("reasoning_content")
        self.messages.extend([{"role": "user", "content": state_text}, dict(message)])
        return PolicyDecision(raw_text=raw_text, reasoning_content=reasoning, messages=list(self.messages), latency_ms=latency_ms)


def create_policy(config: AgentConfig) -> PolicyBackend:
    if config.backend == "fake":
        return FakePolicyBackend()
    if config.backend == "openai":
        return OpenAICompatiblePolicyBackend(config)
    from .hermes_adapter import HermesPolicyBackend

    return HermesPolicyBackend(config)

