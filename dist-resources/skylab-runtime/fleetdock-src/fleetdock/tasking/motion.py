"""任务执行器共享的运动速度规划算法。"""

from __future__ import annotations

import math

from fleetdock.tasking.models import TaskOptions


def resolve_approach_speed(speed_m_s: float, distance_m: float, tolerance_m: float) -> float:
    """计算接近目标点时的限速。

    ``speed_m_s`` 是上层期望速度，单位米/秒；``distance_m`` 是到目标点距离，单位米；
    ``tolerance_m`` 是到点容差，单位米。返回值为下一段接近控制建议速度，单位米/秒。
    """
    slow_radius_m = max(tolerance_m * 6.0, 3.0)
    if distance_m >= slow_radius_m:
        return speed_m_s
    return max(0.4, min(speed_m_s, distance_m * 0.5))


def resolve_profile_speed(
    current_speed_m_s: float,
    max_speed_m_s: float,
    remaining_m: float,
    dt_s: float,
    options: TaskOptions,
    *,
    position_tolerance_m: float | None = None,
) -> float:
    """按加减速约束计算下一控制周期速度。

    ``current_speed_m_s`` 是当前规划速度，``max_speed_m_s`` 是上限速度，单位均为米/秒；
    ``remaining_m`` 是到当前控制目标的剩余距离，单位为米，``dt_s`` 单位为秒；``options``
    提供加速度、减速度和默认位置容差。``position_tolerance_m`` 可传当前航点的实际接受半径，
    未传时沿用计划默认位置容差。返回值同时受最大速度、加速度、减速度和剩余制动距离约束，
    单位为米/秒。
    """
    tolerance_m = options.position_tolerance_m if position_tolerance_m is None else float(position_tolerance_m)
    brake_remaining_m = max(remaining_m - tolerance_m * 0.5, 0.0)
    brake_limited_speed_m_s = math.sqrt(2.0 * options.deceleration_m_s2 * brake_remaining_m)
    target_speed_m_s = min(max_speed_m_s, brake_limited_speed_m_s)
    lower_speed_m_s = max(0.0, current_speed_m_s - options.deceleration_m_s2 * dt_s)
    upper_speed_m_s = min(max_speed_m_s, current_speed_m_s + options.acceleration_m_s2 * dt_s)
    return clamp(target_speed_m_s, lower_speed_m_s, upper_speed_m_s)


def clamp(value: float, lower: float, upper: float) -> float:
    """把 ``value`` 限制在包含 ``lower`` 和 ``upper`` 的闭区间内，并返回限制后的数值。"""
    return max(lower, min(upper, value))
