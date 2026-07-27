"""FleetDock 标准障碍物模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from fleetdock.events import CoordinateFrame, Vector3


class ObstacleKind(str, Enum):
    """障碍物业务类型；用于区分静态目标、动态目标和厂商无法分类的目标。"""

    # 固定障碍物，例如建筑物、围栏、箱体或地图中的禁入实体。
    STATIC = "static"
    # 会移动的障碍物，例如车辆、人员、其他无人机或移动机器人。
    DYNAMIC = "dynamic"
    # 来源系统未声明类型，SDK 只能按保守障碍物处理。
    UNKNOWN = "unknown"


class ObstacleShape(str, Enum):
    """障碍物几何外形；用于让 SDK 以统一方式估算安全距离和绕行可行性。"""

    # 球形或保守圆形包围体；无人机、行人、未知小目标默认使用该形状。
    SPHERE = "sphere"
    # 与障碍物坐标系轴对齐的长方体；适合墙、屋顶、禁入箱体和建筑结构。
    BOX = "box"
    # z 轴方向的圆柱体；适合柱子、树干、塔杆或地面机器人常见竖向障碍。
    CYLINDER = "cylinder"


@dataclass(frozen=True)
class Obstacle:
    """跨品牌、跨设备的标准障碍物。

    ``position_m`` 与 ``velocity_m_s`` 均使用 ``frame`` 声明的坐标系；位置单位为米，速度单位为
    米/秒。``shape`` 描述障碍物外形；球形和圆柱体使用 ``radius_m``，长方体使用 ``size_m`` 表示
    x/y/z 方向全尺寸，单位为米。``confidence`` 为 0 到 1 的可信度，可为空表示 provider 未提供。
    ``observed_at_s`` 和 ``ttl_s`` 使用 ``time.monotonic()`` 秒值，只用于进程内新鲜度判断。
    """

    obstacle_id: str | None
    kind: ObstacleKind = ObstacleKind.UNKNOWN
    shape: ObstacleShape = ObstacleShape.SPHERE
    frame: CoordinateFrame = CoordinateFrame.LOCAL_NED
    position_m: Vector3 = Vector3(0.0, 0.0, 0.0)
    velocity_m_s: Vector3 | None = None
    radius_m: float | None = None
    size_m: Vector3 | None = None
    confidence: float | None = None
    observed_at_s: float = 0.0
    ttl_s: float | None = None

    def is_fresh(self, now_s: float | None = None, *, default_ttl_s: float = 1.0) -> bool:
        """判断障碍物是否仍在有效期内。

        ``now_s`` 默认使用当前单调时钟；``default_ttl_s`` 用于 provider 未给 ``ttl_s`` 时的兜底
        秒数。返回 ``False`` 表示该障碍物不应继续参与碰撞预测。
        """
        now = time.monotonic() if now_s is None else now_s
        ttl = default_ttl_s if self.ttl_s is None else self.ttl_s
        if ttl < 0:
            return False
        return now - self.observed_at_s <= ttl


@dataclass(frozen=True)
class ObstacleSnapshot:
    """某个感知 provider 在同一时刻观察到的一批障碍物。

    ``provider_name`` 是稳定来源名；``frame`` 是快照默认坐标系，单个 ``Obstacle`` 仍可声明自己的
    坐标系。``observed_at_s`` 使用 ``time.monotonic()`` 秒值，``obstacles`` 保存不可变障碍物元组。
    """

    provider_name: str
    frame: CoordinateFrame
    observed_at_s: float
    obstacles: tuple[Obstacle, ...] = ()


class ObstacleProvider(Protocol):
    """障碍物 provider 协议。

    厂商 SDK、仿真器、外部视觉/雷达或业务系统只需实现 ``snapshot()``。如 provider 持有后台线程、
    socket 或文件句柄，可额外实现 ``close()``，设备关闭时 ``AvoidanceManager`` 会尽力调用。
    """

    def snapshot(self) -> ObstacleSnapshot:
        """返回当前障碍物快照；调用应尽量快速且不得阻塞设备控制循环。"""

    def close(self) -> None:
        """释放 provider 持有的后台线程、连接或文件句柄；无资源可释放的实现可以不提供该方法，无返回值。"""
