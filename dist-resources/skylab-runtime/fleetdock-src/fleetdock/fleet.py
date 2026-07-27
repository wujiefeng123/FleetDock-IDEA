"""Fleet device collection and aggregated event subscriptions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
import logging
from threading import RLock
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence, Type, TypeAlias, Union, cast
from uuid import uuid4

from fleetdock._exception_mapping import operation_failure
from fleetdock.avoidance import FleetAvoidanceManager
from fleetdock.devices import BaseDevice, Drone, RobotDog
from fleetdock.events import (
    DeviceEvent,
    DeviceEventHandlerError,
    DeviceEventFilter,
    DeviceEventHandler,
    DeviceEventName,
    EventCategory,
    EventHandlerFailure,
    EventType,
    HandlerErrorPolicy,
)
from fleetdock.models import CapabilityName, DeviceCategory, ProductKey, StandardResponse, normalize_product


logger = logging.getLogger(__name__)


def device_public_id(device: BaseDevice) -> str:
    """返回设备对业务侧最稳定的标识；优先使用 metadata.device_id，否则使用 SDK 实例 ID。"""
    return device.metadata.device_id or device.instance_id


def _normalize_follow_conflict(conflict: "FollowConflict | str") -> "FollowConflict":
    if isinstance(conflict, FollowConflict):
        return conflict
    return FollowConflict(str(conflict))


from fleetdock.fleet_events import (
    FleetEvent,
    FleetEventDeviceSelector,
    FleetEventHandler,
    FleetEventPredicate,
    FleetEvents,
)
from fleetdock.follow_models import (
    BandFollowPolicy,
    CameraFollowPolicy,
    ChaseFollowPolicy,
    FollowConflict,
    FollowMode,
    FollowPolicy,
    FollowPolicySpec,
    RelativeFollowPolicy,
    TrailFollowPolicy,
)
from fleetdock.formation_models import FormationAssignment, FormationLayout, FormationSlotState
from fleetdock.safety import SafetyPolicy


@dataclass(frozen=True)
class FleetOperationResult:
    """Fleet 批量操作结果。

    `operation` 表示本次批量操作名称；`device_results` 按设备业务 ID 保存单设备响应；`ok` 只有在全部成员都成功时为
    True；`succeeded_devices` 和 `failed_devices` 分别给出成功、失败设备 ID，便于上层决定是否继续执行后续 Plan。
    """

    operation: str
    ok: bool
    device_results: dict[str, StandardResponse]
    succeeded_devices: tuple[str, ...]
    failed_devices: tuple[str, ...]

    @property
    def message(self) -> str:
        """返回面向日志或控制台展示的批量操作摘要。"""
        if self.ok:
            return f"Fleet {self.operation} completed for {len(self.succeeded_devices)} devices."
        return f"Fleet {self.operation} failed for {len(self.failed_devices)} devices."


class FollowSession:
    """Fleet 内的一次动态跟随会话。

    FollowSession 由 `fleet.follow(...)` 创建，代表一个 leader 带领一组 followers 的关系集合。
    会话对象只管理 FleetDock SDK 层的跟随关系、冲突约束和相对策略，不在构造时立即启动真实飞控闭环；
    后续控制循环可以读取 `leader`、`followers` 和 `policy_for(...)` 接入遥测与运动命令。
    """

    def __init__(
        self,
        fleet: "Fleet",
        leader: BaseDevice,
        *,
        session_id: Optional[str] = None,
        layout: Optional[FormationLayout] = None,
        assignments: Sequence[FormationAssignment] = (),
        safety_policy: Optional[SafetyPolicy] = None,
    ) -> None:
        self._fleet = fleet
        self._leader = leader
        self._id = session_id or str(uuid4())
        self._followers: dict[int, BaseDevice] = {}
        self._policies: dict[int, FollowPolicySpec] = {}
        self._slot_ids: dict[int, str] = {}
        self._layout = layout
        self._assignments: dict[str, FormationAssignment] = {
            assignment.slot_id: assignment for assignment in assignments
        }
        self._safety_policy = safety_policy
        self._active = True
        self._executor: Any = None

    @property
    def id(self) -> str:
        """返回本次跟随会话的稳定标识，可用于日志、界面状态或后续解除跟随。"""
        return self._id

    @property
    def leader(self) -> BaseDevice:
        """返回当前会话的 leader 设备对象；leader 不会自动随 followers 增删而变化。"""
        return self._leader

    @property
    def followers(self) -> tuple[BaseDevice, ...]:
        """返回当前仍跟随 leader 的设备元组；顺序为加入会话的顺序。"""
        return tuple(self._followers.values())

    @property
    def follower_ids(self) -> tuple[str, ...]:
        """返回当前 followers 的业务设备 ID；未配置业务 ID 时使用 SDK 实例 ID。"""
        return tuple(device_public_id(device) for device in self._followers.values())

    @property
    def layout(self) -> Optional[FormationLayout]:
        """返回当前队形布局；未通过 ``layout`` 创建跟随会话时返回 ``None``。"""
        return self._layout

    @property
    def assignments(self) -> tuple[FormationAssignment, ...]:
        """返回当前队形槽位分配快照；普通跟随会话返回空元组。"""
        return tuple(self._assignments.values())

    @property
    def safety_policy(self) -> Optional[SafetyPolicy]:
        """返回跟随会话默认安全策略；未配置时由执行器使用默认策略。"""
        return self._safety_policy

    @property
    def is_active(self) -> bool:
        """返回会话是否仍处于活动状态；调用 `stop()` 后为 False。"""
        return self._active

    def policy_for(self, follower: BaseDevice) -> FollowPolicySpec:
        """读取指定 follower 的跟随策略。

        `follower` 必须已经加入本会话；返回值为创建或 `add(...)` 时保存的
        跟随策略对象，其中距离单位为米、速度单位为米/秒。
        """
        self._ensure_active()
        follower_key = id(follower)
        if follower_key not in self._policies:
            raise ValueError(f"Device is not a follower in FollowSession {self._id}.")
        return self._policies[follower_key]

    def add(
        self,
        *followers: BaseDevice,
        policy: Optional[FollowPolicySpec] = None,
        conflict: FollowConflict | str = FollowConflict.FAIL,
    ) -> "FollowSession":
        """向当前会话动态加入 follower。

        `followers` 是要跟随本会话 leader 的设备；每个设备必须属于创建该会话的 Fleet。
        `policy` 未传入时使用 `FollowPolicy.relative()` 创建零偏移策略；`conflict` 默认为失败，
        只有显式传入 `FollowConflict.REPLACE` 才会把已有跟随关系迁移到当前 leader。
        返回当前会话对象，便于链式配置。
        """
        self._ensure_active()
        follow_conflict = _normalize_follow_conflict(conflict)
        for follower in followers:
            follow_policy = policy or self._next_layout_policy_for(follower) or FollowPolicy.relative()
            self._fleet._attach_follower(
                session=self,
                follower=follower,
                policy=follow_policy,
                conflict=follow_conflict,
            )
            if self._executor is not None:
                self._executor.mark_policy_changed(follower, follow_policy)
        return self

    def remove(self, *followers: BaseDevice) -> "FollowSession":
        """从当前会话动态移除 follower。

        `followers` 必须是当前会话内的跟随设备；移除后该设备不再占用跟随关系，可以加入其它会话。
        返回当前会话对象，便于继续增删其它设备。
        """
        self._ensure_active()
        for follower in followers:
            self._fleet._detach_follower(self, follower)
        return self

    def set_policy(self, follower: BaseDevice, policy: FollowPolicySpec) -> "FollowSession":
        """更新指定 follower 的跟随策略。

        ``policy`` 是替换后的强类型策略；该方法不会重建会话，下一控制周期生效并发布事件。
        返回当前会话对象。
        """
        self._ensure_active()
        follower_key = id(follower)
        if follower_key not in self._followers:
            raise ValueError(f"Device is not a follower in FollowSession {self._id}.")
        self._policies[follower_key] = policy
        if self._executor is not None:
            self._executor.mark_policy_changed(follower, policy)
        return self

    def transition_to(
        self,
        layout: FormationLayout,
        *,
        duration_s: float = 8.0,
    ) -> "FollowSession":
        """把当前跟随会话切换到新的队形布局。

        ``layout`` 描述新的 follower 槽位；``duration_s`` 单位为秒，当前版本记录在事件载荷中，
        实际平滑由已有跟随控制器按速度限制追向新目标。返回当前会话对象。
        """
        self._ensure_active()
        followers = self.followers
        policies, assignments = layout.policy_by_follower(followers, device_id_getter=device_public_id)
        self._layout = layout
        self._assignments = {assignment.slot_id: assignment for assignment in assignments}
        self._slot_ids = {
            id(follower): assignment.slot_id
            for follower, assignment in zip(followers, assignments)
            if assignment.state is FormationSlotState.ASSIGNED
        }
        for follower, policy in policies.items():
            self.set_policy(follower, policy)
        self._emit_formation_event(
            "custom.follow.formation.transition_completed",
            {
                "layout": layout.name,
                "duration_s": duration_s,
                "assignments": [assignment.__dict__ for assignment in self.assignments],
            },
        )
        return self

    def assign(self, follower: BaseDevice, slot_id: str) -> "FollowSession":
        """把已加入会话的 follower 重新分配到指定队形槽位。

        ``slot_id`` 必须存在于当前 ``layout``；方法会立即更新 follower 的相对位置策略，并返回
        当前会话对象。普通非 layout 跟随会话调用该方法会抛出 ``ValueError``。
        """
        self._ensure_active()
        if self._layout is None:
            raise ValueError("FollowSession has no formation layout.")
        if id(follower) not in self._followers:
            raise ValueError(f"Device is not a follower in FollowSession {self._id}.")
        slot = self._slot_by_id(slot_id)
        current_assignment = self._assignments.get(slot.slot_id)
        if (
            current_assignment is not None
            and current_assignment.state is FormationSlotState.ASSIGNED
            and current_assignment.device_id != device_public_id(follower)
        ):
            raise ValueError(f"Formation slot '{slot_id}' is already assigned.")
        previous_slot_id = self._slot_ids.get(id(follower))
        if previous_slot_id is not None:
            self._assignments[previous_slot_id] = FormationAssignment(
                slot_id=previous_slot_id,
                device_id=None,
                state=FormationSlotState.VACANT,
            )
        self._slot_ids[id(follower)] = slot.slot_id
        self._assignments[slot.slot_id] = FormationAssignment(
            slot_id=slot.slot_id,
            device_id=device_public_id(follower),
            state=FormationSlotState.ASSIGNED,
        )
        self.set_policy(follower, slot.to_follow_policy())
        self._emit_formation_event(
            "custom.follow.formation.member_assigned",
            {"follower_id": device_public_id(follower), "slot_id": slot.slot_id},
        )
        return self

    def update_leader(
        self,
        leader: BaseDevice,
        *,
        conflict: FollowConflict | str = FollowConflict.FAIL,
    ) -> "FollowSession":
        """切换本次会话的 leader。

        新 leader 必须属于同一 Fleet。若新 leader 原本是本会话 follower，会先从 follower 列表移除；
        若它正在跟随其它会话，则按 `conflict` 决定失败或替换。返回当前会话对象。
        """
        self._ensure_active()
        self._fleet._ensure_member(leader)
        if leader is self._leader:
            return self

        follow_conflict = _normalize_follow_conflict(conflict)
        previous_session = self._fleet._follow_by_follower.get(id(leader))
        if previous_session is not None:
            if previous_session is self:
                self._fleet._detach_follower(self, leader)
            elif follow_conflict is FollowConflict.FAIL:
                raise ValueError(f"Device {device_public_id(leader)} is already following another leader.")
            else:
                previous_session.remove(leader)

        if any(active.leader is leader for active in self._fleet.follow_sessions if active is not self):
            raise ValueError("New leader is already leading another FollowSession.")
        self._leader = leader
        if self._executor is not None:
            self._executor.mark_leader_changed(leader)
        return self

    def start(self, options: Optional[Any] = None, **option_overrides: Any) -> "FollowSession":
        """启动产品级跟随执行器。

        `options` 可传入 `FollowExecutionOptions`；也可以直接传 `interval_s=0.2`、
        `max_follow_distance_m=80.0` 等 ``option_overrides`` 覆盖默认值。执行器会在后台周期性
        读取遥测并通过 `device.set_velocity(...)` 发出控制命令；返回当前会话对象。
        """
        self._ensure_active()
        from fleetdock.following import FollowExecutionOptions, FollowExecutor

        if options is None:
            if self._safety_policy is not None and "safety_policy" not in option_overrides:
                option_overrides = {"safety_policy": self._safety_policy, **option_overrides}
            options = FollowExecutionOptions(**option_overrides)
        elif option_overrides:
            values = {**options.__dict__, **option_overrides}
            options = FollowExecutionOptions(**values)

        if self._executor is None:
            self._executor = FollowExecutor(self, options)
        self._executor.start()
        return self

    def pause(self) -> None:
        """暂停后台跟随执行；会话关系仍保留，该方法无返回值。"""
        if self._executor is not None:
            self._executor.pause()

    def resume(self) -> None:
        """恢复已暂停的后台跟随执行；未启动时自动启动，该方法无返回值。"""
        self._ensure_active()
        if self._executor is None:
            self.start()
        else:
            self._executor.resume()

    def stop_execution(self, *, wait_s: float = 5.0) -> None:
        """仅停止后台执行器而不解除关系；``wait_s`` 是最长等待秒数，该方法无返回值。"""
        if self._executor is not None:
            self._executor.stop(wait_s=wait_s)

    def status(self) -> Any:
        """返回当前跟随执行状态快照。"""
        from fleetdock.following import FollowExecutionStatus, FollowSessionState

        if self._executor is not None:
            return self._executor.status()
        return FollowExecutionStatus(
            session_id=self._id,
            state=FollowSessionState.CREATED if self._active else FollowSessionState.STOPPED,
            leader_id=device_public_id(self._leader),
            message="created" if self._active else "stopped",
        )

    def stop(self) -> None:
        """停止本次跟随会话并解除全部 followers。

        该方法会立即清空 Fleet 内部的 follower 索引，使这些设备可以重新跟随其它 leader；
        如果后台执行器已经启动，会先按执行器 stop_action 收尾，再解除关系。
        方法无返回值，重复调用已停止的会话不会产生额外效果。
        """
        if not self._active:
            return
        if self._executor is not None:
            self._executor.stop(detach=True)
        for follower in tuple(self._followers.values()):
            self._fleet._detach_follower(self, follower)
        self._active = False
        self._fleet._forget_follow_session(self)
        logger.debug(
            "Fleet 跟随会话已停止: session_id=%s leader_id=%s",
            self._id,
            device_public_id(self._leader),
        )

    def _record_follower(self, follower: BaseDevice, policy: FollowPolicySpec) -> None:
        self._followers[id(follower)] = follower
        self._policies[id(follower)] = policy
        slot_id = getattr(policy, "metadata", {}).get("formation_slot_id")
        if isinstance(slot_id, str):
            self._slot_ids[id(follower)] = slot_id
            self._assignments[slot_id] = FormationAssignment(
                slot_id=slot_id,
                device_id=device_public_id(follower),
                state=FormationSlotState.ASSIGNED,
            )

    def _drop_follower(self, follower: BaseDevice) -> None:
        slot_id = self._slot_ids.pop(id(follower), None)
        if slot_id is not None:
            self._assignments[slot_id] = FormationAssignment(
                slot_id=slot_id,
                device_id=None,
                state=FormationSlotState.VACANT,
            )
        self._followers.pop(id(follower), None)
        self._policies.pop(id(follower), None)

    def _ensure_active(self) -> None:
        if not self._active:
            raise ValueError(f"FollowSession {self._id} has already been stopped.")

    def _next_layout_policy_for(self, follower: BaseDevice) -> Optional[FollowPolicySpec]:
        if self._layout is None:
            return None
        for slot in self._layout.slots:
            assignment = self._assignments.get(slot.slot_id)
            if assignment is None or assignment.state is FormationSlotState.VACANT:
                return slot.to_follow_policy()
        raise ValueError("Formation layout does not provide enough vacant slots for new follower.")

    def _slot_by_id(self, slot_id: str) -> Any:
        if self._layout is None:
            raise ValueError("FollowSession has no formation layout.")
        for slot in self._layout.slots:
            if slot.slot_id == slot_id:
                return slot
        raise ValueError(f"Formation slot '{slot_id}' does not exist.")

    def _emit_formation_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.leader.events.publish_custom(
            event_type,
            {
                "session_id": self._id,
                "leader_id": device_public_id(self._leader),
                **dict(payload),
            },
        )


class Fleet:
    """FleetDock 设备编队。

    Fleet 保存一组标准设备对象引用，可混合无人机、机器狗和后续设备类型。筛选方法返回新的
    Fleet 视图，但不会复制或重新连接设备；设备状态、事件流和底层适配器仍与原对象共享。
    ``avoidance`` 提供 Fleet 级共享障碍物 provider、共享世界模型、容量保护和队内动态障碍物能力。
    """

    def __init__(
        self,
        devices: Iterable[BaseDevice],
        *,
        name: Optional[str] = None,
        _follow_sessions: Optional[dict[str, FollowSession]] = None,
        _follow_by_follower: Optional[dict[int, FollowSession]] = None,
    ) -> None:
        self._devices = tuple(devices)
        self.name = name
        self.events = FleetEvents(self)
        self.avoidance = FleetAvoidanceManager(self)
        self._follow_sessions = _follow_sessions if _follow_sessions is not None else {}
        self._follow_by_follower = _follow_by_follower if _follow_by_follower is not None else {}

    def __iter__(self) -> Iterator[BaseDevice]:
        return iter(self._devices)

    def __len__(self) -> int:
        return len(self._devices)

    def __bool__(self) -> bool:
        return bool(self._devices)

    def __getitem__(self, index: int) -> BaseDevice:
        return self._devices[index]

    @property
    def devices(self) -> tuple[BaseDevice, ...]:
        """返回当前 Fleet 视图中的设备元组；调用方不应修改设备对象所有权。"""
        return self._devices

    @property
    def device_ids(self) -> tuple[str, ...]:
        """返回当前 Fleet 中每台设备的业务标识，顺序与 `devices` 一致。"""
        return tuple(device_public_id(device) for device in self._devices)

    def where(self, predicate: Callable[[BaseDevice], bool]) -> "Fleet":
        """按自定义条件筛选设备并返回新的 Fleet。

        `predicate` 接收设备对象并返回布尔值，适合按连接状态、电量、业务标签等动态条件筛选。
        """
        return self._spawn(device for device in self._devices if predicate(device))

    def drones(self) -> "DroneFleet":
        """返回仅包含无人机成员的新 `DroneFleet` 视图。"""
        return DroneFleet(
            tuple(device for device in self._devices if isinstance(device, Drone)),
            name=self.name,
            _follow_sessions=self._follow_sessions,
            _follow_by_follower=self._follow_by_follower,
        )

    def robot_dogs(self) -> "RobotDogFleet":
        """返回仅包含机器狗成员的新 `RobotDogFleet` 视图。"""
        return RobotDogFleet(
            tuple(device for device in self._devices if isinstance(device, RobotDog)),
            name=self.name,
            _follow_sessions=self._follow_sessions,
            _follow_by_follower=self._follow_by_follower,
        )

    def by_product(self, *products: ProductKey) -> "Fleet":
        """按产品键筛选设备。

        `products` 可传入 `ProductType.PX4_GENERIC` 等枚举或自定义产品字符串；返回新的 Fleet
        视图，成员仍引用原设备对象。
        """
        normalized = {normalize_product(product) for product in products}
        return self.where(lambda device: device.product_key in normalized)

    def by_capability(self, *capabilities: CapabilityName) -> "Fleet":
        """筛选同时声明支持所有给定能力的设备。

        `capabilities` 使用 `CapabilityName` 枚举；返回新的 Fleet 视图，便于继续传给 `Plan(...)`。
        """
        return self.where(lambda device: all(device.has_capability(capability) for capability in capabilities))

    def by_id(self, *device_ids: str) -> "Fleet":
        """按业务设备 ID 或 SDK 实例 ID 筛选设备。

        `device_ids` 优先匹配 `DeviceMetadata.device_id`，也兼容 SDK 内部 `instance_id`；返回新的
        Fleet 视图。
        """
        selected = set(device_ids)
        return self.where(lambda device: device_public_id(device) in selected or device.instance_id in selected)

    def by_name(self, *names: str) -> "Fleet":
        """按 `DeviceMetadata.name` 筛选设备。

        `names` 是调用方配置的设备显示名称；返回新的 Fleet 视图。
        """
        selected = set(names)
        return self.where(lambda device: device.metadata.name in selected)

    def by_category(self, *categories: DeviceCategory | str) -> "Fleet":
        """按产品声明的设备类别筛选。

        `categories` 可传入 `DeviceCategory.DRONE` 等枚举或类别字符串；返回新的 Fleet 视图。
        """
        selected = {category.value if isinstance(category, Enum) else str(category) for category in categories}
        return self.where(lambda device: device.profile.category.value in selected)

    def with_tag(self, *tags: str) -> "Fleet":
        """筛选包含所有给定业务标签的设备。

        `tags` 来自 `DeviceMetadata.tags`；设备必须包含全部给定标签才会进入返回的 Fleet。
        """
        required = set(tags)
        return self.where(lambda device: required.issubset(set(device.metadata.tags)))

    def with_metadata(self, **metadata: Any) -> "Fleet":
        """按 `DeviceMetadata.extra` 中的键值筛选设备。

        `metadata` 关键字参数会与 `extra` 中的同名值逐项相等比较；返回匹配设备组成的新 Fleet。
        """
        return self.where(lambda device: all(device.metadata.extra.get(key) == value for key, value in metadata.items()))

    def connected(self) -> "Fleet":
        """返回当前状态快照显示控制链路已连接成员组成的新 Fleet 视图。"""
        return self.where(lambda device: device.state.snapshot().connected is True)

    def follow(
        self,
        *,
        leader: BaseDevice,
        followers: Optional[BaseDevice | Iterable[BaseDevice]] = None,
        policy: Optional[FollowPolicySpec] = None,
        follower_policies: Optional[Mapping[BaseDevice, FollowPolicySpec]] = None,
        layout: Optional[FormationLayout] = None,
        safety_policy: Optional[SafetyPolicy] = None,
        conflict: FollowConflict | str = FollowConflict.FAIL,
    ) -> FollowSession:
        """创建一次 Fleet 内的动态跟随会话。

        `leader` 是被跟随设备，必须属于当前 Fleet；`followers` 是要加入跟随的设备集合，
        未传入时默认使用 Fleet 内除 leader 外的全部设备，传入空列表时创建空会话，后续可用
        `session.add(...)` 动态加入。`policy` 是没有单独配置时的默认策略；`follower_policies`
        可为不同 follower 指定独立策略。`layout` 用于声明队形槽位，并会按 follower 顺序转换为
        相对位置跟随策略；`layout` 与 `policy`、`follower_policies` 不能同时传入，避免同一 follower
        出现两个目标来源。`safety_policy` 会作为 `session.start()` 的默认安全前置策略。
        `conflict` 决定 follower 已跟随其它 leader 时是失败还是替换。返回 `FollowSession`，调用方可继续
        `add(...)`、`remove(...)`、`transition_to(...)` 或 `stop()` 动态调整关系。
        """
        self._ensure_member(leader)
        if layout is not None and (policy is not None or follower_policies):
            raise ValueError("layout cannot be combined with policy or follower_policies.")
        policy_by_follower = dict(follower_policies or {})
        for follower in policy_by_follower:
            self._ensure_member(follower)
        if followers is None:
            selected_followers = tuple(policy_by_follower) if policy_by_follower else tuple(
                device for device in self._devices if device is not leader
            )
        else:
            selected_followers = self._normalize_followers(followers)
            if policy_by_follower:
                selected_followers = tuple(dict.fromkeys((*selected_followers, *policy_by_follower)))
        follow_conflict = _normalize_follow_conflict(conflict)
        assignments: tuple[FormationAssignment, ...] = ()
        if layout is not None:
            policy_by_follower, assignments = layout.policy_by_follower(
                selected_followers,
                device_id_getter=device_public_id,
            )
        session = FollowSession(
            self,
            leader,
            layout=layout,
            assignments=assignments,
            safety_policy=safety_policy,
        )
        logger.debug(
            "Fleet 跟随会话创建: session_id=%s leader_id=%s follower_count=%d conflict=%s",
            session.id,
            device_public_id(leader),
            len(selected_followers),
            follow_conflict.value,
        )
        self._follow_sessions[session.id] = session
        try:
            default_policy = policy or FollowPolicy.relative()
            for follower in selected_followers:
                session.add(
                    follower,
                    policy=policy_by_follower.get(follower, default_policy),
                    conflict=follow_conflict,
                )
        except Exception:
            session.stop()
            logger.debug(
                "Fleet 跟随会话创建失败: session_id=%s leader_id=%s",
                session.id,
                device_public_id(leader),
                exc_info=True,
            )
            raise
        return session

    def unfollow(self, session: FollowSession | str) -> None:
        """停止指定 Fleet 跟随会话。

        `session` 可以传入 `fleet.follow(...)` 返回的 `FollowSession` 对象，也可以传入其 `id`
        字符串；调用后会解除该会话内全部 followers，并释放这些设备的跟随关系占用。
        """
        follow_session = self._resolve_follow_session(session)
        follow_session.stop()

    @property
    def follow_sessions(self) -> tuple[FollowSession, ...]:
        """返回当前 Fleet 仍处于活动状态的跟随会话，用于界面展示或调试关系。"""
        return tuple(session for session in self._follow_sessions.values() if session.is_active)

    def connect(self) -> FleetOperationResult:
        """并发连接 Fleet 中的全部设备。

        该方法在调用时立即执行连接，不属于 `Plan` 的任务步骤。每台设备调用标准 `device.connect()` 接口，
        同一 Fleet 内并发执行；返回 `FleetOperationResult`，包含每台设备的响应和成功、失败设备 ID。
        """
        return self._run_operation("connect", lambda device: device.connect())

    def close(self) -> None:
        """依次关闭 Fleet 中的所有设备。

        该方法无返回值；通常在示例或应用退出时放在 finally 中调用，确保底层适配器和事件流释放。
        """
        self.avoidance.close()
        self.events.close()
        for device in self._devices:
            device.close()

    def _run_operation(
        self,
        operation: str,
        callback: Callable[[BaseDevice], StandardResponse],
    ) -> FleetOperationResult:
        devices = tuple(self._devices)
        results: dict[str, StandardResponse] = {}
        logger.debug("Fleet 批量操作开始: operation=%s device_count=%d", operation, len(devices))
        if devices:
            with ThreadPoolExecutor(
                max_workers=len(devices),
                thread_name_prefix=f"fleetdock-fleet-{operation}",
            ) as executor:
                future_map = {executor.submit(callback, device): device for device in devices}
                for future in as_completed(future_map):
                    device = future_map[future]
                    device_id = device_public_id(device)
                    try:
                        results[device_id] = future.result()
                    except Exception as exc:
                        logger.debug(
                            "Fleet 批量操作异常: operation=%s device_id=%s error=%s",
                            operation,
                            device_id,
                            exc,
                            exc_info=True,
                        )
                        results[device_id] = operation_failure(
                            device_id=device.instance_id,
                            product=device.product_key,
                            exc=exc,
                        )
        succeeded = tuple(device_public_id(device) for device in devices if results[device_public_id(device)].ok)
        failed = tuple(device_public_id(device) for device in devices if not results[device_public_id(device)].ok)
        result = FleetOperationResult(
            operation=operation,
            ok=not failed,
            device_results=results,
            succeeded_devices=succeeded,
            failed_devices=failed,
        )
        logger.debug(
            "Fleet 批量操作结束: operation=%s ok=%s succeeded=%d failed=%d",
            operation,
            result.ok,
            len(succeeded),
            len(failed),
        )
        return result

    def _spawn(self, devices: Iterable[BaseDevice]) -> "Fleet":
        return type(self)(
            tuple(devices),
            name=self.name,
            _follow_sessions=self._follow_sessions,
            _follow_by_follower=self._follow_by_follower,
        )

    def _ensure_member(self, device: BaseDevice) -> None:
        if all(item is not device for item in self._devices):
            raise ValueError("Device must belong to this Fleet.")

    def _normalize_followers(
        self,
        followers: BaseDevice | Iterable[BaseDevice],
    ) -> tuple[BaseDevice, ...]:
        if isinstance(followers, BaseDevice):
            return (followers,)
        return tuple(followers)

    def _resolve_follow_session(self, session: FollowSession | str) -> FollowSession:
        if isinstance(session, FollowSession):
            if session.id not in self._follow_sessions:
                raise ValueError(f"FollowSession {session.id} does not belong to this Fleet or is stopped.")
            return session
        try:
            return self._follow_sessions[session]
        except KeyError as exc:
            raise ValueError(f"FollowSession {session} does not belong to this Fleet or is stopped.") from exc

    def _attach_follower(
        self,
        *,
        session: FollowSession,
        follower: BaseDevice,
        policy: FollowPolicySpec,
        conflict: FollowConflict,
    ) -> None:
        self._ensure_member(session.leader)
        self._ensure_member(follower)
        if follower is session.leader:
            raise ValueError("Leader cannot follow itself.")
        if id(session.leader) in self._follow_by_follower:
            raise ValueError("A device that is already following another leader cannot lead a FollowSession.")
        if any(active.leader is follower for active in self.follow_sessions):
            raise ValueError("A device that is leading a FollowSession cannot be added as a follower.")

        previous_session = self._follow_by_follower.get(id(follower))
        if previous_session is not None and previous_session is not session:
            if conflict is FollowConflict.FAIL:
                raise ValueError(f"Device {device_public_id(follower)} is already following another leader.")
            previous_session.remove(follower)

        self._follow_by_follower[id(follower)] = session
        session._record_follower(follower, policy)
        logger.debug(
            "Fleet 跟随设备加入: session_id=%s leader_id=%s follower_id=%s policy=%s",
            session.id,
            device_public_id(session.leader),
            device_public_id(follower),
            policy,
        )

    def _detach_follower(self, session: FollowSession, follower: BaseDevice) -> None:
        if id(follower) not in session._followers:
            raise ValueError(f"Device {device_public_id(follower)} is not in FollowSession {session.id}.")
        self._follow_by_follower.pop(id(follower), None)
        session._drop_follower(follower)
        logger.debug(
            "Fleet 跟随设备移除: session_id=%s leader_id=%s follower_id=%s",
            session.id,
            device_public_id(session.leader),
            device_public_id(follower),
        )

    def _forget_follow_session(self, session: FollowSession) -> None:
        self._follow_sessions.pop(session.id, None)


class DroneFleet(Fleet):
    """仅包含无人机成员的 Fleet 视图，提供无人机专属批量操作。"""

    def drones(self) -> "DroneFleet":
        """返回当前 `DroneFleet` 自身，便于链式调用保持无人机专属语义。"""
        return self

    def robot_dogs(self) -> "RobotDogFleet":
        """返回空的 `RobotDogFleet` 视图，表示当前无人机视图中不存在机器狗成员。"""
        return RobotDogFleet(
            (),
            name=self.name,
            _follow_sessions=self._follow_sessions,
            _follow_by_follower=self._follow_by_follower,
        )

    def unlock(self, force: bool = False) -> FleetOperationResult:
        """并发解锁当前 `DroneFleet` 内全部无人机。

        `force=False` 时执行标准解锁；`force=True` 时显式请求底层适配器执行强制解锁。该方法只存在于
        `DroneFleet`，用于避免误用到其它设备类型；返回聚合每台设备响应的批量结果。
        """
        return self._run_operation("unlock", lambda device: cast(Drone, device).unlock(force=force))

    def lock(self) -> FleetOperationResult:
        """并发锁定当前 `DroneFleet` 内全部无人机并返回批量结果。"""
        return self._run_operation("lock", lambda device: cast(Drone, device).lock())

    def takeoff(self, altitude: Optional[float] = 5.0) -> FleetOperationResult:
        """并发起飞并返回批量结果。

        ``altitude`` 是相对 Home 的目标高度，单位为米；显式传 ``None`` 时要求各适配器保留设备已有
        起飞高度且不主动覆盖。成员不支持该语义时，其单设备响应会失败。
        """
        return self._run_operation("takeoff", lambda device: cast(Drone, device).takeoff(altitude=altitude))

    def land(self) -> FleetOperationResult:
        """并发让当前 `DroneFleet` 内全部无人机降落并返回批量结果。"""
        return self._run_operation("land", lambda device: cast(Drone, device).land())

    def return_to_launch(self) -> FleetOperationResult:
        """并发让当前 `DroneFleet` 内全部无人机返航并返回批量结果。"""
        return self._run_operation("return_to_launch", lambda device: cast(Drone, device).return_to_launch())

    def hover(self) -> FleetOperationResult:
        """并发让当前 `DroneFleet` 内全部无人机悬停并返回批量结果。"""
        return self._run_operation("hover", lambda device: cast(Drone, device).hover())


class RobotDogFleet(Fleet):
    """仅包含机器狗成员的 Fleet 视图，提供足式机器人专属批量操作。"""

    def drones(self) -> "DroneFleet":
        """返回空的 `DroneFleet` 视图，表示当前机器狗视图中不存在无人机成员。"""
        return DroneFleet(
            (),
            name=self.name,
            _follow_sessions=self._follow_sessions,
            _follow_by_follower=self._follow_by_follower,
        )

    def robot_dogs(self) -> "RobotDogFleet":
        """返回当前 `RobotDogFleet` 自身，便于链式调用保持机器狗专属语义。"""
        return self

    def stand(self) -> FleetOperationResult:
        """并发让当前 `RobotDogFleet` 内全部机器狗站立并返回批量结果。"""
        return self._run_operation("stand", lambda device: cast(RobotDog, device).stand())

    def sit(self) -> FleetOperationResult:
        """并发让当前 `RobotDogFleet` 内全部机器狗坐下并返回批量结果。"""
        return self._run_operation("sit", lambda device: cast(RobotDog, device).sit())
