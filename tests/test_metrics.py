from __future__ import annotations

import copy

import pytest

from agent.metrics import EpisodeMetrics
from tests.conftest import terminal_variant


def test_path_length_and_result_fields(obs_initial, obs_middle, obs_done):
    metrics = EpisodeMetrics.from_initial_obs(obs_initial)
    metrics.update(obs_initial, obs_middle)
    metrics.update(obs_middle, obs_done)
    result = metrics.finalize(42, obs_done)
    assert result.path_length == pytest.approx(7.0)
    assert result.outcome == "success"
    assert result.steps == 2
    assert result.final_dist == pytest.approx(0.14)
    assert result.min_obstacle_dist == pytest.approx(1.2)


@pytest.mark.parametrize("outcome", ["success", "collision", "timeout"])
def test_all_outcomes(obs_done, outcome):
    terminal = terminal_variant(obs_done, outcome)
    assert EpisodeMetrics.from_initial_obs(terminal).finalize(1, terminal).outcome == outcome


def test_no_obstacles_yields_none(obs_done):
    result = EpisodeMetrics.from_initial_obs(obs_done).finalize(1, obs_done)
    assert result.min_obstacle_dist is None

