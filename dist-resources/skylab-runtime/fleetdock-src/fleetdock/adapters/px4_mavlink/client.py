"""PX4 MAVSDK 同步客户端门面。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from threading import Lock
from typing import Any, Callable, Dict, Mapping, Optional

from fleetdock.events import CoordinateFrame
from fleetdock.runtime import ManagedWorker, WorkerStatus
from fleetdock.sensors import SensorSourceKind

logger = logging.getLogger(__name__)

class MavsdkClient:
    """单个 PX4 连接的同步 MAVSDK 门面。

    每个实例会占用一个本地 MAVSDK gRPC server 端口；未显式指定时从 50051 开始在当前
    Python 进程内递增分配，避免同进程控制多架 PX4 时端口冲突。
    """

    _server_port_lock = Lock()
    _next_server_port = 50051

    def __init__(
        self,
        endpoint: str,
        timeout_s: float = 10.0,
        mavsdk_server_port: Optional[int] = None,
        camera_stream_uri: Optional[str] = None,
        camera_stream_source: SensorSourceKind = SensorSourceKind.UNKNOWN,
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
        self._system: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_worker: Optional[ManagedWorker] = None
        self._loop_lock = Lock()
        self._connected = False
        self._offboard_started = False
        self._telemetry_future: Optional[Any] = None
        self._sensor_futures: set[Any] = set()
        self._status_lock = Lock()
        self._latest_status: Dict[str, Any] = {}

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
        self._system = (
            System(port=self.mavsdk_server_port)
            if "port" in system_parameters
            else System()
        )
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

    # [UAV-IDE MODIFY] Expose read-only PX4 identity for the IDE's temporary
    # hardware probe without sending any flight-control command.
    def get_system_identity(self, timeout_s: float = 2.0) -> Dict[str, Any]:
        """Return MAVLink IDs and MAVSDK product/version information."""
        return self._run(self._get_system_identity(timeout_s))

    async def _get_system_identity(self, timeout_s: float) -> Dict[str, Any]:
        system = self._require_system()
        identity: Dict[str, Any] = {}

        try:
            async def wait_heartbeat() -> Any:
                async for message in system.mavlink_direct.message("HEARTBEAT"):
                    return message

            heartbeat = await asyncio.wait_for(
                wait_heartbeat(), timeout=max(0.1, float(timeout_s))
            )
            identity["system_id"] = int(heartbeat.system_id)
            identity["component_id"] = int(heartbeat.component_id)
        except Exception:
            logger.debug("读取 PX4 HEARTBEAT 标识失败", exc_info=True)

        async def optional_info(call: Callable[[], Any]) -> Any:
            try:
                return await asyncio.wait_for(
                    call(), timeout=max(0.1, float(timeout_s))
                )
            except Exception:
                return None

        version, identification, product = await asyncio.gather(
            optional_info(system.info.get_version),
            optional_info(system.info.get_identification),
            optional_info(system.info.get_product),
        )
        if version is not None:
            identity["firmware_version"] = ".".join(
                str(int(getattr(version, field, 0)))
                for field in ("flight_sw_major", "flight_sw_minor", "flight_sw_patch")
            )
            identity["firmware_git_hash"] = str(
                getattr(version, "flight_sw_git_hash", "") or ""
            )
        if identification is not None:
            identity["hardware_uid"] = str(
                getattr(identification, "hardware_uid", "") or ""
            )
            identity["legacy_uid"] = str(
                getattr(identification, "legacy_uid", "") or ""
            )
        if product is not None:
            identity["vendor_name"] = str(
                getattr(product, "vendor_name", "") or ""
            )
            identity["product_name"] = str(
                getattr(product, "product_name", "") or ""
            )
            identity["vendor_id"] = int(getattr(product, "vendor_id", 0) or 0)
            identity["product_id"] = int(getattr(product, "product_id", 0) or 0)
        return identity

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

    def land(self) -> None:
        """停止已启动的 Offboard 控制并同步发送降落命令；成功时无返回值。"""
        self._run(self._land())

    async def _land(self) -> None:
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        await system.action.land()

    def return_to_launch(self) -> None:
        """停止 Offboard 并发送返航命令；目标由 PX4 Home 点决定，成功时无返回值。"""
        self._run(self._return_to_launch())

    async def _return_to_launch(self) -> None:
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        await system.action.return_to_launch()

    def hold(self) -> None:
        """停止 Offboard 控制并同步请求 PX4 保持当前位置；成功时无返回值。"""
        self._run(self._hold())

    async def _hold(self) -> None:
        system = self._require_system()
        await self._stop_offboard_if_started(system)
        await system.action.hold()

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
        return {
            "endpoint": self.endpoint,
            "is_connected": self._connected,
            "offboard_started": self._offboard_started,
            **telemetry,
        }

    def start_telemetry(self, callback: Callable[[str, Mapping[str, Any], Any], None]) -> None:
        """启动后台遥测消费任务。

        ``callback`` 在事件循环线程中接收流名称、可序列化标准字段和 MAVSDK 原生对象；
        已启动时不重复创建任务。该方法立即返回，资源须通过 ``stop_telemetry`` 或 ``close`` 释放。
        """
        if self._telemetry_future is not None and not self._telemetry_future.done():
            return
        loop = self._ensure_event_loop()
        self._telemetry_future = asyncio.run_coroutine_threadsafe(self._monitor_telemetry(callback), loop)
        logger.debug("PX4 后台遥测订阅已启动")

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

    async def _monitor_telemetry(self, callback: Callable[[str, Mapping[str, Any], Any], None]) -> None:
        telemetry = self._require_system().telemetry
        consumers = (
            self._consume_telemetry("position", telemetry.position, callback, self._map_position),
            self._consume_telemetry(
                "local_position",
                telemetry.position_velocity_ned,
                callback,
                self._map_position_velocity_ned,
            ),
            self._consume_telemetry("home", telemetry.home, callback, self._map_home),
            self._consume_telemetry("velocity_ned", telemetry.velocity_ned, callback, self._map_velocity_ned),
            self._consume_telemetry("attitude", telemetry.attitude_euler, callback, self._map_attitude),
            self._consume_telemetry("battery", telemetry.battery, callback, self._map_battery),
            self._consume_telemetry("armed", telemetry.armed, callback, lambda value: {"armed": bool(value)}),
            self._consume_telemetry("in_air", telemetry.in_air, callback, lambda value: {"in_air": bool(value)}),
            self._consume_telemetry("landed_state", telemetry.landed_state, callback, self._map_landed_state),
            self._consume_telemetry("flight_mode", telemetry.flight_mode, callback, self._map_flight_mode),
            self._consume_telemetry("health", telemetry.health, callback, self._map_health),
        )
        await asyncio.gather(*consumers)

    async def _consume_telemetry(
        self,
        stream_name: str,
        stream_factory: Callable[[], Any],
        callback: Callable[[str, Mapping[str, Any], Any], None],
        mapper: Callable[[Any], Dict[str, Any]],
    ) -> None:
        try:
            async for value in stream_factory():
                data = mapper(value)
                with self._status_lock:
                    self._latest_status[stream_name] = dict(data)
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
        return {
            "frame": CoordinateFrame.LOCAL_NED.value,
            "position_m": {
                "x": float(position.north_m),
                "y": float(position.east_m),
                "z": float(position.down_m),
            },
        }

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
        data = {"remaining_fraction": float(battery.remaining_percent)}
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
        return future.result(timeout=self.timeout_s + 5.0)

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
