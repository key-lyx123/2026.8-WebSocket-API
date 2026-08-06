"""WebSocket wire protocol implementation for api.md protocol 1.x."""

from __future__ import annotations

import json
import math
from typing import Any

from websockets.sync.client import connect as websocket_connect


class ProtocolError(RuntimeError):
    """The peer sent a message that violates api.md."""


class VersionMismatchError(ProtocolError):
    """The server protocol major version is unsupported."""


class ServerError(ProtocolError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"server error {code}: {detail}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value: Any, length: int, field: str) -> None:
    _require(isinstance(value, list) and len(value) == length, f"{field} must be a {length}-item array")
    _require(all(_is_number(item) for item in value), f"{field} must contain finite numbers")


def encode_json(message: dict[str, Any]) -> str:
    try:
        return json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not valid JSON: {exc}") from exc


def decode_json(payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("message is not valid UTF-8") from exc
    if not isinstance(payload, str):
        raise ProtocolError("WebSocket message must be text or UTF-8 bytes")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc.msg}") from exc
    _require(isinstance(value, dict), "message must be a JSON object")
    return value


def raise_if_error(message: dict[str, Any]) -> None:
    if message.get("type") == "error":
        raise ServerError(str(message.get("code", "UNKNOWN")), str(message.get("detail", "")))


def assert_compatible(hello: dict[str, Any]) -> None:
    raise_if_error(hello)
    _require(hello.get("type") == "hello", "first server message must be hello")
    version = hello.get("protocol_version")
    _require(isinstance(version, str), "hello.protocol_version must be a string")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise ProtocolError("hello.protocol_version is malformed") from exc
    if major != 1:
        raise VersionMismatchError(f"unsupported protocol major version: {major}")
    _require(isinstance(hello.get("env_name"), str), "hello.env_name is required")
    config = hello.get("config")
    _require(isinstance(config, dict), "hello.config is required")
    required = {
        "lidar_layers", "lidar_points_per_layer", "lidar_max_range",
        "ultrasonic_count", "ultrasonic_max_range", "control_dt",
        "max_episode_time", "v_max_xy", "v_max_z", "yawrate_max",
        "goal_tolerance", "robot_radius", "arena_size",
    }
    _require(not (required - config.keys()), f"hello.config missing fields: {sorted(required - config.keys())}")
    _vector(config["arena_size"], 3, "hello.config.arena_size")


def validate_obs(obs: dict[str, Any]) -> None:
    raise_if_error(obs)
    _require(obs.get("type") == "obs", "expected obs message")
    _require(isinstance(obs.get("episode_id"), int), "obs.episode_id must be int")
    _require(isinstance(obs.get("step_id"), int) and obs["step_id"] >= 0, "obs.step_id must be a non-negative int")

    agent = obs.get("agent")
    _require(isinstance(agent, dict), "obs.agent is required")
    for name in ("pos", "vel", "angular_vel", "linear_accel"):
        _vector(agent.get(name), 3, f"obs.agent.{name}")
    _require(_is_number(agent.get("yaw")), "obs.agent.yaw must be finite")

    lidar = obs.get("lidar_3d")
    _require(isinstance(lidar, list), "obs.lidar_3d must be an array")
    ultrasonic = obs.get("ultrasonic")
    _require(isinstance(ultrasonic, list) and all(_is_number(v) for v in ultrasonic), "obs.ultrasonic must contain finite numbers")
    height = obs.get("height_agl")
    _require(height is None or _is_number(height), "obs.height_agl must be finite or null")

    goal = obs.get("goal")
    _require(isinstance(goal, dict), "obs.goal is required")
    _vector(goal.get("pos"), 3, "obs.goal.pos")
    for name in ("dist", "bearing_xy", "bearing_z"):
        _require(_is_number(goal.get(name)), f"obs.goal.{name} must be finite")
    _require(isinstance(obs.get("obstacles"), list), "obs.obstacles must be an array")

    flags = obs.get("flags")
    _require(isinstance(flags, dict), "obs.flags is required")
    flag_names = ("collision", "goal_reached", "timeout")
    _require(all(isinstance(flags.get(name), bool) for name in flag_names), "obs.flags values must be bool")
    done = obs.get("done")
    _require(isinstance(done, bool), "obs.done must be bool")
    active = sum(bool(flags[name]) for name in flag_names)
    _require(active <= 1, "terminal flags must be mutually exclusive")
    _require(done == (active == 1), "obs.done must match terminal flags")


def reset_msg(seed: int) -> dict[str, Any]:
    if not isinstance(seed, int) or seed < -1:
        raise ValueError("seed must be -1 or a non-negative integer")
    return {"type": "reset", "seed": seed, "config_override": None}


def action_msg(obs: dict[str, Any], action_id: int) -> dict[str, Any]:
    if not isinstance(action_id, int) or isinstance(action_id, bool) or not 0 <= action_id <= 9:
        raise ValueError("action_id must be an integer between 0 and 9")
    return {
        "type": "action",
        "episode_id": obs["episode_id"],
        "step_id": obs["step_id"],
        "action_id": action_id,
    }


def all_finish_msg(reason: str, total_episodes: int) -> dict[str, Any]:
    if reason not in {"converged", "interrupted", "error"}:
        raise ValueError("invalid all_finish reason")
    if not isinstance(total_episodes, int) or total_episodes < 0:
        raise ValueError("total_episodes must be a non-negative integer")
    return {"type": "all_finish", "reason": reason, "total_episodes": total_episodes}


class WebSocketProtocolClient:
    """Thin synchronous transport wrapper; it contains no policy or metrics logic."""

    def __init__(self, sim_url: str) -> None:
        self.sim_url = sim_url
        self.websocket: Any | None = None

    def connect(self) -> None:
        self.websocket = websocket_connect(
            self.sim_url,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10,
            max_size=None,
        )

    def send(self, message: dict[str, Any]) -> None:
        if self.websocket is None:
            raise ProtocolError("WebSocket is not connected")
        self.websocket.send(encode_json(message))

    def receive(self) -> dict[str, Any]:
        if self.websocket is None:
            raise ProtocolError("WebSocket is not connected")
        message = decode_json(self.websocket.recv())
        raise_if_error(message)
        return message

    def close(self) -> None:
        if self.websocket is not None:
            self.websocket.close()
            self.websocket = None

