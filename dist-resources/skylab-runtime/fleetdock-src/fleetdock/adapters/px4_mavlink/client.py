"""PX4 MAVSDK 同步客户端门面。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
from contextlib import contextmanager
from threading import Lock, local
import time
from typing import Any, Callable, Dict, Mapping, Optional
from uuid import uuid4

from fleetdock.errors import FleetDockError
from fleetdock.events import CoordinateFrame
from fleetdock.models import ErrorCode
from fleetdock.runtime import ManagedWorker, WorkerStatus
from fleetdock.sensors import SensorSourceKind
from fleetdock.takeoff import (
    TakeoffExecutionState,
    TakeoffMethod,
    TakeoffOptions,
    TakeoffProgressCallback,
    TakeoffStatus,
)

logger = logging.getLogger(__name__)

class MavsdkClient:
    """单个 PX4 连接的同步 MAVSDK 门面。

    每个实例会占用一个本地 MAVSDK gRPC server 端口；未显式指定时从 50051 开始在当前
    Python 进程内递增分配，避免同进程控制多架 PX4 时端口冲突。
    """

    _server_port_lock = Lock()
    _next_server_port = 50051
    _TELEMETRY_STREAMS = frozenset({
        "position",
        "local_position",
        "home",
        "velocity_ned",
        "attitude",
        "battery",
        "armed",
        "in_air",
        "landed_state",
        "flight_mode",
        "health",
    })
    _BASELINE_TELEMETRY_STREAMS = frozenset({
        "battery",
        "armed",
        "in_air",
        "landed_state",
        "flight_mode",
        "health",
    })

    def __init__(
        self,
        endpoint: str,
        timeout_s: float = 10.0,
        mavsdk_server_port: Optional[int] = None,
        camera_stream_uri: Optional[str] = None,
        camera_stream_source: SensorSourceKind = SensorSourceKind.UNKNOWN,
        sender_system_id: int = 245,
        sender_component_id: int = 190,
        target_system_id: int = 1,
        target_component_id: int = 1,
        link_profile: str = "standard",
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.mavsdk_server_port = (
            int(mavsdk_server_port)
            if mavsdk_server_port is not None
            else self._allocate_server_port()
        )
        self._camera_stream_uri_override = str(camera_stream_uri).strip() if camera_stream_uri else None
        self.camera_stream_source = camera_stream_source
        self.sender_system_id = int(sender_system_id)
        self.sender_component_id = int(sender_component_id)
        self.target_system_id = int(target_system_id)
        self.target_component_id = int(target_component_id)
        selected_link_profile = str(link_profile).strip().lower()
        if selected_link_profile not in {"simulation", "standard", "low_bandwidth"}:
            raise ValueError("link_profile must be simulation, standard, or low_bandwidth.")
        self.link_profile = selected_link_profile
        self._system: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_worker: Optional[ManagedWorker] = None
        self._loop_lock = Lock()
        self._connected = False
        self._offboard_started = False
        self._telemetry_future: Optional[Any] = None
        self._telemetry_callback: Optional[Callable[[str, Mapping[str, Any], Any], None]] = None
        self._telemetry_demand_lock = Lock()
        self._telemetry_demands: Dict[str, frozenset[str]] = {}
        self._telemetry_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._telemetry_release_handles: Dict[str, asyncio.TimerHandle] = {}
        self._telemetry_callback_queue: Optional[asyncio.Queue[tuple[str, Mapping[str, Any], Any]]] = None
        self._telemetry_dispatcher: Optional[asyncio.Task[Any]] = None
        self._telemetry_dropped_notifications = 0
        self._telemetry_queue_high_watermark = 0
        self._sensor_futures: set[Any] = set()
        self._status_lock = Lock()
        self._latest_status: Dict[str, Any] = {}
        self._status_updated_at: Dict[str, float] = {}
        self._command_context = local()

    @contextmanager
    def _use_command_timeout(self, timeout_s: Optional[float]):
        """在当前调用线程内临时覆盖标准命令等待上限。"""
        previous = getattr(self._command_context, "timeout_s", None)
        self._command_context.timeout_s = timeout_s
        try:
            yield
        finally:
            self._command_context.timeout_s = previous

    def connect(self) -> None:
        """连接 PX4 并验证双向 MAVLink 通道。

        调用时同步等待心跳和版本握手，最长等待 ``timeout_s`` 秒；成功后无返回值，
        连接失败或未安装 MAVSDK 时抛出异常，由适配器转换为标准响应。
        """
        self._run(self._connect())

    async def _connect(self) -> None:
        if self._connected:
            return
        try:
            from mavsdk import System
        except ImportError as exc:
            raise RuntimeError("mavsdk is required for PX4 connections.") from exc

        system_parameters = inspect.signature(System).parameters
        system_kwargs: Dict[str, Any] = {}
        if "port" in system_parameters:
            system_kwargs["port"] = self.mavsdk_server_port
        if "sysid" in system_parameters:
            system_kwargs["sysid"] = self.sender_system_id
        if "compid" in system_parameters:
            system_kwargs["compid"] = self.sender_component_id
        self._system = System(**system_kwargs)
        system = self._system
        await system.connect(system_address=self.endpoint)

        async def wait_connected() -> None:
            async for state in system.core.connection_state():
                if state.is_connected:
                    return

        await asyncio.wait_for(wait_connected(), timeout=self.timeout_s)
        await self._wait_for_bidirectional_mavlink()
        self._connected = True

    @classmethod
    def _allocate_server_port(cls) -> int:
        with cls._server_port_lock:
            port = cls._next_server_port
            cls._next_server_port += 1
        return port

    async def _wait_for_bidirectional_mavlink(self) -> None:
        if self._system is None:
            raise RuntimeError("MAVSDK connection has not been initialized.")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s
        last_error: Optional[Exception] = None
        while True:
            remaining_s = deadline - loop.time()
            if remaining_s <= 0:
                raise RuntimeError(
                    "PX4 heartbeat received, but bidirectional MAVLink verification failed. "
                    "Check the WSL-to-Windows MAVLink target address."
                ) from last_error
            try:
                await asyncio.wait_for(self._system.info.get_version(), timeout=min(remaining_s, 5.0))
                return
            except Exception as exc:
                last_error = exc
                sleep_s = min(0.5, max(0.0, deadline - loop.time()))
                if sleep_s <= 0:
                    continue
                logger.debug("PX4 双向 MAVLink 握手暂未就绪，继续重试: error=%s", exc)
                await asyncio.sleep(sleep_s)

    def arm(self, force: bool = False) -> None:
        """向 PX4 发送解锁命令。

        `force=False` 时调用标准 arm；`force=True` 时显式调用 MAVSDK `arm_force()`，仅适用于
        已采取安全措施的台架测试或仿真诊断。`arm_force()` 不能替代飞行预检，PX4 仍可能在命令成功后
        主动上锁。该方法同步等待底层命令完成，无返回值；失败时抛出异常，
        由上层适配器转换成标准错误响应。
        """
        action = self._require_system().action
        self._run(action.arm_force() if force else action.arm())

    def disarm(self) -> None:
        """同步发送 PX4 上锁命令；成功时无返回值，底层失败时抛出 MAVSDK 异常。"""
        self._run(self._require_system().action.disarm())

    def takeoff(self, altitude: Optional[float] = None) -> None:
        """按可选目标高度同步发送 PX4 起飞命令。

        ``altitude`` 是相对 Home 点高度，单位为米；传入数值时先通过 MAVSDK 写入 PX4 起飞高度参数，
        传 ``None`` 时保留飞控现有配置并直接发送 Takeoff。设备应已解锁；成功时无返回值，失败时
        抛出异常，由上层适配器转换成标准响应。
        """
        self._run(self._takeoff(altitude))

    async def _takeoff(self, altitude: Optional[float]) -> None:
        system = self._require_system()
        if altitude is not None:
            logger.debug("PX4 起飞前设置目标高度: altitude_m=%.2f", altitude)
            await system.action.set_takeoff_altitude(altitude)
        else:
            logger.debug("PX4 使用飞控现有起飞高度发送命令，不覆盖高度参数")
        await system.action.takeoff()

    def execute_takeoff(
        self,
        options: TakeoffOptions,
        *,
        progress_callback: Optional[TakeoffProgressCallback] = None,
        cancellation_check: Optional[Callable[[], None]] = None,
    ) -> TakeoffStatus:
        """自动选择 PX4 起飞链路并等待物理高度确认。

        ``options`` 使用跨厂商高度、速度、超时和安全边界；方法依据最新遥测自动选择飞控原生
        起飞或本地位置闭环，不接受调用方指定 MAVSDK/Offboard 策略。``progress_callback`` 接收
        不可变状态快照，``cancellation_check`` 可抛出取消异常。成功返回 ``COMPLETED`` 状态；
        全球位置与 Home 可用时完成后交接到飞控 Hold，仅有本地定位时保持 Offboard 位置控制；
        失败前若已可能离地会优先请求 Land，绝不直接在空中上锁。
        """
        return self._run(self._execute_takeoff(options, progress_callback, cancellation_check))

    async def _execute_takeoff(
        self,
        options: TakeoffOptions,
        progress_callback: Optional[TakeoffProgressCallback],
        cancellation_check: Optional[Callable[[], None]],
    ) -> TakeoffStatus:
        execution_id = str(uuid4())
        status = TakeoffStatus(
            execution_id=execution_id,
            state=TakeoffExecutionState.PRECHECKING,
            target_altitude_m=options.target_altitude_m,
        )

        def publish(**changes: object) -> None:
            nonlocal status
            status = status.changed(**changes)
            if progress_callback is not None:
                progress_callback(status)

        publish(reason="正在评估飞控原生起飞与本地位置闭环条件。")
        deadline = time.monotonic() + options.timeout_s
        await self._wait_for_takeoff_telemetry(deadline, options, cancellation_check)
        method = self._resolve_takeoff_method(options)
        if method is TakeoffMethod.AUTOPILOT and not self._global_hold_is_available(
            options.telemetry_max_age_s
        ):
            raise RuntimeError(
                "TAKEOFF_STRATEGY_UNAVAILABLE: native takeoff requires fresh global position and health, "
                "plus a valid Home reference."
            )
        publish(
            state=TakeoffExecutionState.STARTING,
            method=method,
            reason=(
                "本地位置遥测新鲜且健康，使用有界本地位置闭环。"
                if method is TakeoffMethod.LOCAL_POSITION
                else "本地位置闭环条件不足，使用飞控原生起飞。"
            ),
        )
        try:
            if method is TakeoffMethod.LOCAL_POSITION:
                await self._execute_local_position_takeoff(
                    options,
                    deadline=deadline,
                    publish=publish,
                    cancellation_check=cancellation_check,
                )
            else:
                method = await self._execute_native_takeoff(
                    options,
                    deadline=deadline,
                    publish=publish,
                    cancellation_check=cancellation_check,
                )
            publish(
                state=TakeoffExecutionState.HOLDING,
                method=method,
                progress=1.0,
                reason="目标高度已确认，正在根据定位能力选择保持方式。",
            )
            completion_reason = await self._complete_takeoff_handoff(
                self._require_system(),
                method=method,
                health_max_age_s=options.telemetry_max_age_s,
            )
            publish(
                state=TakeoffExecutionState.COMPLETED,
                method=method,
                progress=1.0,
                reason=completion_reason,
            )
            return status
        except BaseException as exc:
            publish(state=TakeoffExecutionState.ABORTED, reason=str(exc))
            await self._safe_abort_takeoff()
            raise

    async def _wait_for_takeoff_telemetry(
        self,
        deadline: float,
        options: TakeoffOptions,
        cancellation_check: Optional[Callable[[], None]],
    ) -> None:
        while time.monotonic() < deadline:
            if cancellation_check is not None:
                cancellation_check()
            status = self.get_status()
            health = status.get("health", {})
            battery = status.get("battery", {})
            common_telemetry_is_fresh = all(
                self._stream_is_fresh(stream_name, options.telemetry_max_age_s)
                for stream_name in ("attitude", "battery", "health")
            )
            if common_telemetry_is_fresh and status.get("attitude") and battery and health and (
                self._local_position_is_ready(options)
                or (
                    status.get("relative_altitude_m") is not None
                    and self._stream_is_fresh("position", options.telemetry_max_age_s)
                )
            ):
                remaining = battery.get("remaining_fraction")
                if remaining is not None and float(remaining) < options.min_battery_fraction:
                    raise RuntimeError("TAKEOFF_PRECHECK_FAILED: battery is below the configured minimum.")
                if health.get("is_armable") is False and not self._local_position_is_ready(options):
                    raise RuntimeError("TAKEOFF_PRECHECK_FAILED: flight controller reports not armable.")
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("TAKEOFF_TELEMETRY_STALE: required takeoff telemetry did not become ready.")

    def _resolve_takeoff_method(self, options: TakeoffOptions) -> TakeoffMethod:
        return (
            TakeoffMethod.LOCAL_POSITION
            if self._local_position_is_ready(options)
            else TakeoffMethod.AUTOPILOT
        )

    def _local_position_is_ready(self, options: TakeoffOptions) -> bool:
        return self._local_position_control_is_ready(options.telemetry_max_age_s)

    def _local_position_control_is_ready(self, max_age_s: float = 5.0) -> bool:
        with self._status_lock:
            local = self._latest_status.get("local_position")
            health = self._latest_status.get("health", {})
            attitude = self._latest_status.get("attitude")
        return bool(
            isinstance(local, Mapping)
            and local.get("position_m")
            and isinstance(attitude, Mapping)
            and all(
                self._stream_is_fresh(stream_name, max_age_s)
                for stream_name in ("local_position", "attitude", "health")
            )
            and health.get("is_local_position_ok") is True
        )

    def _stream_is_fresh(self, stream_name: str, max_age_s: float) -> bool:
        with self._status_lock:
            updated_at = self._status_updated_at.get(stream_name)
        return updated_at is not None and time.monotonic() - updated_at <= max_age_s

    async def _execute_native_takeoff(
        self,
        options: TakeoffOptions,
        *,
        deadline: float,
        publish: Callable[..., None],
        cancellation_check: Optional[Callable[[], None]],
    ) -> TakeoffMethod:
        system = self._require_system()
        if not self.get_status().get("armed", {}).get("armed", False):
            await system.action.arm()
        await system.action.set_takeoff_altitude(options.target_altitude_m)
        baseline = float(self.get_status().get("relative_altitude_m") or 0.0)
        await system.action.takeoff()
        progress_deadline = min(deadline, time.monotonic() + options.native_progress_timeout_s)
        while time.monotonic() < deadline:
            if cancellation_check is not None:
                cancellation_check()
            current = self.get_status()
            if not self._stream_is_fresh("position", options.telemetry_max_age_s):
                raise RuntimeError("TAKEOFF_TELEMETRY_STALE: relative altitude is stale.")
            altitude = current.get("relative_altitude_m")
            measured = float(altitude) - baseline if altitude is not None else None
            if measured is not None:
                progress = min(1.0, max(0.0, measured / options.target_altitude_m))
                publish(
                    state=TakeoffExecutionState.ASCENDING,
                    current_altitude_m=measured,
                    progress=progress,
                )
                if measured >= options.target_altitude_m - options.altitude_tolerance_m:
                    return TakeoffMethod.AUTOPILOT
            if time.monotonic() >= progress_deadline:
                in_air = bool(current.get("in_air", {}).get("in_air", False))
                if (measured is None or measured < options.altitude_tolerance_m) and not in_air:
                    if self._local_position_is_ready(options):
                        logger.debug("PX4 原生起飞无物理进展，离地前自动切换本地位置闭环")
                        await self._execute_local_position_takeoff(
                            options,
                            deadline=deadline,
                            publish=publish,
                            cancellation_check=cancellation_check,
                            already_armed=True,
                        )
                        return TakeoffMethod.LOCAL_POSITION
                    raise RuntimeError("TAKEOFF_NO_PROGRESS: native takeoff produced no physical progress.")
                progress_deadline = deadline
            await asyncio.sleep(0.1)
        raise TimeoutError("TAKEOFF_ASCENT_TIMEOUT: target altitude was not reached.")

    async def _execute_local_position_takeoff(
        self,
        options: TakeoffOptions,
        *,
        deadline: float,
        publish: Callable[..., None],
        cancellation_check: Optional[Callable[[], None]],
        already_armed: bool = False,
    ) -> None:
        from mavsdk.offboard import PositionNedYaw

        system = self._require_system()
        current = self.get_status()
        local = current["local_position"]["position_m"]
        ground_north_m = float(local["x"])
        ground_east_m = float(local["y"])
        ground_down_m = float(local["z"])
        yaw_deg = float(current.get("attitude", {}).get("yaw_deg", 0.0))
        interval_s = 1.0 / options.control_rate_hz
        raw_mode = False

        async def send(height_m: float) -> None:
            if raw_mode:
                await self._send_raw_local_position_setpoint(
                    ground_north_m,
                    ground_east_m,
                    ground_down_m - height_m,
                    yaw_deg,
                )
            else:
                await system.offboard.set_position_ned(
                    PositionNedYaw(
                        ground_north_m,
                        ground_east_m,
                        ground_down_m - height_m,
                        yaw_deg,
                    )
                )

        prestream_deadline = min(deadline, time.monotonic() + 1.0)
        while time.monotonic() < prestream_deadline:
            if cancellation_check is not None:
                cancellation_check()
            await send(0.0)
            await asyncio.sleep(interval_s)
        try:
            await system.offboard.start()
            self._offboard_started = True
        except Exception as exc:
            logger.debug("PX4 MAVSDK Offboard 启动失败，尝试有界原始 MAVLink 兼容路径", exc_info=True)
            raw_mode = True
            await self._start_raw_offboard_fallback(
                ground_north_m,
                ground_east_m,
                ground_down_m,
                yaw_deg,
                interval_s,
                min(deadline, time.monotonic() + 10.0),
                cancellation_check,
            )
            self._offboard_started = False
            if time.monotonic() >= deadline:
                raise RuntimeError("TAKEOFF_OFFBOARD_REJECTED: local control did not start.") from exc

        telemetry_armed = bool(current.get("armed", {}).get("armed", False))
        arm_task = None if already_armed or telemetry_armed else asyncio.create_task(system.action.arm())
        started = time.monotonic()
        try:
            while time.monotonic() < deadline:
                if cancellation_check is not None:
                    cancellation_check()
                elapsed = time.monotonic() - started
                commanded_height = min(options.target_altitude_m, elapsed * options.climb_speed_m_s)
                await send(commanded_height)
                measured = self._validate_local_takeoff_safety(
                    options,
                    ground_north_m,
                    ground_east_m,
                    ground_down_m,
                )
                publish(
                    state=TakeoffExecutionState.ASCENDING,
                    method=TakeoffMethod.LOCAL_POSITION,
                    current_altitude_m=measured,
                    progress=min(1.0, max(0.0, measured / options.target_altitude_m)),
                )
                if arm_task is not None and arm_task.done():
                    arm_task.result()
                if measured >= options.target_altitude_m - options.altitude_tolerance_m:
                    if arm_task is not None:
                        await arm_task
                    return
                await asyncio.sleep(interval_s)
        finally:
            if arm_task is not None and not arm_task.done():
                arm_task.cancel()
                await asyncio.gather(arm_task, return_exceptions=True)
        raise TimeoutError("TAKEOFF_ASCENT_TIMEOUT: local position target was not reached.")

    def _validate_local_takeoff_safety(
        self,
        options: TakeoffOptions,
        ground_north_m: float,
        ground_east_m: float,
        ground_down_m: float,
    ) -> float:
        current = self.get_status()
        stale_streams = tuple(
            stream_name
            for stream_name in ("local_position", "attitude")
            if not self._stream_is_fresh(stream_name, options.telemetry_max_age_s)
        )
        if stale_streams:
            logger.debug(
                "PX4 本地起飞控制遥测过期: streams=%s max_age_s=%.2f",
                ",".join(stale_streams),
                options.telemetry_max_age_s,
            )
            raise RuntimeError(
                f"TAKEOFF_TELEMETRY_STALE: stale control telemetry: {', '.join(stale_streams)}."
            )
        if not self._stream_is_fresh("battery", options.battery_telemetry_max_age_s):
            logger.debug(
                "PX4 本地起飞电池监督遥测过期: max_age_s=%.2f",
                options.battery_telemetry_max_age_s,
            )
            raise RuntimeError("TAKEOFF_TELEMETRY_STALE: stale supervisory telemetry: battery.")
        local = current.get("local_position", {}).get("position_m", {})
        north_m = float(local["x"])
        east_m = float(local["y"])
        down_m = float(local["z"])
        height_m = ground_down_m - down_m
        if math.hypot(north_m - ground_north_m, east_m - ground_east_m) > options.max_horizontal_deviation_m:
            raise RuntimeError("TAKEOFF_POSITION_LIMIT: horizontal deviation exceeded the safety boundary.")
        attitude = current.get("attitude", {})
        if max(abs(float(attitude.get("roll_deg", 0.0))), abs(float(attitude.get("pitch_deg", 0.0)))) > options.max_tilt_deg:
            raise RuntimeError("TAKEOFF_TILT_LIMIT: vehicle tilt exceeded the safety boundary.")
        remaining = current.get("battery", {}).get("remaining_fraction")
        if remaining is not None and float(remaining) < options.min_battery_fraction:
            raise RuntimeError("TAKEOFF_PRECHECK_FAILED: battery fell below the configured minimum.")
        if height_m > options.target_altitude_m + max(options.altitude_tolerance_m, 0.25):
            raise RuntimeError("TAKEOFF_POSITION_LIMIT: measured height exceeded the target boundary.")
        return height_m

    async def _start_raw_offboard_fallback(
        self,
        north_m: float,
        east_m: float,
        down_m: float,
        yaw_deg: float,
        interval_s: float,
        deadline: float,
        cancellation_check: Optional[Callable[[], None]],
    ) -> None:
        from mavsdk.mavlink_direct import MavlinkMessage

        system = self._require_system()
        direct = getattr(system, "mavlink_direct", None)
        if direct is None:
            raise RuntimeError("TAKEOFF_OFFBOARD_REJECTED: raw MAVLink compatibility is unavailable.")
        next_mode_request = 0.0
        while time.monotonic() < deadline:
            if cancellation_check is not None:
                cancellation_check()
            await self._send_raw_local_position_setpoint(north_m, east_m, down_m, yaw_deg)
            now = time.monotonic()
            if now >= next_mode_request:
                await self._send_raw_offboard_mode_request(MavlinkMessage)
                next_mode_request = now + 0.8
            mode = str(self.get_status().get("flight_mode", {}).get("flight_mode", "")).lower()
            if "offboard" in mode:
                return
            await asyncio.sleep(interval_s)
        raise TimeoutError("TAKEOFF_OFFBOARD_REJECTED: raw compatibility mode was not confirmed.")

    async def _send_raw_local_position_setpoint(
        self,
        north_m: float,
        east_m: float,
        down_m: float,
        yaw_deg: float,
    ) -> None:
        from mavsdk.mavlink_direct import MavlinkMessage

        fields = {
            "time_boot_ms": int(time.monotonic() * 1000.0) & 0xFFFFFFFF,
            "target_system": self.target_system_id,
            "target_component": self.target_component_id,
            "coordinate_frame": 1,
            "type_mask": 2552,
            "x": north_m,
            "y": east_m,
            "z": down_m,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "afx": 0.0,
            "afy": 0.0,
            "afz": 0.0,
            "yaw": math.radians(yaw_deg),
            "yaw_rate": 0.0,
        }
        await self._require_system().mavlink_direct.send_message(MavlinkMessage(
            "SET_POSITION_TARGET_LOCAL_NED",
            self.sender_system_id,
            self.sender_component_id,
            self.target_system_id,
            self.target_component_id,
            json.dumps(fields, separators=(",", ":")),
        ))

    async def _send_raw_offboard_mode_request(self, message_type: Any) -> None:
        direct = self._require_system().mavlink_direct
        command = {
            "target_system": self.target_system_id,
            "target_component": self.target_component_id,
            "command": 176,
            "confirmation": 0,
            "param1": 1.0,
            "param2": 6.0,
            "param3": 0.0,
            "param4": 0.0,
            "param5": 0.0,
            "param6": 0.0,
            "param7": 0.0,
        }
        await direct.send_message(message_type(
            "COMMAND_LONG",
            self.sender_system_id,
            self.sender_component_id,
            self.target_system_id,
            self.target_component_id,
            json.dumps(command, separators=(",", ":")),
        ))
        set_mode = {
            "target_system": self.target_system_id,
            "base_mode": 1,
            "custom_mode": 6 << 16,
        }
        await direct.send_message(message_type(
            "SET_MODE",
            self.sender_system_id,
            self.sender_component_id,
            self.target_system_id,
            0,
            json.dumps(set_mode, separators=(",", ":")),
        ))

    async def _safe_abort_takeoff(self) -> None:
        system = self._require_system()
        current = self.get_status()
        armed = current.get("armed", {}).get("armed")
        in_air = current.get("in_air", {}).get("in_air")
        if armed is True or in_air is True:
            try:
                await self._land()
            except Exception:
                logger.debug("起飞中止后的自动 Land 请求失败", exc_info=True)
            return
        try:
            await self._stop_offboard_if_started(system)
        except Exception:
            logger.debug("起飞中止时停止 Offboard 失败", exc_info=True)

    async def _complete_takeoff_handoff(
        self,
        system: Any,
        *,
        method: TakeoffMethod,
        health_max_age_s: float,
    ) -> str:
        if self._global_hold_is_available(health_max_age_s):
            if self._offboard_started:
                await self._stop_offboard_if_started(system)
            else:
                await system.action.hold()
            logger.debug("PX4 起飞完成后使用全球定位 Hold 交接: method=%s", method.value)
            return "目标高度已确认；全球位置和 Home 可用，已交接给飞控 Hold。"

        if method is TakeoffMethod.LOCAL_POSITION and self._offboard_started:
            logger.debug("PX4 起飞完成后保持本地 Offboard 位置目标，跳过无 GPS 条件下的 Hold 交接")
            return "目标高度已确认；当前仅本地定位可用，继续由 Offboard 保持位置。"

        raise RuntimeError(
            "TAKEOFF_HOLD_UNAVAILABLE: neither global Hold nor an active local Offboard controller is available."
        )

    def _global_hold_is_available(self, max_age_s: float = 5.0) -> bool:
        if not all(
            self._stream_is_fresh(stream_name, max_age_s)
            for stream_name in ("position", "health")
        ):
            return False
        with self._status_lock:
            health = self._latest_status.get("health", {})
            position = self._latest_status.get("position", {})
            home = self._latest_status.get("home", {})
        return bool(
            isinstance(health, Mapping)
            and health.get("is_global_position_ok") is True
            and health.get("is_home_position_ok") is True
            and isinstance(position, Mapping)
            and position.get("latitude_deg") is not None
            and position.get("longitude_deg") is not None
            and isinstance(home, Mapping)
            and (
                home.get("absolute_altitude_m") is not None
                or (home.get("latitude_deg") is not None and home.get("longitude_deg") is not None)
            )
        )

    def land(self) -> None:
        """根据定位能力退出本地控制并同步发送降落命令。

        全球位置与 Home 可用时先正常退出 Offboard；仅有光流等本地定位时跳过会触发 Hold 的
        ``offboard.stop()``，直接请求 PX4 Land。命令被飞控接受时无返回值，底层拒绝或链路异常时
        抛出异常。
        """
        self._run(self._land())

    async def _land(self) -> None:
        system = self._require_system()
        if self._offboard_started and self._global_hold_is_available():
            await self._stop_offboard_if_started(system)
        elif self._offboard_started:
            logger.debug("PX4 仅本地定位可用，跳过 Offboard Stop 并直接请求 Land")
        try:
            await system.action.land()
        except Exception:
            logger.debug("PX4 MAVSDK Land 请求失败，尝试一次原始 MAVLink NAV_LAND", exc_info=True)
            await self._send_raw_land()
        self._offboard_started = False

    async def _send_raw_land(self) -> None:
        """通过动态系统/组件 ID 补发一次 MAVLink NAV_LAND；仅作为 Action Land 的有限回退。"""
        try:
            from mavsdk.mavlink_direct import MavlinkMessage
        except ImportError as exc:
            raise RuntimeError("LANDING_REJECTED: raw MAVLink compatibility is unavailable.") from exc
        direct = getattr(self._require_system(), "mavlink_direct", None)
        if direct is None:
            raise RuntimeError("LANDING_REJECTED: raw MAVLink compatibility is unavailable.")
        fields = {
            "target_system": self.target_system_id,
            "target_component": self.target_component_id,
            "command": 21,
            "confirmation": 0,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": None,
            "param5": None,
            "param6": None,
            "param7": None,
        }
        await direct.send_message(MavlinkMessage(
            "COMMAND_LONG",
            self.sender_system_id,
            self.sender_component_id,
            self.target_system_id,
            self.target_component_id,
            json.dumps(fields, separators=(",", ":")),
        ))

    def return_to_launch(self) -> None:
        """停止 Offboard 并发送返航命令；目标由 PX4 Home 点决定，成功时无返回值。"""
        self._run(self._return_to_launch())

    async def _return_to_launch(self) -> None:
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        await system.action.return_to_launch()

    def hold(self) -> None:
        """根据当前定位链路同步保持无人机位置；成功时无返回值。

        全球位置和 Home 健康时退出 Offboard 并使用 PX4 原生 Hold；仅本地位置健康时保持或
        启动 Offboard，并锁定最新 Local NED 位置和偏航角。两条安全链路都不可用时抛出
        ``FleetDockError``，不会盲目切换飞行模式。
        """
        self._run(self._hold())

    async def _hold(self) -> None:
        system = self._require_system()
        if self._global_hold_is_available():
            try:
                offboard_was_started = self._offboard_started
                await self._stop_offboard_if_started(system)
                if not offboard_was_started:
                    await system.action.hold()
            except Exception as exc:
                logger.debug("PX4 全球定位 Hold 请求失败", exc_info=True)
                raise FleetDockError(
                    ErrorCode.HOLD_STRATEGY_UNAVAILABLE,
                    "PX4 未能进入全球定位 Hold。",
                ) from exc
            logger.debug("PX4 使用全球定位原生 Hold")
            return
        if not self._local_position_control_is_ready():
            raise FleetDockError(
                ErrorCode.HOLD_STRATEGY_UNAVAILABLE,
                "当前没有可用于安全悬停的健康本地定位链路或全球定位/Home 链路。",
            )

        from mavsdk.offboard import PositionNedYaw

        current = self.get_status()
        local = current["local_position"]["position_m"]
        yaw_deg = float(current.get("attitude", {}).get("yaw_deg", 0.0))
        target = PositionNedYaw(
            float(local["x"]),
            float(local["y"]),
            float(local["z"]),
            yaw_deg,
        )
        try:
            if not self._offboard_started:
                for _ in range(10):
                    await system.offboard.set_position_ned(target)
                    await asyncio.sleep(0.1)
                await system.offboard.start()
                self._offboard_started = True
            else:
                await system.offboard.set_position_ned(target)
        except Exception as exc:
            logger.debug("PX4 本地 Offboard 位置保持失败", exc_info=True)
            raise FleetDockError(
                ErrorCode.HOLD_STRATEGY_UNAVAILABLE,
                "PX4 未能建立本地 Offboard 位置保持。",
            ) from exc
        logger.debug("PX4 使用本地 Offboard 位置保持")

    def goto_location(self, latitude_deg: float, longitude_deg: float, altitude_m: float, yaw_deg: float) -> None:
        """同步发送全球坐标导航目标。

        ``latitude_deg``、``longitude_deg`` 单位为度，``altitude_m`` 是 AMSL 高度（米），
        ``yaw_deg`` 是偏航角（度）；
        发送前会停止 Offboard 控制，成功时无返回值。
        """
        self._run(self._goto_location(latitude_deg, longitude_deg, altitude_m, yaw_deg))

    async def _goto_location(
        self,
        latitude_deg: float,
        longitude_deg: float,
        altitude_m: float,
        yaw_deg: float,
    ) -> None:
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        await system.action.goto_location(
            latitude_deg,
            longitude_deg,
            altitude_m,
            yaw_deg,
        )

    def set_velocity(
        self,
        x: float,
        y: float,
        z: float,
        speed: float,
        yaw_deg: Optional[float] = None,
    ) -> None:
        """按本地 NED 坐标系同步发送 Offboard 速度目标。

        ``x``、``y``、``z`` 是前/北、右/东、上方向分量，方法会归一化方向后乘以 ``speed`` 米/秒；
        向上的 ``z`` 会转换为 NED down 负值。``speed=0`` 始终发送零速度，负数或非有限值会抛出
        ``ValueError``。``yaw_deg=None`` 时保持最新遥测偏航角。首次调用启动 Offboard；无返回值。
        """
        values = (float(x), float(y), float(z), float(speed))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Velocity direction and speed must be finite.")
        if speed < 0.0:
            raise ValueError("Velocity speed cannot be negative.")
        direction_norm = math.sqrt(x * x + y * y + z * z)
        if speed > 0.0 and direction_norm == 0.0:
            raise ValueError("A non-zero velocity speed requires a direction.")
        if speed == 0.0:
            x = y = z = 0.0
        else:
            x, y, z = x / direction_norm, y / direction_norm, z / direction_norm
        self._run(self._set_velocity(x, y, z, speed, yaw_deg))

    def set_body_velocity(
        self,
        forward_m_s: float,
        right_m_s: float,
        up_m_s: float,
        yaw_rate_deg_s: float,
    ) -> None:
        """同步发送 PX4 机体系速度和偏航角速度目标。

        ``forward_m_s``、``right_m_s``、``up_m_s`` 单位为米/秒，沿机体前、右、上为正；
        ``yaw_rate_deg_s`` 单位为度/秒，向右为正。方法会按 MAVSDK ``VelocityBodyYawspeed``
        语义转换上方向并在必要时启动 Offboard，成功时无返回值。
        """
        values = tuple(float(value) for value in (
            forward_m_s,
            right_m_s,
            up_m_s,
            yaw_rate_deg_s,
        ))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Body velocity values must be finite.")
        self._run(self._set_body_velocity(*values))

    async def _set_body_velocity(
        self,
        forward_m_s: float,
        right_m_s: float,
        up_m_s: float,
        yaw_rate_deg_s: float,
    ) -> None:
        from mavsdk.offboard import VelocityBodyYawspeed

        system = self._require_system()
        await system.offboard.set_velocity_body(
            VelocityBodyYawspeed(forward_m_s, right_m_s, -up_m_s, yaw_rate_deg_s)
        )
        if not self._offboard_started:
            await system.offboard.start()
            self._offboard_started = True

    async def _set_velocity(
        self,
        x: float,
        y: float,
        z: float,
        speed: float,
        yaw_deg: Optional[float],
    ) -> None:
        from mavsdk.offboard import VelocityNedYaw

        system = self._require_system()
        scale = speed
        if yaw_deg is None:
            with self._status_lock:
                attitude = self._latest_status.get("attitude", {})
            yaw_deg = float(attitude.get("yaw_deg", 0.0))
        await system.offboard.set_velocity_ned(
            VelocityNedYaw(x * scale, y * scale, -z * scale, float(yaw_deg))
        )
        if not self._offboard_started:
            await system.offboard.start()
            self._offboard_started = True

    def set_position(
        self,
        x_m: float,
        y_m: float,
        z_m: float,
        yaw_deg: Optional[float] = None,
    ) -> None:
        """按本地坐标同步发送 Offboard 位置目标。

        ``x_m``、``y_m`` 分别是相对 EKF 原点的北向和东向位置，``z_m`` 是向上为正的高度，
        单位均为米；``yaw_deg`` 是可选绝对偏航角。方法会转换为 MAVSDK Local NED 位置目标，
        首次调用时启动 Offboard，成功时无返回值。
        """
        self._run(self._set_position(x_m, y_m, z_m, yaw_deg))

    async def _set_position(
        self,
        x_m: float,
        y_m: float,
        z_m: float,
        yaw_deg: Optional[float],
    ) -> None:
        from mavsdk.offboard import PositionNedYaw

        system = self._require_system()
        if yaw_deg is None:
            with self._status_lock:
                attitude = self._latest_status.get("attitude", {})
            yaw_deg = float(attitude.get("yaw_deg", 0.0))
        await system.offboard.set_position_ned(
            PositionNedYaw(float(x_m), float(y_m), -float(z_m), float(yaw_deg))
        )
        if not self._offboard_started:
            await system.offboard.start()
            self._offboard_started = True

    async def _stop_offboard_if_started(self, system: Any) -> None:
        if self._offboard_started:
            await system.offboard.stop()
            self._offboard_started = False

    def stop(self) -> None:
        """发送零速度目标停止平移；保持 Offboard 控制和当前偏航角，成功时无返回值。"""
        self.set_velocity(0.0, 0.0, 0.0, 0.0)

    def execute_mission(
        self,
        mission_items: list[Mapping[str, Any]],
        *,
        timeout_s: float,
        max_duration_s: Optional[float] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        guard_callback: Optional[Callable[[], None]] = None,
    ) -> bool:
        """上传并执行 PX4 Mission 航点。

        ``mission_items`` 是已转换的全球航点；``timeout_s`` 是异常超时秒数，
        ``max_duration_s`` 是可选正常执行上限。进度变化时同步调用
        ``progress_callback(current, total)``，等待期间周期调用 ``guard_callback``。完整飞完返回
        ``True``；达到时长上限后暂停 Mission、切换悬停并返回 ``False``。
        """
        return bool(self._run(
            self._execute_mission(
                mission_items,
                timeout_s=timeout_s,
                max_duration_s=max_duration_s,
                progress_callback=progress_callback,
                guard_callback=guard_callback,
            )
        ))

    def cancel_mission(self) -> None:
        """暂停当前 PX4 Mission 并切换 Hold。

        调用时同步等待 MAVSDK ``pause_mission`` 和 ``hold`` 完成；没有活动任务时由 MAVSDK 保持幂等语义。
        该方法无返回值，底层失败时抛出异常并由适配器转换为标准响应或日志。
        """
        self._run(self._cancel_mission())

    async def _cancel_mission(self) -> None:
        system = self._require_system()
        pause_mission = getattr(system.mission, "pause_mission", None)
        if callable(pause_mission):
            await pause_mission()
        await system.action.hold()

    async def _execute_mission(
        self,
        mission_items: list[Mapping[str, Any]],
        *,
        timeout_s: float,
        max_duration_s: Optional[float],
        progress_callback: Optional[Callable[[int, int], None]],
        guard_callback: Optional[Callable[[], None]],
    ) -> bool:
        if not mission_items:
            raise ValueError("mission_items must not be empty.")
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        from mavsdk.mission import MissionPlan

        items = [self._build_mission_item(item) for item in mission_items]
        plan = MissionPlan(items)
        clear_mission = getattr(system.mission, "clear_mission", None)
        if callable(clear_mission):
            await clear_mission()
        await system.mission.upload_mission(plan)
        await system.mission.start_mission()
        return await self._wait_mission_complete(
            system,
            total=len(items),
            timeout_s=timeout_s,
            max_duration_s=max_duration_s,
            progress_callback=progress_callback,
            guard_callback=guard_callback,
        )

    @staticmethod
    def _build_mission_item(item: Mapping[str, Any]) -> Any:
        from mavsdk.mission import MissionItem

        latitude_deg = float(item["latitude_deg"])
        longitude_deg = float(item["longitude_deg"])
        relative_altitude_m = float(item["relative_altitude_m"])
        speed_m_s = float(item.get("speed_m_s", 0.0))
        is_fly_through = bool(item.get("is_fly_through", True))
        gimbal_pitch_deg = float("nan")
        gimbal_yaw_deg = float("nan")
        camera_action = getattr(MissionItem.CameraAction, "NONE", 0)
        loiter_time_s = float(item.get("loiter_time_s", 0.0))
        camera_photo_interval_s = 0.0
        acceptance_radius_m = float(item.get("acceptance_radius_m", 0.0))
        yaw_deg = float(item.get("yaw_deg", float("nan")))
        camera_photo_distance_m = 0.0
        vehicle_action = getattr(MissionItem.VehicleAction, "NONE", 0)
        try:
            return MissionItem(
                latitude_deg,
                longitude_deg,
                relative_altitude_m,
                speed_m_s,
                is_fly_through,
                gimbal_pitch_deg,
                gimbal_yaw_deg,
                camera_action,
                loiter_time_s,
                camera_photo_interval_s,
                acceptance_radius_m,
                yaw_deg,
                camera_photo_distance_m,
                vehicle_action,
            )
        except TypeError:
            return MissionItem(
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                relative_altitude_m=relative_altitude_m,
                speed_m_s=speed_m_s,
                is_fly_through=is_fly_through,
                gimbal_pitch_deg=gimbal_pitch_deg,
                gimbal_yaw_deg=gimbal_yaw_deg,
                camera_action=camera_action,
                loiter_time_s=loiter_time_s,
                camera_photo_interval_s=camera_photo_interval_s,
                acceptance_radius_m=acceptance_radius_m,
                yaw_deg=yaw_deg,
                camera_photo_distance_m=camera_photo_distance_m,
                vehicle_action=vehicle_action,
            )

    async def _wait_mission_complete(
        self,
        system: Any,
        *,
        total: int,
        timeout_s: float,
        max_duration_s: Optional[float],
        progress_callback: Optional[Callable[[int, int], None]],
        guard_callback: Optional[Callable[[], None]],
    ) -> bool:
        loop = asyncio.get_running_loop()
        timeout_deadline = loop.time() + timeout_s
        duration_deadline = loop.time() + max_duration_s if max_duration_s is not None else None
        last_current = -1
        progress_stream = system.mission.mission_progress().__aiter__()
        progress_task = asyncio.create_task(progress_stream.__anext__())
        try:
            while True:
                now_s = loop.time()
                if duration_deadline is not None and now_s >= duration_deadline:
                    pause_mission = getattr(system.mission, "pause_mission", None)
                    if callable(pause_mission):
                        await pause_mission()
                    await system.action.hold()
                    return False
                remaining_timeout_s = timeout_deadline - now_s
                if remaining_timeout_s <= 0:
                    raise TimeoutError("PX4 mission did not complete before timeout.")
                remaining_duration_s = (
                    duration_deadline - now_s if duration_deadline is not None else remaining_timeout_s
                )
                wait_s = min(0.2, remaining_timeout_s, remaining_duration_s)
                done, _ = await asyncio.wait({progress_task}, timeout=max(0.0, wait_s))
                if guard_callback is not None:
                    guard_callback()
                if not done:
                    continue
                try:
                    progress = progress_task.result()
                except StopAsyncIteration as exc:
                    raise RuntimeError("PX4 mission progress stream ended before completion.") from exc
                current = int(getattr(progress, "current", 0))
                mission_total = int(getattr(progress, "total", total))
                if current != last_current and progress_callback is not None:
                    progress_callback(current, mission_total)
                    last_current = current
                if current >= mission_total:
                    return True
                progress_task = asyncio.create_task(progress_stream.__anext__())
        finally:
            if not progress_task.done():
                progress_task.cancel()

    def emergency_stop(self) -> None:
        """同步发送 PX4 kill 并结束本地 Offboard 状态；仅用于紧急停机，成功时无返回值。"""
        self._run(self._require_system().action.kill())
        self._offboard_started = False

    def get_status(self) -> Dict[str, Any]:
        """返回当前连接、Offboard 和最新遥测状态快照；返回字典与内部缓存相互独立。"""
        with self._status_lock:
            telemetry = dict(self._latest_status)
            updated_at = dict(self._status_updated_at)
        return {
            "endpoint": self.endpoint,
            "is_connected": self._connected,
            "offboard_started": self._offboard_started,
            "telemetry_updated_at_s": updated_at,
            **telemetry,
        }

    def start_telemetry(self, callback: Callable[[str, Mapping[str, Any], Any], None]) -> None:
        """启动后台遥测消费任务。

        ``callback`` 在独立的有序分发线程中接收流名称、可序列化标准字段和 MAVSDK 原生对象。
        连接后只常驻电池、解锁、空中、着陆、模式和健康等低带宽安全状态；位置、速度、姿态和
        Home 由任务、事件或状态会话通过 ``acquire_telemetry()`` 按需启停。同一路原生流会在多个
        使用者之间复用。接收协程先覆盖最新状态，再把通知交给有界队列；过载时丢弃最旧通知，
        不积压历史状态。已启动时不重复创建任务；方法无返回值，资源须通过 ``stop_telemetry`` 或
        ``close`` 释放。
        """
        if self._telemetry_future is not None and not self._telemetry_future.done():
            return
        self._telemetry_callback = callback
        with self._telemetry_demand_lock:
            self._telemetry_demands["baseline"] = self._BASELINE_TELEMETRY_STREAMS
        loop = self._ensure_event_loop()
        self._telemetry_future = asyncio.run_coroutine_threadsafe(self._run_telemetry(callback), loop)
        logger.debug("PX4 后台遥测订阅已启动")

    def acquire_telemetry(
        self,
        stream_names: tuple[str, ...] | frozenset[str] | set[str],
        *,
        owner: str = "consumer",
    ) -> Callable[[], None]:
        """申请一组 PX4 控制遥测并返回幂等释放函数。

        ``stream_names`` 使用 FleetDock 内部标准流名；多个任务、事件订阅和 WS 会话申请同一路时只
        建立一个 MAVSDK 原生订阅。``owner`` 仅用于 DEBUG 诊断，不得包含凭据。申请可以发生在连接
        前，遥测启动后会自动生效；释放最后一个需求后停止非基础流。调用者必须在 ``finally`` 中
        执行返回函数，业务 Demo 不需要直接使用本内部能力。
        """
        selected = frozenset(str(name) for name in stream_names)
        unknown = selected - self._TELEMETRY_STREAMS
        if unknown:
            raise LookupError(f"Unknown PX4 telemetry streams: {', '.join(sorted(unknown))}")
        token = f"{owner}:{uuid4().hex}"
        with self._telemetry_demand_lock:
            self._telemetry_demands[token] = selected
        self._schedule_telemetry_reconcile()
        logger.debug("PX4 遥测需求已申请: owner=%s streams=%s", owner, ",".join(sorted(selected)))
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            with self._telemetry_demand_lock:
                self._telemetry_demands.pop(token, None)
            self._schedule_telemetry_reconcile()
            logger.debug("PX4 遥测需求已释放: owner=%s streams=%s", owner, ",".join(sorted(selected)))

        return release

    def available_sensor_streams(self) -> tuple[str, ...]:
        """返回当前 MAVSDK 版本可用于标准传感器转换的 PX4 流名称。

        该结果表示客户端具备订阅 API，不保证飞控或载荷一定持续发送对应消息；调用方应结合
        ``SensorStreamStatus`` 的最近接收时间判断运行期数据是否有效。
        """
        streams = ["imu", "attitude", "magnetometer", "gnss", "barometer", "distance", "airspeed"]
        if self._camera_stream_uri_override:
            streams.append("camera")
        return tuple(streams)

    def camera_stream_uri(self) -> str:
        """同步等待 PX4 MAVLink 相机组件发布视频流地址并返回。

        返回值通常是 RTSP 等视频 URI；方法最多等待客户端 ``timeout_s`` 秒，不记录 URI，避免把其中
        可能包含的凭据写入日志。飞控未连接、相机未发布 ``VIDEO_STREAM_INFORMATION`` 或 URI 为空时
        抛出 ``RuntimeError``。
        """
        if self._camera_stream_uri_override:
            return self._camera_stream_uri_override
        try:
            uri = str(self._run(self._camera_stream_uri()))
            self._camera_stream_uri_override = uri
            return uri
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("PX4 camera video stream information is unavailable.") from exc

    async def _camera_stream_uri(self) -> str:
        camera = self._require_system().camera

        async def wait_for_uri() -> str:
            async for update in camera.video_stream_info():
                info = getattr(update, "video_stream_info", None)
                settings = getattr(info, "settings", None)
                uri = str(getattr(settings, "uri", "")).strip()
                if uri:
                    return uri
            raise RuntimeError("PX4 camera video stream ended before publishing a URI.")

        try:
            return await asyncio.wait_for(wait_for_uri(), timeout=self.timeout_s)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("PX4 camera did not publish a video stream URI before timeout.") from exc

    def subscribe_sensor(self, stream_name: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        """订阅一个 PX4 原生传感器流并返回幂等取消函数。

        ``stream_name`` 来自 ``available_sensor_streams()``；``callback`` 在 MAVSDK 事件循环线程接收
        原生对象，转换和用户回调由上层 SensorBackend/SensorHub 负责。融合 IMU 与姿态流会在消费前
        请求 20 Hz 输出；飞控不支持频率设置时保留其默认输出并记录 DEBUG 日志。设备须先成功连接。
        """
        if stream_name not in self.available_sensor_streams():
            raise LookupError(f"PX4 sensor stream is not available: {stream_name}")
        system = self._require_system()
        telemetry = system.telemetry
        factory_names = {
            "imu": "imu",
            "attitude": "attitude_euler",
            "magnetometer": "imu",
            "gnss": "raw_gps",
            "barometer": "scaled_pressure",
            "distance": "distance_sensor",
            "airspeed": "fixedwing_metrics",
        }
        stream_factory = (
            system.camera.video_stream_info
            if stream_name == "camera"
            else getattr(telemetry, factory_names[stream_name])
        )
        rate_requests = {
            "imu": (getattr(telemetry, "set_rate_imu", None), 20.0),
            "attitude": (getattr(telemetry, "set_rate_attitude_euler", None), 20.0),
            "magnetometer": (getattr(telemetry, "set_rate_imu", None), 20.0),
        }
        rate_setter, rate_hz = rate_requests.get(stream_name, (None, None))
        loop = self._ensure_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._consume_sensor_stream(
                stream_name,
                stream_factory,
                callback,
                rate_setter=rate_setter,
                rate_hz=rate_hz,
            ),
            loop,
        )
        with self._loop_lock:
            self._sensor_futures.add(future)
        closed = False

        def unsubscribe() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            with self._loop_lock:
                self._sensor_futures.discard(future)
            if not future.done():
                future.cancel()

        return unsubscribe

    async def _consume_sensor_stream(
        self,
        stream_name: str,
        stream_factory: Callable[[], Any],
        callback: Callable[[Any], None],
        *,
        rate_setter: Optional[Callable[[float], Any]] = None,
        rate_hz: Optional[float] = None,
    ) -> None:
        try:
            if rate_setter is not None and rate_hz is not None:
                try:
                    await rate_setter(rate_hz)
                    logger.debug("PX4 传感器输出频率已请求: stream=%s rate_hz=%.1f", stream_name, rate_hz)
                except Exception:
                    logger.debug(
                        "PX4 传感器输出频率请求失败，继续使用飞控默认频率: stream=%s rate_hz=%.1f",
                        stream_name,
                        rate_hz,
                        exc_info=True,
                    )
            async for value in stream_factory():
                callback(value)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("PX4 传感器流异常: stream=%s", stream_name, exc_info=True)

    async def _run_telemetry(self, callback: Callable[[str, Mapping[str, Any], Any], None]) -> None:
        callback_queue: asyncio.Queue[tuple[str, Mapping[str, Any], Any]] = asyncio.Queue(maxsize=128)
        self._telemetry_callback_queue = callback_queue
        self._telemetry_dispatcher = asyncio.create_task(
            self._dispatch_telemetry_callbacks(callback_queue, callback)
        )
        try:
            await self._reconcile_telemetry_streams()
            await asyncio.Future()
        finally:
            tasks = tuple(self._telemetry_tasks.values())
            self._telemetry_tasks.clear()
            handles = tuple(self._telemetry_release_handles.values())
            self._telemetry_release_handles.clear()
            for handle in handles:
                handle.cancel()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            dispatcher = self._telemetry_dispatcher
            self._telemetry_dispatcher = None
            self._telemetry_callback_queue = None
            if dispatcher is not None:
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)

    def _schedule_telemetry_reconcile(self) -> None:
        future = self._telemetry_future
        loop = self._loop
        if future is None or future.done() or loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._reconcile_telemetry_streams(), loop)

    def _desired_telemetry_streams(self) -> frozenset[str]:
        with self._telemetry_demand_lock:
            return frozenset().union(*self._telemetry_demands.values()) if self._telemetry_demands else frozenset()

    async def _reconcile_telemetry_streams(self) -> None:
        desired = self._desired_telemetry_streams()
        for stream_name in tuple(self._telemetry_tasks):
            if stream_name not in desired:
                if stream_name not in self._telemetry_release_handles:
                    self._telemetry_release_handles[stream_name] = asyncio.get_running_loop().call_later(
                        2.0,
                        self._stop_telemetry_stream_if_unused,
                        stream_name,
                    )
            else:
                handle = self._telemetry_release_handles.pop(stream_name, None)
                if handle is not None:
                    handle.cancel()
        queue = self._telemetry_callback_queue
        if queue is None:
            return
        for stream_name in sorted(desired):
            current = self._telemetry_tasks.get(stream_name)
            if current is not None and not current.done():
                continue
            stream_factory, mapper, rate_setter, rate_hz = self._telemetry_stream_definition(stream_name)
            task = asyncio.create_task(
                self._consume_telemetry(
                    stream_name,
                    stream_factory,
                    self._enqueue_telemetry_callback,
                    mapper,
                    rate_setter=rate_setter,
                    rate_hz=rate_hz,
                )
            )
            self._telemetry_tasks[stream_name] = task
            logger.debug("PX4 按需遥测流已启动: stream=%s rate_hz=%s", stream_name, rate_hz)

    def _stop_telemetry_stream_if_unused(self, stream_name: str) -> None:
        self._telemetry_release_handles.pop(stream_name, None)
        if stream_name in self._desired_telemetry_streams():
            return
        task = self._telemetry_tasks.pop(stream_name, None)
        if task is not None:
            task.cancel()
            logger.debug("PX4 按需遥测流已停止: stream=%s", stream_name)

    def _enqueue_telemetry_callback(
        self,
        stream_name: str,
        data: Mapping[str, Any],
        native_data: Any,
    ) -> None:
        callback_queue = self._telemetry_callback_queue
        if callback_queue is None:
            return
        if callback_queue.full():
            try:
                callback_queue.get_nowait()
                callback_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self._telemetry_dropped_notifications += 1
            logger.debug("PX4 遥测事件分发队列已满，丢弃最旧事件: limit=%d", callback_queue.maxsize)
        callback_queue.put_nowait((stream_name, data, native_data))
        self._telemetry_queue_high_watermark = max(
            self._telemetry_queue_high_watermark,
            callback_queue.qsize(),
        )

    def telemetry_diagnostics(self) -> Dict[str, Any]:
        """返回按需遥测、分发积压和丢弃计数的可序列化诊断快照。

        返回的流名称使用 FleetDock 内部标准名，不包含连接地址、凭据或 MAVSDK 原生对象；计数覆盖
        当前客户端生命周期，可用于判断低带宽数传是否发生降频或消费者背压。
        """
        callback_queue = self._telemetry_callback_queue
        return {
            "link_profile": self.link_profile,
            "requested_streams": tuple(sorted(self._desired_telemetry_streams())),
            "active_streams": tuple(sorted(self._desired_telemetry_streams())) if self._telemetry_future is not None else (),
            "callback_pending": callback_queue.qsize() if callback_queue is not None else 0,
            "callback_capacity": callback_queue.maxsize if callback_queue is not None else 128,
            "callback_high_watermark": self._telemetry_queue_high_watermark,
            "dropped_notifications": self._telemetry_dropped_notifications,
        }

    def _telemetry_stream_definition(
        self,
        stream_name: str,
    ) -> tuple[Callable[[], Any], Callable[[Any], Dict[str, Any]], Optional[Callable[[float], Any]], Optional[float]]:
        telemetry = self._require_system().telemetry
        definitions: Dict[str, tuple[str, Callable[[Any], Dict[str, Any]], Optional[str]]] = {
            "position": ("position", self._map_position, "set_rate_position"),
            "local_position": ("position_velocity_ned", self._map_position_velocity_ned, "set_rate_position_velocity_ned"),
            "home": ("home", self._map_home, None),
            "velocity_ned": ("velocity_ned", self._map_velocity_ned, "set_rate_velocity_ned"),
            "attitude": ("attitude_euler", self._map_attitude, "set_rate_attitude_euler"),
            "battery": ("battery", self._map_battery, "set_rate_battery"),
            "armed": ("armed", lambda value: {"armed": bool(value)}, None),
            "in_air": ("in_air", lambda value: {"in_air": bool(value)}, None),
            "landed_state": ("landed_state", self._map_landed_state, None),
            "flight_mode": ("flight_mode", self._map_flight_mode, None),
            "health": ("health", self._map_health, None),
        }
        factory_name, mapper, setter_name = definitions[stream_name]
        rate_setter = getattr(telemetry, setter_name, None) if setter_name is not None else None
        rates = {
            "simulation": {"position": 5.0, "local_position": 10.0, "velocity_ned": 10.0, "attitude": 10.0, "battery": 1.0},
            "standard": {"position": 1.0, "local_position": 5.0, "velocity_ned": 5.0, "attitude": 5.0, "battery": 1.0},
            "low_bandwidth": {"position": 0.5, "local_position": 2.0, "velocity_ned": 2.0, "attitude": 2.0, "battery": 0.2},
        }
        return getattr(telemetry, factory_name), mapper, rate_setter if callable(rate_setter) else None, rates[self.link_profile].get(stream_name)

    @staticmethod
    async def _dispatch_telemetry_callbacks(
        callback_queue: asyncio.Queue[tuple[str, Mapping[str, Any], Any]],
        callback: Callable[[str, Mapping[str, Any], Any], None],
    ) -> None:
        while True:
            stream_name, data, native_data = await callback_queue.get()
            try:
                await asyncio.to_thread(callback, stream_name, data, native_data)
            except Exception:
                logger.debug("PX4 遥测事件分发失败: stream=%s", stream_name, exc_info=True)
            finally:
                callback_queue.task_done()

    async def _consume_telemetry(
        self,
        stream_name: str,
        stream_factory: Callable[[], Any],
        callback: Callable[[str, Mapping[str, Any], Any], None],
        mapper: Callable[[Any], Dict[str, Any]],
        *,
        rate_setter: Optional[Callable[[float], Any]] = None,
        rate_hz: Optional[float] = None,
    ) -> None:
        try:
            if rate_setter is not None and rate_hz is not None:
                try:
                    await rate_setter(rate_hz)
                    logger.debug("PX4 控制遥测频率已请求: stream=%s rate_hz=%.1f", stream_name, rate_hz)
                except Exception:
                    logger.debug(
                        "PX4 控制遥测频率请求失败，继续使用飞控默认频率: stream=%s rate_hz=%.1f",
                        stream_name,
                        rate_hz,
                        exc_info=True,
                    )
                    callback(
                        "telemetry_error",
                        {"stream": stream_name, "error": "rate_configuration_failed"},
                        None,
                    )
            async for value in stream_factory():
                data = mapper(value)
                with self._status_lock:
                    self._latest_status[stream_name] = dict(data)
                    self._status_updated_at[stream_name] = time.monotonic()
                    if stream_name == "position":
                        self._latest_status.update(data)
                callback(stream_name, data, value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("PX4 遥测流异常: stream=%s error=%s", stream_name, exc, exc_info=True)
            callback("telemetry_error", {"stream": stream_name, "error": str(exc)}, exc)

    @staticmethod
    def _map_position(position: Any) -> Dict[str, Any]:
        return {
            "latitude_deg": float(position.latitude_deg),
            "longitude_deg": float(position.longitude_deg),
            "absolute_altitude_m": float(position.absolute_altitude_m),
            "relative_altitude_m": float(position.relative_altitude_m),
        }

    @staticmethod
    def _map_velocity_ned(velocity: Any) -> Dict[str, Any]:
        return {
            "north_m_s": float(velocity.north_m_s),
            "east_m_s": float(velocity.east_m_s),
            "down_m_s": float(velocity.down_m_s),
        }

    @staticmethod
    def _map_position_velocity_ned(position_velocity: Any) -> Dict[str, Any]:
        position = position_velocity.position
        data = {
            "frame": CoordinateFrame.LOCAL_NED.value,
            "position_m": {
                "x": float(position.north_m),
                "y": float(position.east_m),
                "z": float(position.down_m),
            },
        }
        velocity = getattr(position_velocity, "velocity", None)
        if velocity is not None:
            data["velocity_m_s"] = {
                "x": float(velocity.north_m_s),
                "y": float(velocity.east_m_s),
                "z": float(velocity.down_m_s),
            }
        return data

    @staticmethod
    def _map_home(home: Any) -> Dict[str, Any]:
        return {
            "latitude_deg": float(home.latitude_deg),
            "longitude_deg": float(home.longitude_deg),
            "absolute_altitude_m": float(home.absolute_altitude_m),
        }

    @staticmethod
    def _map_attitude(attitude: Any) -> Dict[str, Any]:
        return {
            "roll_deg": float(attitude.roll_deg),
            "pitch_deg": float(attitude.pitch_deg),
            "yaw_deg": float(attitude.yaw_deg),
        }

    @staticmethod
    def _map_battery(battery: Any) -> Dict[str, Any]:
        remaining_fraction = float(battery.remaining_percent)
        # MAVSDK 的不同遥测来源可能返回 0~1 比例或 0~100 百分数，统一收敛为 FleetDock 的 0~1 契约。
        if remaining_fraction > 1.0:
            remaining_fraction /= 100.0
        data = {"remaining_fraction": remaining_fraction}
        if getattr(battery, "voltage_v", None) is not None:
            data["voltage_v"] = float(battery.voltage_v)
        return data

    @staticmethod
    def _map_flight_mode(flight_mode: Any) -> Dict[str, Any]:
        value = getattr(flight_mode, "name", None) or getattr(flight_mode, "value", None) or str(flight_mode)
        return {"flight_mode": str(value).lower()}

    @staticmethod
    def _map_landed_state(landed_state: Any) -> Dict[str, Any]:
        value = getattr(landed_state, "name", None) or getattr(landed_state, "value", None) or str(landed_state)
        return {"landed_state": str(value).lower()}

    @staticmethod
    def _map_health(health: Any) -> Dict[str, Any]:
        names = (
            "is_gyrometer_calibration_ok",
            "is_accelerometer_calibration_ok",
            "is_magnetometer_calibration_ok",
            "is_local_position_ok",
            "is_global_position_ok",
            "is_home_position_ok",
            "is_armable",
        )
        return {name: bool(getattr(health, name)) for name in names if hasattr(health, name)}

    def stop_telemetry(self) -> None:
        """取消后台遥测任务并等待取消信号进入事件循环；可重复调用且无返回值。"""
        future = self._telemetry_future
        self._telemetry_future = None
        self._telemetry_callback = None
        with self._telemetry_demand_lock:
            self._telemetry_demands.clear()
        if future is not None and not future.done():
            future.cancel()
            loop = self._loop
            if loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=1.0)
                except Exception:
                    logger.debug("等待 PX4 遥测任务停止时出现异常", exc_info=True)
        logger.debug("PX4 后台遥测订阅已停止")

    def close(self) -> None:
        """关闭遥测、MAVSDK 异步资源和事件循环线程。

        该方法可在 ``finally`` 中重复调用；完成后客户端恢复为未连接状态且无返回值。
        """
        self.stop_telemetry()
        with self._loop_lock:
            sensor_futures = tuple(self._sensor_futures)
            self._sensor_futures.clear()
        for future in sensor_futures:
            if not future.done():
                future.cancel()
        loop = self._loop
        worker = self._loop_worker
        if loop is not None and loop.is_running() and self._system is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_async_resources(), loop).result(timeout=5.0)
            except Exception:
                logger.debug("关闭 MAVSDK 系统资源时出现异常", exc_info=True)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if worker is not None:
            worker.join(timeout_s=2.0)
        if loop is not None and not loop.is_running() and not loop.is_closed():
            loop.close()
        self._loop = None
        self._loop_worker = None
        self._system = None
        self._connected = False
        logger.debug("PX4 MAVSDK 客户端已关闭")

    async def _shutdown_async_resources(self) -> None:
        system = self._system
        if system is not None and self._connected:
            try:
                await self._stop_offboard_if_started(system)
            except Exception:
                logger.debug("关闭 PX4 客户端时停止 Offboard 失败", exc_info=True)
        await self._close_system()
        current_task = asyncio.current_task()
        pending_tasks = [task for task in asyncio.all_tasks() if task is not current_task and not task.done()]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await asyncio.get_running_loop().shutdown_default_executor()
        await asyncio.get_running_loop().shutdown_asyncgens()
        await asyncio.sleep(0)

    async def _close_system(self) -> None:
        system = self._system
        if system is None:
            return
        channels = {}
        for plugin in getattr(system, "_plugins", {}).values():
            stub = getattr(plugin, "_stub", None)
            for method in vars(stub).values() if stub is not None else ():
                for reference in getattr(method, "_references", ()):
                    if callable(getattr(reference, "close", None)):
                        channels[id(reference)] = reference
        for channel in channels.values():
            close = getattr(channel, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        stop_server = getattr(system, "_stop_mavsdk_server", None)
        server_process = getattr(system, "_server_process", None)
        if callable(stop_server):
            stop_server()
        wait_for_server = getattr(server_process, "wait", None)
        if callable(wait_for_server):
            try:
                wait_for_server(timeout=2.0)
            except Exception:
                logger.debug("等待 MAVSDK server 进程退出时出现异常", exc_info=True)

    def _require_system(self) -> Any:
        if self._system is None or not self._connected:
            raise RuntimeError("MAVSDK connection has not been established.")
        return self._system

    def _run(self, coroutine: Any) -> Any:
        loop = self._ensure_event_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        command_timeout_s = getattr(self._command_context, "timeout_s", None)
        wait_timeout_s = float(command_timeout_s) if command_timeout_s is not None else self.timeout_s + 5.0
        try:
            return future.result(timeout=wait_timeout_s)
        except TimeoutError as exc:
            if future.done():
                raise
            future.cancel()
            # 让事件循环至少运行一轮以处理协程取消，避免客户端随后关闭时遗留 pending task。
            cancellation_barrier = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop)
            try:
                cancellation_barrier.result(timeout=min(wait_timeout_s, 1.0))
            except BaseException:
                logger.debug("PX4 超时协程取消确认未完成", exc_info=True)
            logger.debug("PX4 标准命令等待超时: timeout_s=%.3f", wait_timeout_s)
            raise TimeoutError(f"PX4 command did not finish within {wait_timeout_s:.3f} seconds.") from exc

    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                loop = self._loop

                def request_loop_stop() -> None:
                    if loop.is_running():
                        loop.call_soon_threadsafe(loop.stop)

                self._loop_worker = ManagedWorker(
                    f"fleetdock-mavsdk-{self.mavsdk_server_port}",
                    lambda: self._run_event_loop(loop),
                    request_stop=request_loop_stop,
                ).start()
            return self._loop

    def worker_status(self) -> Optional[WorkerStatus]:
        """返回 MAVSDK 事件循环线程状态；尚未发起连接或命令时返回 ``None``。"""
        worker = self._loop_worker
        return worker.status() if worker is not None else None

    @staticmethod
    def _run_event_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()
