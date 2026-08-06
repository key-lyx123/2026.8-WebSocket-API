from __future__ import annotations

import json

from agent.policy import FakePolicyBackend, OpenAICompatiblePolicyBackend
from agent.schemas import AgentConfig


def test_fake_policy_resets_action_sequence():
    policy = FakePolicyBackend([1, 9])
    assert policy.decide("s0").raw_text == "1"
    assert policy.decide("s1").raw_text == "9"
    policy.reset_episode()
    assert policy.decide("s0-new").raw_text == "1"
    assert len(policy.messages) == 2


def test_openai_compatible_backend_normalizes_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"choices": [{"message": {"role": "assistant", "content": "4", "reasoning_content": "clear"}}]}).encode()

    monkeypatch.setattr("agent.policy.urllib.request.urlopen", lambda request, timeout: Response())
    backend = OpenAICompatiblePolicyBackend(AgentConfig(backend="openai"))
    decision = backend.decide("state")
    assert decision.raw_text == "4"
    assert decision.reasoning_content == "clear"
    assert len(decision.messages) == 2
