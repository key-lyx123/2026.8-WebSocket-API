from __future__ import annotations

import copy
import math

import pytest

from agent.protocol import (
    ProtocolError,
    ServerError,
    VersionMismatchError,
    action_msg,
    assert_compatible,
    decode_json,
    encode_json,
    raise_if_error,
    validate_obs,
)


def test_hello_major_version_one_passes(hello):
    assert_compatible(hello)


def test_hello_major_version_two_is_rejected(hello):
    hello["protocol_version"] = "2.0"
    with pytest.raises(VersionMismatchError):
        assert_compatible(hello)


def test_missing_hello_field_is_rejected(hello):
    del hello["config"]["control_dt"]
    with pytest.raises(ProtocolError, match="control_dt"):
        assert_compatible(hello)


def test_server_error_becomes_exception():
    with pytest.raises(ServerError) as error:
        raise_if_error({"type": "error", "code": "BAD_FIELD", "detail": "missing seed"})
    assert error.value.code == "BAD_FIELD"
    assert error.value.detail == "missing seed"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_json_cannot_be_sent(value):
    with pytest.raises(ProtocolError):
        encode_json({"value": value})


def test_json_round_trip_is_utf8():
    assert decode_json(encode_json({"消息": "正常"})) == {"消息": "正常"}


def test_action_echoes_observation_identifiers(obs_initial):
    assert action_msg(obs_initial, 9) == {
        "type": "action", "episode_id": 1, "step_id": 0, "action_id": 9
    }


def test_terminal_flags_must_match_done(obs_initial):
    broken = copy.deepcopy(obs_initial)
    broken["done"] = True
    with pytest.raises(ProtocolError, match="terminal flags"):
        validate_obs(broken)


def test_terminal_flags_are_mutually_exclusive(obs_done):
    obs_done["flags"]["collision"] = True
    with pytest.raises(ProtocolError, match="mutually exclusive"):
        validate_obs(obs_done)

