"""Fleet 跟随队形布局模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from fleetdock.follow_models import FollowPolicy, FollowPolicySpec


class FormationSlotState(str, Enum):
    """队形槽位分配状态。

    该状态用于 ``FormationAssignment`` 展示某个槽位是否已经绑定 follower；它不表示设备连接、
    飞行或执行器运行状态。
    """

    # 槽位已经分配给一个 follower。
    ASSIGNED = "assigned"
    # 槽位暂时没有设备占用，可用于后续补位。
    VACANT = "vacant"


@dataclass(frozen=True)
class FormationSlot:
    """队形中的一个 follower 目标槽位。

    ``x_m``、``y_m``、``z_m`` 单位均为米，默认按 leader 本地坐标系解释：x 为前后方向，
    y 为左右方向，z 为垂直方向。槽位最终会被 SDK 编译成 ``FollowPolicy.relative(...)``，
    开发者无需直接处理底层跟随控制器。
    """

    slot_id: str
    x_m: float
    y_m: float = 0.0
    z_m: float = 0.0
    frame: str = "leader_local"
    max_speed_m_s: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_follow_policy(self) -> FollowPolicySpec:
        """把槽位转换为相对位置跟随策略。

        返回值可直接交给 ``FollowSession`` 保存；策略 metadata 会带上 ``formation_slot_id``，
        便于状态查询和队形变换时追踪槽位来源。
        """
        return FollowPolicy.relative(
            x_m=self.x_m,
            y_m=self.y_m,
            z_m=self.z_m,
            frame=self.frame,
            max_speed_m_s=self.max_speed_m_s,
            metadata={"formation_slot_id": self.slot_id, **dict(self.metadata)},
        )


@dataclass(frozen=True)
class FormationAssignment:
    """队形槽位与 follower 的绑定关系。

    ``slot_id`` 来自 ``FormationLayout.slots``；``device_id`` 是业务设备 ID 或 SDK 实例 ID；
    ``state`` 表示当前槽位是否已占用。该对象用于状态查询和事件展示，不要求调用方直接构造。
    """

    slot_id: str
    device_id: Optional[str]
    state: FormationSlotState


@dataclass(frozen=True)
class FormationTransitionOptions:
    """队形变换参数。

    ``duration_s`` 是期望变换耗时，单位为秒；第一版执行器会立即更新目标槽位，并由已有
    跟随控制器按速度限制追向新目标。后续可在该对象上扩展真正的插值和平滑约束。
    """

    duration_s: float = 8.0


@dataclass(frozen=True)
class FormationLayout:
    """可传给 ``fleet.follow(..., layout=...)`` 的队形布局。

    布局只描述 follower 相对于 leader 的槽位，不负责启动控制循环；Fleet 会按 follower 顺序
    把槽位分配给设备，并转换为相对位置跟随策略。
    """

    name: str
    slots: tuple[FormationSlot, ...]
    min_separation_m: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", tuple(self.slots))
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("Formation slot ids must be unique.")

    @classmethod
    def custom(
        cls,
        slots: Iterable[FormationSlot],
        *,
        name: str = "custom",
        min_separation_m: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "FormationLayout":
        """创建自定义队形布局。

        ``slots`` 是相对 leader 的槽位序列，顺序会用于默认分配 follower；距离单位均为米。
        ``name`` 是布局名称，出现在状态和事件中；``min_separation_m`` 可作为该布局建议的
        最小间距；``metadata`` 保存调用方业务标记。返回可直接传给 ``fleet.follow(..., layout=...)``
        的布局对象。
        """
        return cls(name=name, slots=tuple(slots), min_separation_m=min_separation_m, metadata=dict(metadata or {}))

    @classmethod
    def line(
        cls,
        *,
        spacing_m: float = 8.0,
        count: int = 2,
        x_m: float = -8.0,
        altitude_step_m: float = 0.0,
    ) -> "FormationLayout":
        """创建横队布局。

        ``spacing_m`` 是相邻 follower 的横向距离，单位为米；``count`` 是槽位数量；``x_m`` 表示
        槽位位于 leader 前后方向的偏移，负值通常表示在 leader 后方；``altitude_step_m`` 是相邻
        槽位的高度差，单位为米。返回横队布局对象。
        """
        offsets = _centered_offsets(count, spacing_m)
        return cls(
            name="line",
            slots=tuple(
                FormationSlot(f"line-{index + 1}", x_m=x_m, y_m=offset, z_m=index * altitude_step_m)
                for index, offset in enumerate(offsets)
            ),
            min_separation_m=spacing_m,
        )

    @classmethod
    def column(
        cls,
        *,
        spacing_m: float = 8.0,
        count: int = 2,
        y_m: float = 0.0,
        altitude_step_m: float = 0.0,
    ) -> "FormationLayout":
        """创建纵队布局。

        ``spacing_m`` 是前后相邻槽位距离，单位为米；``count`` 是槽位数量；``y_m`` 是统一横向偏移；
        ``altitude_step_m`` 是相邻槽位的高度差，单位为米。第一个槽位位于 leader 后方一个间距处。
        返回纵队布局对象。
        """
        return cls(
            name="column",
            slots=tuple(
                FormationSlot(
                    f"column-{index + 1}",
                    x_m=-(index + 1) * spacing_m,
                    y_m=y_m,
                    z_m=index * altitude_step_m,
                )
                for index in range(count)
            ),
            min_separation_m=spacing_m,
        )

    @classmethod
    def v_shape(
        cls,
        *,
        spacing_m: float = 8.0,
        count: int = 2,
        altitude_step_m: float = 0.0,
    ) -> "FormationLayout":
        """创建 V 字队形布局。

        ``spacing_m`` 是同侧前后和左右的基础间隔，单位为米；``count`` 是槽位数量；
        ``altitude_step_m`` 是相邻槽位的高度差，单位为米。槽位按左、右、左、右顺序生成，
        便于按 follower 传入顺序自动分配。返回 V 字队形布局对象。
        """
        slots: list[FormationSlot] = []
        for index in range(count):
            rank = index // 2 + 1
            side = -1.0 if index % 2 == 0 else 1.0
            side_name = "left" if side < 0.0 else "right"
            slots.append(
                FormationSlot(
                    f"{side_name}-{rank}",
                    x_m=-rank * spacing_m,
                    y_m=side * rank * spacing_m,
                    z_m=index * altitude_step_m,
                )
            )
        return cls(name="v_shape", slots=tuple(slots), min_separation_m=spacing_m)

    @classmethod
    def grid(
        cls,
        *,
        rows: int,
        columns: int,
        spacing_m: float = 8.0,
        altitude_step_m: float = 0.0,
    ) -> "FormationLayout":
        """创建矩形网格队形。

        ``rows`` 和 ``columns`` 决定槽位数量；``spacing_m`` 是行列间距，单位为米；网格整体位于
        leader 后方，适合多机平面覆盖或展示队形；``altitude_step_m`` 是相邻槽位的高度差，单位为米。
        返回矩形网格布局对象。
        """
        if rows <= 0 or columns <= 0:
            raise ValueError("Formation grid rows and columns must be positive.")
        y_offsets = _centered_offsets(columns, spacing_m)
        slots: list[FormationSlot] = []
        for row in range(rows):
            for column, y_m in enumerate(y_offsets):
                index = row * columns + column
                slots.append(
                    FormationSlot(
                        f"grid-{row + 1}-{column + 1}",
                        x_m=-(row + 1) * spacing_m,
                        y_m=y_m,
                        z_m=index * altitude_step_m,
                    )
                )
        return cls(name="grid", slots=tuple(slots), min_separation_m=spacing_m)

    def policy_by_follower(
        self,
        followers: Iterable[Any],
        *,
        device_id_getter: Any,
    ) -> tuple[dict[Any, FollowPolicySpec], tuple[FormationAssignment, ...]]:
        """按 follower 顺序生成跟随策略和槽位分配。

        ``followers`` 是将被加入 ``FollowSession`` 的设备对象；``device_id_getter`` 返回设备业务 ID。
        当 follower 数量超过槽位数量时抛出 ``ValueError``，避免设备没有明确队形目标。
        """
        follower_tuple = tuple(followers)
        if len(follower_tuple) > len(self.slots):
            raise ValueError("Formation layout does not provide enough slots for followers.")
        policies: dict[Any, FollowPolicySpec] = {}
        assignments: list[FormationAssignment] = []
        for follower, slot in zip(follower_tuple, self.slots):
            policies[follower] = slot.to_follow_policy()
            assignments.append(
                FormationAssignment(
                    slot_id=slot.slot_id,
                    device_id=str(device_id_getter(follower)),
                    state=FormationSlotState.ASSIGNED,
                )
            )
        for slot in self.slots[len(follower_tuple):]:
            assignments.append(
                FormationAssignment(
                    slot_id=slot.slot_id,
                    device_id=None,
                    state=FormationSlotState.VACANT,
                )
            )
        return policies, tuple(assignments)


def _centered_offsets(count: int, spacing_m: float) -> tuple[float, ...]:
    if count <= 0:
        raise ValueError("Formation slot count must be positive.")
    center = (count - 1) / 2.0
    return tuple((index - center) * spacing_m for index in range(count))
