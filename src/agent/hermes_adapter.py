"""Hermes Agent compatibility boundary and episode conversation state."""

from __future__ import annotations

import importlib
import inspect
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import SYSTEM_PROMPT
from .schemas import AgentConfig, PolicyDecision


class HermesUnavailableError(RuntimeError):
    pass


def load_ai_agent() -> type:
    try:
        module = importlib.import_module("run_agent")
        return module.AIAgent
    except (ImportError, AttributeError) as first_error:
        repo = os.environ.get("HERMES_REPO")
        if repo:
            resolved = Path(repo).expanduser().resolve()
            if resolved.is_dir() and str(resolved) not in sys.path:
                sys.path.insert(0, str(resolved))
            try:
                module = importlib.import_module("run_agent")
                return module.AIAgent
            except (ImportError, AttributeError):
                pass
        raise HermesUnavailableError(
            "Hermes Agent is unavailable. Run scripts/setup_hermes.sh, then set "
            "HERMES_REPO=/absolute/path/to/hermes-agent. No download is performed automatically."
        ) from first_error


def _construct_agent(agent_class: type, config: AgentConfig) -> Any:
    signature = inspect.signature(agent_class)
    parameters = signature.parameters
    accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
    candidates: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key or "EMPTY",
        "quiet_mode": True,
        "skip_context_files": True,
        "skip_memory": True,
        "enabled_toolsets": [],
        "max_iterations": 1,
        "ephemeral_system_prompt": SYSTEM_PROMPT,
    }
    if "base_url" in parameters or accepts_kwargs:
        candidates["base_url"] = config.api_base
    elif "api_base" in parameters:
        candidates["api_base"] = config.api_base
    kwargs = candidates if accepts_kwargs else {key: value for key, value in candidates.items() if key in parameters}
    return agent_class(**kwargs)


def _reasoning_from_messages(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("reasoning_content") is not None:
            return str(message["reasoning_content"])
    return None


class HermesPolicyBackend:
    def __init__(self, config: AgentConfig, agent_class: type | None = None) -> None:
        self.config = config
        self.agent = _construct_agent(agent_class or load_ai_agent(), config)
        self.history: list[dict[str, Any]] = []

    def reset_episode(self) -> None:
        self.history = []

    def decide(self, state_text: str) -> PolicyDecision:
        started = time.perf_counter()
        result = self.agent.run_conversation(user_message=state_text, conversation_history=self.history)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(result, dict):
            raise RuntimeError("Hermes run_conversation returned a non-dictionary result")
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Hermes result has no messages list")
        self.history = [dict(message) for message in messages]
        raw = result.get("final_response")
        if isinstance(raw, dict):
            raw = raw.get("content")
        if raw is None:
            for message in reversed(self.history):
                if message.get("role") == "assistant" and message.get("content") is not None:
                    raw = message["content"]
                    break
        if raw is None:
            raise RuntimeError("Hermes result has no final response")
        return PolicyDecision(
            raw_text=str(raw),
            reasoning_content=_reasoning_from_messages(self.history),
            messages=list(self.history),
            latency_ms=latency_ms,
        )

