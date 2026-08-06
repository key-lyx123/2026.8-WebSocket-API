"""Small public data structures used across agent modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgentConfig:
    model: str = "drone-nav-lora"
    api_base: str = "http://127.0.0.1:8000/v1"
    api_key: str = "EMPTY"
    backend: str = "hermes"
    request_timeout_s: float = 180.0
    max_action_retries: int = 1
    invalid_action_fallback: int = 0

    def __post_init__(self) -> None:
        if self.backend not in {"hermes", "fake", "openai"}:
            raise ValueError(f"unsupported backend: {self.backend}")
        if not 0 <= self.invalid_action_fallback <= 9:
            raise ValueError("invalid_action_fallback must be between 0 and 9")
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        if self.max_action_retries < 0:
            raise ValueError("max_action_retries cannot be negative")

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "AgentConfig":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass
class EpisodeResult:
    seed: int
    outcome: str
    steps: int
    final_dist: float
    path_length: float
    min_obstacle_dist: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyDecision:
    raw_text: str
    reasoning_content: str | None
    messages: list[dict[str, Any]]
    latency_ms: float

