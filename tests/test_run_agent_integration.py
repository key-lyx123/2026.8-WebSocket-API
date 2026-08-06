from __future__ import annotations

import json
import socket
import threading

from websockets.sync.client import connect

from agent.protocol import decode_json, encode_json
from agent.run_agent import OnlineAgentSession, main, run_episode
from agent.schemas import AgentConfig
from tests.mock_sim_server import MockStats, run_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_mock_server_two_episode_cli_and_trace(tmp_path):
    port = _free_port()
    ready = threading.Event()
    finished = threading.Event()
    stats = MockStats()
    server = threading.Thread(target=run_server, args=("127.0.0.1", port, ready, finished, stats), daemon=True)
    server.start()
    assert ready.wait(5)
    output = tmp_path / "episodes.jsonl"
    trace = tmp_path / "trace.jsonl"
    exit_code = main([
        "--backend", "fake",
        "--sim-url", f"ws://127.0.0.1:{port}",
        "--seeds", "0-1",
        "--output", str(output),
        "--trace-output", str(trace),
        "--overwrite",
    ])
    server.join(5)
    results = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    transitions = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert [result["outcome"] for result in results] == ["success", "success"]
    assert [result["seed"] for result in results] == [0, 1]
    assert len(transitions) == 4
    assert stats.connections == 1
    assert stats.episodes == 2
    assert stats.actions == 4
    assert stats.all_finish_count == 1
    assert stats.invalid_messages == 0


def test_mock_server_rejects_invalid_action_identifiers():
    port = _free_port()
    ready = threading.Event()
    finished = threading.Event()
    stats = MockStats()
    server = threading.Thread(target=run_server, args=("127.0.0.1", port, ready, finished, stats), daemon=True)
    server.start()
    assert ready.wait(5)
    with connect(f"ws://127.0.0.1:{port}") as websocket:
        assert decode_json(websocket.recv())["type"] == "hello"
        websocket.send(encode_json({"type": "reset", "seed": 0, "config_override": None}))
        obs = decode_json(websocket.recv())
        websocket.send(encode_json({"type": "action", "episode_id": obs["episode_id"], "step_id": 99, "action_id": 1}))
        error = decode_json(websocket.recv())
    server.join(5)
    assert error["type"] == "error"
    assert error["code"] == "BAD_FIELD"
    assert stats.invalid_messages == 1


def test_python_api_reuses_one_session_for_two_seeds():
    port = _free_port()
    ready = threading.Event()
    finished = threading.Event()
    stats = MockStats()
    server = threading.Thread(target=run_server, args=("127.0.0.1", port, ready, finished, stats), daemon=True)
    server.start()
    assert ready.wait(5)
    url = f"ws://127.0.0.1:{port}"
    config = {"backend": "fake"}
    with OnlineAgentSession(url, AgentConfig.from_dict(config)) as session:
        config["_session"] = session
        results = [run_episode(url, seed, config) for seed in (10, 11)]
        session.finish("converged", 2)
    server.join(5)
    assert [result["seed"] for result in results] == [10, 11]
    assert all(result["outcome"] == "success" for result in results)
    assert stats.connections == 1
    assert stats.all_finish_count == 1
