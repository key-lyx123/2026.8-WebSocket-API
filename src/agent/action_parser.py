"""Strict parsing of the model's discrete action output."""

from __future__ import annotations

import json
import re


class ActionParseError(ValueError):
    pass


_EXACT_ACTION = re.compile(r"[0-9]")


def parse_action_with_status(text: str) -> tuple[int, str]:
    if not isinstance(text, str):
        raise ActionParseError("action output must be text")
    stripped = text.strip()
    if _EXACT_ACTION.fullmatch(stripped):
        return int(stripped), "exact"
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        value = None
    if isinstance(value, dict) and set(value) == {"action_id"}:
        action_id = value["action_id"]
        if isinstance(action_id, int) and not isinstance(action_id, bool) and 0 <= action_id <= 9:
            return action_id, "json"
    raise ActionParseError("expected one integer from 0 to 9")


def parse_action_id(text: str) -> int:
    return parse_action_with_status(text)[0]

