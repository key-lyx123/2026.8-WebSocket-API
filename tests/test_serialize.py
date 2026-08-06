from __future__ import annotations

import copy

from agent.serialize import serialize_state


def test_output_is_deterministic_and_input_is_unchanged(obs_initial):
    before = copy.deepcopy(obs_initial)
    assert serialize_state(obs_initial) == serialize_state(obs_initial)
    assert obs_initial == before


def test_yaw_is_radians_and_zero_height_is_kept(obs_initial):
    text = serialize_state(obs_initial)
    assert "0.785 rad" in text
    assert "距地高度：0.00 m" in text
    yaw_line = next(line for line in text.splitlines() if "yaw" in line)
    assert "度" not in yaw_line


def test_none_height_is_explicit(obs_middle):
    assert "距地高度：unavailable" in serialize_state(obs_middle)


def test_empty_obstacles_and_lidar_is_not_serialized(obs_done):
    text = serialize_state(obs_done)
    assert "无可见障碍物" in text
    assert "lidar_3d" not in text


def test_obstacles_are_sorted_by_distance(obs_initial):
    text = serialize_state(obs_initial)
    assert text.index("id=0") < text.index("id=1")
