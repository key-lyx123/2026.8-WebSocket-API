from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def hello() -> dict:
    return load_fixture("hello.json")


@pytest.fixture
def obs_initial() -> dict:
    return load_fixture("obs_initial.json")


@pytest.fixture
def obs_middle() -> dict:
    return load_fixture("obs_middle.json")


@pytest.fixture
def obs_done() -> dict:
    return load_fixture("obs_done.json")


def terminal_variant(obs: dict, outcome: str) -> dict:
    result = copy.deepcopy(obs)
    result["done"] = True
    result["flags"] = {"collision": False, "goal_reached": False, "timeout": False}
    result["flags"][{"success": "goal_reached", "collision": "collision", "timeout": "timeout"}[outcome]] = True
    return result

