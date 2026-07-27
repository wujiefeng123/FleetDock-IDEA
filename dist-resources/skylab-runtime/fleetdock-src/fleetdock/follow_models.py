"""Fleet 动态跟随策略模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, TypeAlias, Union

class FollowConflict(str, Enum):
    """跟随关系冲突处理策略。

    当某个设备已经在当前 Fleet 的活动跟随关系中担任 follower 时，新的 `fleet.follow(...)`
    或 `FollowSession.add(...)` 会按该枚举决定是直接失败，还是先从旧关系中移除再加入新关系。
    """

    # 发现 follower 已经跟随其它 leader 时直接抛出 ValueError，适合默认的保守控制流程。
    FAIL = "fail"
    # 发现 follower 已经跟随其它 leader 时先解除旧关系，再加入新的 FollowSession。
    REPLACE = "replace"


class FollowMode(str, Enum):
    """跟随策略类型常量。

    FleetDock 的 `fleet.follow(...)` 负责保存 leader/follower 关系和策略；具体控制循环可按
    `policy.mode` 选择不同跟随效果。这里不把“链式跟随”做成简单策略，因为它需要额外的
    冲突约束、队列重排和失联处理，不能只靠单个 follower 的偏移参数安全表达。
    """

    # 固定相对位置：follower 追向 leader 坐标加一个固定偏移，适合编队/队形保持。
    RELATIVE = "relative"
    # 追踪跟随：follower 始终朝向 leader，并按目标距离靠近或拉开，适合表现“跟着目标走”。
    CHASE = "chase"
    # 轨迹跟随：follower 追 leader 几秒前经过的位置，效果像沿着 leader 的航迹排队。
    TRAIL = "trail"
    # 视角跟随：follower 保持在 leader 后侧/侧后方，并持续朝向 leader，适合作为观察机。
    CAMERA = "camera"
    # 距离带跟随：距离落在 min/max 区间内就悬停，过近拉开、过远追上，适合安全距离控制。
    BAND = "band"


@dataclass(frozen=True)
class RelativeFollowPolicy:
    """相对位置跟随策略。

    `x_m`、`y_m`、`z_m` 单位均为米，表示 follower 希望保持在 leader 局部坐标系中的相对偏移；
    默认约定 x 为前后方向、y 为左右方向、z 为上下方向。`frame` 用于标记偏移解释坐标系，
    `max_speed_m_s` 可选限制 follower 调整位置时的最大速度，单位为米/秒；当前 SDK 先保存策略，
    后续接入真实控制循环时由适配器或任务层解释执行。
    """

    x_m: float = 0.0
    y_m: float = 0.0
    z_m: float = 0.0
    frame: str = "leader_local"
    max_speed_m_s: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> FollowMode:
        """返回策略类型常量，用于执行器选择固定相对位置跟随逻辑。"""
        return FollowMode.RELATIVE


@dataclass(frozen=True)
class ChaseFollowPolicy:
    """追踪跟随策略。

    follower 会朝向 leader，并努力把水平距离保持在 `distance_m` 附近；当距离已经足够接近时，
    控制循环应停止水平移动但继续保持朝向。`vertical_offset_m` 预留给支持高度闭环的执行层。
    """

    distance_m: float = 10.0
    vertical_offset_m: float = 0.0
    max_speed_m_s: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> FollowMode:
        """返回策略类型常量，用于执行器选择追踪跟随逻辑。"""
        return FollowMode.CHASE


@dataclass(frozen=True)
class TrailFollowPolicy:
    """轨迹跟随策略。

    follower 追向 leader `delay_s` 秒前经过的位置；相比固定偏移，它更像“沿着前机走过的路跟上去”，
    转弯时队形会自然拉出弧线。`distance_m` 是历史样本不足时的备用追踪距离。
    """

    delay_s: float = 2.0
    distance_m: float = 8.0
    max_speed_m_s: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> FollowMode:
        """返回策略类型常量，用于执行器选择轨迹跟随逻辑。"""
        return FollowMode.TRAIL


@dataclass(frozen=True)
class CameraFollowPolicy:
    """视角跟随策略。

    follower 尽量停在 leader 运动方向的后侧，并用 `side_m` 拉开侧向距离；机头持续朝向 leader，
    适合把一架飞机作为观测机/拍摄机。leader 速度太小时会退化为普通追踪距离。
    """

    distance_m: float = 12.0
    side_m: float = 4.0
    vertical_offset_m: float = 0.0
    max_speed_m_s: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> FollowMode:
        """返回策略类型常量，用于执行器选择视角跟随逻辑。"""
        return FollowMode.CAMERA


@dataclass(frozen=True)
class BandFollowPolicy:
    """距离带跟随策略。

    follower 只在距离小于 `min_distance_m` 或大于 `max_distance_m` 时移动；距离处在安全区间内时
    保持悬停并朝向 leader，适合展示“不会贴太近，也不会丢太远”的安全跟随。
    """

    min_distance_m: float = 8.0
    max_distance_m: float = 14.0
    max_speed_m_s: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> FollowMode:
        """返回策略类型常量，用于执行器选择距离带跟随逻辑。"""
        return FollowMode.BAND


FollowPolicySpec: TypeAlias = Union[
    RelativeFollowPolicy,
    ChaseFollowPolicy,
    TrailFollowPolicy,
    CameraFollowPolicy,
    BandFollowPolicy,
]
"""任一可被跟随执行器识别的策略对象类型。"""


class FollowPolicy:
    """跟随策略工厂。

    该类用于给开发者提供一个统一、可发现的策略入口，例如
    `FollowPolicy.camera(...)` 或 `FollowPolicy.chase(...)`。每个工厂方法都会返回对应的
    强类型策略对象；业务代码应统一通过该工厂创建策略，避免直接依赖内部策略数据类。
    """

    @staticmethod
    def relative(
        *,
        x_m: float = 0.0,
        y_m: float = 0.0,
        z_m: float = 0.0,
        frame: str = "leader_local",
        max_speed_m_s: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FollowPolicySpec:
        """创建固定相对位置跟随策略。

        `x_m`、`y_m`、`z_m` 单位为米，按 `frame` 指定的坐标系解释；默认 `leader_local`
        表示 leader 局部坐标系。`max_speed_m_s` 用于限制 follower 调整位置的最大速度，
        单位为米/秒；`metadata` 会复制到返回的策略对象，供上层保存业务标记。
        返回可直接传给 `fleet.follow(...)` 或 `session.add(...)` 的相对位置跟随策略对象。
        """
        return RelativeFollowPolicy(
            x_m=x_m,
            y_m=y_m,
            z_m=z_m,
            frame=frame,
            max_speed_m_s=max_speed_m_s,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def chase(
        *,
        distance_m: float = 10.0,
        vertical_offset_m: float = 0.0,
        max_speed_m_s: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FollowPolicySpec:
        """创建追踪跟随策略。

        follower 会朝向 leader，并把水平距离控制在 `distance_m` 附近；`vertical_offset_m`
        单位为米，供支持高度闭环的执行层使用。`max_speed_m_s` 单位为米/秒，`metadata`
        会复制到返回的策略对象。返回追踪跟随策略对象。
        """
        return ChaseFollowPolicy(
            distance_m=distance_m,
            vertical_offset_m=vertical_offset_m,
            max_speed_m_s=max_speed_m_s,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def trail(
        *,
        delay_s: float = 2.0,
        distance_m: float = 8.0,
        max_speed_m_s: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FollowPolicySpec:
        """创建轨迹跟随策略。

        follower 会追向 leader `delay_s` 秒前经过的位置；`distance_m` 单位为米，
        用于历史样本不足时的备用追踪距离。`max_speed_m_s` 单位为米/秒，`metadata`
        会复制到返回的策略对象。返回轨迹跟随策略对象。
        """
        return TrailFollowPolicy(
            delay_s=delay_s,
            distance_m=distance_m,
            max_speed_m_s=max_speed_m_s,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def camera(
        *,
        distance_m: float = 12.0,
        side_m: float = 4.0,
        vertical_offset_m: float = 0.0,
        max_speed_m_s: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FollowPolicySpec:
        """创建视角跟随策略。

        follower 尽量位于 leader 后侧或侧后方，并持续朝向 leader；`distance_m`、`side_m`
        和 `vertical_offset_m` 单位为米，适合把某架飞机作为观察机或拍摄机。`max_speed_m_s`
        单位为米/秒，`metadata` 会复制到返回的策略对象。返回视角跟随策略对象。
        """
        return CameraFollowPolicy(
            distance_m=distance_m,
            side_m=side_m,
            vertical_offset_m=vertical_offset_m,
            max_speed_m_s=max_speed_m_s,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def band(
        *,
        min_distance_m: float = 8.0,
        max_distance_m: float = 14.0,
        max_speed_m_s: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FollowPolicySpec:
        """创建距离带跟随策略。

        follower 只在与 leader 的水平距离小于 `min_distance_m` 或大于 `max_distance_m`
        时移动；两个距离参数单位均为米。`max_speed_m_s` 单位为米/秒，`metadata`
        会复制到返回的策略对象。返回距离带跟随策略对象。
        """
        return BandFollowPolicy(
            min_distance_m=min_distance_m,
            max_distance_m=max_distance_m,
            max_speed_m_s=max_speed_m_s,
            metadata=dict(metadata or {}),
        )



