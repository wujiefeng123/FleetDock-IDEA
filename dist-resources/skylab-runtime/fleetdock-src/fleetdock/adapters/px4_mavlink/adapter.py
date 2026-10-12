"""PX4 MAVSDK 标准设备适配器。"""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager, nullcontext
from threading import Event, Lock
from typing import Any, Callable, Dict, Mapping, Optional, cast

from fleetdock.adapters.base import DeviceAdapter
from fleetdock.adapters.control import ControlAuthority, ControlConflictError
from fleetdock.adapters.px4_mavlink.client import MavsdkClient
from fleetdock.adapters.px4_mavlink.sensors import _create_px4_integrated_sensor_backend
from fleetdock.events import (
    CoordinateFrame,
    EventCategory,
    EventType,
    OriginalEvent,
    RouteCompletionReason,
    serialize_event_data,
)
from fleetdock.event_models import STANDARD_EVENT_CLASSES
from fleetdock.models import CapabilityName, ErrorCode, InitMode, StandardCommand, StandardResponse
from fleetdock.movement import MoveTargetKind
from fleetdock.readiness import ReadinessIssue, ReadinessRequirement
from fleetdock.sensors import SensorSourceKind
from fleetdock.takeoff import (
    TakeoffExecutionState,
    TakeoffOptions,
    TakeoffProgressCallback,
    TakeoffStatus,
)

logger = logging.getLogger(__name__)

