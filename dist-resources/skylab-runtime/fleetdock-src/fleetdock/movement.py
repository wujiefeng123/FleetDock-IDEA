"""FleetDock 标准移动目标与坐标转换工具。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Mapping, TypeAlias, Union


class MoveTargetKind(str, Enum):
    """标准移动目标类型；产品 profile 用它声明可接受的移动语义。"""

    GPS_POINT = "gps_point"  # 全球经纬度目标，适合具备全球定位能力的设备。
    RELATIVE_POINT_2D = "relative_point_2d"  # 任务局部平面中的绝对目标点。
    RELATIVE_POINT_3D = "relative_point_3d"  # 任务局部三维空间中的绝对目标点。
    RELATIVE_MOVE_2D = "relative_move_2d"  # 相对设备当前朝向的平面位移。
    RELATIVE_MOVE_3D = "relative_move_3d"  # 相对设备当前朝向的三维位移。


@dataclass(frozen=True)
class GpsPoint:
    """全球 GPS 目标点。

    ``latitude_deg`` 和 ``longitude_deg`` 单位为度；``altitude_m`` 是相对平均海平面的
    绝对海拔，单位为米，与 MAVSDK ``goto_location`` 的高度语义一致；``yaw_deg`` 是可选
    目标偏航角，单位为度。该对象描述“去这个地理位置”，不包含当前位置。
    """

    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    yaw_deg: float | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90.")
        if not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180.")


@dataclass(frozen=True)
class RelativePoint2D:
    """任务局部平面中的目标点。

    ``x_m`` 和 ``y_m`` 单位为米，分别表示任务坐标中的前向/北向轴和右向/东向轴；
    ``yaw_deg`` 是可选绝对朝向角，单位为度。该对象适合机械狗等平面移动设备。
    """

    x_m: float
    y_m: float
    yaw_deg: float | None = None


@dataclass(frozen=True)
class RelativePoint3D:
    """任务局部三维坐标中的目标点。

    ``x_m``、``y_m`` 和 ``z_m`` 单位为米；x/y 分别对应局部北向和东向，z 轴向上为正。
    ``yaw_deg`` 是可选绝对偏航角，单位为度。PX4 适配器会在内部转换为 Local NED。
    """

    x_m: float
    y_m: float
    z_m: float
    yaw_deg: float | None = None


@dataclass(frozen=True)
class RelativeMove2D:
    """相对设备当前朝向的平面位移。

    ``forward_m`` 和 ``right_m`` 单位为米，正值分别表示向前和向右移动；
    ``yaw_deg`` 是本次移动附加的相对转角，单位为度。执行时需要设备具备当前位姿。
    """

    forward_m: float = 0.0
    right_m: float = 0.0
    yaw_deg: float = 0.0


@dataclass(frozen=True)
class RelativeMove3D:
    """相对设备当前朝向的三维位移。

    ``forward_m``、``right_m`` 和 ``up_m`` 单位为米，正值分别表示向前、向右和向上；
    ``yaw_deg`` 是本次移动附加的相对偏航角，单位为度。执行时需要设备具备当前位姿。
    """

    forward_m: float = 0.0
    right_m: float = 0.0
    up_m: float = 0.0
    yaw_deg: float = 0.0


MovePoint: TypeAlias = Union[GpsPoint, RelativePoint2D, RelativePoint3D]
MoveTarget: TypeAlias = Union[MovePoint, RelativeMove2D, RelativeMove3D]


def move_target_kind(target: MoveTarget) -> MoveTargetKind:
    """返回 ``target`` 对应的标准目标类型；类型未知时抛出 ``TypeError``。"""
    mapping = {
        GpsPoint: MoveTargetKind.GPS_POINT,
        RelativePoint2D: MoveTargetKind.RELATIVE_POINT_2D,
        RelativePoint3D: MoveTargetKind.RELATIVE_POINT_3D,
        RelativeMove2D: MoveTargetKind.RELATIVE_MOVE_2D,
        RelativeMove3D: MoveTargetKind.RELATIVE_MOVE_3D,
    }
    try:
        return mapping[type(target)]
    except KeyError as exc:
        raise TypeError(f"unsupported move target type: {type(target).__name__}") from exc


def serialize_move_target(target: MoveTarget) -> Mapping[str, object]:
    """把标准移动目标转换为可序列化命令参数。

    ``target`` 是 GPS 点、局部点或相对位移对象；返回映射包含 ``target_kind`` 类型标记和
    ``target`` 字段数据，供设备对象跨适配器传递，不改变原对象。
    """
    return {
        "target_kind": move_target_kind(target).value,
        "target": asdict(target),
    }


def offset_gps_point(
    origin: GpsPoint,
    *,
    north_m: float,
    east_m: float,
    altitude_m: float | None = None,
    yaw_deg: float | None = None,
) -> GpsPoint:
    """从 GPS 原点按局部北/东偏移生成新目标点。

    ``origin`` 是局部坐标 ``(0, 0)`` 对应的全球点；``north_m`` 和 ``east_m`` 单位为米，
    ``yaw_deg`` 是可选目标偏航角（度），``altitude_m`` 未提供时沿用原点绝对海拔。
    返回新的 ``GpsPoint``。该转换使用 WGS84 长半轴局部近似，不用于跨区域大尺度测绘。
    """
    earth_radius_m = 6378137.0
    latitude_rad = math.radians(origin.latitude_deg)
    latitude_deg = origin.latitude_deg + math.degrees(north_m / earth_radius_m)
    longitude_scale = earth_radius_m * max(abs(math.cos(latitude_rad)), 1e-6)
    longitude_deg = origin.longitude_deg + math.degrees(east_m / longitude_scale)
    return GpsPoint(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_m=origin.altitude_m if altitude_m is None else altitude_m,
        yaw_deg=yaw_deg,
    )


def gps_offset_m(origin: GpsPoint, point: GpsPoint) -> tuple[float, float]:
    """返回 ``point`` 相对 ``origin`` 的局部北向、东向偏移，单位为米。"""
    earth_radius_m = 6378137.0
    latitude_rad = math.radians(origin.latitude_deg)
    north_m = math.radians(point.latitude_deg - origin.latitude_deg) * earth_radius_m
    east_m = (
        math.radians(point.longitude_deg - origin.longitude_deg)
        * earth_radius_m
        * max(abs(math.cos(latitude_rad)), 1e-6)
    )
    return north_m, east_m
