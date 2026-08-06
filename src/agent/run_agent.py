"""OnlineAgentSession, compatibility API, and command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from .action_parser import ActionParseError, parse_action_with_status
from .config import CORRECTION_PROMPT
from .metrics import EpisodeMetrics
from .policy import PolicyBackend, create_policy
from .protocol import (
    ProtocolError,
    WebSocketProtocolClient,
    action_msg,
    all_finish_msg,
    assert_compatible,
    reset_msg,
    validate_obs,
)
from .schemas import AgentConfig, EpisodeResult, PolicyDecision
from .serialize import serialize_state

LOGGER = logging.getLogger("agent.run_agent")
TransitionHook = Callable[[dict[str, Any]], None]


class OnlineAgentSession:
    def __init__(
        self,
        sim_url: str,
        agent_config: AgentConfig,
        policy: PolicyBackend | None = None,
        transport: WebSocketProtocolClient | None = None,
    ) -> None:
        self.sim_url = sim_url
        self.config = agent_config
        self.policy = policy or create_policy(agent_config)
        self.transport = transport or WebSocketProtocolClient(sim_url)
        self.state = "DISCONNECTED"
        self.completed_episodes = 0

    def __enter__(self) -> "OnlineAgentSession":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def connect(self) -> None:
        if self.state != "DISCONNECTED":
            raise ProtocolError(f"cannot connect while state is {self.state}")
        self.transport.connect()
        try:
            hello = self.transport.receive()
            assert_compatible(hello)
        except Exception:
            self.transport.close()
            raise
        self.state = "WAIT_RESET"

    def _receive_obs(self, expected_episode: int | None = None, expected_step: int | None = None) -> dict[str, Any]:
        obs = self.transport.receive()
        validate_obs(obs)
        if expected_episode is not None and obs["episode_id"] != expected_episode:
            raise ProtocolError(f"unexpected episode_id {obs['episode_id']}; expected {expected_episode}")
        if expected_step is not None and obs["step_id"] != expected_step:
            raise ProtocolError(f"unexpected step_id {obs['step_id']}; expected {expected_step}")
        return obs

    def _policy_decision(self, state_text: str) -> tuple[PolicyDecision, bool]:
        last_error: Exception | None = None
        for attempt in range(self.config.max_action_retries + 1):
            try:
                return self.policy.decide(state_text), False
            except Exception as exc:  # policy/model errors are isolated from protocol errors
                last_error = exc
                LOGGER.warning("policy call failed (attempt %s/%s): %s", attempt + 1, self.config.max_action_retries + 1, exc)
        LOGGER.error("policy unavailable; using fallback action after: %s", last_error)
        return PolicyDecision(str(self.config.invalid_action_fallback), None, [], 0.0), True

    def _choose_action(self, state_text: str) -> tuple[int, str, PolicyDecision]:
        decision, policy_failed = self._policy_decision(state_text)
        if policy_failed:
            return self.config.invalid_action_fallback, "fallback", decision
        try:
            action_id, status = parse_action_with_status(decision.raw_text)
            return action_id, status, decision
        except ActionParseError:
            try:
                corrected = self.policy.decide(CORRECTION_PROMPT)
                action_id, _ = parse_action_with_status(corrected.raw_text)
                return action_id, "retry", corrected
            except Exception as exc:
                LOGGER.warning("invalid policy output after correction; using fallback: %s", exc)
                return self.config.invalid_action_fallback, "fallback", decision

    def run_episode(self, seed: int, transition_hook: TransitionHook | None = None) -> EpisodeResult:
        if self.state != "WAIT_RESET":
            raise ProtocolError(f"cannot reset while state is {self.state}")
        self.policy.reset_episode()
        self.transport.send(reset_msg(seed))
        self.state = "RUNNING"
        obs = self._receive_obs(expected_step=0)
        metrics = EpisodeMetrics.from_initial_obs(obs)

        while not obs["done"]:
            state_text = serialize_state(obs)
            action_id, parse_status, decision = self._choose_action(state_text)
            self.transport.send(action_msg(obs, action_id))
            next_obs = self._receive_obs(expected_episode=obs["episode_id"], expected_step=obs["step_id"] + 1)
            metrics.update(obs, next_obs)
            if transition_hook is not None:
                transition_hook({
                    "seed": seed,
                    "episode_id": obs["episode_id"],
                    "step_id": obs["step_id"],
                    "obs": obs,
                    "state_text": state_text,
                    "raw_model_output": decision.raw_text,
                    "action_id": action_id,
                    "next_obs": next_obs,
                    "latency_ms": decision.latency_ms,
                    "parse_status": parse_status,
                })
            obs = next_obs

        self.state = "WAIT_RESET"
        self.completed_episodes += 1
        return metrics.finalize(seed, obs)

    def finish(self, reason: str = "converged", total_episodes: int | None = None) -> None:
        if self.state != "WAIT_RESET":
            raise ProtocolError(f"all_finish is only valid in WAIT_RESET, not {self.state}")
        self.transport.send(all_finish_msg(reason, self.completed_episodes if total_episodes is None else total_episodes))
        reply = self.transport.receive()
        if reply.get("type") != "bye":
            raise ProtocolError("expected bye after all_finish")
        self.state = "FINISHED"

    def close(self) -> None:
        self.transport.close()
        self.state = "CLOSED"


def run_episode(sim_url: str, seed: int, agent_config: dict[str, Any]) -> dict[str, Any]:
    existing = agent_config.get("_session")
    if existing is not None:
        if not isinstance(existing, OnlineAgentSession):
            raise TypeError("agent_config['_session'] must be an OnlineAgentSession")
        return existing.run_episode(seed).to_dict()

    config = AgentConfig.from_dict(agent_config)
    session = OnlineAgentSession(sim_url, config)
    session.connect()
    try:
        result = session.run_episode(seed)
        session.finish("converged", 1)
        return result.to_dict()
    finally:
        session.close()


def parse_seeds(value: str) -> list[int]:
    text = value.strip()
    if text.isdigit():
        return [int(text)]
    if text.count("-") == 1:
        start_text, end_text = text.split("-")
        if start_text.isdigit() and end_text.isdigit():
            start, end = int(start_text), int(end_text)
            if start <= end:
                return list(range(start, end + 1))
            raise argparse.ArgumentTypeError("seed range cannot be reversed")
    raise argparse.ArgumentTypeError("seeds must be a non-negative integer or A-B range")


def _jsonl_writer(path: Path, overwrite: bool) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w" if overwrite else "a", encoding="utf-8", newline="\n")


def _write_json_line(handle: TextIO, value: dict[str, Any]) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    handle.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the protocol 1.x online drone navigation agent")
    parser.add_argument("--sim-url", default="ws://127.0.0.1:8765")
    parser.add_argument("--api-base", default=os.environ.get("AGENT_API_BASE", "http://127.0.0.1:8000/v1"))
    parser.add_argument("--api-key", default=os.environ.get("AGENT_API_KEY", "EMPTY"))
    parser.add_argument("--model", "--model-path", dest="model", default=os.environ.get("AGENT_MODEL", "drone-nav-lora"))
    parser.add_argument("--lora-path", help="compatibility option: validated locally and never passed to Hermes")
    parser.add_argument("--backend", choices=("hermes", "fake", "openai"), default="hermes")
    parser.add_argument("--seeds", type=parse_seeds, required=True, metavar="A-B")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--invalid-action-fallback", type=int, choices=range(10), default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), stream=sys.stderr, format="%(levelname)s %(message)s")
    if args.lora_path and not Path(args.lora_path).exists():
        parser.error(f"--lora-path does not exist: {args.lora_path}")
    if args.lora_path:
        LOGGER.info("LoRA path validated; vLLM must register it under model alias %s", args.model)

    config = AgentConfig(
        model=args.model,
        api_base=args.api_base,
        api_key=args.api_key,
        backend=args.backend,
        request_timeout_s=args.request_timeout,
        invalid_action_fallback=args.invalid_action_fallback,
    )
    trace_handle: TextIO | None = None
    try:
        with _jsonl_writer(args.output, args.overwrite) as result_handle:
            if args.trace_output:
                trace_handle = _jsonl_writer(args.trace_output, args.overwrite)
            hook = (lambda transition: _write_json_line(trace_handle, transition)) if trace_handle else None
            session = OnlineAgentSession(args.sim_url, config)
            session.connect()
            try:
                for seed in args.seeds:
                    result = session.run_episode(seed, transition_hook=hook)
                    _write_json_line(result_handle, result.to_dict())
                    LOGGER.info("seed=%s outcome=%s steps=%s", seed, result.outcome, result.steps)
                session.finish("converged", len(args.seeds))
            finally:
                session.close()
    except KeyboardInterrupt:
        LOGGER.warning("interrupted; connection closed without all_finish during an active episode")
        return 130
    finally:
        if trace_handle is not None:
            trace_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
