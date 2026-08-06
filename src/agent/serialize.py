"""Deterministic state-to-text serialization shared by all LLM clients."""

from __future__ import annotations

from typing import Any


def _vec(values: list[float], digits: int = 2) -> str:
    return "(" + ", ".join(f"{value:.{digits}f}" for value in values) + ")"


def _obstacle_line(obstacle: dict[str, Any]) -> str:
    parts = [
        f"- id={obstacle.get('id', 'unknown')}",
        f"type={obstacle.get('type', 'unknown')}",
    ]
    if isinstance(obstacle.get("pos"), list):
        parts.append(f"pos={_vec(obstacle['pos'], 1)}")
    if "radius" in obstacle:
        parts.append(f"radius={obstacle['radius']:.1f}")
    if "height" in obstacle:
        parts.append(f"height={obstacle['height']:.1f}")
    if isinstance(obstacle.get("size"), list):
        parts.append(f"size={_vec(obstacle['size'], 1)}")
    parts.append(f"distance={obstacle['distance']:.2f} m")
    return " ".join(parts)


def serialize_state(obs: dict[str, Any]) -> str:
    agent = obs["agent"]
    goal = obs["goal"]
    obstacles = sorted(obs.get("obstacles", []), key=lambda item: item["distance"])
    height = obs.get("height_agl")

    lines = [
        "当前状态：",
        f"- episode_id：{obs['episode_id']}",
        f"- step_id：{obs['step_id']}",
        f"- 无人机位置：{_vec(agent['pos'])} m",
        f"- 无人机速度：{_vec(agent['vel'])} m/s",
        f"- 无人机朝向(yaw)：{agent['yaw']:.3f} rad",
        f"- 距地高度：{height:.2f} m" if height is not None else "- 距地高度：unavailable",
        f"- 目标位置：{_vec(goal['pos'])} m",
        f"- 目标距离：{goal['dist']:.2f} m",
        f"- 目标水平方位：{goal['bearing_xy']:.3f} rad",
        f"- 目标垂直方位：{goal['bearing_z']:.3f} rad",
        "",
        "障碍物：",
    ]
    if obstacles:
        lines.extend(_obstacle_line(obstacle) for obstacle in obstacles)
    else:
        lines.append("- 无可见障碍物")
    lines.extend([
        "",
        "超声波[N,NE,E,SE,S,SW,W,NW]：",
        ", ".join(f"{value:.2f}" for value in obs["ultrasonic"]) + " m",
        "",
        "请选择下一步动作，只输出动作编号。",
    ])
    return "\n".join(lines)

