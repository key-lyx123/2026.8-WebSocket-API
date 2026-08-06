import pytest

from agent.action_parser import ActionParseError, parse_action_with_status, parse_action_id


@pytest.mark.parametrize("action_id", range(10))
def test_exact_actions(action_id):
    assert parse_action_id(str(action_id)) == action_id


def test_whitespace_and_json_compatibility():
    assert parse_action_with_status(" 9\n") == (9, "exact")
    assert parse_action_with_status('{"action_id": 4}') == (4, "json")


@pytest.mark.parametrize("text", ["10", "-1", "", "1 2", "动作是 9，因为安全", '{"action_id": 1, "why": "x"}', '{"action_id": true}'])
def test_invalid_outputs_are_rejected(text):
    with pytest.raises(ActionParseError):
        parse_action_id(text)

