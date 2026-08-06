from __future__ import annotations

import pytest

from agent.hermes_adapter import HermesPolicyBackend, HermesUnavailableError, load_ai_agent
from agent.schemas import AgentConfig


class StubAIAgent:
    instances = []

    def __init__(self, model, base_url, api_key, quiet_mode, skip_context_files, skip_memory, enabled_toolsets, max_iterations, ephemeral_system_prompt):
        self.kwargs = locals().copy()
        self.calls = []
        self.__class__.instances.append(self)

    def run_conversation(self, user_message, conversation_history):
        self.calls.append((user_message, list(conversation_history)))
        messages = [*conversation_history, {"role": "user", "content": user_message}, {"role": "assistant", "content": "9", "reasoning_content": "safe"}]
        return {"messages": messages, "final_response": "9"}


def test_hermes_configuration_and_episode_history():
    config = AgentConfig(model="drone-nav-lora", api_base="http://localhost:8000/v1")
    backend = HermesPolicyBackend(config, agent_class=StubAIAgent)
    assert "lora_path" not in backend.agent.kwargs
    assert backend.agent.kwargs["base_url"] == config.api_base
    first = backend.decide("state 0")
    second = backend.decide("state 1")
    assert first.raw_text == "9" and first.reasoning_content == "safe"
    assert len(second.messages) == 4
    assert len(backend.agent.calls[1][1]) == 2
    backend.reset_episode()
    backend.decide("new episode")
    assert backend.agent.calls[-1][1] == []


def test_missing_hermes_has_actionable_error(monkeypatch):
    monkeypatch.delenv("HERMES_REPO", raising=False)
    monkeypatch.setattr("agent.hermes_adapter.importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError(name)))
    with pytest.raises(HermesUnavailableError, match="scripts/setup_hermes.sh"):
        load_ai_agent()

