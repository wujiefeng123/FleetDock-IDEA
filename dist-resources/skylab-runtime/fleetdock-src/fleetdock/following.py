"""Product-level follow execution runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
import math
import threading
import time
from typing import Any, Callable, Deque, Mapping, Optional, Union

from fleetdock.devices import BaseDevice, DeviceState
from fleetdock.events import CoordinateFrame, EventCategory, EventType
from fleetdock.models import CapabilityName
from fleetdock.movement import GpsPoint, gps_offset_m
from fleetdock.follow_models import (
    BandFollowPolicy,
    CameraFollowPolicy,
    ChaseFollowPolicy,
    FollowMode,
    FollowPolicySpec,
    RelativeFollowPolicy,
    TrailFollowPolicy,
)
from fleetdock.fleet import FollowSession, device_public_id
from fleetdock.runtime import ManagedWorker, WorkerStatus
from fleetdock.safety import SafetyAction, SafetyDecision, SafetyPolicy, SafetySupervisor


class FollowSessionState(str, Enum):
    """跟随执行生命周期状态；用于 ``FollowExecutionStatus.state`` 诊断后台闭环是否可继续运行。"""

    CREATED = "created"  # 会话执行器已创建，但尚未启动后台控制循环。
    RUNNING = "running"  # 后台控制循环正在按周期读取遥测并发送 follower 命令。
    PAUSED = "paused"  # 控制循环暂挂，不发送新的 follower 运动命令。
    FAILED = "failed"  # 控制循环因安全检查、遥测或命令异常失败退出。
    STOPPED = "stopped"  # 控制循环已停止，并已按 stop_action 执行收尾动作。


class FollowLostAction(str, Enum):
    """跟随遥测丢失或执行失败后的处理动作。"""

    HOVER = "hover"  # 遥测丢失时请求相关设备悬停或停止在当前位置。
    RETURN_TO_LAUNCH = "return_to_launch"  # 遥测丢失时请求支持返航的设备返回 Home 点。
    FAIL_ONLY = "fail_only"  # 只标记执行失败，不额外发送悬停或返航命令。


class FollowStopAction(str, Enum):
    """停止跟随执行器时对 follower 发送的收尾动作。"""

    HOLD_VELOCITY_ZERO = "hold_velocity_zero"  # 停止时发送零速度移动命令，适合速度控制设备。
    HOVER = "hover"  # 停止时优先调用设备悬停能力。
    NONE = "none"  # 停止时不发送额外控制命令，由上层接管设备状态。


@dataclass(frozen=True)
class FollowExecutionOptions:
    """跟随执行器运行参数。

    `interval_s` 是控制周期；`max_command_failures` 用于连续命令失败保护；`max_follow_distance_m`
    和 `min_separation_m` 是旧版便捷安全边界，会合并进 ``safety_policy``；`stop_action`
    决定正常停止时是否让 follower 悬停。``safety_policy`` 可统一配置限高、间距、电量、
    failsafe 和遥测超时等安全前置规则。
    """

    interval_s: float = 0.2
    telemetry_timeout_s: float = 2.0
    max_command_failures: int = 5
    max_follow_distance_m: float = 80.0
    min_separation_m: float = 5.0
    default_max_speed_m_s: float = 6.0
    target_tolerance_m: float = 0.8
    on_leader_lost: FollowLostAction = FollowLostAction.HOVER
    on_follower_lost: FollowLostAction = FollowLostAction.HOVER
    stop_action: FollowStopAction = FollowStopAction.HOVER
    safety_policy: Optional[SafetyPolicy] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "on_leader_lost", _normalize_lost_action(self.on_leader_lost))
        object.__setattr__(self, "on_follower_lost", _normalize_lost_action(self.on_follower_lost))
        object.__setattr__(self, "stop_action", _normalize_stop_action(self.stop_action))
        object.__setattr__(
            self,
            "safety_policy",
            SafetyPolicy.from_follow_options(
                telemetry_timeout_s=self.telemetry_timeout_s,
                max_follow_distance_m=self.max_follow_distance_m,
                min_separation_m=self.min_separation_m,
                base_policy=self.safety_policy,
            ),
        )


@dataclass(frozen=True)
class FollowCommand:
    """一次跟随 tick 计算出的统一运动命令。

    ``x``、``y``、``z`` 是标准速度方向分量，``speed`` 单位为米/秒；``target_*_m`` 字段在适用时
    记录本次控制目标，便于状态查询或事件观察。
    """

    x: float
    y: float
    z: float
    speed: float
    yaw_deg: Optional[float]
    target_x_m: Optional[float] = None
    target_y_m: Optional[float] = None
    target_z_m: Optional[float] = None
    distance_to_leader_m: Optional[float] = None
    distance_to_target_m: Optional[float] = None
    reason: str = ""


@dataclass(frozen=True)
class FollowFollowerState:
    """单个 follower 的跟随执行状态快照。"""

    follower_id: str
    mode: FollowMode
    distance_to_leader_m: Optional[float] = None
    distance_to_target_m: Optional[float] = None
    last_command_at: Optional[float] = None
    command_failures: int = 0
    telemetry_ok: bool = False
    message: str = ""


@dataclass(frozen=True)
class FollowExecutionStatus:
    """跟随会话执行状态快照。

    时间字段使用 ``time.monotonic()`` 秒值，仅用于进程内耗时判断；``worker`` 暴露后台线程状态，
    ``follower_states`` 记录每个 follower 最近一次距离、遥测和命令失败信息。
    """

    session_id: str
    state: FollowSessionState
    leader_id: str
    follower_states: Mapping[str, FollowFollowerState] = field(default_factory=dict)
    message: str = ""
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    failed_at: Optional[float] = None
    worker: Optional[WorkerStatus] = None

    @property
    def followers(self) -> Mapping[str, FollowFollowerState]:
        """返回 follower 状态映射；键是业务设备 ID，值为最近一次跟随控制状态。"""
        return self.follower_states


class FollowController:
    """无设备 I/O 的跟随运动控制器。

    ``compute()`` 只根据本地平面坐标、跟随策略和 leader 历史轨迹计算标准方向与速度，
    不读取设备、不启动线程也不发送命令，便于独立测试和替换控制算法。坐标单位为米，
    返回速度单位为米/秒，偏航角单位为度。
    """

    def __init__(self, options: FollowExecutionOptions) -> None:
        self._options = options

    def compute(
        self,
        follower_xy: tuple[float, float],
        leader_xy: tuple[float, float],
        policy: FollowPolicySpec,
        leader_history: tuple[tuple[float, float, float], ...] = (),
    ) -> FollowCommand:
        """计算单次跟随命令。

        ``follower_xy`` 和 ``leader_xy`` 是同一平面坐标系下的 x/y 米坐标；``policy`` 是跟随策略；
        ``leader_history`` 元素依次为单调时钟秒值、x 米和 y 米。返回的 ``FollowCommand`` 使用
        标准方向向量、米/秒速度和可选偏航角，不执行任何设备 I/O。
        """
        distance_m = math.hypot(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
        yaw_deg = _yaw_to(follower_xy, leader_xy)
        if distance_m < self._options.min_separation_m and policy.mode is not FollowMode.RELATIVE:
            unit_x, unit_y, _ = _unit_vector(follower_xy[0] - leader_xy[0], follower_xy[1] - leader_xy[1])
            return FollowCommand(
                unit_x, unit_y, 0.0,
                self._speed_for(policy, self._options.min_separation_m - distance_m, 0.9),
                yaw_deg, distance_to_leader_m=distance_m, distance_to_target_m=distance_m,
                reason="separation guard",
            )
        if isinstance(policy, RelativeFollowPolicy):
            return self._toward(follower_xy, (leader_xy[0] + policy.x_m, leader_xy[1] + policy.y_m), None, policy, "relative")
        if isinstance(policy, ChaseFollowPolicy):
            error_m = distance_m - policy.distance_m
            if abs(error_m) <= self._options.target_tolerance_m:
                return FollowCommand(0.0, 0.0, 0.0, 0.0, yaw_deg, distance_to_leader_m=distance_m, reason="chase hold")
            unit_x, unit_y, _ = _unit_vector(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
            direction = 1.0 if error_m > 0.0 else -1.0
            return FollowCommand(unit_x * direction, unit_y * direction, 0.0, self._speed_for(policy, abs(error_m), 0.8), yaw_deg, distance_to_leader_m=distance_m, distance_to_target_m=abs(error_m), reason="chase")
        if isinstance(policy, TrailFollowPolicy):
            target = self._trail_target(leader_history, policy.delay_s)
            if target is None:
                unit_x, unit_y, _ = _unit_vector(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
                target = (leader_xy[0] - unit_x * policy.distance_m, leader_xy[1] - unit_y * policy.distance_m)
            return self._toward(follower_xy, target, yaw_deg, policy, "trail")
        if isinstance(policy, CameraFollowPolicy):
            forward = self._leader_forward(leader_history)
            if forward is None:
                forward = _unit_vector(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])[:2]
            target = (leader_xy[0] - forward[0] * policy.distance_m - forward[1] * policy.side_m, leader_xy[1] - forward[1] * policy.distance_m + forward[0] * policy.side_m)
            return self._toward(follower_xy, target, yaw_deg, policy, "camera")
        if isinstance(policy, BandFollowPolicy):
            if policy.min_distance_m <= distance_m <= policy.max_distance_m:
                return FollowCommand(0.0, 0.0, 0.0, 0.0, yaw_deg, distance_to_leader_m=distance_m, reason="band hold")
            unit_x, unit_y, _ = _unit_vector(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
            direction = 1.0 if distance_m > policy.max_distance_m else -1.0
            boundary_m = policy.max_distance_m if direction > 0.0 else policy.min_distance_m
            error_m = abs(distance_m - boundary_m)
            return FollowCommand(unit_x * direction, unit_y * direction, 0.0, self._speed_for(policy, error_m, 0.8), yaw_deg, distance_to_leader_m=distance_m, distance_to_target_m=error_m, reason="band")
        raise RuntimeError(f"Unsupported follow policy: {policy!r}")

    def _toward(self, follower_xy: tuple[float, float], target_xy: tuple[float, float], yaw_deg: Optional[float], policy: FollowPolicySpec, reason: str) -> FollowCommand:
        unit_x, unit_y, distance_m = _unit_vector(target_xy[0] - follower_xy[0], target_xy[1] - follower_xy[1])
        speed = 0.0 if distance_m <= self._options.target_tolerance_m else self._speed_for(policy, distance_m)
        return FollowCommand(unit_x if speed else 0.0, unit_y if speed else 0.0, 0.0, speed, yaw_deg, target_x_m=target_xy[0], target_y_m=target_xy[1], distance_to_target_m=distance_m, reason=f"{reason} hold" if speed == 0.0 else reason)

    def _speed_for(self, policy: FollowPolicySpec, distance_m: float, gain: float = 0.55) -> float:
        return min(policy.max_speed_m_s or self._options.default_max_speed_m_s, max(0.0, distance_m * gain))

    @staticmethod
    def _leader_forward(history: tuple[tuple[float, float, float], ...]) -> Optional[tuple[float, float]]:
        if len(history) < 2:
            return None
        newest = history[-1]
        for older in reversed(history[:-1]):
            unit_x, unit_y, distance_m = _unit_vector(newest[1] - older[1], newest[2] - older[2])
            if distance_m >= 0.5:
                return unit_x, unit_y
        return None

    @staticmethod
    def _trail_target(history: tuple[tuple[float, float, float], ...], delay_s: float) -> Optional[tuple[float, float]]:
        target_time = time.monotonic() - delay_s
        for sample_time, x_m, y_m in history:
            if sample_time >= target_time:
                return x_m, y_m
        return (history[0][1], history[0][2]) if history else None


class FollowExecutor:
    """后台跟随执行器。

    执行器把 `FollowSession` 中保存的关系和策略解释为持续的 `device.set_velocity(...)` 命令。它不创建
    新的关系，也不绕过设备对象；所有真实控制仍通过 FleetDock 标准设备 API 发出。
    """

    def __init__(
        self,
        session: FollowSession,
        options: Optional[FollowExecutionOptions] = None,
    ) -> None:
        self._session = session
        self._options = options or FollowExecutionOptions()
        self._safety = SafetySupervisor(self._options.safety_policy)
        self._controller = FollowController(self._options)
        self._state = FollowSessionState.CREATED
        self._message = "created"
        self._follower_states: dict[str, FollowFollowerState] = {}
        self._leader_history: Deque[tuple[float, float, float]] = deque(maxlen=300)
        self._worker: Optional[ManagedWorker] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._lock = threading.RLock()
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._failed_at: Optional[float] = None
        self._last_error: Optional[BaseException] = None
        self._global_origin: Optional[GpsPoint] = None
        self._shared_local_frame_id: Optional[str] = None
        self._telemetry_releases: dict[int, Callable[[], None]] = {}

    @property
    def options(self) -> FollowExecutionOptions:
        """返回执行器运行参数；对象包含控制周期、安全距离、遥测超时和停止策略。"""
        return self._options

    def start(self) -> "FollowExecutor":
        """启动后台跟随控制循环并返回当前执行器。

        启动前会校验 follower 非空且具备移动与停止能力；运行后按 ``options.interval_s`` 秒周期读取
        状态快照并发送标准 ``move`` 命令。已运行时重复调用直接返回当前对象。
        """
        self._refresh_telemetry_leases()
        try:
            self._validate_preflight()
        except BaseException:
            self._release_telemetry_leases()
            raise
        with self._lock:
            if self._state is FollowSessionState.RUNNING:
                return self
            if self._state is FollowSessionState.PAUSED:
                self.resume()
                return self
            if self._state is FollowSessionState.FAILED:
                raise RuntimeError(f"FollowSession {self._session.id} execution has failed: {self._message}")
            if self._worker is not None and self._worker.status().is_alive:
                return self

            self._stop_event.clear()
            self._pause_event.clear()
            self._state = FollowSessionState.RUNNING
            self._message = "running"
            self._started_at = time.monotonic()
            self._worker = ManagedWorker(
                f"fleetdock-follow-{self._session.id[:8]}",
                self._run,
                request_stop=self._stop_event.set,
                on_error=self._fail,
            ).start()
        self._emit("custom.follow.session.started", {"state": self._state.value})
        return self

    def pause(self) -> None:
        """暂停后台控制循环；暂停期间不发送新的 follower 运动命令，无返回值。"""
        with self._lock:
            if self._state is not FollowSessionState.RUNNING:
                return
            self._pause_event.set()
            self._state = FollowSessionState.PAUSED
            self._message = "paused"
        self._emit("custom.follow.session.paused", {"state": self._state.value})

    def resume(self) -> None:
        """恢复已暂停的后台控制循环；仅在当前状态为 PAUSED 时生效，无返回值。"""
        with self._lock:
            if self._state is not FollowSessionState.PAUSED:
                return
            self._pause_event.clear()
            self._state = FollowSessionState.RUNNING
            self._message = "running"
        self._emit("custom.follow.session.resumed", {"state": self._state.value})

    def stop(self, *, detach: bool = False, wait_s: float = 5.0) -> None:
        """停止后台控制循环并按停止策略收尾。

        ``detach`` 仅写入事件载荷，表示调用方是否准备接管设备；``wait_s`` 单位为秒，用于等待后台
        线程退出。方法无返回值，停止后会发布 ``custom.follow.session.stopped``。
        """
        worker: Optional[ManagedWorker]
        with self._lock:
            worker = self._worker
            if self._state is FollowSessionState.STOPPED:
                return
            self._stop_event.set()
            self._pause_event.clear()

        if worker is not None:
            worker.join(timeout_s=wait_s)

        self._apply_stop_action()
        self._release_telemetry_leases()
        with self._lock:
            self._state = FollowSessionState.STOPPED
            self._message = "stopped"
            self._stopped_at = time.monotonic()
        self._emit("custom.follow.session.stopped", {"state": self._state.value, "detach": detach})

    def status(self) -> FollowExecutionStatus:
        """返回线程安全的跟随执行状态快照，包含会话状态、follower 状态和后台线程诊断。"""
        with self._lock:
            return FollowExecutionStatus(
                session_id=self._session.id,
                state=self._state,
                leader_id=device_public_id(self._session.leader),
                follower_states=dict(self._follower_states),
                message=self._message,
                started_at=self._started_at,
                stopped_at=self._stopped_at,
                failed_at=self._failed_at,
                worker=self._worker.status() if self._worker is not None else None,
            )

    def mark_policy_changed(self, follower: BaseDevice, policy: FollowPolicySpec) -> None:
        """记录 follower 策略变更并发布自定义事件。

        ``follower`` 是策略变化的设备，``policy`` 是新的跟随策略；方法不立即发送运动命令，无返回值。
        """
        self._emit(
            "custom.follow.policy.changed",
            {
                "follower_id": device_public_id(follower),
                "mode": policy.mode.value,
            },
        )

    def mark_leader_changed(self, leader: BaseDevice) -> None:
        """记录 leader 变更，清空历史轨迹并发布自定义事件；后续 tick 会从新 leader 重新采样，无返回值。"""
        with self._lock:
            self._leader_history.clear()
        self._refresh_telemetry_leases()
        self._emit("custom.follow.leader.changed", {"leader_id": device_public_id(leader)})

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(self._options.interval_s)
                continue
            try:
                self._tick()
            except BaseException as exc:
                self._fail(exc)
                break
            time.sleep(self._options.interval_s)

    def _tick(self) -> None:
        if not self._session.is_active:
            self.stop(wait_s=0.0)
            return

        self._refresh_telemetry_leases()
        now = time.monotonic()
        leader_state = self._session.leader.state.snapshot()
        leader_xy = self._common_xy(leader_state, now_s=now)
        leader_decision = self._safety.evaluate_device(self._session.leader, now_s=now)
        self._publish_safety_decision(self._session.leader, leader_decision)
        if leader_xy is None or not leader_decision.allowed:
            message = (
                leader_decision.violations[0].message
                if leader_decision.violations
                else "leader local position is not available"
            )
            self._handle_leader_lost(message)
            return

        self._leader_history.append((now, leader_xy[0], leader_xy[1]))
        devices = (self._session.leader, *self._session.followers)
        common_positions = {
            device_public_id(device): self._common_xy(device.state.snapshot(), now_s=now)
            for device in devices
        }
        spacing_decision = self._safety.evaluate_fleet_spacing(
            devices,
            now_s=now,
            positions={key: value for key, value in common_positions.items() if value is not None},
        )
        if spacing_decision.violations:
            for follower in self._session.followers:
                self._publish_safety_decision(follower, spacing_decision)

        for follower in self._session.followers:
            follower_id = device_public_id(follower)
            follower_state = follower.state.snapshot()
            follower_xy = common_positions.get(follower_id)
            policy = self._session.policy_for(follower)
            follower_decision = self._safety.evaluate_device(follower, now_s=now)
            self._publish_safety_decision(follower, follower_decision)
            if follower_xy is None or not follower_decision.allowed:
                self._record_follower_state(
                    follower_id,
                    policy,
                    telemetry_ok=False,
                    message=(
                        follower_decision.violations[0].message
                        if follower_decision.violations
                        else "follower local position is not available"
                    ),
                )
                self._emit("custom.follow.follower.telemetry_lost", {"follower_id": follower_id})
                self._apply_safety_action(follower, follower_decision)
                if (
                    self._options.on_follower_lost is FollowLostAction.RETURN_TO_LAUNCH
                    and hasattr(follower, "return_to_launch")
                    and follower_decision.action is SafetyAction.NONE
                ):
                    follower.return_to_launch()
                continue
            if self._global_origin is None and not _frames_are_compatible(
                leader_state.local_position_frame,
                follower_state.local_position_frame,
            ):
                raise RuntimeError(
                    f"{follower_id} local position frame {follower_state.local_position_frame.value} "
                    f"does not match leader frame {leader_state.local_position_frame.value}."
                )

            distance_to_leader_m = math.hypot(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
            leader_distance_decision = self._safety.evaluate_leader_distance(
                follower,
                self._session.leader,
                follower_xy=follower_xy,
                leader_xy=leader_xy,
            )
            self._publish_safety_decision(follower, leader_distance_decision)
            if not leader_distance_decision.allowed:
                self._record_follower_state(
                    follower_id,
                    policy,
                    distance_to_leader_m=distance_to_leader_m,
                    telemetry_ok=True,
                    message=leader_distance_decision.violations[0].message,
                )
                self._apply_safety_action(follower, leader_distance_decision)
                continue
            if not spacing_decision.allowed:
                self._record_follower_state(
                    follower_id,
                    policy,
                    distance_to_leader_m=distance_to_leader_m,
                    telemetry_ok=True,
                    message=spacing_decision.violations[0].message,
                )
                self._apply_safety_action(follower, spacing_decision)
                continue

            command = self._compute_command(follower_xy, leader_xy, policy, distance_to_leader_m)
            command = self._with_vertical_command(command, follower_state, leader_state, policy)
            command_decision = self._safety.evaluate_command(follower, command, now_s=now)
            self._publish_safety_decision(follower, command_decision)
            if not command_decision.allowed:
                self._record_follower_state(
                    follower_id,
                    policy,
                    distance_to_leader_m=distance_to_leader_m,
                    telemetry_ok=True,
                    message=command_decision.violations[0].message,
                )
                self._apply_safety_action(follower, command_decision)
                continue
            response = follower.set_velocity(
                x=command.x,
                y=command.y,
                z=command.z,
                speed=command.speed,
                yaw_deg=command.yaw_deg,
            )
            previous = self._follower_states.get(follower_id)
            failures = previous.command_failures if previous is not None else 0
            if response.ok:
                failures = 0
                message = command.reason
            else:
                failures += 1
                message = response.message
                self._emit(
                    "custom.follow.follower.command_failed",
                    {"follower_id": follower_id, "failures": failures, "message": response.message},
                )
                if failures >= self._options.max_command_failures:
                    raise RuntimeError(f"{follower_id} follow command failed {failures} times: {response.message}")

            self._record_follower_state(
                follower_id,
                policy,
                distance_to_leader_m=command.distance_to_leader_m,
                distance_to_target_m=command.distance_to_target_m,
                last_command_at=now,
                command_failures=failures,
                telemetry_ok=True,
                message=message,
            )
            self._emit(
                "custom.follow.follower.target_updated",
                {
                    "follower_id": follower_id,
                    "mode": policy.mode.value,
                    "target_x_m": command.target_x_m,
                    "target_y_m": command.target_y_m,
                    "speed_m_s": command.speed,
                    "yaw_deg": command.yaw_deg,
                },
            )

    def _validate_preflight(self) -> None:
        if not self._session.followers:
            raise ValueError("FollowSession requires at least one follower before execution starts.")
        for follower in self._session.followers:
            if not follower.profile.has_capability(CapabilityName.MOVE):
                raise RuntimeError(f"{device_public_id(follower)} does not support motion control.")
            if not follower.profile.has_capability(CapabilityName.STOP):
                raise RuntimeError(f"{device_public_id(follower)} does not support safe stop.")

        devices = (self._session.leader, *self._session.followers)
        states = tuple(device.state.snapshot() for device in devices)
        if all(state.latitude_deg is not None and state.longitude_deg is not None for state in states):
            leader_state = states[0]
            leader_latitude_deg = leader_state.latitude_deg
            leader_longitude_deg = leader_state.longitude_deg
            if leader_latitude_deg is None or leader_longitude_deg is None:
                raise RuntimeError("leader global position disappeared during follow preflight.")
            self._global_origin = GpsPoint(
                float(leader_latitude_deg),
                float(leader_longitude_deg),
                float(leader_state.absolute_altitude_m or 0.0),
            )
            self._shared_local_frame_id = None
            return

        frame_ids = tuple(device.metadata.local_frame_id for device in devices)
        if frame_ids[0] is None or any(frame_id != frame_ids[0] for frame_id in frame_ids[1:]):
            raise RuntimeError(
                "Follow execution requires global positions for every member, or the same explicit "
                "DeviceMetadata.local_frame_id for a genuinely shared local map."
            )
        leader_frame = states[0].local_position_frame
        if any(not _frames_are_compatible(leader_frame, state.local_position_frame) for state in states[1:]):
            raise RuntimeError("Follow execution members use incompatible local coordinate frame conventions.")
        self._global_origin = None
        self._shared_local_frame_id = frame_ids[0]

    def _common_xy(self, state: DeviceState, *, now_s: float) -> Optional[tuple[float, float]]:
        """把设备位置转换为当前跟随会话的公共米制平面坐标。"""
        if self._global_origin is not None:
            if not _global_telemetry_is_fresh(state, now_s, self._options.telemetry_timeout_s):
                return None
            if state.latitude_deg is None or state.longitude_deg is None:
                return None
            return gps_offset_m(
                self._global_origin,
                GpsPoint(
                    float(state.latitude_deg),
                    float(state.longitude_deg),
                    float(state.absolute_altitude_m or self._global_origin.altitude_m),
                ),
            )
        return _local_xy(state)

    def _compute_command(
        self,
        follower_xy: tuple[float, float],
        leader_xy: tuple[float, float],
        policy: FollowPolicySpec,
        distance_to_leader_m: float,
    ) -> FollowCommand:
        del distance_to_leader_m
        return self._controller.compute(follower_xy, leader_xy, policy, tuple(self._leader_history))

    def _with_vertical_command(
        self,
        command: FollowCommand,
        follower_state: DeviceState,
        leader_state: DeviceState,
        policy: FollowPolicySpec,
    ) -> FollowCommand:
        offset_m = _vertical_offset_m(policy)
        if offset_m is None:
            return command
        follower_altitude_m = _altitude_for_vertical_control(follower_state)
        leader_altitude_m = _altitude_for_vertical_control(leader_state)
        if follower_altitude_m is None or leader_altitude_m is None:
            return command
        target_altitude_m = leader_altitude_m + offset_m
        error_m = target_altitude_m - follower_altitude_m
        if abs(error_m) <= self._options.target_tolerance_m:
            return replace(command, z=0.0, target_z_m=target_altitude_m)
        vertical_direction = 1.0 if error_m > 0.0 else -1.0
        speed = command.speed
        if speed <= 0.0:
            speed = min(self._options.default_max_speed_m_s, max(0.0, abs(error_m) * 0.45))
        return replace(command, z=vertical_direction, speed=speed, target_z_m=target_altitude_m)

    def _record_follower_state(
        self,
        follower_id: str,
        policy: FollowPolicySpec,
        *,
        distance_to_leader_m: Optional[float] = None,
        distance_to_target_m: Optional[float] = None,
        last_command_at: Optional[float] = None,
        command_failures: int = 0,
        telemetry_ok: bool = False,
        message: str = "",
    ) -> None:
        with self._lock:
            self._follower_states[follower_id] = FollowFollowerState(
                follower_id=follower_id,
                mode=policy.mode,
                distance_to_leader_m=distance_to_leader_m,
                distance_to_target_m=distance_to_target_m,
                last_command_at=last_command_at,
                command_failures=command_failures,
                telemetry_ok=telemetry_ok,
                message=message,
            )

    def _publish_safety_decision(self, device: BaseDevice, decision: SafetyDecision) -> None:
        for violation in decision.violations:
            try:
                device.events.publish(
                    EventType.SAFETY_VIOLATION_RAISED,
                    EventCategory.ALERT,
                    violation.to_event_payload(),
                )
            except RuntimeError:
                pass

    def _apply_safety_action(self, device: BaseDevice, decision: SafetyDecision) -> None:
        if decision.action is SafetyAction.NONE:
            return
        ok: Optional[bool] = None
        message = ""
        try:
            if decision.action is SafetyAction.BLOCK_COMMAND:
                ok = None
                message = "command blocked by safety policy"
            elif decision.action is SafetyAction.HOVER:
                self._hover_or_zero(device)
                ok = True
                message = "hover requested by safety policy"
            elif decision.action is SafetyAction.STOP:
                response = device.stop()
                ok = response.ok
                message = response.message
            elif decision.action is SafetyAction.RETURN_TO_LAUNCH:
                if hasattr(device, "return_to_launch"):
                    response = device.return_to_launch()
                    ok = response.ok
                    message = response.message
                else:
                    response = device.stop()
                    ok = response.ok
                    message = response.message
            elif decision.action is SafetyAction.EMERGENCY_STOP:
                response = device.emergency_stop()
                ok = response.ok
                message = response.message
        except Exception as exc:
            ok = False
            message = str(exc)
        try:
            device.events.publish(
                EventType.SAFETY_ACTION_TRIGGERED,
                EventCategory.ALERT,
                {
                    "action": decision.action.value,
                    "device_id": device_public_id(device),
                    "violation_codes": [violation.code for violation in decision.violations],
                    "ok": ok,
                    "message": message,
                    "metadata": {"session_id": self._session.id},
                },
            )
        except RuntimeError:
            pass

    def _handle_leader_lost(self, message: str) -> None:
        with self._lock:
            self._message = message
        self._emit("custom.follow.leader.telemetry_lost", {"message": message})
        if self._options.on_leader_lost is FollowLostAction.RETURN_TO_LAUNCH:
            for follower in self._session.followers:
                if hasattr(follower, "return_to_launch"):
                    follower.return_to_launch()
        elif self._options.on_leader_lost is FollowLostAction.HOVER:
            for follower in self._session.followers:
                self._hover_or_zero(follower)

    def _apply_stop_action(self) -> None:
        if self._options.stop_action is FollowStopAction.NONE:
            return
        for follower in self._session.followers:
            if self._options.stop_action is FollowStopAction.HOVER:
                self._hover_or_zero(follower)
            elif self._options.stop_action is FollowStopAction.HOLD_VELOCITY_ZERO:
                follower.set_velocity(
                    x=0.0,
                    y=0.0,
                    z=0.0,
                    speed=0.0,
                    yaw_deg=follower.state.snapshot().yaw_deg,
                )

    @staticmethod
    def _hover_or_zero(follower: BaseDevice) -> None:
        if hasattr(follower, "hover"):
            response = follower.hover()
            if response.ok:
                return
        follower.set_velocity(
            x=0.0,
            y=0.0,
            z=0.0,
            speed=0.0,
            yaw_deg=follower.state.snapshot().yaw_deg,
        )

    def _fail(self, exc: BaseException) -> None:
        self._last_error = exc
        self._release_telemetry_leases()
        with self._lock:
            self._state = FollowSessionState.FAILED
            self._message = str(exc)
            self._failed_at = time.monotonic()
        self._emit("custom.follow.session.failed", {"message": str(exc)})

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        data = {
            "session_id": self._session.id,
            "leader_id": device_public_id(self._session.leader),
            **dict(payload),
        }
        try:
            self._session.leader.events.publish_custom(event_type, data)
        except Exception:
            return

    def _refresh_telemetry_leases(self) -> None:
        """让跟随会话涉及的设备在会话存续期间持续提供移动相关遥测。"""
        devices = (self._session.leader, *self._session.followers)
        current_ids = {id(device) for device in devices}
        with self._lock:
            stale_ids = set(self._telemetry_releases) - current_ids
            stale_releases = [self._telemetry_releases.pop(device_id) for device_id in stale_ids]
            missing = [device for device in devices if id(device) not in self._telemetry_releases]
        for release in stale_releases:
            release()
        acquired: list[tuple[int, Callable[[], None]]] = []
        try:
            for device in missing:
                acquired.append((id(device), device._acquire_capability_telemetry(CapabilityName.MOVE)))
        except BaseException:
            for _, release in acquired:
                release()
            raise
        with self._lock:
            for device_id, release in acquired:
                existing = self._telemetry_releases.setdefault(device_id, release)
                if existing is not release:
                    release()

    def _release_telemetry_leases(self) -> None:
        """释放跟随会话持有的全部遥测需求。"""
        with self._lock:
            releases = tuple(self._telemetry_releases.values())
            self._telemetry_releases.clear()
        for release in releases:
            release()


def _local_xy(state: DeviceState) -> Optional[tuple[float, float]]:
    if state.local_x_m is None or state.local_y_m is None:
        return None
    return float(state.local_x_m), float(state.local_y_m)


def _telemetry_is_fresh(state: DeviceState, now_s: float, timeout_s: float) -> bool:
    updated_at_s = state.local_position_updated_at_s
    return updated_at_s is None or now_s - updated_at_s <= timeout_s


def _global_telemetry_is_fresh(state: DeviceState, now_s: float, timeout_s: float) -> bool:
    updated_at_s = state.global_position_updated_at_s
    return updated_at_s is None or now_s - updated_at_s <= timeout_s


def _vertical_offset_m(policy: FollowPolicySpec) -> Optional[float]:
    if isinstance(policy, RelativeFollowPolicy):
        return policy.z_m
    if isinstance(policy, (ChaseFollowPolicy, CameraFollowPolicy)):
        return policy.vertical_offset_m
    return None


def _altitude_for_vertical_control(state: DeviceState) -> Optional[float]:
    if state.relative_altitude_m is not None:
        return float(state.relative_altitude_m)
    if state.local_z_m is None:
        return None
    if state.local_position_frame is CoordinateFrame.LOCAL_NED:
        return -float(state.local_z_m)
    return float(state.local_z_m)


def _frames_are_compatible(leader: CoordinateFrame, follower: CoordinateFrame) -> bool:
    if CoordinateFrame.UNKNOWN in (leader, follower):
        return True
    return leader is follower


def _yaw_to(source_xy: tuple[float, float], target_xy: tuple[float, float]) -> float:
    return math.degrees(math.atan2(target_xy[1] - source_xy[1], target_xy[0] - source_xy[0]))


def _unit_vector(delta_x_m: float, delta_y_m: float) -> tuple[float, float, float]:
    distance_m = math.hypot(delta_x_m, delta_y_m)
    if distance_m <= 1e-6:
        return 0.0, 0.0, 0.0
    return delta_x_m / distance_m, delta_y_m / distance_m, distance_m


def _normalize_lost_action(action: Union[FollowLostAction, str]) -> FollowLostAction:
    if isinstance(action, FollowLostAction):
        return action
    return FollowLostAction(str(action))


def _normalize_stop_action(action: Union[FollowStopAction, str]) -> FollowStopAction:
    if isinstance(action, FollowStopAction):
        return action
    return FollowStopAction(str(action))
