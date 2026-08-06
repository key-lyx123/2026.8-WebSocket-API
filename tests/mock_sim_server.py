"""Protocol-faithful offline simulator used by integration and smoke tests."""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from websockets.sync.server import serve

from agent.protocol import decode_json, encode_json


HELLO = {
    "type": "hello",
    "protocol_version": "1.0",
    "env_name": "mock_webots_crazyflie_3d_v1",
    "config": {
        "lidar_layers": 16,
        "lidar_points_per_layer": 180,
        "lidar_max_range": 50.0,
        "ultrasonic_count": 8,
        "ultrasonic_max_range": 5.0,
        "control_dt": 0.1,
        "max_episode_time": 60.0,
        "v_max_xy": 0.5,
        "v_max_z": 0.5,
        "yawrate_max": 2.094,
        "goal_tolerance": 0.5,
        "robot_radius": 0.08,
        "arena_size": [20.0, 20.0, 3.0],
    },
}


@dataclass
class MockStats:
    connections: int = 0
    episodes: int = 0
    actions: int = 0
    all_finish_count: int = 0
    invalid_messages: int = 0


def make_obs(episode_id: int, step_id: int, done: bool) -> dict[str, Any]:
    position = [step_id * 0.3, 0.0, 1.0 + step_id * 0.1]
    return {
        "type": "obs",
        "episode_id": episode_id,
        "step_id": step_id,
        "agent": {
            "pos": position,
            "vel": [0.3 if not done else 0.0, 0.0, 0.1 if not done else 0.0],
            "yaw": 0.0,
            "angular_vel": [0.0, 0.0, 0.0],
            "linear_accel": [0.0, 0.0, -9.81],
        },
        "lidar_3d": [],
        "ultrasonic": [5.0] * 8,
        "height_agl": position[2],
        "goal": {
            "pos": [0.6, 0.0, 1.2],
            "dist": 0.0 if done else max(0.0, 0.65 - step_id * 0.3),
            "bearing_xy": 0.0,
            "bearing_z": 0.1,
        },
        "obstacles": [] if done else [{"id": 0, "type": "box", "pos": [3.0, 2.0, 0.0], "size": [1.0, 1.0, 1.0], "distance": 2.5 - step_id * 0.1}],
        "flags": {"collision": False, "goal_reached": done, "timeout": False},
        "done": done,
    }


def _error(websocket, code: str, detail: str, stats: MockStats, finished: threading.Event) -> None:
    stats.invalid_messages += 1
    websocket.send(encode_json({"type": "error", "code": code, "detail": detail}))
    finished.set()


def handle_connection(websocket, stats: MockStats, finished: threading.Event) -> None:
    stats.connections += 1
    websocket.send(encode_json(HELLO))
    state = "WAIT_RESET"
    episode_id = 0
    step_id = 0
    while True:
        try:
            message = decode_json(websocket.recv())
        except Exception as exc:
            _error(websocket, "BAD_JSON", str(exc), stats, finished)
            break
        message_type = message.get("type")
        if state == "WAIT_RESET" and message_type == "reset":
            seed = message.get("seed")
            if not isinstance(seed, int) or seed < -1 or "config_override" not in message:
                _error(websocket, "BAD_FIELD", "reset requires seed and config_override", stats, finished)
                break
            episode_id += 1
            stats.episodes += 1
            step_id = 0
            state = "RUNNING"
            websocket.send(encode_json(make_obs(episode_id, step_id, False)))
        elif state == "RUNNING" and message_type == "action":
            valid = (
                message.get("episode_id") == episode_id
                and message.get("step_id") == step_id
                and isinstance(message.get("action_id"), int)
                and not isinstance(message.get("action_id"), bool)
                and 0 <= message["action_id"] <= 9
            )
            if not valid:
                _error(websocket, "BAD_FIELD", "invalid action identifiers or action_id", stats, finished)
                break
            stats.actions += 1
            step_id += 1
            done = step_id == 2
            websocket.send(encode_json(make_obs(episode_id, step_id, done)))
            if done:
                state = "WAIT_RESET"
        elif state == "WAIT_RESET" and message_type == "all_finish":
            if not isinstance(message.get("total_episodes"), int):
                _error(websocket, "BAD_FIELD", "all_finish requires total_episodes", stats, finished)
                break
            stats.all_finish_count += 1
            websocket.send(encode_json({"type": "bye", "reason": "all_finish received"}))
            finished.set()
            break
        else:
            _error(websocket, "WRONG_STATE", f"{message_type} received while {state}", stats, finished)
            break


def run_server(host: str, port: int, ready: threading.Event, finished: threading.Event, stats: MockStats, timeout_s: float = 30.0) -> None:
    with serve(lambda websocket: handle_connection(websocket, stats, finished), host, port, ping_interval=20, ping_timeout=60, max_size=None) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        ready.set()
        finished.wait(timeout_s)
        server.shutdown()
        worker.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    ready = threading.Event()
    finished = threading.Event()
    stats = MockStats()

    def signal_ready() -> None:
        ready.wait()
        if args.ready_file:
            args.ready_file.write_text("ready", encoding="utf-8")

    signal = threading.Thread(target=signal_ready, daemon=True)
    signal.start()
    run_server(args.host, args.port, ready, finished, stats, args.timeout)
    if args.summary_file:
        args.summary_file.write_text(json.dumps(asdict(stats), allow_nan=False), encoding="utf-8")
    return 0 if finished.is_set() else 2


if __name__ == "__main__":
    raise SystemExit(main())
