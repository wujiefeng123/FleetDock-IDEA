"""航点、航线及内置航线生成器。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Optional, Sequence, cast
from uuid import uuid4

from fleetdock.movement import (
    GpsPoint,
    MovePoint,
    RelativePoint2D,
    RelativePoint3D,
    gps_offset_m,
    offset_gps_point,
)
from fleetdock.tasking.models import RouteContinuity, RoutePattern, WaypointAction

@dataclass(frozen=True)
class Waypoint:
    """航点对象，描述标准移动目标及其执行参数。

    新代码应优先通过 ``target`` 传入 ``GpsPoint``、``RelativePoint2D`` 或
    ``RelativePoint3D``。保留的 x/y/z 和经纬度字段用于兼容已有代码与事件序列化；
    所有距离和高度单位为米，经纬度与朝向单位为度。
    """

    x_m: Optional[float] = None
    y_m: Optional[float] = None
    z_m: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    speed_m_s: Optional[float] = None
    yaw_deg: Optional[float] = None
    acceptance_radius_m: Optional[float] = None
    settle_time_s: Optional[float] = None
    hold_time_s: float = 0.0
    action: WaypointAction | str = WaypointAction.NONE
    waypoint_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    target: Optional[MovePoint] = None

    def __post_init__(self) -> None:
        if self.target is None:
            return
        if isinstance(self.target, GpsPoint):
            object.__setattr__(self, "latitude_deg", self.target.latitude_deg)
            object.__setattr__(self, "longitude_deg", self.target.longitude_deg)
            object.__setattr__(self, "altitude_m", self.target.altitude_m)
            object.__setattr__(self, "yaw_deg", self.target.yaw_deg)
        elif isinstance(self.target, RelativePoint3D):
            object.__setattr__(self, "x_m", self.target.x_m)
            object.__setattr__(self, "y_m", self.target.y_m)
            object.__setattr__(self, "z_m", self.target.z_m)
            object.__setattr__(self, "yaw_deg", self.target.yaw_deg)
        elif isinstance(self.target, RelativePoint2D):
            object.__setattr__(self, "x_m", self.target.x_m)
            object.__setattr__(self, "y_m", self.target.y_m)
            object.__setattr__(self, "yaw_deg", self.target.yaw_deg)

    @classmethod
    def from_target(cls, target: MovePoint, **options: Any) -> "Waypoint":
        """从标准位置目标创建并返回航点。

        ``target`` 是 GPS 或局部位置目标；``options`` 可补充米/秒速度、米制到达容差、
        秒制停留时间和到点动作。
        """
        return cls(target=target, **options)

    def to_move_target(self) -> MovePoint:
        """返回可直接交给 ``device.move`` 或 ``plan.move`` 的标准位置目标。"""
        if self.target is not None:
            return self.target
        if self.latitude_deg is not None and self.longitude_deg is not None and self.altitude_m is not None:
            return GpsPoint(self.latitude_deg, self.longitude_deg, self.altitude_m, self.yaw_deg)
        if self.x_m is None or self.y_m is None:
            raise ValueError("waypoint requires either GPS coordinates or local x_m/y_m.")
        if self.z_m is not None:
            return RelativePoint3D(self.x_m, self.y_m, self.z_m, self.yaw_deg)
        return RelativePoint2D(self.x_m, self.y_m, self.yaw_deg)


@dataclass(frozen=True)
class RouteTransform2D:
    """本地平面航线的不可变仿射变换参数。

    变换按“相对 ``pivot`` 缩放、旋转，再平移”的顺序执行。距离单位为米，旋转单位为度；
    ``scale_x/scale_y`` 必须大于零。``rotate_yaw`` 为真时同步旋转显式航点 yaw。
    该模型不适用于经纬度航线，GPS 路线应先在业务明确的本地坐标系内规划。
    """

    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation_deg: float = 0.0
    translation_x_m: float = 0.0
    translation_y_m: float = 0.0
    pivot_x_m: float = 0.0
    pivot_y_m: float = 0.0
    rotate_yaw: bool = True

    def __post_init__(self) -> None:
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ValueError("scale_x and scale_y must be greater than 0.")


@dataclass(frozen=True)
class RouteGeometry:
    """航线生成器保留的几何语义。

    ``kind`` 表示线、圆弧、圆形、椭圆或螺旋等几何类型；``params`` 保存生成时使用的
    半径、中心点、角度、圈数等参数，单位沿用对应 ``Route`` 工厂方法的公开说明。该对象
    不直接控制设备，供 SDK 平滑执行、可视化和后续适配器降级时识别曲线语义。
    """

    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    """航线对象，封装航点序列和航线类型，供 ``Plan(drone).route(...)`` 执行。"""

    waypoints: tuple[Waypoint, ...]
    route_id: str = field(default_factory=lambda: str(uuid4()))
    name: Optional[str] = None
    pattern: RoutePattern | str = RoutePattern.POLYLINE
    close: bool = False
    continuity: RouteContinuity | str = RouteContinuity.PREFERRED
    geometry: Optional[RouteGeometry] = None

    @property
    def targets(self) -> tuple[MovePoint, ...]:
        """返回按航线顺序排列的标准位置目标，可逐个交给 ``device.move`` 或 ``plan.move``。"""
        return tuple(waypoint.to_move_target() for waypoint in self.waypoints)

    @property
    def estimated_length_m(self) -> float | None:
        """返回本地航点折线的预计总长度（米）；GPS 航线或坐标不完整时返回 ``None``。

        该值用于任务时长、演示里程和容量规划，不包含避障运行期插入的临时绕行距离。
        """

        if len(self.waypoints) < 2:
            return 0.0
        total_m = 0.0
        for first, second in zip(self.waypoints, self.waypoints[1:]):
            if any(
                value is None
                for value in (first.x_m, first.y_m, second.x_m, second.y_m)
            ):
                return None
            first_x = cast(float, first.x_m)
            first_y = cast(float, first.y_m)
            second_x = cast(float, second.x_m)
            second_y = cast(float, second.y_m)
            dx = second_x - first_x
            dy = second_y - first_y
            if first.z_m is not None and second.z_m is not None:
                dz = float(second.z_m) - float(first.z_m)
                total_m += math.sqrt(dx * dx + dy * dy + dz * dz)
            else:
                total_m += math.hypot(dx, dy)
        return total_m

    def transform(self, transform: RouteTransform2D, *, name: Optional[str] = None) -> "Route":
        """对本地米制航线执行二维缩放、旋转和平移并返回新航线。

        ``transform`` 的坐标均使用当前航线本地 x/y 坐标系；z、高度、速度、动作、到达条件和
        metadata 保持不变。含任意 GPS 航点或缺少 x/y 的航线会抛出 ``ValueError``，避免在
        未声明投影和参考原点时错误变换经纬度。``name`` 未传时沿用原航线名称。
        """
        if not isinstance(transform, RouteTransform2D):
            raise TypeError("transform must be RouteTransform2D.")
        transformed: list[Waypoint] = []
        for waypoint in self.waypoints:
            if waypoint.latitude_deg is not None or waypoint.longitude_deg is not None:
                raise ValueError("GPS routes cannot be transformed without an explicit local projection.")
            if waypoint.x_m is None or waypoint.y_m is None:
                raise ValueError("Local route transform requires x_m and y_m for every waypoint.")
            relative_x_m = (waypoint.x_m - transform.pivot_x_m) * transform.scale_x
            relative_y_m = (waypoint.y_m - transform.pivot_y_m) * transform.scale_y
            rotated_x_m, rotated_y_m = _rotate_xy(relative_x_m, relative_y_m, transform.rotation_deg)
            x_m = rotated_x_m + transform.pivot_x_m + transform.translation_x_m
            y_m = rotated_y_m + transform.pivot_y_m + transform.translation_y_m
            yaw_deg = waypoint.yaw_deg
            if yaw_deg is not None and transform.rotate_yaw:
                yaw_deg = (yaw_deg + transform.rotation_deg + 180.0) % 360.0 - 180.0
            target: MovePoint
            if waypoint.z_m is not None:
                target = RelativePoint3D(x_m, y_m, waypoint.z_m, yaw_deg)
            else:
                target = RelativePoint2D(x_m, y_m, yaw_deg)
            transformed.append(_copy_waypoint_with_target(waypoint, target))
        return Route(
            tuple(transformed),
            name=self.name if name is None else name,
            pattern=self.pattern,
            close=self.close,
            continuity=self.continuity,
            geometry=self.geometry,
        )

    @staticmethod
    def polyline(
        points: Sequence[Mapping[str, float] | MovePoint | Waypoint],
        *,
        name: Optional[str] = None,
    ) -> "Route":
        """生成折线航线。

        该方法在调用时把 points 中的 Waypoint 或包含 x_m/y_m/z_m 等字段的
        映射转换为标准航点序列，不会立即控制设备飞行；返回的 Route 需要交给
        Plan(drone).route(...) 执行。points 按顺序描述航点位置，本地坐标单位为米；
        name 为可选航线名称，用于事件和日志标识。返回值为 Route 对象，pattern 为
        RoutePattern.POLYLINE。
        """
        return Route(
            tuple(_coerce_waypoint(point) for point in points),
            name=name,
            pattern=RoutePattern.POLYLINE,
            geometry=RouteGeometry("polyline", {}),
        )

    @staticmethod
    def polygon(
        points: Sequence[Mapping[str, float] | MovePoint | Waypoint],
        *,
        name: Optional[str] = None,
        close: bool = True,
    ) -> "Route":
        """生成多边形航线。

        该方法在调用时把 points 转换为多边形边界航点，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。points 使用 Waypoint
        或包含 x_m/y_m/z_m 等字段的映射描述顶点，本地坐标单位为米；close=True
        时会自动追加首点形成闭合航线；name 为可选航线名称。返回值为 Route 对象，
        pattern 为 RoutePattern.POLYGON。
        """
        waypoints = [_coerce_waypoint(point) for point in points]
        if close and waypoints and not _same_local_xy(waypoints[0], waypoints[-1]):
            first = waypoints[0]
            waypoints.append(
                Waypoint(
                    x_m=first.x_m,
                    y_m=first.y_m,
                    z_m=first.z_m,
                    latitude_deg=first.latitude_deg,
                    longitude_deg=first.longitude_deg,
                    altitude_m=first.altitude_m,
                    speed_m_s=first.speed_m_s,
                    yaw_deg=first.yaw_deg,
                    acceptance_radius_m=first.acceptance_radius_m,
                    settle_time_s=first.settle_time_s,
                    hold_time_s=first.hold_time_s,
                    action=first.action,
                    metadata=first.metadata,
                )
            )
        return Route(
            tuple(waypoints),
            name=name,
            pattern=RoutePattern.POLYGON,
            close=close,
            geometry=RouteGeometry("polygon", {"close": close}),
        )

    def to_global(self, origin: GpsPoint, *, altitude_m: Optional[float] = None) -> "Route":
        """把本地米制航线转换为 GPS 航线并返回新对象。

        ``origin`` 对应本地 ``(0, 0)``；x/y 分别按北向/东向米制偏移转换。``altitude_m``
        可统一覆盖绝对海拔，否则优先使用航点 z/altitude，最后沿用原点海拔。
        """
        converted = []
        for waypoint in self.waypoints:
            if waypoint.latitude_deg is not None and waypoint.longitude_deg is not None:
                converted.append(waypoint)
                continue
            waypoint_altitude_m = altitude_m
            if waypoint_altitude_m is None:
                if waypoint.altitude_m is not None:
                    waypoint_altitude_m = waypoint.altitude_m
                elif waypoint.z_m is not None:
                    waypoint_altitude_m = origin.altitude_m + waypoint.z_m
            target = offset_gps_point(
                origin,
                north_m=float(waypoint.x_m or 0.0),
                east_m=float(waypoint.y_m or 0.0),
                altitude_m=waypoint_altitude_m,
                yaw_deg=waypoint.yaw_deg,
            )
            converted.append(_copy_waypoint_with_target(waypoint, target))
        return Route(
            tuple(converted),
            name=self.name,
            pattern=self.pattern,
            close=self.close,
            continuity=self.continuity,
            geometry=self.geometry,
        )

    @staticmethod
    def circle(
        *,
        radius_m: float,
        center_x_m: float = 0.0,
        center_y_m: float = 0.0,
        segments: int = 36,
        name: Optional[str] = None,
        clockwise: bool = True,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
        gps_origin: Optional[GpsPoint] = None,
        altitude_m: Optional[float] = None,
    ) -> "Route":
        """生成圆形航线。

        该方法在调用时按半径和分段数生成圆形航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。radius_m 为圆半径，
        center_x_m/center_y_m 为圆心本地坐标，单位均为米；segments 为圆周分段数；
        clockwise 表示是否顺时针生成；action、acceptance_radius_m 和 hold_time_s
        会应用到生成的每个航点，单位分别为标准动作、米和秒；name 为可选航线名称。
        返回值为 Route 对象，pattern 为 RoutePattern.CIRCLE。
        ``gps_origin`` 指定本地原点对应的 GPS 点，``altitude_m`` 是输出航点绝对海拔；
        未提供 ``gps_origin`` 时返回本地米制航线。
        """
        if radius_m <= 0:
            raise ValueError("radius_m must be greater than 0.")
        return Route.ellipse(
            radius_x_m=radius_m,
            radius_y_m=radius_m,
            center_x_m=center_x_m,
            center_y_m=center_y_m,
            segments=segments,
            name=name,
            clockwise=clockwise,
            pattern=RoutePattern.CIRCLE,
            action=action,
            acceptance_radius_m=acceptance_radius_m,
            hold_time_s=hold_time_s,
            gps_origin=gps_origin,
            altitude_m=altitude_m,
        )

    @staticmethod
    def ellipse(
        *,
        radius_x_m: float,
        radius_y_m: float,
        center_x_m: float = 0.0,
        center_y_m: float = 0.0,
        segments: int = 48,
        name: Optional[str] = None,
        clockwise: bool = True,
        pattern: RoutePattern | str = RoutePattern.ELLIPSE,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
        gps_origin: Optional[GpsPoint] = None,
        altitude_m: Optional[float] = None,
    ) -> "Route":
        """生成椭圆航线。

        该方法在调用时按长短轴半径和分段数生成椭圆航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。radius_x_m/radius_y_m
        为 x/y 方向半径，center_x_m/center_y_m 为中心点本地坐标，单位均为米；
        segments 为分段数；clockwise 表示是否顺时针生成；pattern 用于内部复用圆形
        航线类型；action、acceptance_radius_m 和 hold_time_s 会应用到生成的每个
        航点，单位分别为标准动作、米和秒；name 为可选航线名称。返回值为 Route
        对象，默认 pattern 为 RoutePattern.ELLIPSE。
        ``gps_origin`` 指定本地原点对应的 GPS 点，``altitude_m`` 是输出航点绝对海拔；
        未提供 ``gps_origin`` 时返回本地米制航线。
        """
        if radius_x_m <= 0 or radius_y_m <= 0:
            raise ValueError("ellipse radii must be greater than 0.")
        count = max(8, int(segments))
        direction = -1.0 if clockwise else 1.0
        points: list[Waypoint] = []
        for index in range(count + 1):
            angle = direction * 2.0 * math.pi * index / count
            points.append(
                _route_waypoint(
                    center_x_m + radius_x_m * math.cos(angle),
                    center_y_m + radius_y_m * math.sin(angle),
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
        route = Route(
            _with_local_origin_anchor(points),
            name=name,
            pattern=pattern,
            close=True,
            continuity=RouteContinuity.REQUIRED,
            geometry=RouteGeometry(
                "circle" if pattern == RoutePattern.CIRCLE else "ellipse",
                {
                    "radius_x_m": radius_x_m,
                    "radius_y_m": radius_y_m,
                    "center_x_m": center_x_m,
                    "center_y_m": center_y_m,
                    "clockwise": clockwise,
                },
            ),
        )
        return route.to_global(gps_origin, altitude_m=altitude_m) if gps_origin is not None else route

    @staticmethod
    def arc(
        *,
        radius_m: float,
        start_deg: float,
        end_deg: float,
        center_x_m: float = 0.0,
        center_y_m: float = 0.0,
        segments: int = 24,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
        gps_origin: Optional[GpsPoint] = None,
        altitude_m: Optional[float] = None,
    ) -> "Route":
        """生成圆弧航线。

        该方法在调用时按起止角度生成圆弧航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。radius_m 为半径，
        center_x_m/center_y_m 为圆心本地坐标，单位均为米；start_deg/end_deg 为
        起止角度，单位为度；segments 为分段数；action、acceptance_radius_m 和
        hold_time_s 会应用到生成的每个航点，单位分别为标准动作、米和秒；name 为
        可选航线名称。返回值为 Route 对象，pattern 为 RoutePattern.ARC。
        ``gps_origin`` 指定本地原点对应的 GPS 点，``altitude_m`` 是输出航点绝对海拔；
        未提供 ``gps_origin`` 时返回本地米制航线。
        """
        if radius_m <= 0:
            raise ValueError("radius_m must be greater than 0.")
        count = max(2, int(segments))
        start = math.radians(start_deg)
        end = math.radians(end_deg)
        points: list[Waypoint] = []
        for index in range(count + 1):
            angle = start + (end - start) * index / count
            points.append(
                _route_waypoint(
                    center_x_m + radius_m * math.cos(angle),
                    center_y_m + radius_m * math.sin(angle),
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
        route = Route(
            _with_local_origin_anchor(points),
            name=name,
            pattern=RoutePattern.ARC,
            continuity=RouteContinuity.REQUIRED,
            geometry=RouteGeometry(
                "arc",
                {
                    "radius_m": radius_m,
                    "start_deg": start_deg,
                    "end_deg": end_deg,
                    "center_x_m": center_x_m,
                    "center_y_m": center_y_m,
                },
            ),
        )
        return route.to_global(gps_origin, altitude_m=altitude_m) if gps_origin is not None else route

    @staticmethod
    def grid(
        *,
        width_m: float,
        height_m: float,
        spacing_m: float,
        origin_x_m: float = 0.0,
        origin_y_m: float = 0.0,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
        gps_origin: Optional[GpsPoint] = None,
        altitude_m: Optional[float] = None,
    ) -> "Route":
        """生成矩形网格覆盖航线。

        该方法在调用时生成割草机式矩形网格航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。width_m/height_m 为
        覆盖矩形尺寸，spacing_m 为相邻扫描线间距，origin_x_m/origin_y_m 为矩形
        原点本地坐标，单位均为米；action、acceptance_radius_m 和 hold_time_s 会
        应用到生成的每个航点，单位分别为标准动作、米和秒；name 为可选航线名称。
        返回值为 Route 对象，pattern 为 RoutePattern.GRID。
        ``gps_origin`` 指定本地原点对应的 GPS 点，``altitude_m`` 是输出航点绝对海拔；
        未提供 ``gps_origin`` 时返回本地米制航线。
        """
        if width_m <= 0 or height_m <= 0 or spacing_m <= 0:
            raise ValueError("width_m, height_m and spacing_m must be greater than 0.")
        rows = max(1, math.ceil(height_m / spacing_m))
        points: list[Waypoint] = []
        for row in range(rows + 1):
            y = origin_y_m + min(row * spacing_m, height_m)
            left = _route_waypoint(origin_x_m, y, action=action, acceptance_radius_m=acceptance_radius_m, hold_time_s=hold_time_s)
            right = _route_waypoint(
                origin_x_m + width_m,
                y,
                action=action,
                acceptance_radius_m=acceptance_radius_m,
                hold_time_s=hold_time_s,
            )
            if row % 2 == 0:
                points.extend((left, right))
            else:
                points.extend((right, left))
        route = Route(
            tuple(points),
            name=name,
            pattern=RoutePattern.GRID,
            geometry=RouteGeometry("grid", {"width_m": width_m, "height_m": height_m, "spacing_m": spacing_m}),
        )
        return route.to_global(gps_origin, altitude_m=altitude_m) if gps_origin is not None else route

    @staticmethod
    def survey_area(
        boundary: Sequence[Mapping[str, float] | MovePoint | Waypoint],
        *,
        spacing_m: float,
        angle_deg: float = 0.0,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> "Route":
        """生成本地多边形面积覆盖航线。

        该方法用于测绘、巡检、搜索等需要覆盖一块区域的场景，会在调用时把
        boundary 描述的本地多边形区域转换为割草机式航点序列，供
        Plan(drone).route(...) 后续执行。boundary 使用 Waypoint 或包含
        x_m/y_m 的映射描述边界点，单位为米；spacing_m 表示相邻扫描线间距，
        单位为米；angle_deg 表示扫描线相对本地坐标系的旋转角度，单位为度；
        action 表示每个覆盖航点到达后的标准动作；acceptance_radius_m 表示
        到达判定半径，单位为米；hold_time_s 表示到点停留时间，单位为秒。
        ``name`` 是可选航线显示名称。返回值为 Route 对象，pattern 为 RoutePattern.SURVEY_AREA。
        """
        if spacing_m <= 0:
            raise ValueError("spacing_m must be greater than 0.")
        boundary_points = [_coerce_waypoint(point) for point in boundary]
        if len(boundary_points) < 3:
            raise ValueError("survey_area requires at least three boundary points.")
        gps_origin = _gps_origin_for_waypoints(boundary_points)
        if gps_origin is None:
            local_points = [(float(point.x_m or 0.0), float(point.y_m or 0.0)) for point in boundary_points]
        else:
            for point in boundary_points:
                if point.latitude_deg is None or point.longitude_deg is None:
                    raise ValueError("GPS boundary point requires latitude and longitude.")
            local_points = [
                gps_offset_m(
                    gps_origin,
                    GpsPoint(
                        float(cast(float, point.latitude_deg)),
                        float(cast(float, point.longitude_deg)),
                        float(point.altitude_m if point.altitude_m is not None else gps_origin.altitude_m),
                    ),
                )
                for point in boundary_points
            ]
        rotated = [_rotate_xy(x, y, -angle_deg) for x, y in local_points]
        min_y = min(y for _, y in rotated)
        max_y = max(y for _, y in rotated)
        rows = max(1, math.ceil((max_y - min_y) / spacing_m))
        waypoints: list[Waypoint] = []
        for row in range(rows + 1):
            y = min(min_y + row * spacing_m, max_y)
            intersections = _polygon_scanline_intersections(rotated, y)
            pairs = [(intersections[index], intersections[index + 1]) for index in range(0, len(intersections) - 1, 2)]
            if row % 2 == 1:
                pairs.reverse()
            for start_x, end_x in pairs:
                segment = ((end_x, y), (start_x, y)) if row % 2 == 1 else ((start_x, y), (end_x, y))
                for x, segment_y in segment:
                    original_x, original_y = _rotate_xy(x, segment_y, angle_deg)
                    waypoints.append(
                        Waypoint(
                            x_m=original_x,
                            y_m=original_y,
                            action=action,
                            acceptance_radius_m=acceptance_radius_m,
                            hold_time_s=hold_time_s,
                        )
                    )
        if len(waypoints) < 2:
            raise ValueError("survey_area could not generate enough waypoints from boundary.")
        route = Route(
            _with_local_origin_anchor(waypoints),
            name=name,
            pattern=RoutePattern.SURVEY_AREA,
            geometry=RouteGeometry("survey_area", {"spacing_m": spacing_m, "angle_deg": angle_deg}),
        )
        return route.to_global(gps_origin) if gps_origin is not None else route

    @staticmethod
    def spiral(
        *,
        max_radius_m: float,
        spacing_m: float,
        turns: float = 3.0,
        entry_radius_m: float = 0.0,
        start_angle_deg: float = 0.0,
        clockwise: bool = False,
        center_x_m: float = 0.0,
        center_y_m: float = 0.0,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
        gps_origin: Optional[GpsPoint] = None,
        altitude_m: Optional[float] = None,
    ) -> "Route":
        """生成螺旋搜索航线。

        该方法在调用时从中心向外生成螺旋航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。max_radius_m 为最大
        搜索半径，``spacing_m`` 为相邻生成航点允许的近似最大间距，center_x_m/center_y_m 为中心点本地坐标，
        单位均为米；turns 为螺旋圈数，``entry_radius_m`` 是从中心沿入口方向直飞后开始旋转的半径，
        可用于让航线首段与传感器验证过的安全出口一致，实际径向圈间距约为
        ``(max_radius_m - entry_radius_m) / turns``；
        ``start_angle_deg`` 是螺旋入口相对本地 x 轴的角度，``clockwise`` 控制顺时针方向；
        action、acceptance_radius_m 和 hold_time_s
        会应用到生成的每个航点，单位分别为标准动作、米和秒；name 为可选航线名称。
        返回值为 Route 对象，pattern 为 RoutePattern.SPIRAL。
        ``gps_origin`` 指定本地原点对应的 GPS 点，``altitude_m`` 是输出航点绝对海拔；
        未提供 ``gps_origin`` 时返回本地米制航线。
        """
        if max_radius_m <= 0 or spacing_m <= 0 or turns <= 0:
            raise ValueError("max_radius_m, spacing_m and turns must be greater than 0.")
        if entry_radius_m < 0 or entry_radius_m >= max_radius_m:
            raise ValueError("entry_radius_m must be greater than or equal to 0 and less than max_radius_m.")
        theta_max = 2.0 * math.pi * turns
        curve_length_m = _spiral_curve_length(max_radius_m, entry_radius_m, turns)
        estimated_length_m = entry_radius_m + curve_length_m
        count = max(12, int(math.ceil(curve_length_m / spacing_m)))
        points: list[Waypoint] = []
        direction = -1.0 if clockwise else 1.0
        start_angle_rad = math.radians(start_angle_deg)
        if entry_radius_m > 0:
            points.append(
                _route_waypoint(
                    center_x_m,
                    center_y_m,
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
        for index in range(count + 1):
            ratio = index / count
            angle = start_angle_rad + direction * theta_max * ratio
            radius = entry_radius_m + (max_radius_m - entry_radius_m) * ratio
            points.append(
                _route_waypoint(
                    center_x_m + radius * math.cos(angle),
                    center_y_m + radius * math.sin(angle),
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
        route = Route(
            tuple(points),
            name=name,
            pattern=RoutePattern.SPIRAL,
            continuity=RouteContinuity.REQUIRED,
            geometry=RouteGeometry(
                "spiral",
                {
                    "max_radius_m": max_radius_m,
                    "spacing_m": spacing_m,
                    "turns": turns,
                    "entry_radius_m": entry_radius_m,
                    "start_angle_deg": start_angle_deg,
                    "clockwise": clockwise,
                    "estimated_length_m": estimated_length_m,
                    "radial_spacing_m": (max_radius_m - entry_radius_m) / turns,
                    "center_x_m": center_x_m,
                    "center_y_m": center_y_m,
                },
            ),
        )
        return route.to_global(gps_origin, altitude_m=altitude_m) if gps_origin is not None else route

    @staticmethod
    def spiral_search(
        *,
        target_length_m: float,
        max_radius_m: float,
        waypoint_spacing_m: float = 3.0,
        entry_radius_m: float = 0.0,
        start_angle_deg: float = 0.0,
        clockwise: bool = False,
        center_x_m: float = 0.0,
        center_y_m: float = 0.0,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> "Route":
        """按目标总里程生成从中心向外扩展的本地螺旋搜索航线。

        ``target_length_m``、``max_radius_m``、``waypoint_spacing_m`` 和 ``entry_radius_m`` 单位为米；
        目标里程必须大于最大半径。``entry_radius_m`` 大于 0 时，航线先从中心沿入口方向直飞该距离，
        再开始向外旋转，该直线段计入目标总里程。
        SDK 通过数值求解圈数，使生成折线的 ``estimated_length_m`` 接近目标值；``start_angle_deg`` 和
        ``clockwise`` 控制入口朝向与旋转方向；``center_x_m``、``center_y_m`` 是本地中心坐标（米），
        ``name`` 是可选航线名；``action``、``acceptance_radius_m``（米）和 ``hold_time_s``（秒）会应用到
        每个生成航点。返回本地 ``RoutePattern.SPIRAL`` 航线，不立即控制设备；
        当前入口不支持 GPS 原点，全球航线可在生成后调用 ``to_global()``。
        """

        if target_length_m <= 0 or max_radius_m <= 0 or waypoint_spacing_m <= 0:
            raise ValueError("Spiral search length, radius and waypoint spacing must be greater than 0.")
        if entry_radius_m < 0 or entry_radius_m >= max_radius_m:
            raise ValueError("Spiral search entry radius must be greater than or equal to 0 and less than max radius.")
        if target_length_m <= max_radius_m:
            raise ValueError("Spiral search target length must be greater than max radius.")

        low_turns = 0.05
        high_turns = 1.0

        def curve_length(turns: float) -> float:
            return entry_radius_m + _spiral_curve_length(max_radius_m, entry_radius_m, turns)

        while curve_length(high_turns) < target_length_m:
            high_turns *= 2.0
            if high_turns > 64.0:
                raise ValueError("Spiral search target length requires too many turns.")
        for _ in range(48):
            middle = (low_turns + high_turns) * 0.5
            if curve_length(middle) < target_length_m:
                low_turns = middle
            else:
                high_turns = middle
        route = Route.spiral(
            max_radius_m=max_radius_m,
            spacing_m=waypoint_spacing_m,
            turns=(low_turns + high_turns) * 0.5,
            entry_radius_m=entry_radius_m,
            start_angle_deg=start_angle_deg,
            clockwise=clockwise,
            center_x_m=center_x_m,
            center_y_m=center_y_m,
            name=name,
            action=action,
            acceptance_radius_m=acceptance_radius_m,
            hold_time_s=hold_time_s,
        )
        return Route(
            route.waypoints,
            name=route.name,
            pattern=route.pattern,
            continuity=route.continuity,
            geometry=RouteGeometry(
                "spiral",
                {
                        **(dict(route.geometry.params) if route.geometry is not None else {}),
                        "target_length_m": target_length_m,
                },
            ),
        )

    @staticmethod
    def corridor(
        centerline: Sequence[Mapping[str, float] | MovePoint | Waypoint],
        *,
        width_m: float,
        name: Optional[str] = None,
        action: WaypointAction = WaypointAction.NONE,
        acceptance_radius_m: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> "Route":
        """生成走廊区域航线。

        该方法在调用时根据中心线和宽度生成走廊边界航点序列，不会立即控制设备飞行；
        返回的 Route 需要交给 Plan(drone).route(...) 执行。centerline 使用
        Waypoint 或包含 x_m/y_m 的映射描述走廊中心线点，本地坐标单位为米；
        width_m 为走廊宽度，单位为米；action、acceptance_radius_m 和 hold_time_s
        会应用到生成的每个航点，单位分别为标准动作、米和秒；name 为可选航线名称。
        返回值为 Route 对象，pattern 为 RoutePattern.CORRIDOR。
        """
        if width_m <= 0:
            raise ValueError("width_m must be greater than 0.")
        centers = [_coerce_waypoint(point) for point in centerline]
        if len(centers) < 2:
            raise ValueError("corridor requires at least two centerline points.")
        gps_origin = _gps_origin_for_waypoints(centers)
        if gps_origin is not None:
            for point in centers:
                if point.latitude_deg is None or point.longitude_deg is None:
                    raise ValueError("GPS centerline point requires latitude and longitude.")
            centers = [
                Waypoint(
                    x_m=gps_offset_m(
                        gps_origin,
                        GpsPoint(
                            float(cast(float, point.latitude_deg)),
                            float(cast(float, point.longitude_deg)),
                            float(point.altitude_m if point.altitude_m is not None else gps_origin.altitude_m),
                        ),
                    )[0],
                    y_m=gps_offset_m(
                        gps_origin,
                        GpsPoint(
                            float(cast(float, point.latitude_deg)),
                            float(cast(float, point.longitude_deg)),
                            float(point.altitude_m if point.altitude_m is not None else gps_origin.altitude_m),
                        ),
                    )[1],
                )
                for point in centers
            ]
        half = width_m / 2.0
        left: list[Waypoint] = []
        right: list[Waypoint] = []
        for index, point in enumerate(centers):
            if index == 0:
                next_point = centers[index + 1]
                dx = float(next_point.x_m or 0.0) - float(point.x_m or 0.0)
                dy = float(next_point.y_m or 0.0) - float(point.y_m or 0.0)
            else:
                prev_point = centers[index - 1]
                dx = float(point.x_m or 0.0) - float(prev_point.x_m or 0.0)
                dy = float(point.y_m or 0.0) - float(prev_point.y_m or 0.0)
            length = math.hypot(dx, dy) or 1.0
            nx = -dy / length
            ny = dx / length
            left.append(
                _route_waypoint(
                    float(point.x_m or 0.0) + nx * half,
                    float(point.y_m or 0.0) + ny * half,
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
            right.append(
                _route_waypoint(
                    float(point.x_m or 0.0) - nx * half,
                    float(point.y_m or 0.0) - ny * half,
                    action=action,
                    acceptance_radius_m=acceptance_radius_m,
                    hold_time_s=hold_time_s,
                )
            )
        route = Route(
            _with_local_origin_anchor(left + list(reversed(right)) + [left[0]]),
            name=name,
            pattern=RoutePattern.CORRIDOR,
            close=True,
            geometry=RouteGeometry("corridor", {"width_m": width_m}),
        )
        return route.to_global(gps_origin) if gps_origin is not None else route


def _coerce_waypoint(value: Mapping[str, Any] | MovePoint | Waypoint) -> Waypoint:
    if isinstance(value, Waypoint):
        return value
    if isinstance(value, (GpsPoint, RelativePoint2D, RelativePoint3D)):
        return Waypoint.from_target(value)
    data = dict(value)
    x = data.get("x_m", data.get("x"))
    y = data.get("y_m", data.get("y"))
    z = data.get("z_m", data.get("z"))
    return Waypoint(
        x_m=float(x) if x is not None else None,
        y_m=float(y) if y is not None else None,
        z_m=float(z) if z is not None else None,
        latitude_deg=float(data["latitude_deg"]) if data.get("latitude_deg") is not None else None,
        longitude_deg=float(data["longitude_deg"]) if data.get("longitude_deg") is not None else None,
        altitude_m=float(data["altitude_m"]) if data.get("altitude_m") is not None else None,
        speed_m_s=float(data["speed_m_s"]) if data.get("speed_m_s") is not None else None,
        yaw_deg=float(data["yaw_deg"]) if data.get("yaw_deg") is not None else None,
        acceptance_radius_m=float(data["acceptance_radius_m"]) if data.get("acceptance_radius_m") is not None else None,
        settle_time_s=float(data["settle_time_s"]) if data.get("settle_time_s") is not None else None,
        hold_time_s=float(data.get("hold_time_s", 0.0)),
        action=data.get("action", WaypointAction.NONE),
        waypoint_id=str(data.get("waypoint_id") or uuid4()),
        metadata=dict(data.get("metadata") or {}),
    )


def _same_local_xy(first: Waypoint, second: Waypoint) -> bool:
    if first.latitude_deg is not None or second.latitude_deg is not None:
        return first.latitude_deg == second.latitude_deg and first.longitude_deg == second.longitude_deg
    return first.x_m == second.x_m and first.y_m == second.y_m


def _gps_origin_for_waypoints(waypoints: Sequence[Waypoint]) -> Optional[GpsPoint]:
    gps_points = [
        waypoint
        for waypoint in waypoints
        if waypoint.latitude_deg is not None and waypoint.longitude_deg is not None
    ]
    if not gps_points:
        return None
    if len(gps_points) != len(waypoints):
        raise ValueError("route points cannot mix GPS and local coordinates.")
    first = gps_points[0]
    if first.latitude_deg is None or first.longitude_deg is None:
        raise ValueError("GPS route point requires latitude and longitude.")
    return GpsPoint(
        float(first.latitude_deg),
        float(first.longitude_deg),
        float(first.altitude_m or 0.0),
        first.yaw_deg,
    )


def _copy_waypoint_with_target(waypoint: Waypoint, target: MovePoint) -> Waypoint:
    return Waypoint.from_target(
        target,
        speed_m_s=waypoint.speed_m_s,
        acceptance_radius_m=waypoint.acceptance_radius_m,
        settle_time_s=waypoint.settle_time_s,
        hold_time_s=waypoint.hold_time_s,
        action=waypoint.action,
        metadata=waypoint.metadata,
    )


def _with_local_origin_anchor(points: Sequence[Waypoint]) -> tuple[Waypoint, ...]:
    if not points:
        return ()
    first = points[0]
    if float(first.x_m or 0.0) == 0.0 and float(first.y_m or 0.0) == 0.0:
        return tuple(points)
    return (Waypoint(x_m=0.0, y_m=0.0), *tuple(points))


def _spiral_curve_length(max_radius_m: float, entry_radius_m: float, turns: float) -> float:
    """返回从入口半径扩展到最大半径的阿基米德螺旋曲线长度（米）。"""

    theta_max = 2.0 * math.pi * turns
    scale = (max_radius_m - entry_radius_m) / theta_max

    def antiderivative(radius_m: float) -> float:
        return 0.5 * (
            radius_m * math.sqrt(radius_m * radius_m + scale * scale)
            + scale * scale * math.asinh(radius_m / scale)
        )

    return (antiderivative(max_radius_m) - antiderivative(entry_radius_m)) / scale


def _route_waypoint(
    x_m: float,
    y_m: float,
    *,
    action: WaypointAction,
    acceptance_radius_m: Optional[float],
    hold_time_s: float,
) -> Waypoint:
    return Waypoint(
        x_m=x_m,
        y_m=y_m,
        action=action,
        acceptance_radius_m=acceptance_radius_m,
        hold_time_s=hold_time_s,
    )


def _rotate_xy(x_m: float, y_m: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    return x_m * cos_angle - y_m * sin_angle, x_m * sin_angle + y_m * cos_angle


def _polygon_scanline_intersections(points: Sequence[tuple[float, float]], y_m: float) -> list[float]:
    intersections: list[float] = []
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        if y1 == y2:
            continue
        lower_y = min(y1, y2)
        upper_y = max(y1, y2)
        if y_m < lower_y or y_m >= upper_y:
            continue
        ratio = (y_m - y1) / (y2 - y1)
        intersections.append(x1 + ratio * (x2 - x1))
    intersections.sort()
    return intersections


