from __future__ import annotations

import copy

import pytest

from agent.policy import FakePolicyBackend
from agent.protocol import ProtocolError
from agent.run_agent import OnlineAgentSession, parse_seeds
from agent.schemas import AgentConfig, PolicyDecision


class MemoryTransport:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []
        self.connected = False

    def connect(self):
        self.connected = True

    def receive(self):
        return self.incoming.pop(0)

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.connected = False


@pytest.mark.parametrize("text,expected", [("5", [5]), ("0-3", [0, 1, 2, 3])])
def test_seed_parser(text, expected):
    assert parse_seeds(text) == expected


@pytest.mark.parametrize("text", ["-1", "3-1", "a", "1-2-3"])
def test_seed_parser_rejects_invalid_values(text):
    with pytest.raises(Exception):
        parse_seeds(text)


def test_session_retries_invalid_output_and_finishes(hello, obs_initial, obs_done):
    terminal = copy.deepcopy(obs_done)
    terminal["step_id"] = 1
    transport = MemoryTransport([hello, obs_initial, terminal, {"type": "bye", "reason": "ok"}])
    policy = FakePolicyBackend(["bad output", 9])
    session = OnlineAgentSession("ws://unused", AgentConfig(backend="fake"), policy=policy, transport=transport)
    transitions = []
    session.connect()
    result = session.run_episode(4, transitions.append)
    session.finish(total_episodes=1)
    assert result.outcome == "success"
    assert transport.sent[1]["action_id"] == 9
    assert transitions[0]["parse_status"] == "retry"
    assert transport.sent[-1]["type"] == "all_finish"


def test_session_falls_back_after_two_invalid_outputs(hello, obs_initial, obs_done):
    terminal = copy.deepcopy(obs_done)
    terminal["step_id"] = 1
    transport = MemoryTransport([hello, obs_initial, terminal])
    policy = FakePolicyBackend(["bad", "still bad"])
    session = OnlineAgentSession("ws://unused", AgentConfig(backend="fake", invalid_action_fallback=0), policy=policy, transport=transport)
    transitions = []
    session.connect()
    session.run_episode(2, transitions.append)
    assert transport.sent[1]["action_id"] == 0
    assert transitions[0]["parse_status"] == "fallback"


def test_policy_call_is_retried_once(hello, obs_initial, obs_done):
    class FlakyPolicy:
        calls = 0

        def reset_episode(self):
            self.calls = 0

        def decide(self, state_text):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("model timeout")
            return PolicyDecision("1", None, [], 2.0)

    terminal = copy.deepcopy(obs_done)
    terminal["step_id"] = 1
    transport = MemoryTransport([hello, obs_initial, terminal])
    policy = FlakyPolicy()
    session = OnlineAgentSession("ws://unused", AgentConfig(backend="fake", max_action_retries=1), policy=policy, transport=transport)
    session.connect()
    session.run_episode(2)
    assert policy.calls == 2
    assert transport.sent[1]["action_id"] == 1


def test_terminal_initial_observation_sends_no_action(hello, obs_done):
    terminal = copy.deepcopy(obs_done)
    terminal["step_id"] = 0
    transport = MemoryTransport([hello, terminal])
    session = OnlineAgentSession("ws://unused", AgentConfig(backend="fake"), policy=FakePolicyBackend(), transport=transport)
    session.connect()
    session.run_episode(1)
    assert [message["type"] for message in transport.sent] == ["reset"]


def test_finish_is_rejected_during_episode(hello, obs_initial):
    transport = MemoryTransport([hello, obs_initial])
    session = OnlineAgentSession("ws://unused", AgentConfig(backend="fake"), policy=FakePolicyBackend(), transport=transport)
    session.connect()
    session.transport.send({"type": "reset", "seed": 0, "config_override": None})
    session.state = "RUNNING"
    with pytest.raises(ProtocolError):
        session.finish()
