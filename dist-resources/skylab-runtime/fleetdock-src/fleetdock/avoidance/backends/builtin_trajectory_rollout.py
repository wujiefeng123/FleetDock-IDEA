"""纯 Python 轨迹采样避障 planner 后端。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace

from fleetdock.avoidance.core import (
    AvoidanceDimensions,
    AvoidancePlanRequest,
    AvoidancePlanResult,
    PlannerStatus,
)
from fleetdock.events import Vector3
from fleetdock.obstacles import Obstacle, ObstacleShape

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TrajectoryCandidate:
    """内部候选轨迹；保存临时点、代价和最小安全余量。"""

    detour_points_m: tuple[Vector3, ...]
    score: float
    min_margin_m: float


class BuiltinTrajectoryRolloutPlanner:
    """FleetDock 内置的纯 Python 局部轨迹采样 planner。

    该后端参考 DWA/DWB 的局部候选轨迹思想，面向低速 SDK route 执行场景生成左、右、上、下及组合绕行
    候选，并对每条候选轨迹重新做障碍物外形和安全半径检查。它不依赖 ROS2、OMPL 或 C++ 扩展，因此
    可在 Windows 和 Linux 的基础 Python 环境中直接运行。该后端只返回规划结果，不直接控制设备。
    """

    def plan(self, request: AvoidancePlanRequest) -> AvoidancePlanResult:
        """计算局部绕行结果。

        ``request`` 中的坐标和障碍物必须已经由 ``AvoidanceManager`` 归一到同一局部坐标系；返回
        ``REPLAN`` 时，``detour_points_m`` 是同一坐标系下的临时绕行点。若没有风险障碍物、前视段过短或
        所有候选都不满足安全余量，返回 ``NO_SAFE_PATH``，由上层策略执行悬停、停止或返航。
        """
        candidate = self._best_trajectory_rollout(request)
        if candidate is None:
            return AvoidancePlanResult(
                PlannerStatus.NO_SAFE_PATH,
                message="builtin trajectory rollout found no safe candidate",
                diagnostics={"backend": self.__class__.__name__},
            )
        logger.debug(
            "内置轨迹采样已选择绕行候选: obstacle_id=%s score=%.3f min_margin_m=%.3f detour_count=%d",
            getattr(request.risk.obstacle, "obstacle_id", None),
            candidate.score,
            candidate.min_margin_m,
            len(candidate.detour_points_m),
        )
        return AvoidancePlanResult(
            PlannerStatus.REPLAN,
            detour_points_m=candidate.detour_points_m,
            message="builtin trajectory rollout selected detour",
            diagnostics={
                "backend": self.__class__.__name__,
                "score": candidate.score,
                "min_margin_m": candidate.min_margin_m,
            },
        )

    def _best_trajectory_rollout(self, request: AvoidancePlanRequest) -> _TrajectoryCandidate | None:
        obstacle = request.risk.obstacle
        if obstacle is None:
            return None
        start_m = request.start_m
        target_m = request.target_m
        policy = request.policy
        dx = target_m.x - start_m.x
        dy = target_m.y - start_m.y
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return None
        unit_x = dx / length
        unit_y = dy / length
        perp_x = -unit_y
        perp_y = unit_x
        start_to_obstacle_x = obstacle.position_m.x - start_m.x
        start_to_obstacle_y = obstacle.position_m.y - start_m.y
        cross = dx * start_to_obstacle_y - dy * start_to_obstacle_x
        preferred_side = -1.0 if cross > 0 else 1.0
        clearance = policy.replan_lateral_clearance_m
        if clearance is None:
            clearance = max(policy.safety_radius_m + self._obstacle_horizontal_extent_m(obstacle) + 1.0, 1.0)
        vertical_clearance = policy.replan_vertical_clearance_m
        if vertical_clearance is None:
            vertical_clearance = max(policy.safety_radius_m + self._obstacle_vertical_extent_m(obstacle) + 1.0, clearance)
        forward_clearance = max(policy.safety_radius_m + self._obstacle_horizontal_extent_m(obstacle), clearance)

        candidate_specs: list[tuple[float, float, float]] = []
        vertical_offsets: tuple[float, ...] = (0.0,)
        if policy.dimensions is AvoidanceDimensions.THREE_D and vertical_clearance > 0.0:
            vertical_offsets = (0.0, -vertical_clearance, vertical_clearance)
        lateral_clearances = tuple(clearance * multiplier for multiplier in (1.0, 2.0, 3.0, 4.0))
        for z_offset in vertical_offsets:
            for lateral_clearance in lateral_clearances:
                candidate_specs.append((preferred_side, z_offset, lateral_clearance))
                candidate_specs.append((-preferred_side, z_offset, lateral_clearance))
        if policy.dimensions is AvoidanceDimensions.THREE_D and vertical_clearance > 0.0:
            candidate_specs.append((0.0, -vertical_clearance, clearance))
            candidate_specs.append((0.0, vertical_clearance, clearance))

        candidates: list[_TrajectoryCandidate] = []
        seen: set[tuple[float, float, float]] = set()
        for side, z_offset, lateral_clearance in candidate_specs:
            key = (side, z_offset, lateral_clearance)
            if key in seen:
                continue
            seen.add(key)
            detours = self._rollout_detour_points(
                start_m,
                target_m,
                obstacle,
                side=side,
                z_offset=z_offset,
                clearance=lateral_clearance,
                forward_clearance=forward_clearance,
                unit_x=unit_x,
                unit_y=unit_y,
                perp_x=perp_x,
                perp_y=perp_y,
            )
            min_margin = self._trajectory_min_margin(
                (start_m, *detours),
                request.obstacles,
                speed_m_s=max(request.speed_m_s, 0.01),
                dimensions=policy.dimensions,
                safety_radius_m=policy.safety_radius_m,
            )
            if min_margin <= 0.0:
                continue
            score = self._trajectory_score(start_m, target_m, detours, min_margin, policy.dimensions, policy.safety_radius_m)
            candidates.append(_TrajectoryCandidate(detours, score, min_margin))
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate.score)

    @staticmethod
    def _rollout_detour_points(
        start_m: Vector3,
        target_m: Vector3,
        obstacle: Obstacle,
        *,
        side: float,
        z_offset: float,
        clearance: float,
        forward_clearance: float,
        unit_x: float,
        unit_y: float,
        perp_x: float,
        perp_y: float,
    ) -> tuple[Vector3, ...]:
        lateral_x = perp_x * side * clearance
        lateral_y = perp_y * side * clearance
        detour_z = target_m.z + z_offset
        return (
            Vector3(start_m.x + lateral_x, start_m.y + lateral_y, detour_z),
            Vector3(
                obstacle.position_m.x + lateral_x - unit_x * forward_clearance,
                obstacle.position_m.y + lateral_y - unit_y * forward_clearance,
                detour_z,
            ),
            Vector3(
                obstacle.position_m.x + lateral_x + unit_x * forward_clearance,
                obstacle.position_m.y + lateral_y + unit_y * forward_clearance,
                detour_z,
            ),
        )

    def _trajectory_min_margin(
        self,
        points: tuple[Vector3, ...],
        obstacles: tuple[Obstacle, ...],
        *,
        speed_m_s: float,
        dimensions: AvoidanceDimensions,
        safety_radius_m: float,
    ) -> float:
        if not obstacles:
            return float("inf")
        initial_margins = tuple(
            self._distance_to_obstacle_surface(points[0], obstacle, dimensions) - safety_radius_m
            for obstacle in obstacles
        )
        escape_mode = any(margin < 0.0 for margin in initial_margins)
        min_margin = float("inf") if escape_mode else min(initial_margins)
        latest_margins = list(initial_margins)
        elapsed_s = 0.0
        sample_interval_m = max(0.25, safety_radius_m * 0.5)
        for segment_index, (start, target) in enumerate(zip(points, points[1:])):
            length_m = self._distance(start, target, dimensions)
            samples = max(2, min(24, int(length_m / sample_interval_m) + 1))
            for index in range(samples):
                if segment_index == 0 and index == 0:
                    continue
                ratio = index / max(1, samples - 1)
                sample_time_s = elapsed_s + (length_m * ratio) / speed_m_s
                point = Vector3(
                    start.x + (target.x - start.x) * ratio,
                    start.y + (target.y - start.y) * ratio,
                    start.z + (target.z - start.z) * ratio,
                )
                for obstacle_index, obstacle in enumerate(obstacles):
                    clearance_m = self._distance_to_obstacle_surface(point, self._obstacle_at(obstacle, sample_time_s), dimensions)
                    margin_m = clearance_m - safety_radius_m
                    latest_margins[obstacle_index] = margin_m
                    if escape_mode:
                        initial_margin_m = initial_margins[obstacle_index]
                        if initial_margin_m < 0.0 and margin_m < initial_margin_m - 1e-6:
                            return margin_m
                        if initial_margin_m >= 0.0 and margin_m <= 0.0:
                            return margin_m
                        if margin_m > 0.0:
                            min_margin = min(min_margin, margin_m)
                    else:
                        min_margin = min(min_margin, margin_m)
                        if min_margin <= 0.0:
                            return min_margin
            elapsed_s += length_m / speed_m_s
        if escape_mode:
            final_margin_m = min(latest_margins)
            if final_margin_m <= 0.0:
                return final_margin_m
            return min(min_margin, final_margin_m)
        return min_margin

    def _trajectory_score(
        self,
        start_m: Vector3,
        target_m: Vector3,
        detours: tuple[Vector3, ...],
        min_margin_m: float,
        dimensions: AvoidanceDimensions,
        safety_radius_m: float,
    ) -> float:
        path_points = (start_m, *detours, target_m)
        path_length_m = sum(self._distance(first, second, dimensions) for first, second in zip(path_points, path_points[1:]))
        direct_length_m = self._distance(start_m, target_m, dimensions)
        vertical_cost_m = 0.0
        if dimensions is AvoidanceDimensions.THREE_D and detours:
            vertical_cost_m = sum(abs(point.z - target_m.z) for point in detours) / len(detours)
        clearance_reward_m = min(max(min_margin_m, 0.0), safety_radius_m * 2.0)
        return (path_length_m - direct_length_m) + vertical_cost_m * 0.25 - clearance_reward_m * 0.1

    def _distance_to_obstacle_surface(self, point: Vector3, obstacle: Obstacle, dimensions: AvoidanceDimensions) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            half_x, half_y, half_z = self._obstacle_box_half_extents_m(obstacle)
            dx = max(abs(point.x - obstacle.position_m.x) - half_x, 0.0)
            dy = max(abs(point.y - obstacle.position_m.y) - half_y, 0.0)
            if dimensions is AvoidanceDimensions.TWO_D:
                return math.hypot(dx, dy)
            dz = max(abs(point.z - obstacle.position_m.z) - half_z, 0.0)
            return math.sqrt(dx * dx + dy * dy + dz * dz)
        if shape is ObstacleShape.CYLINDER:
            radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
            horizontal_m = max(math.hypot(point.x - obstacle.position_m.x, point.y - obstacle.position_m.y) - radius_m, 0.0)
            if dimensions is AvoidanceDimensions.TWO_D:
                return horizontal_m
            half_height_m = self._obstacle_vertical_extent_m(obstacle)
            vertical_m = max(abs(point.z - obstacle.position_m.z) - half_height_m, 0.0)
            return math.sqrt(horizontal_m * horizontal_m + vertical_m * vertical_m)
        radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
        return max(self._distance(point, obstacle.position_m, dimensions) - radius_m, 0.0)

    @staticmethod
    def _distance(first: Vector3, second: Vector3, dimensions: AvoidanceDimensions) -> float:
        dx = first.x - second.x
        dy = first.y - second.y
        if dimensions is AvoidanceDimensions.TWO_D:
            return math.hypot(dx, dy)
        dz = first.z - second.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    @staticmethod
    def _obstacle_shape(obstacle: Obstacle) -> ObstacleShape:
        if isinstance(obstacle.shape, ObstacleShape):
            return obstacle.shape
        try:
            return ObstacleShape(str(obstacle.shape))
        except ValueError:
            return ObstacleShape.SPHERE

    def _obstacle_horizontal_extent_m(self, obstacle: Obstacle) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            half_x, half_y, _ = self._obstacle_box_half_extents_m(obstacle)
            return math.hypot(half_x, half_y)
        return max(float(obstacle.radius_m or 0.0), 0.0)

    def _obstacle_vertical_extent_m(self, obstacle: Obstacle) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            _, _, half_z = self._obstacle_box_half_extents_m(obstacle)
            return half_z
        if shape is ObstacleShape.CYLINDER and obstacle.size_m is not None:
            return max(abs(obstacle.size_m.z) * 0.5, 0.0)
        return max(float(obstacle.radius_m or 0.0), 0.0)

    @staticmethod
    def _obstacle_box_half_extents_m(obstacle: Obstacle) -> tuple[float, float, float]:
        if obstacle.size_m is not None:
            return (
                max(abs(obstacle.size_m.x) * 0.5, 0.0),
                max(abs(obstacle.size_m.y) * 0.5, 0.0),
                max(abs(obstacle.size_m.z) * 0.5, 0.0),
            )
        radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
        return (radius_m, radius_m, radius_m)

    @staticmethod
    def _obstacle_at(obstacle: Obstacle, elapsed_s: float) -> Obstacle:
        if obstacle.velocity_m_s is None or elapsed_s <= 0.0:
            return obstacle
        velocity = obstacle.velocity_m_s
        return replace(
            obstacle,
            position_m=Vector3(
                obstacle.position_m.x + velocity.x * elapsed_s,
                obstacle.position_m.y + velocity.y * elapsed_s,
                obstacle.position_m.z + velocity.z * elapsed_s,
            ),
        )