class Px4MavlinkAdapter(DeviceAdapter):
    """把 PX4 MAVSDK 能力转换为 FleetDock 标准命令和事件的设备适配器。

    该类由产品注册表和适配器工厂创建；调用方通常通过 ``Drone`` 使用，不应依赖内部
    MAVSDK 对象。适配器负责同步命令响应、后台遥测转换和关闭资源。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client: MavsdkClient = self._build_client()
        self.armed = False
        self.flying = False
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        self.latest_telemetry: Dict[str, Any] = {}
        self._telemetry_state: Dict[str, Any] = {}
        self._state_lock = Lock()
        self._control_authority = ControlAuthority()
        self._takeoff_cancel_event: Optional[Event] = None
        self._telemetry_lease_lock = Lock()
        self._telemetry_leases: Dict[str, Dict[str, Any]] = {}
        self._sensor_backend = (
            _create_px4_integrated_sensor_backend(self.client)
            if callable(getattr(self.client, "available_sensor_streams", None))
            and callable(getattr(self.client, "subscribe_sensor", None))
            else None
        )
        self.device_events.set_demand_handler(self._acquire_event_telemetry)

    def _build_client(self) -> MavsdkClient:
        if self.connection.mode == InitMode.MANAGED_OBJECT:
            return self.connection.managed_object

        client_factory = self.connection.get_param("mavsdk_client_factory")
        if client_factory is not None:
            return client_factory(self.connection)

        timeout_s = float(self.connection.get_param("timeout_s", 10.0))
        server_port = self.connection.get_param("mavsdk_server_port")
        return MavsdkClient(
            str(self.connection.get_param("endpoint")),
            timeout_s=timeout_s,
            mavsdk_server_port=int(server_port) if server_port is not None else None,
            camera_stream_uri=self.connection.get_param("camera_stream_uri"),
            camera_stream_source=self.connection.get_param("camera_stream_source", SensorSourceKind.UNKNOWN),
            sender_system_id=int(self.connection.get_param("sender_system_id", 245)),
            sender_component_id=int(self.connection.get_param("sender_component_id", 190)),
            target_system_id=int(self.connection.get_param("target_system_id", 1)),
            target_component_id=int(self.connection.get_param("target_component_id", 1)),
            link_profile=str(self.connection.get_param("link_profile", "standard")),
        )

    def _command_scope(self, command: StandardCommand) -> Any:
        @contextmanager
        def scope():
            release = self._acquire_command_telemetry(command.command)
            use_timeout = getattr(self.client, "_use_command_timeout", None)
            timeout_scope = use_timeout(command.timeout_s) if callable(use_timeout) else nullcontext()
            try:
                with timeout_scope:
                    yield
            finally:
                release()

        return scope()

    def _acquire_command_telemetry(self, command: Any) -> Callable[[], None]:
        streams_by_command = {
            "takeoff": {"position", "local_position", "home", "attitude", "battery", "health", "in_air"},
            "land": {"position", "local_position", "attitude", "in_air", "landed_state"},
            "return_to_launch": {"position", "home", "health", "in_air"},
            "hover": {"position", "local_position", "attitude", "health"},
            "move": {"local_position", "attitude", "velocity_ned", "health"},
            "stop": {"local_position", "attitude", "velocity_ned", "health"},
            "get_status": {"position", "local_position", "home", "velocity_ned", "attitude"},
        }
        command_name = command.value if hasattr(command, "value") else str(command)
        streams = streams_by_command.get(command_name, set())
        return self._acquire_client_telemetry(streams, owner=f"command.{command_name}")

    def _acquire_event_telemetry(self, event_filter: Any) -> Callable[[], None]:
        event_types: set[EventType] = set()
        candidates = event_filter if isinstance(event_filter, tuple) else (event_filter,)
        for candidate in candidates:
            if isinstance(candidate, EventType):
                event_types.add(candidate)
                continue
            if isinstance(candidate, str):
                try:
                    event_types.add(EventType(candidate))
                except ValueError:
                    continue
                continue
            if isinstance(candidate, type):
                event_types.update(
                    event_type
                    for event_type, event_class in STANDARD_EVENT_CLASSES.items()
                    if event_class is candidate
                )
        streams_by_event = {
            EventType.GLOBAL_POSITION_UPDATED: "position",
            EventType.LOCAL_POSITION_UPDATED: "local_position",
            EventType.HOME_POSITION_UPDATED: "home",
            EventType.VELOCITY_UPDATED: "velocity_ned",
            EventType.ATTITUDE_UPDATED: "attitude",
            EventType.BATTERY_UPDATED: "battery",
            EventType.HEALTH_UPDATED: "health",
            EventType.ARMED_CHANGED: "armed",
            EventType.IN_AIR_CHANGED: "in_air",
            EventType.LANDED_STATE_CHANGED: "landed_state",
            EventType.FLIGHT_MODE_CHANGED: "flight_mode",
        }
        streams = {streams_by_event[event_type] for event_type in event_types if event_type in streams_by_event}
        return self._acquire_client_telemetry(streams, owner="event.subscription")

    def _acquire_client_telemetry(self, streams: set[str], *, owner: str) -> Callable[[], None]:
        if not streams:
            return lambda: None
        lease_id = f"lease-{id(streams):x}-{time.monotonic_ns()}"
        lease: Dict[str, Any] = {"streams": frozenset(streams), "owner": owner, "release": lambda: None}
        acquire = getattr(self.client, "acquire_telemetry", None)
        if callable(acquire):
            lease["release"] = acquire(lease["streams"], owner=owner)
        with self._telemetry_lease_lock:
            self._telemetry_leases[lease_id] = lease
        closed = False

        def release() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            with self._telemetry_lease_lock:
                active = self._telemetry_leases.pop(lease_id, None)
            if active is not None:
                active["release"]()

        return release

    def _rebind_telemetry_leases(self) -> None:
        acquire = getattr(self.client, "acquire_telemetry", None)
        with self._telemetry_lease_lock:
            leases = tuple(self._telemetry_leases.values())
        for lease in leases:
            lease["release"]()
            lease["release"] = (
                acquire(lease["streams"], owner=lease["owner"])
                if callable(acquire)
                else lambda: None
            )

    def additional_readiness_issues(self, capability: Any, state: Any) -> tuple[ReadinessIssue, ...]:
        """返回 PX4 针对目标能力追加的就绪问题。

        ``capability`` 是 FleetDock 标准能力，``state`` 是标准状态快照。起飞和通用路线允许
        “健康的本地位置”与“健康的全球位置和 Home”两条链路择一满足；原生 Mission 强制全球链路，
        Offboard/手动控制强制本地位置和姿态。返回空元组表示可执行，否则返回供
        ``wait_until_ready`` 展示的中文问题。
        """
        local_ready = bool(
            state.health_local_position_ok is True
            and state.local_x_m is not None
            and state.local_y_m is not None
            and state.local_z_m is not None
        )
        global_ready = bool(
            state.health_global_position_ok is True
            and state.health_home_position_ok is True
            and state.latitude_deg is not None
            and state.longitude_deg is not None
            and state.relative_altitude_m is not None
            and (
                state.home_absolute_altitude_m is not None
                or (state.home_latitude_deg is not None and state.home_longitude_deg is not None)
            )
        )
        if capability in {CapabilityName.OFFBOARD, CapabilityName.MANUAL_CONTROL}:
            if local_ready and state.yaw_deg is not None:
                return ()
            return (
                ReadinessIssue(
                    ReadinessRequirement.LOCAL_POSITION,
                    "PX4 Offboard 控制需要健康的本地三维位置和姿态遥测。",
                ),
            )
        if capability is CapabilityName.MISSION:
            if global_ready:
                return ()
            return (
                ReadinessIssue(
                    ReadinessRequirement.GLOBAL_POSITION,
                    "PX4 原生 Mission 需要健康的全球位置、相对高度和 Home 参考。",
                ),
            )
        if capability not in {CapabilityName.TAKEOFF, CapabilityName.ROUTE}:
            return ()
        if local_ready or global_ready:
            logger.debug(
                "PX4 自适应定位链路已就绪: device_id=%s capability=%s local=%s global=%s",
                self.device_id,
                capability.value,
                local_ready,
                global_ready,
            )
            return ()
        return (
            ReadinessIssue(
                ReadinessRequirement.LOCAL_POSITION,
                "PX4 起飞或路线需要健康的本地位置，或健康的全球位置、相对高度和 Home 参考。",
            ),
        )

    def _handle_connect(self, command: StandardCommand) -> StandardResponse:
        if self.connected:
            return StandardResponse.success(
                command,
                "PX4 MAVSDK already connected.",
                {"endpoint": self.connection.get_param("endpoint"), "mode": self.connection.mode.value},
            )
        self._connect_client_with_retry()
        self.connected = True
        self.publish_standard_event(
            EventType.CONNECTION_CHANGED,
            EventCategory.LIFECYCLE,
            {"previous": "disconnected", "current": "connected"},
            correlation_id=command.command_id,
        )
        start_telemetry = getattr(self.client, "start_telemetry", None)
        if callable(start_telemetry):
            start_telemetry(self._handle_telemetry)
        return StandardResponse.success(
            command,
            "PX4 MAVSDK connected.",
            {"endpoint": self.connection.get_param("endpoint"), "mode": self.connection.mode.value},
        )

    def _connect_client_with_retry(self) -> None:
        attempts = int(self.connection.get_param("connect_attempts", 2))
        retry_delay_s = float(self.connection.get_param("connect_retry_delay_s", 5.0))
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                self.client.connect()
                return
            except Exception as exc:
                last_error = exc
                logger.debug("PX4 MAVSDK 连接尝试失败: attempt=%s/%s error=%s", attempt, attempts, exc)
                close = getattr(self.client, "close", None)
                if callable(close) and self._owns_client:
                    close()
                if attempt >= attempts or self.connection.mode == InitMode.MANAGED_OBJECT:
                    break
                time.sleep(retry_delay_s)
                self.client = self._build_client()
                self._rebind_telemetry_leases()
                if self._sensor_backend is not None:
                    self._sensor_backend.replace_client(self.client)
        if last_error is not None:
            raise last_error

    def _handle_unlock(self, command: StandardCommand) -> StandardResponse:
        force = bool(command.params.get("force", False))
        self.client.arm(force=force)
        previous = self.armed
        self.armed = True
        self._publish_command_state_change(
            "armed",
            previous,
            self.armed,
            EventType.ARMED_CHANGED,
            command,
        )
        return StandardResponse.success(command, "PX4 armed.", {"armed": self.armed, "force": force})

    def _handle_lock(self, command: StandardCommand) -> StandardResponse:
        active_control = self._control_authority.status()
        if self.flying or (
            active_control is not None
            and active_control.owner in {"takeoff", "route"}
        ):
            logger.debug(
                "PX4 拒绝空中或活动飞行任务期间解锁: device_id=%s flying=%s control_owner=%s",
                self.device_id,
                self.flying,
                active_control.owner if active_control is not None else None,
            )
            return StandardResponse.failure(
                command,
                ErrorCode.DEVICE_STATE_ERROR,
                "PX4 cannot disarm while airborne or while a flight task owns control.",
            )
        self.client.disarm()
        previous = self.armed
        self.armed = False
        self._publish_command_state_change(
            "armed",
            previous,
            self.armed,
            EventType.ARMED_CHANGED,
            command,
        )
        return StandardResponse.success(command, "PX4 disarmed.", {"armed": self.armed})

    def _handle_takeoff(self, command: StandardCommand) -> StandardResponse:
        altitude_param = command.params.get("altitude", 5.0)
        altitude = None if altitude_param is None else float(altitude_param)
        if altitude is not None and altitude <= 0:
            raise ValueError("altitude must be greater than 0.")
        if not self.armed:
            return StandardResponse.failure(command, ErrorCode.DEVICE_STATE_ERROR, "PX4 must be armed before takeoff.")

        logger.debug(
            "PX4 发送起飞命令: device_id=%s altitude_m=%s use_configured_altitude=%s",
            self.device_id,
            altitude,
            altitude is None,
        )
        self.client.takeoff(altitude)
        self.flying = True
        return StandardResponse.success(
            command,
            "PX4 takeoff command sent.",
            {"altitude": altitude, "uses_configured_altitude": altitude is None},
        )

    def execute_takeoff(
        self,
        options: TakeoffOptions,
        *,
        progress_callback: Optional[TakeoffProgressCallback] = None,
        cancellation_check: Optional[Callable[[], None]] = None,
    ) -> TakeoffStatus:
        """自动选择 PX4 控制链路并确认物理起飞完成。

        ``options`` 使用 FleetDock 通用安全参数；``progress_callback`` 接收强类型状态；
        ``cancellation_check`` 用于任务取消。方法内部自动解锁，不要求业务代码判断 PX4 或 MAVSDK
        策略；成功时返回 ``COMPLETED``，失败时抛出带稳定错误码前缀的异常。
        """
        if not self.connected:
            raise RuntimeError("NOT_CONNECTED: device is not connected.")
        execute = getattr(self.client, "execute_takeoff", None)
        if not callable(execute):
            raise RuntimeError("TAKEOFF_STRATEGY_UNAVAILABLE: managed PX4 client lacks confirmed takeoff support.")
        cancelled = Event()
        self._takeoff_cancel_event = cancelled

        def check_cancelled() -> None:
            if cancelled.is_set():
                raise RuntimeError("TAKEOFF_CANCELLED: takeoff was preempted by a safety action.")
            if cancellation_check is not None:
                cancellation_check()

        last_state: Optional[TakeoffExecutionState] = None

        def publish(status: TakeoffStatus) -> None:
            nonlocal last_state
            if last_state is None:
                event_type = EventType.TAKEOFF_STARTED
            elif status.state is TakeoffExecutionState.COMPLETED:
                event_type = EventType.TAKEOFF_COMPLETED
            elif status.state is TakeoffExecutionState.ABORTED:
                event_type = EventType.TAKEOFF_ABORTED
            elif status.method is not None and last_state is TakeoffExecutionState.PRECHECKING:
                event_type = EventType.TAKEOFF_STRATEGY_SELECTED
            else:
                event_type = EventType.TAKEOFF_PROGRESS_UPDATED
            self.publish_standard_event(event_type, EventCategory.STATE, status)
            last_state = status.state
            if progress_callback is not None:
                progress_callback(status)

        logger.debug(
            "PX4 开始自动适配起飞: device_id=%s target_altitude_m=%.2f",
            self.device_id,
            options.target_altitude_m,
        )

        release_telemetry = self._acquire_client_telemetry(
            {"position", "local_position", "home", "attitude", "battery", "health", "in_air"},
            owner="task.takeoff",
        )
        try:
            with self._control_authority.acquire(
                "takeoff",
                50,
                cancel=cancelled.set,
            ):
                result = execute(
                    options,
                    progress_callback=publish,
                    cancellation_check=check_cancelled,
                )
                self.armed = True
                self.flying = True
                return result
        except ControlConflictError as exc:
            raise RuntimeError(f"TAKEOFF_CONTROL_CONFLICT: {exc}") from exc
        finally:
            release_telemetry()
            self._takeoff_cancel_event = None

    def acquire_capability_telemetry(self, capability: CapabilityName) -> Callable[[], None]:
        """为一次标准能力检查或会话申请必要遥测并返回幂等释放函数。

        ``capability`` 使用 FleetDock 标准能力；PX4 适配器自动选择本地或全球位置、姿态、速度、
        Home、健康和电池等流，并复用已有订阅。调用方须在等待、任务或会话结束时释放，业务 Demo
        无需直接调用本适配器入口。
        """
        streams_by_capability = {
            CapabilityName.TAKEOFF: {"position", "local_position", "home", "attitude", "battery", "health"},
            CapabilityName.ROUTE: {"position", "local_position", "home", "velocity_ned", "attitude", "health"},
            CapabilityName.MISSION: {"position", "home", "velocity_ned", "health"},
            CapabilityName.MOVE: {"position", "local_position", "velocity_ned", "attitude", "health"},
            CapabilityName.OFFBOARD: {"local_position", "velocity_ned", "attitude", "health"},
            CapabilityName.MANUAL_CONTROL: {"local_position", "velocity_ned", "attitude", "health", "in_air"},
            CapabilityName.STATUS: {"position", "local_position", "home", "velocity_ned", "attitude"},
        }
        return self._acquire_client_telemetry(
            streams_by_capability.get(capability, set()),
            owner=f"capability.{capability.value}",
        )

    def supports_confirmed_takeoff(self) -> bool:
        """返回当前 PX4 client 是否提供自动适配且可确认物理完成的起飞入口。"""
        return callable(getattr(self.client, "execute_takeoff", None))

    def _handle_land(self, command: StandardCommand) -> StandardResponse:
        with self._control_authority.acquire("land", 80, preempt=True):
            self.client.land()
        return StandardResponse.success(
            command,
            "PX4 land command sent.",
            {"flying": self.flying, "landing_requested": True},
        )

    def _handle_return_to_launch(self, command: StandardCommand) -> StandardResponse:
        with self._control_authority.acquire("return_to_launch", 80, preempt=True):
            self.client.return_to_launch()
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(command, "PX4 return-to-launch command sent.")

    def _handle_hover(self, command: StandardCommand) -> StandardResponse:
        with self._control_authority.acquire("hover", 70, preempt=True):
            self.client.hold()
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(command, "PX4 hold command sent.")

    def _handle_move(self, command: StandardCommand) -> StandardResponse:
        active_control = self._control_authority.status()
        if active_control is not None and active_control.owner != "move":
            return StandardResponse.failure(
                command,
                ErrorCode.DEVICE_STATE_ERROR,
                f"PX4 control is owned by '{active_control.owner}'.",
            )
        if command.params.get("control_mode") == "body_velocity":
            velocity = {
                "forward_m_s": float(command.params.get("forward_m_s", 0.0)),
                "right_m_s": float(command.params.get("right_m_s", 0.0)),
                "up_m_s": float(command.params.get("up_m_s", 0.0)),
                "yaw_rate_deg_s": float(command.params.get("yaw_rate_deg_s", 0.0)),
            }
            self.client.set_body_velocity(**velocity)
            self.last_velocity = dict(velocity)
            return StandardResponse.success(
                command,
                "PX4 body velocity command sent.",
                {"body_velocity": dict(velocity)},
            )
        if command.params.get("control_mode") != "velocity":
            return self._handle_move_target(command)
        velocity = {
            "x": float(command.params.get("x", 0.0)),
            "y": float(command.params.get("y", 0.0)),
            "z": float(command.params.get("z", 0.0)),
            "speed": float(command.params.get("speed", 1.0)),
        }
        if command.params.get("yaw_deg") is not None:
            velocity["yaw_deg"] = float(command.params["yaw_deg"])
        self.client.set_velocity(**velocity)
        self.last_velocity = velocity
        return StandardResponse.success(command, "PX4 velocity command sent.", {"velocity": dict(velocity)})

    def _handle_move_target(self, command: StandardCommand) -> StandardResponse:
        target_kind = MoveTargetKind(str(command.params["target_kind"]))
        target = dict(command.params["target"])
        if target_kind is MoveTargetKind.GPS_POINT:
            global_yaw_deg = float(target["yaw_deg"]) if target.get("yaw_deg") is not None else 0.0
            self.client.goto_location(
                float(target["latitude_deg"]),
                float(target["longitude_deg"]),
                float(target["altitude_m"]),
                global_yaw_deg,
            )
            return StandardResponse.success(
                command,
                "PX4 global move target sent.",
                {"target_kind": target_kind.value, "target": target},
            )

        set_position = getattr(self.client, "set_position", None)
        if not callable(set_position):
            return StandardResponse.failure(
                command,
                ErrorCode.UNSUPPORTED_COMMAND,
                "PX4 client does not provide local position target control.",
            )

        yaw_deg: float | None
        if target_kind is MoveTargetKind.RELATIVE_POINT_3D:
            x_m = float(target["x_m"])
            y_m = float(target["y_m"])
            z_m = float(target["z_m"])
            yaw_deg = float(target["yaw_deg"]) if target.get("yaw_deg") is not None else None
        elif target_kind is MoveTargetKind.RELATIVE_MOVE_3D:
            current = dict(command.params.get("current_position") or {})
            if current.get("x_m") is None or current.get("y_m") is None or current.get("z_m") is None:
                return StandardResponse.failure(
                    command,
                    ErrorCode.DEVICE_STATE_ERROR,
                    "PX4 relative move requires current local position telemetry.",
                )
            current_yaw_deg = float(current.get("yaw_deg") or 0.0)
            yaw_rad = math.radians(current_yaw_deg)
            forward_m = float(target["forward_m"])
            right_m = float(target["right_m"])
            x_m = float(current["x_m"]) + forward_m * math.cos(yaw_rad) - right_m * math.sin(yaw_rad)
            y_m = float(current["y_m"]) + forward_m * math.sin(yaw_rad) + right_m * math.cos(yaw_rad)
            z_m = -float(current["z_m"]) + float(target["up_m"])
            yaw_deg = current_yaw_deg + float(target["yaw_deg"])
        else:
            return StandardResponse.failure(
                command,
                ErrorCode.UNSUPPORTED_COMMAND,
                f"PX4 does not support move target '{target_kind.value}'.",
            )

        set_position(x_m, y_m, z_m, yaw_deg=yaw_deg)
        return StandardResponse.success(
            command,
            "PX4 local position target sent.",
            {
                "target_kind": target_kind.value,
                "position": {"x_m": x_m, "y_m": y_m, "z_m": z_m, "yaw_deg": yaw_deg},
            },
        )

    def _handle_stop(self, command: StandardCommand) -> StandardResponse:
        with self._control_authority.acquire("stop", 70, preempt=True):
            self.client.stop()
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(command, "PX4 stop command sent.", {"velocity": dict(self.last_velocity)})

    def _handle_emergency_stop(self, command: StandardCommand) -> StandardResponse:
        with self._control_authority.acquire("emergency_stop", 100, preempt=True):
            self.client.emergency_stop()
        self.armed = False
        self.flying = False
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(command, "PX4 emergency stop command sent.", self.get_vendor_state())

    def _handle_get_status(self, command: StandardCommand) -> StandardResponse:
        return StandardResponse.success(command, "PX4 status.", self.get_vendor_state())

    def supports_native_route(self, route: Any, *, state: Any = None) -> bool:
        """判断航线能否由 MAVSDK Mission 原生执行。

        ``route`` 至少需要两个航点；全球航点要求 ``state`` 含 Home 绝对海拔，以便转换为
        MAVSDK Mission 的相对高度；本地航点还要求当前全球经纬度。返回 ``True`` 表示 client
        支持 Mission 且坐标可转换，否则返回 ``False``。
        """
        if not callable(getattr(self.client, "execute_mission", None)):
            return False
        waypoints = tuple(getattr(route, "waypoints", ()))
        if len(waypoints) < 2:
            return False
        if all(getattr(waypoint, "latitude_deg", None) is not None and getattr(waypoint, "longitude_deg", None) is not None for waypoint in waypoints[1:]):
            return state is not None and getattr(state, "home_absolute_altitude_m", None) is not None
        return (
            state is not None
            and getattr(state, "latitude_deg", None) is not None
            and getattr(state, "longitude_deg", None) is not None
        )

    def execute_native_route(
        self,
        route: Any,
        *,
        state: Any = None,
        altitude_m: Optional[float] = None,
        speed_m_s: float = 0.0,
        heading: Any = None,
        timeout_s: float = 60.0,
        max_duration_s: Optional[float] = None,
        guard_callback: Optional[Callable[[], None]] = None,
        event_callback: Optional[Callable[..., None]] = None,
        route_event_callback: Optional[Callable[..., None]] = None,
        completion_callback: Optional[Callable[[str], None]] = None,
        action_callback: Optional[Callable[..., None]] = None,
    ) -> StandardResponse:
        """在按需持有全球位置、Home、速度和健康遥测期间执行 PX4 原生航线。

        ``route`` 是标准航线，``state`` 提供坐标原点；``altitude_m``、``speed_m_s``、``timeout_s``
        分别使用米、米/秒和秒，``heading`` 是标准航向策略；``max_duration_s`` 是可选正常执行秒数
        上限。``guard_callback``、``event_callback``、``route_event_callback``、
        ``completion_callback`` 和 ``action_callback`` 分别报告守卫、航点、航线、完成与到点动作。
        SDK 自动复用底层遥测，并在完成、失败或取消后释放；返回 FleetDock 标准响应。
        """
        release = self._acquire_client_telemetry(
            {"position", "home", "velocity_ned", "attitude", "health", "in_air"},
            owner="task.route",
        )
        try:
            return self._execute_native_route_impl(
                route,
                state=state,
                altitude_m=altitude_m,
                speed_m_s=speed_m_s,
                heading=heading,
                timeout_s=timeout_s,
                max_duration_s=max_duration_s,
                guard_callback=guard_callback,
                event_callback=event_callback,
                route_event_callback=route_event_callback,
                completion_callback=completion_callback,
                action_callback=action_callback,
            )
        finally:
            release()

    def _execute_native_route_impl(
        self,
        route: Any,
        *,
        state: Any = None,
        altitude_m: Optional[float] = None,
        speed_m_s: float = 0.0,
        heading: Any = None,
        timeout_s: float = 60.0,
        max_duration_s: Optional[float] = None,
        guard_callback: Optional[Callable[[], None]] = None,
        event_callback: Optional[Callable[..., None]] = None,
        route_event_callback: Optional[Callable[..., None]] = None,
        completion_callback: Optional[Callable[[str], None]] = None,
        action_callback: Optional[Callable[..., None]] = None,
    ) -> StandardResponse:
        """使用 PX4 Mission 原生任务系统同步执行 FleetDock 航线。

        ``route`` 是待执行航线，``state`` 提供本地转全球坐标原点；``altitude_m``（米）、
        ``speed_m_s``（米/秒）和 ``heading`` 补充航点参数，``timeout_s`` 是异常超时秒数；
        ``max_duration_s`` 是达到后正常暂停 Mission 并悬停的可选秒数上限；
        ``guard_callback``、``event_callback`` 和 ``route_event_callback`` 分别处理守卫、航点进度与
        航线开始事件；``completion_callback`` 接收正常完成原因，``action_callback`` 处理到点动作。
        返回标准响应，不支持原生执行时返回 ``UNSUPPORTED_COMMAND``。
        """
        if not self.supports_native_route(route, state=state):
            return StandardResponse(
                ok=False,
                command_id="native-route",
                device_id=self.device_id,
                product=self.product,
                code=ErrorCode.UNSUPPORTED_COMMAND,
                message="PX4 native route requires MAVSDK mission support and global position.",
            )

        mission_items = self._build_route_mission_items(route, state=state, altitude_m=altitude_m, speed_m_s=speed_m_s)
        route_start_index = self._mission_route_start_index(route)
        completed_indices: set[int] = set()
        last_started_index = route_start_index - 1
        route_started = False

        def publish_route_started() -> None:
            nonlocal route_started
            if not route_started and route_event_callback is not None:
                route_event_callback(EventType.ROUTE_STARTED)
                route_started = True

        def publish_started(index: int) -> None:
            nonlocal last_started_index
            if event_callback is not None and index > last_started_index and index < len(route.waypoints):
                publish_route_started()
                event_callback(EventType.WAYPOINT_STARTED, route.waypoints[index], index, None)
                last_started_index = index

        def on_progress(current: int, total: int) -> None:
            if current <= 0:
                publish_started(route_start_index)
                return
            waypoint_index = min(route_start_index + current - 1, len(route.waypoints) - 1)
            publish_started(waypoint_index)
            if waypoint_index not in completed_indices:
                waypoint = route.waypoints[waypoint_index]
                if event_callback is not None:
                    event_callback(EventType.WAYPOINT_REACHED, waypoint, waypoint_index, None)
                    event_callback(EventType.WAYPOINT_SETTLED, waypoint, waypoint_index, None)
                if action_callback is not None:
                    action_callback(waypoint, waypoint_index, None)
                completed_indices.add(waypoint_index)
            publish_started(waypoint_index + 1)

        try:
            mission_kwargs = {
                "timeout_s": timeout_s,
                "progress_callback": on_progress,
                "guard_callback": guard_callback,
            }
            if max_duration_s is not None:
                mission_kwargs["max_duration_s"] = max_duration_s
            mission_executor = cast(Callable[..., bool], self.client.execute_mission)
            cancel_mission = getattr(self.client, "cancel_mission", None)
            with self._control_authority.acquire(
                "route",
                40,
                cancel=cancel_mission if callable(cancel_mission) else None,
            ):
                mission_completed = mission_executor(mission_items, **mission_kwargs)
        except Exception as exc:
            logger.debug("PX4 原生航线执行失败: route=%s error=%s", getattr(route, "name", None), exc, exc_info=True)
            return StandardResponse(
                ok=False,
                command_id="native-route",
                device_id=self.device_id,
                product=self.product,
                code=ErrorCode.UNKNOWN_ERROR,
                message=str(exc) or type(exc).__name__,
            )
        completion_reason = (
            RouteCompletionReason.DURATION_REACHED
            if mission_completed is False
            else RouteCompletionReason.ROUTE_FINISHED
        )
        if completion_callback is not None:
            completion_callback(completion_reason.value)
        elif route_event_callback is not None:
            route_event_callback(EventType.ROUTE_COMPLETED)
        self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse(
            ok=True,
            command_id="native-route",
            device_id=self.device_id,
            product=self.product,
            message=f"PX4 native mission route completed: {completion_reason.value}.",
            data={
                "waypoint_count": len(route.waypoints),
                "mission_item_count": len(mission_items),
                "route_completion_reason": completion_reason.value,
            },
        )

    def cancel_route(self) -> None:
        """暂停当前 PX4 Mission 并切换 Hold。

        若底层 client 提供 ``cancel_mission`` 则使用 PX4 原生暂停/保持能力；无原生取消入口时回退
        标准停止命令。该方法无返回值，没有正在执行的 Mission 时保持幂等。
        """
        cancel_mission = getattr(self.client, "cancel_mission", None)
        if callable(cancel_mission):
            cancel_mission()
            self.last_velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
            return
        super().cancel_route()

    def _build_route_mission_items(
        self,
        route: Any,
        *,
        state: Any,
        altitude_m: Optional[float],
        speed_m_s: float,
    ) -> list[Mapping[str, Any]]:
        origin_latitude_deg = getattr(state, "latitude_deg", None) if state is not None else None
        origin_longitude_deg = getattr(state, "longitude_deg", None) if state is not None else None
        current_relative_altitude_m = getattr(state, "relative_altitude_m", None) if state is not None else None
        home_absolute_altitude_m = getattr(state, "home_absolute_altitude_m", None) if state is not None else None
        mission_items: list[Mapping[str, Any]] = []
        for waypoint in route.waypoints[self._mission_route_start_index(route):]:
            latitude_deg = getattr(waypoint, "latitude_deg", None)
            longitude_deg = getattr(waypoint, "longitude_deg", None)
            is_global_waypoint = latitude_deg is not None and longitude_deg is not None
            if latitude_deg is None or longitude_deg is None:
                if origin_latitude_deg is None or origin_longitude_deg is None:
                    raise ValueError("PX4 local route requires current global position telemetry.")
                latitude_deg, longitude_deg = self._local_offset_to_global(
                    float(origin_latitude_deg),
                    float(origin_longitude_deg),
                    float(getattr(waypoint, "x_m", 0.0) or 0.0),
                    float(getattr(waypoint, "y_m", 0.0) or 0.0),
                )
            waypoint_altitude_m = getattr(waypoint, "altitude_m", None)
            if is_global_waypoint and waypoint_altitude_m is not None:
                if home_absolute_altitude_m is None:
                    raise ValueError("PX4 GPS route requires home absolute altitude telemetry.")
                waypoint_altitude_m = float(waypoint_altitude_m) - float(home_absolute_altitude_m)
            if waypoint_altitude_m is None:
                waypoint_altitude_m = getattr(waypoint, "z_m", None)
            if waypoint_altitude_m is None:
                waypoint_altitude_m = altitude_m if altitude_m is not None else current_relative_altitude_m
            if waypoint_altitude_m is None:
                raise ValueError("PX4 native route requires altitude_m, waypoint altitude, or current relative altitude.")
            waypoint_yaw_deg = getattr(waypoint, "yaw_deg", None)
            mission_items.append(
                {
                    "latitude_deg": latitude_deg,
                    "longitude_deg": longitude_deg,
                    "relative_altitude_m": float(waypoint_altitude_m),
                    "speed_m_s": float(getattr(waypoint, "speed_m_s", None) or speed_m_s),
                    "acceptance_radius_m": float(getattr(waypoint, "acceptance_radius_m", None) or 0.0),
                    "yaw_deg": float(waypoint_yaw_deg) if waypoint_yaw_deg is not None else float("nan"),
                    "loiter_time_s": float(getattr(waypoint, "hold_time_s", 0.0) or 0.0),
                    "is_fly_through": float(getattr(waypoint, "hold_time_s", 0.0) or 0.0) <= 0,
                }
            )
        return mission_items

    @staticmethod
    def _mission_route_start_index(route: Any) -> int:
        waypoints = tuple(getattr(route, "waypoints", ()))
        if not waypoints:
            return 0
        first = waypoints[0]
        if getattr(first, "latitude_deg", None) is not None and getattr(first, "longitude_deg", None) is not None:
            return 0
        return 1

    @staticmethod
    def _local_offset_to_global(
        origin_latitude_deg: float,
        origin_longitude_deg: float,
        north_m: float,
        east_m: float,
    ) -> tuple[float, float]:
        earth_radius_m = 6378137.0
        latitude_rad = math.radians(origin_latitude_deg)
        latitude_deg = origin_latitude_deg + math.degrees(north_m / earth_radius_m)
        longitude_deg = origin_longitude_deg + math.degrees(east_m / (earth_radius_m * max(math.cos(latitude_rad), 1e-6)))
        return latitude_deg, longitude_deg

    def get_vendor_state(self) -> Dict[str, Any]:
        """返回 PX4 原生状态和标准适配器状态快照；返回字典不会暴露内部可变缓存。"""
        status = self.client.get_status() if self.connected else {}
        control_lease = self._control_authority.status()
        with self._state_lock:
            telemetry = dict(self.latest_telemetry)
        return {
            "connected": self.connected,
            "armed": self.armed,
            "flying": self.flying,
            "last_velocity": dict(self.last_velocity),
            "px4": status,
            "telemetry": telemetry,
            "control_owner": control_lease.owner if control_lease is not None else None,
        }

    def telemetry_diagnostics(self) -> Dict[str, Any]:
        """返回 PX4 按需遥测流、链路配置、分发积压和丢弃计数的安全诊断快照。"""
        diagnostics = getattr(self.client, "telemetry_diagnostics", None)
        return dict(diagnostics()) if callable(diagnostics) else {}

    def get_managed_object(self) -> Any:
        """返回当前托管的 ``MavsdkClient`` 或兼容 client，供高级单进程接入使用。"""
        return self.client

    def sensor_backends(self) -> tuple[Any, ...]:
        """返回复用当前 MAVSDK/MAVLink 连接的 PX4 标准传感器后端。

        兼容测试或调用方注入的旧 client 若未实现传感器流协议，则返回空元组，不影响控制和事件能力。
        """
        return (self._sensor_backend,) if self._sensor_backend is not None else ()

    @property
    def _owns_client(self) -> bool:
        return self.connection.mode != InitMode.MANAGED_OBJECT or self.connection.close_managed_object

    def close(self) -> None:
        """停止遥测、关闭底层 client 并发布断开事件；可重复调用且无返回值。"""
        if self._takeoff_cancel_event is not None:
            self._takeoff_cancel_event.set()
        self.device_events.set_demand_handler(None)
        with self._telemetry_lease_lock:
            leases = tuple(self._telemetry_leases.values())
            self._telemetry_leases.clear()
        for lease in leases:
            lease["release"]()
        stop_telemetry = getattr(self.client, "stop_telemetry", None)
        if callable(stop_telemetry):
            stop_telemetry()
        close_client = getattr(self.client, "close", None)
        if callable(close_client) and self._owns_client:
            close_client()
        if self.connected:
            self.connected = False
            self.publish_standard_event(
                EventType.CONNECTION_CHANGED,
                EventCategory.LIFECYCLE,
                {"previous": "connected", "current": "disconnected"},
            )

    def _handle_telemetry(self, stream_name: str, data: Mapping[str, Any], native_data: Any = None) -> None:
        payload = dict(data)
        raw_data = serialize_event_data(native_data) if native_data is not None else payload
        original = OriginalEvent(
            provider="px4",
            event_name=f"mavsdk.telemetry.{stream_name}",
            raw_data=raw_data,
            native_data=native_data,
        )
        with self._state_lock:
            self.latest_telemetry[stream_name] = payload

        if stream_name == "position":
            self.publish_standard_event(
                EventType.GLOBAL_POSITION_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "home":
            self.publish_standard_event(
                EventType.HOME_POSITION_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "local_position":
            self.publish_standard_event(
                EventType.LOCAL_POSITION_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "velocity_ned":
            self.publish_standard_event(
                EventType.VELOCITY_UPDATED,
                EventCategory.TELEMETRY,
                {
                    "frame": CoordinateFrame.LOCAL_NED.value,
                    "linear_velocity_m_s": {
                        "x": payload["north_m_s"],
                        "y": payload["east_m_s"],
                        "z": payload["down_m_s"],
                    },
                },
                original=original,
            )
        elif stream_name == "attitude":
            self.publish_standard_event(
                EventType.ATTITUDE_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "battery":
            self.publish_standard_event(
                EventType.BATTERY_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "health":
            self.publish_standard_event(
                EventType.HEALTH_UPDATED,
                EventCategory.TELEMETRY,
                payload,
                original=original,
            )
        elif stream_name == "armed":
            self._publish_changed_state("armed", payload["armed"], EventType.ARMED_CHANGED, original)
            self.armed = bool(payload["armed"])
        elif stream_name == "in_air":
            self._publish_changed_state("in_air", payload["in_air"], EventType.IN_AIR_CHANGED, original)
            self.flying = bool(payload["in_air"])
        elif stream_name == "flight_mode":
            self._publish_changed_state(
                "flight_mode",
                payload["flight_mode"],
                EventType.FLIGHT_MODE_CHANGED,
                original,
            )
        elif stream_name == "landed_state":
            self._publish_changed_state(
                "landed_state",
                payload["landed_state"],
                EventType.LANDED_STATE_CHANGED,
                original,
            )
        elif stream_name == "telemetry_error":
            self.publish_standard_event(
                EventType.ERROR_OCCURRED,
                EventCategory.ALERT,
                payload,
                original=original,
            )
        else:
            self.publish_vendor_event(
                f"vendor.px4.{stream_name}.updated",
                EventCategory.TELEMETRY,
                raw_data,
                native_data=native_data,
            )

    def _publish_changed_state(
        self,
        name: str,
        current: Any,
        event_type: EventType,
        original: OriginalEvent,
    ) -> None:
        with self._state_lock:
            previous = self._telemetry_state.get(name)
            self._telemetry_state[name] = current
        if previous == current:
            return
        self.publish_standard_event(
            event_type,
            EventCategory.STATE,
            {"previous": previous, "current": current},
            original=original,
        )

    def _publish_command_state_change(
        self,
        name: str,
        previous: Any,
        current: Any,
        event_type: EventType,
        command: StandardCommand,
    ) -> None:
        with self._state_lock:
            telemetry_previous = self._telemetry_state.get(name, previous)
            self._telemetry_state[name] = current
        if telemetry_previous == current:
            return
        self.publish_standard_event(
            event_type,
            EventCategory.STATE,
            {"previous": telemetry_previous, "current": current},
            correlation_id=command.command_id,
        )
