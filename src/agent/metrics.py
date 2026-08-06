"""Episode metrics with finite JSON-safe outputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError, validate_obs
from .schemas import EpisodeResult


def _position(obs: dict[str, Any]) -> tuple[float, float, float]:
    return tuple(float(value) for value in obs["agent"]["pos"])  # type: ignore[return-value]


@dataclass
class EpisodeMetrics:
    path_length: float = 0.0
    min_obstacle_dist: float | None = None

    @classmethod
    def from_initial_obs(cls, obs: dict[str, Any]) -> "EpisodeMetrics":
        validate_obs(obs)
        result = cls()
        result._observe_obstacles(obs)
        return result

    def _observe_obstacles(self, obs: dict[str, Any]) -> None:
        distances = [float(item["distance"]) for item in obs["obstacles"] if "distance" in item]
        if distances:
            candidate = min(distances)
            if not math.isfinite(candidate):
                raise ProtocolError("obstacle distance must be finite")
            self.min_obstacle_dist = candidate if self.min_obstacle_dist is None else min(self.min_obstacle_dist, candidate)

    def update(self, previous_obs: dict[str, Any], next_obs: dict[str, Any]) -> None:
        validate_obs(next_obs)
        previous = _position(previous_obs)
        current = _position(next_obs)
        self.path_length += math.dist(previous, current)
        self._observe_obstacles(next_obs)

    def finalize(self, seed: int, terminal_obs: dict[str, Any]) -> EpisodeResult:
        validate_obs(terminal_obs)
        if not terminal_obs["done"]:
            raise ProtocolError("cannot finalize a non-terminal observation")
        flags = terminal_obs["flags"]
        if flags["collision"]:
            outcome = "collision"
        elif flags["goal_reached"]:
            outcome = "success"
        elif flags["timeout"]:
            outcome = "timeout"
        else:  # guarded by validate_obs, retained for defensive clarity
            raise ProtocolError("terminal observation has no outcome")
        return EpisodeResult(
            seed=seed,
            outcome=outcome,
            steps=terminal_obs["step_id"],
            final_dist=float(terminal_obs["goal"]["dist"]),
            path_length=self.path_length,
            min_obstacle_dist=self.min_obstacle_dist,
        )

