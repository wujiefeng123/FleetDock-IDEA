"""航线平滑执行使用的轻量轨迹采样工具。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence

from fleetdock.tasking.routes import Waypoint


@dataclass(frozen=True)
class TrajectoryPoint:
    """轨迹上的一个本地坐标采样点。

    ``x_m/y_m/z_m`` 使用执行器所在的本地坐标系，单位为米；``waypoint`` 保留关联航点。
    ``waypoint_index`` 为原始 ``Route.waypoints`` 序号；SDK 局部重规划插入的临时点使用 ``None``，
    避免临时绕行点被误识别为标准航点事件。
    """

    x_m: float
    y_m: float
    z_m: float | None
    waypoint: Waypoint
    waypoint_index: Optional[int]
    speed_m_s: float | None = None


@dataclass(frozen=True)
class TrajectorySample:
    """按路径弧长采样得到的位置和切向方向。

    ``distance_m`` 是从轨迹起点累计的弧长，单位为米；``tangent_x/tangent_y`` 是水平面
    单位切向量，用于生成速度方向和切向 yaw。
    """

    x_m: float
    y_m: float
    z_m: float | None
    distance_m: float
    tangent_x: float
    tangent_y: float
    speed_m_s: float | None


class TrajectoryPath:
    """可按整条航线进度投影和前视采样的二维/三维路径。

    路径以本地米制坐标表达；无人机使用 ``z_m`` 做高度跟踪，机器狗等二维设备可忽略
    ``z_m``。该对象不发送设备命令，只提供确定性的几何计算。
    """

    def __init__(self, points: Sequence[TrajectoryPoint]) -> None:
        if len(points) < 2:
            raise ValueError("trajectory path requires at least two points.")
        self.points = tuple(points)
        self._distances = [0.0]
        for previous, current in zip(self.points, self.points[1:]):
            self._distances.append(
                self._distances[-1] + math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m)
            )
        if self._distances[-1] <= 0:
            raise ValueError("trajectory path length must be greater than 0.")

    @property
    def length_m(self) -> float:
        """返回整条轨迹的水平弧长，单位为米。"""

        return self._distances[-1]

    def waypoint_distance_m(self, waypoint_index: int) -> float:
        """返回指定来源航点在轨迹上的累计距离。

        ``waypoint_index`` 是原始 `Route.waypoints` 中从 0 开始的航点序号；返回值是该航点
        距轨迹起点的水平累计弧长，单位为米。找不到对应航点时返回整条轨迹长度。
        """

        for point, distance_m in zip(self.points, self._distances):
            if point.waypoint_index == waypoint_index:
                return distance_m
        return self.length_m

    def source_waypoint_index_at_or_before(self, distance_m: float) -> Optional[int]:
        """返回指定进度前最近的原始航点序号。

        ``distance_m`` 是路径累计水平弧长，单位为米；临时绕行点没有原始航点序号，会被跳过。
        返回 ``None`` 表示当前位置之前没有可用于事件锚定的原始航点。
        """

        selected: Optional[int] = None
        for point, point_distance_m in zip(self.points, self._distances):
            if point_distance_m > distance_m + 1e-6:
                break
            if point.waypoint_index is not None:
                selected = point.waypoint_index
        return selected

    def source_points_after(self, distance_m: float) -> tuple[TrajectoryPoint, ...]:
        """返回指定进度之后仍未经过的原始航点轨迹点。

        ``distance_m`` 是当前路径累计水平弧长，单位为米；返回值只包含带原始 ``waypoint_index`` 的点，
        用于局部重规划后继续保留原路线航点事件和航点动作语义。
        """

        return tuple(
            point
            for point, point_distance_m in zip(self.points, self._distances)
            if point.waypoint_index is not None and point_distance_m > distance_m + 1e-6
        )

    def project(self, x_m: float, y_m: float) -> float:
        """把当前位置投影到整条轨迹。

        ``x_m`` 和 ``y_m`` 是设备当前本地坐标，单位为米；返回值是轨迹上距离该坐标最近的点
        对应的累计弧长，单位为米，用于连续执行器判断整条路线进度。
        """

        best_distance_m = 0.0
        best_error_m = float("inf")
        for index, (start, end) in enumerate(zip(self.points, self.points[1:])):
            dx = end.x_m - start.x_m
            dy = end.y_m - start.y_m
            segment_length_sq = dx * dx + dy * dy
            if segment_length_sq <= 0:
                continue
            t = ((x_m - start.x_m) * dx + (y_m - start.y_m) * dy) / segment_length_sq
            t = max(0.0, min(1.0, t))
            projected_x = start.x_m + dx * t
            projected_y = start.y_m + dy * t
            error_m = math.hypot(x_m - projected_x, y_m - projected_y)
            if error_m < best_error_m:
                best_error_m = error_m
                best_distance_m = self._distances[index] + math.sqrt(segment_length_sq) * t
        return best_distance_m

    def project_near(self, x_m: float, y_m: float, center_distance_m: float, window_m: float) -> float:
        """在指定进度窗口内投影当前位置。

        ``x_m`` 和 ``y_m`` 是设备当前本地坐标，单位为米；``center_distance_m`` 是上一轮已确认的
        轨迹进度，``window_m`` 是允许向前/向后搜索的弧长窗口，单位均为米。返回值是窗口内最近点的
        累计弧长，适合螺旋、扇形等相邻曲线较近时避免跳到错误圈层。
        """

        lower_m = max(0.0, center_distance_m - max(0.0, window_m * 0.25))
        upper_m = min(self.length_m, center_distance_m + max(0.0, window_m))
        best_distance_m = center_distance_m
        best_error_m = float("inf")
        for index, (start, end) in enumerate(zip(self.points, self.points[1:])):
            start_distance = self._distances[index]
            end_distance = self._distances[index + 1]
            if end_distance < lower_m or start_distance > upper_m:
                continue
            dx = end.x_m - start.x_m
            dy = end.y_m - start.y_m
            segment_length_sq = dx * dx + dy * dy
            if segment_length_sq <= 0:
                continue
            t = ((x_m - start.x_m) * dx + (y_m - start.y_m) * dy) / segment_length_sq
            segment_length_m = math.sqrt(segment_length_sq)
            min_t = max(0.0, (lower_m - start_distance) / segment_length_m)
            max_t = min(1.0, (upper_m - start_distance) / segment_length_m)
            t = max(min_t, min(max_t, t))
            projected_x = start.x_m + dx * t
            projected_y = start.y_m + dy * t
            error_m = math.hypot(x_m - projected_x, y_m - projected_y)
            if error_m < best_error_m:
                best_error_m = error_m
                best_distance_m = start_distance + segment_length_m * t
        return best_distance_m

    def sample(self, distance_m: float) -> TrajectorySample:
        """按累计弧长采样轨迹位置。

        ``distance_m`` 是距轨迹起点的期望水平弧长，单位为米；超出范围时自动夹紧到起点或终点。
        返回 `TrajectorySample`，包含本地坐标、切向单位向量和该位置继承的建议速度。
        """

        distance_m = max(0.0, min(self.length_m, distance_m))
        for index, (start_distance, end_distance) in enumerate(zip(self._distances, self._distances[1:])):
            if distance_m > end_distance and index < len(self.points) - 2:
                continue
            start = self.points[index]
            end = self.points[index + 1]
            segment_length_m = max(end_distance - start_distance, 1e-9)
            ratio = (distance_m - start_distance) / segment_length_m
            x_m = start.x_m + (end.x_m - start.x_m) * ratio
            y_m = start.y_m + (end.y_m - start.y_m) * ratio
            z_m = self._interpolate_z(start.z_m, end.z_m, ratio)
            tangent_x = (end.x_m - start.x_m) / segment_length_m
            tangent_y = (end.y_m - start.y_m) / segment_length_m
            return TrajectorySample(
                x_m=x_m,
                y_m=y_m,
                z_m=z_m,
                distance_m=distance_m,
                tangent_x=tangent_x,
                tangent_y=tangent_y,
                speed_m_s=end.speed_m_s,
            )
        last = self.points[-1]
        previous = self.points[-2]
        dx = last.x_m - previous.x_m
        dy = last.y_m - previous.y_m
        length_m = math.hypot(dx, dy) or 1.0
        return TrajectorySample(
            x_m=last.x_m,
            y_m=last.y_m,
            z_m=last.z_m,
            distance_m=self.length_m,
            tangent_x=dx / length_m,
            tangent_y=dy / length_m,
            speed_m_s=last.speed_m_s,
        )

    @staticmethod
    def _interpolate_z(start_z_m: float | None, end_z_m: float | None, ratio: float) -> float | None:
        if start_z_m is None and end_z_m is None:
            return None
        start = end_z_m if start_z_m is None else start_z_m
        end = start_z_m if end_z_m is None else end_z_m
        if start is None or end is None:
            return None
        return start + (end - start) * ratio
