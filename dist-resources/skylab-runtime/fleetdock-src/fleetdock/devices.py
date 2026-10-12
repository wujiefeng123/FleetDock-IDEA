"""Standard FleetDock device objects."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
import time
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4

from fleetdock.events import (
    EventType,
    ArmedChangedEvent,
    BatteryUpdatedEvent,
    ConnectionChangedEvent,
    CoordinateFrame,
    DeviceEvent,
    DeviceEventStream,
    EventHub,
    EventSource,
    FailsafeChangedEvent,
    FlightModeChangedEvent,
    GlobalPositionUpdatedEvent,
    HealthUpdatedEvent,
    HomePositionUpdatedEvent,
    InAirChangedEvent,
    LandedStateChangedEvent,
    LocalPositionUpdatedEvent,
    AttitudeUpdatedEvent,
    VelocityUpdatedEvent,
    StandardEventType,
    parse_standard_event_type,
)
from fleetdock.errors import FleetDockValidationError
from fleetdock.debug import DeviceDebug
from fleetdock.avoidance import AvoidanceManager
from fleetdock.factory import AdapterFactory, default_factory
from fleetdock.models import (
    CapabilityName,
    CommandName,
    ConnectionInfo,
    DeviceMetadata,
    ErrorCode,
    FirmwareKey,
    ProductKey,
    StandardCommand,
    StandardResponse,
    normalize_firmware,
    normalize_product,
)
from fleetdock.movement import (
    GpsPoint,
    MoveTarget,
    RelativePoint3D,
    move_target_kind,
    serialize_move_target,
)
from fleetdock.manual_control import ManualControlSession
from fleetdock.sensors import SensorHub, SensorType

logger = logging.getLogger(__name__)


class DeviceLifecycleState(str, Enum):
    """设备对象生命周期状态；描述 SDK 资源状态，不替代飞控的 armed/in_air 等业务状态。"""

    CREATED = "created"  # 对象和适配器已创建，但尚未确认控制链路连接成功。
    CONNECTED = "connected"  # 最近一次 connect 命令成功，控制链路已建立但不保证设备已就绪。
    CLOSING = "closing"  # 正在释放订阅、适配器和后台遥测资源。
    CLOSED = "closed"  # 资源已释放；后续命令会返回 DEVICE_STATE_ERROR。


@dataclass(frozen=True)
class DeviceState:
    """设备最近一次标准事件汇总出的状态快照。

    字段均为可空值；None 表示当前尚未收到对应遥测或底层 SDK 未提供该状态。经纬度单位为度，
    高度和本地位置单位为米，速度单位为米/秒，姿态角单位为度。
    """

    connected: Optional[bool] = None
    armed: Optional[bool] = None
    in_air: Optional[bool] = None
    landed_state: Optional[str] = None
    flight_mode: Optional[str] = None
    failsafe: Optional[bool] = None
    battery_remaining_fraction: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    absolute_altitude_m: Optional[float] = None
    relative_altitude_m: Optional[float] = None
    global_position_updated_at_s: Optional[float] = None
    home_latitude_deg: Optional[float] = None
    home_longitude_deg: Optional[float] = None
    home_absolute_altitude_m: Optional[float] = None
    health_local_position_ok: Optional[bool] = None
    health_global_position_ok: Optional[bool] = None
    health_home_position_ok: Optional[bool] = None
    health_armable: Optional[bool] = None
    local_x_m: Optional[float] = None
    local_y_m: Optional[float] = None
    local_z_m: Optional[float] = None
    local_position_frame: CoordinateFrame = CoordinateFrame.UNKNOWN
    local_position_updated_at_s: Optional[float] = None
    velocity_x_m_s: Optional[float] = None
    velocity_y_m_s: Optional[float] = None
    velocity_z_m_s: Optional[float] = None
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    yaw_deg: Optional[float] = None
    battery_updated_at_s: Optional[float] = None
    last_event_updated_at_s: Optional[float] = None


class DeviceStateView:
    """设备标准状态的线程安全只读入口。

    状态由 ``BaseDevice`` 根据最新标准事件在内部更新；调用方使用 ``snapshot()`` 获取不可变
    ``DeviceState``，不得直接修改设备状态。快照只代表调用瞬间，持续控制和安全判断应在每轮重新获取。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._current = DeviceState()

    def snapshot(self) -> DeviceState:
        """返回调用瞬间的不可变设备状态快照。

        字段来自最近一次标准事件；位置、速度和姿态单位及坐标系由 ``DeviceState`` 声明，
        ``*_updated_at_s`` 使用进程单调时钟秒值，仅用于判断当前进程内的数据新鲜度。
        """
        with self._lock:
            return self._current

    def _update(self, **changes: Any) -> None:
        with self._lock:
            self._current = replace(self._current, **changes)


class BaseDevice:
    """FleetDock 标准设备对象基类。

    用户通常通过 `Drone`、`RobotDog` 等子类使用。构造参数中 `product` 是必选产品枚举
    或产品键，`firmware_version` 可选指定固件/SDK 版本，`params` 是 SDK 创建模式的
    初始化参数，`metadata` 保存业务扩展信息；`managed_object` 用于托管调用方已创建的
    原生 SDK 对象。设备创建时会根据产品 profile 选择控制适配器，并创建统一的 ``events``、
    ``sensors`` 与 ``debug`` 单设备入口；外部传感器后端应在设备创建后注册到 ``device.sensors``。
    """

    def __init__(
        self,
        product: ProductKey,
        firmware_version: Optional[FirmwareKey] = None,
        params: Optional[Mapping[str, Any]] = None,
        metadata: Optional[DeviceMetadata] = None,
        *,
        managed_object: Any = None,
        close_managed_object: bool = False,
        connection: Optional[ConnectionInfo] = None,
        event_hub: Optional[EventHub] = None,
        adapter_factory: AdapterFactory = default_factory,
    ) -> None:
        self.instance_id = str(uuid4())
        self.product = product
        self.product_key = normalize_product(product)
        self.connection = connection or self._build_connection(
            params or {},
            managed_object,
            close_managed_object=close_managed_object,
        )
        self.metadata = metadata or DeviceMetadata()
        self.event_hub = event_hub or EventHub()
        self.profile = adapter_factory.registry.get(product)
        self.firmware_version = normalize_firmware(self.profile.resolve_firmware(firmware_version))
        if self.firmware_version is None:
            raise FleetDockValidationError(
                f"product {self.product_key} has no default firmware",
                details={"product": self.product_key},
            )
        adapter_type = adapter_factory.registry.get_adapter(product, self.firmware_version)
        self.events = DeviceEventStream(
            EventSource(
                device_id=self.metadata.device_id or self.instance_id,
                product=self.product_key,
                device_category=self.profile.category.value,
                adapter=adapter_type.__name__,
                instance_id=self.instance_id,
            )
        )
        self.sensors = SensorHub(self.metadata.device_id or self.instance_id, event_stream=self.events)
        self.state = DeviceStateView()
        self._lifecycle_state = DeviceLifecycleState.CREATED
        self._close_lock = RLock()
        self._manual_control_lock = RLock()
        self._command_observer_lock = RLock()
        self._command_observers: list[Callable[..., None]] = []
        self._manual_control_session: Optional[ManualControlSession] = None
        self.debug = DeviceDebug(self.sensors)
        self.avoidance = AvoidanceManager(self)
        self._state_unsubscribe = self.events.subscribe(self._update_state_from_event)
        try:
            self.adapter = adapter_factory.create(
                self.instance_id,
                product,
                self.firmware_version,
                self.connection,
                self.event_hub,
                self.events,
            )
            for sensor_backend in self.adapter.sensor_backends():
                self.sensors.register_backend(sensor_backend)
        except Exception:
            self._state_unsubscribe()
            self.debug.close()
            self.sensors.close()
            self.events.close()
            raise
        logger.debug(
            "设备创建完成: instance_id=%s product=%s firmware=%s mode=%s",
            self.instance_id,
            self.product_key,
            self.firmware_version,
            self.connection.mode.value,
        )

    @staticmethod
    def _build_connection(
        params: Mapping[str, Any],
        managed_object: Any = None,
        *,
        close_managed_object: bool = False,
    ) -> ConnectionInfo:
        if managed_object is not None:
            return ConnectionInfo.from_managed_object(
                managed_object,
                close_on_close=close_managed_object,
                **dict(params),
            )
        return ConnectionInfo.from_params(**dict(params))

    @classmethod
    def from_managed_object(
        cls,
        product: ProductKey,
        managed_object: Any,
        params: Optional[Mapping[str, Any]] = None,
        metadata: Optional[DeviceMetadata] = None,
        firmware_version: Optional[FirmwareKey] = None,
        event_hub: Optional[EventHub] = None,
        adapter_factory: AdapterFactory = default_factory,
        close_managed_object: bool = False,
    ) -> "BaseDevice":
        """从外部已创建的原生 SDK 对象构建设备。

        ``product`` 选择产品 profile；``managed_object`` 由调用方创建；``params`` 补充端点等信息；
        ``metadata`` 保存业务标识；``firmware_version`` 选择适配器；``event_hub`` 和
        ``adapter_factory`` 用于高级注入；``close_managed_object=False`` 表示原生对象仍归调用方所有；
        设为 ``True`` 才由设备在关闭或连接失败时一并释放。内置传感器由适配器自动装配；应用自定义
        传感器可在返回设备后通过 ``device.sensors`` 扩展。返回对应设备类实例。
        """
        return cls(
            product=product,
            params=params,
            metadata=metadata,
            firmware_version=firmware_version,
            managed_object=managed_object,
            event_hub=event_hub,
            adapter_factory=adapter_factory,
            close_managed_object=close_managed_object,
        )

    def __enter__(self) -> "BaseDevice":
        """返回当前设备以支持 ``with``；进入上下文不会隐式连接或执行设备动作。"""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出 ``with`` 时幂等调用 ``close()`` 释放 SDK 资源，不隐式返航、降落或停止设备。"""
        self.close()

    @property
    def native_client(self) -> Any:
        """返回底层托管的原生 SDK 对象，仅供高级集成或调试使用。"""
        return self.adapter.get_managed_object()

    def has_capability(self, capability: CapabilityName) -> bool:
        """判断当前产品是否声明 ``capability`` 标准能力并返回布尔结果。"""
        return self.profile.has_capability(capability)

    def supports_event(self, event_type: Any) -> bool:
        """判断 ``event_type`` 枚举或事件名是否由产品声明支持并返回布尔结果。"""
        return self.profile.supports_event(event_type)

    def supports_sensor(self, sensor_kind: SensorType) -> bool:
        """判断运行时发现结果中是否存在指定标准或扩展传感器类型。

        ``sensor_kind`` 使用公开 ``SensorKind`` 或 ``ExtensionSensorKind``；首次调用可能连接已配置的
        传感器后端并执行能力发现，但不会启动图像、点云等原始数据流。返回 ``True`` 表示至少存在一个可订阅传感器。
        """

        return self.sensors.supports(sensor_kind)

    @property
    def supported_events(self) -> frozenset[StandardEventType]:
        """返回当前产品声明支持的标准事件枚举集合。"""
        return frozenset(parse_standard_event_type(value) for value in self.profile.supported_events)

    def add_command_observer(self, observer: Callable[..., None]) -> Callable[[], None]:
        """订阅标准命令的开始、完成和失败阶段，并返回幂等取消订阅函数。

        ``observer`` 接收 ``stage``、``command``、可选 ``response`` 和可选 ``error``；
        观察器仅用于诊断和追踪，不得改变命令结果。观察器自身异常会被隔离，无返回值要求。
        """
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._command_observer_lock:
            self._command_observers.append(observer)

        def unsubscribe() -> None:
            with self._command_observer_lock:
                if observer in self._command_observers:
                    self._command_observers.remove(observer)

        return unsubscribe

    def _notify_command_observers(
        self,
        stage: str,
        command: StandardCommand,
        response: Optional[StandardResponse] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        with self._command_observer_lock:
            observers = tuple(self._command_observers)
        for observer in observers:
            try:
                observer(stage, command, response, error)
            except Exception:
                logger.debug("设备命令观察器执行失败", exc_info=True)

    def close(self) -> None:
        """幂等释放设备适配器、事件订阅和后台遥测资源；重复调用不会重复关闭底层资源，无返回值。"""
        with self._close_lock:
            if self._lifecycle_state in {DeviceLifecycleState.CLOSING, DeviceLifecycleState.CLOSED}:
                return
            self._lifecycle_state = DeviceLifecycleState.CLOSING
            logger.debug("设备开始关闭: instance_id=%s", self.instance_id)
            try:
                try:
                    self._close_manual_control(protect=False)
                finally:
                    try:
                        self.debug.close()
                    finally:
                        try:
                            self.sensors.close()
                        finally:
                            try:
                                self.avoidance.close()
                            finally:
                                self.adapter.close()
            finally:
                self._state_unsubscribe()
                self.events.close()
                with self._command_observer_lock:
                    self._command_observers.clear()
                self._lifecycle_state = DeviceLifecycleState.CLOSED
            logger.debug("设备关闭完成: instance_id=%s", self.instance_id)

    @property
    def lifecycle_state(self) -> DeviceLifecycleState:
        """返回设备对象当前资源生命周期状态；该状态不表示定位、解锁或飞行状态。"""
        return self._lifecycle_state

    def execute(
        self,
        command: CommandName,
        params: Optional[Mapping[str, Any]] = None,
        timeout_s: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StandardResponse:
        """执行标准命令并返回标准响应。

        `command` 是 FleetDock 标准命令枚举，`params` 是命令参数，`timeout_s` 是可选正数秒值；
        PX4 会把它传入同步 MAVSDK 等待边界，超时返回 ``COMMAND_TIMEOUT``。`metadata` 会写入
        命令追踪上下文。通常由具体设备方法封装调用。
        """
        if self._lifecycle_state in {DeviceLifecycleState.CLOSING, DeviceLifecycleState.CLOSED}:
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.DEVICE_STATE_ERROR,
                message="Device is closing or closed.",
                data={"lifecycle_state": self._lifecycle_state.value},
            )
        command_metadata = {
            "device_metadata": self.metadata.extra,
            "firmware_version": self.firmware_version,
            **dict(metadata or {}),
        }
        standard_command = StandardCommand(
            command=command,
            device_id=self.instance_id,
            product=self.product,
            params=params or {},
            timeout_s=timeout_s,
            metadata=command_metadata,
        )
        logger.debug(
            "设备命令开始: instance_id=%s command=%s command_id=%s param_keys=%s",
            self.instance_id,
            command.value,
            standard_command.command_id,
            tuple(standard_command.params),
        )
        self._notify_command_observers("started", standard_command)
        try:
            response = self.adapter.execute(standard_command)
        except Exception as error:
            self._notify_command_observers("failed", standard_command, error=error)
            raise
        self._notify_command_observers("completed", standard_command, response=response)
        if command is CommandName.CONNECT and response.ok:
            self._lifecycle_state = DeviceLifecycleState.CONNECTED
        logger.debug(
            "设备命令完成: instance_id=%s command=%s command_id=%s ok=%s code=%s",
            self.instance_id,
            command.value,
            standard_command.command_id,
            response.ok,
            response.code.value,
        )
        return response

    def connect(self) -> StandardResponse:
        """建立控制链路并立即返回响应；成功不表示定位、Home 或解锁条件已经就绪。"""
        return self.execute(CommandName.CONNECT)

    def move(
        self,
        target: MoveTarget,
    ) -> StandardResponse:
        """按强类型目标移动设备并返回标准响应。

        ``target`` 可以是 GPS 点、任务局部目标点或相对当前位置的位移对象。设备会先根据产品
        profile 校验目标类型，再由适配器转换成 PX4、仿真器或机器狗厂商协议。调用方不需要传入
        当前坐标；相对移动所需的当前位置和姿态由设备遥测及适配器获取。
        """
        target_kind = move_target_kind(target)
        if not self.profile.supports_move_target(target_kind):
            logger.debug(
                "移动目标被拒绝: instance_id=%s product=%s target_kind=%s",
                self.instance_id,
                self.product_key,
                target_kind.value,
            )
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.UNSUPPORTED_COMMAND,
                message=(
                    f"Product '{self.product_key}' does not support move target "
                    f"'{target_kind.value}'."
                ),
                data={"target_kind": target_kind.value},
            )
        params = dict(serialize_move_target(target))
        snapshot = self.state.snapshot()
        params["current_position"] = {
            "x_m": snapshot.local_x_m,
            "y_m": snapshot.local_y_m,
            "z_m": snapshot.local_z_m,
            "yaw_deg": snapshot.yaw_deg,
        }
        return self.execute(CommandName.MOVE, params)

    def set_velocity(
        self,
        *,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        speed: float = 0.0,
        yaw_deg: Optional[float] = None,
    ) -> StandardResponse:
        """按任务局部方向发送高级速度目标并返回标准响应。

        ``x``、``y``、``z`` 分别表示前/北、右/东、上方向，SDK 会先归一化三轴方向，再乘以
        ``speed`` 得到米/秒速度；因此方向向量的长度不改变最终速度。``speed`` 必须为非负有限值，
        为 0 时始终发送三轴零速度；非零速度要求至少一个方向分量非零。``yaw_deg`` 是可选目标朝向角，
        单位为度。厂商适配器负责转换到 PX4 NED、DJI 或足式设备坐标系。该入口主要供航线、编队和
        跟随控制器使用；普通目标移动应优先调用 ``move(target)``。
        """
        values = (float(x), float(y), float(z), float(speed))
        if not all(math.isfinite(value) for value in values):
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.VALIDATION_ERROR,
                message="Velocity direction and speed must be finite.",
            )
        if speed < 0.0:
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.VALIDATION_ERROR,
                message="Velocity speed cannot be negative.",
            )
        direction_norm = math.sqrt(x * x + y * y + z * z)
        if speed > 0.0 and direction_norm == 0.0:
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.VALIDATION_ERROR,
                message="A non-zero velocity speed requires a direction.",
            )
        if speed == 0.0:
            x = y = z = 0.0
        else:
            x, y, z = x / direction_norm, y / direction_norm, z / direction_norm
        params: dict[str, Any] = {
            "control_mode": "velocity",
            "x": x,
            "y": y,
            "z": z,
            "speed": speed,
        }
        if yaw_deg is not None:
            params["yaw_deg"] = yaw_deg
        return self.execute(CommandName.MOVE, params)

    def set_body_velocity(
        self,
        *,
        forward_m_s: float = 0.0,
        right_m_s: float = 0.0,
        up_m_s: float = 0.0,
        yaw_rate_deg_s: float = 0.0,
    ) -> StandardResponse:
        """发送机体系手动速度目标并立即返回标准响应。

        ``forward_m_s``、``right_m_s`` 和 ``up_m_s`` 分别沿设备机头前方、右方和上方为正，单位为
        米/秒；``yaw_rate_deg_s`` 是向右为正的偏航角速度，单位为度/秒。所有值必须有限；本方法只
        发送一次目标，不提供输入超时保护，持续人工控制应优先使用 ``manual_control()``。产品未声明
        ``MANUAL_CONTROL`` 时返回 ``UNSUPPORTED_COMMAND``，不会退化为任务局部 NED/ENU 控制。
        """
        if not self.has_capability(CapabilityName.MANUAL_CONTROL):
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.UNSUPPORTED_COMMAND,
                message=f"Product '{self.product_key}' does not support manual body velocity control.",
            )
        values = tuple(float(value) for value in (
            forward_m_s,
            right_m_s,
            up_m_s,
            yaw_rate_deg_s,
        ))
        if not all(math.isfinite(value) for value in values):
            return StandardResponse.operation_failure(
                device_id=self.instance_id,
                product=self.product,
                code=ErrorCode.VALIDATION_ERROR,
                message="Manual body velocity values must be finite.",
            )
        return self.execute(CommandName.MOVE, {
            "control_mode": "body_velocity",
            "forward_m_s": values[0],
            "right_m_s": values[1],
            "up_m_s": values[2],
            "yaw_rate_deg_s": values[3],
        })

    def manual_control(self, options=None):
        """创建带限速、周期刷新和输入超时保护的手动控制会话。

        ``options`` 是可选 ``ManualControlOptions``；省略时使用 0.5 秒输入超时、3 米/秒水平速度、
        1 米/秒垂直速度和 45 度/秒偏航速度上限。设备必须声明 ``MANUAL_CONTROL``，且连接、遥测、
        本地位置、解锁和在空中状态均已就绪；否则抛出 ``RuntimeError``。返回的
        ``ManualControlSession`` 应通过 ``with`` 或 ``close()`` 释放，同一设备同时只允许一个会话。
        """
        from fleetdock.manual_control import ManualControlOptions, ManualControlSession

        selected_options = options or ManualControlOptions()
        if not isinstance(selected_options, ManualControlOptions):
            raise TypeError("options must be a ManualControlOptions instance.")
        release_telemetry = self._acquire_capability_telemetry(CapabilityName.MANUAL_CONTROL)
        report = self.readiness_for(CapabilityName.MANUAL_CONTROL)
        if not report.ready:
            release_telemetry()
            messages = "; ".join(issue.message for issue in report.issues)
            raise RuntimeError(f"Device is not ready for manual control: {messages}")
        with self._manual_control_lock:
            if self._manual_control_session is not None:
                release_telemetry()
                raise RuntimeError("A manual control session is already active for this device.")
            session = ManualControlSession(self, selected_options, release_telemetry=release_telemetry)
            self._manual_control_session = session
            return session

    def _release_manual_control(self, session) -> None:
        with self._manual_control_lock:
            if self._manual_control_session is session:
                self._manual_control_session = None

    def _close_manual_control(self, *, protect: bool = True) -> None:
        with self._manual_control_lock:
            session = self._manual_control_session
        if session is not None:
            session.close(protect=protect)

    def stop(self) -> StandardResponse:
        """停止设备当前运动，立即执行并返回标准响应。"""
        return self.execute(CommandName.STOP)

    def emergency_stop(self) -> StandardResponse:
        """执行设备急停动作，立即执行并返回标准响应。"""
        return self.execute(CommandName.EMERGENCY_STOP)

    def get_status(self) -> StandardResponse:
        """读取设备当前状态，立即执行并返回标准响应。"""
        return self.execute(CommandName.GET_STATUS)

    def readiness_for(
        self,
        capability: CapabilityName,
        *,
        extra_requirements=(),
        ignored_requirements=(),
    ):
        """返回设备执行 ``capability`` 前的强类型就绪报告。

        标准评估会检查产品能力声明、连接和该能力需要的位置或安全状态；
        ``extra_requirements`` 可追加调用场景要求，``ignored_requirements`` 可显式忽略非必需条件。
        返回值是调用时不可变快照，不会等待遥测变化。
        """
        from fleetdock.readiness import evaluate_readiness

        return evaluate_readiness(
            self,
            capability,
            extra_requirements=extra_requirements,
            ignored_requirements=ignored_requirements,
        )

    def _acquire_capability_telemetry(self, capability: CapabilityName) -> Callable[[], None]:
        acquire = getattr(self.adapter, "acquire_capability_telemetry", None)
        return acquire(capability) if callable(acquire) else lambda: None

    def diagnostics(self):
        """返回不含敏感连接参数的强类型设备诊断快照；用于健康检查、日志和运维界面。"""
        from fleetdock.readiness import DeviceDiagnostic

        state = self.state.snapshot()
        telemetry_age_s = None
        if state.last_event_updated_at_s is not None:
            telemetry_age_s = max(0.0, time.monotonic() - state.last_event_updated_at_s)
        event_status = self.events.status()
        telemetry_diagnostics = getattr(self.adapter, "telemetry_diagnostics", None)
        telemetry = dict(telemetry_diagnostics()) if callable(telemetry_diagnostics) else {}
        return DeviceDiagnostic(
            device_id=self.metadata.device_id or self.instance_id,
            product=self.product_key,
            lifecycle_state=self.lifecycle_state.value,
            connected=bool(getattr(self.adapter, "connected", False)),
            maturity=self.profile.maturity,
            telemetry_age_s=telemetry_age_s,
            armed=state.armed,
            in_air=state.in_air,
            flight_mode=state.flight_mode,
            failsafe=state.failsafe,
            event_pending_events=event_status.pending_events,
            event_dropped_events=event_status.dropped_events,
            event_queue_capacity=event_status.queue_capacity,
            event_queue_high_watermark=event_status.queue_high_watermark,
            telemetry_requested_streams=tuple(telemetry.get("requested_streams", ())),
            telemetry_callback_pending=int(telemetry.get("callback_pending", 0)),
            telemetry_dropped_notifications=int(telemetry.get("dropped_notifications", 0)),
        )

    def _update_state_from_event(self, event: DeviceEvent) -> None:
        self._apply_state_event(event, time.monotonic())

    def _apply_state_event(self, event: DeviceEvent, updated_at_s: float) -> None:
        changes: dict[str, Any] = {"last_event_updated_at_s": updated_at_s}
        if isinstance(event, HomePositionUpdatedEvent):
            changes.update(
                home_latitude_deg=event.position.latitude_deg,
                home_longitude_deg=event.position.longitude_deg,
                home_absolute_altitude_m=event.position.absolute_altitude_m,
            )
        elif isinstance(event, GlobalPositionUpdatedEvent):
            changes.update(
                latitude_deg=event.position.latitude_deg,
                longitude_deg=event.position.longitude_deg,
                absolute_altitude_m=event.position.absolute_altitude_m,
                relative_altitude_m=event.position.relative_altitude_m,
                global_position_updated_at_s=updated_at_s,
            )
        elif isinstance(event, LocalPositionUpdatedEvent):
            changes.update(
                local_x_m=event.position.position_m.x,
                local_y_m=event.position.position_m.y,
                local_z_m=event.position.position_m.z,
                local_position_frame=event.position.frame,
                local_position_updated_at_s=updated_at_s,
            )
        elif isinstance(event, VelocityUpdatedEvent):
            changes.update(
                velocity_x_m_s=event.velocity.linear_velocity_m_s.x,
                velocity_y_m_s=event.velocity.linear_velocity_m_s.y,
                velocity_z_m_s=event.velocity.linear_velocity_m_s.z,
            )
        elif isinstance(event, AttitudeUpdatedEvent):
            changes.update(
                roll_deg=event.attitude.roll_deg,
                pitch_deg=event.attitude.pitch_deg,
                yaw_deg=event.attitude.yaw_deg,
            )
        elif isinstance(event, BatteryUpdatedEvent):
            changes.update(
                battery_remaining_fraction=event.battery.remaining_fraction,
                battery_updated_at_s=updated_at_s,
            )
        elif isinstance(event, ConnectionChangedEvent):
            changes["connected"] = str(event.change.current) == "connected"
        elif isinstance(event, ArmedChangedEvent):
            changes["armed"] = bool(event.change.current)
        elif isinstance(event, InAirChangedEvent):
            changes["in_air"] = bool(event.change.current)
        elif isinstance(event, LandedStateChangedEvent):
            changes["landed_state"] = str(event.change.current)
        elif isinstance(event, FlightModeChangedEvent):
            changes["flight_mode"] = str(event.change.current)
        elif isinstance(event, FailsafeChangedEvent):
            changes["failsafe"] = bool(event.change.current)
        elif isinstance(event, HealthUpdatedEvent):
            changes.update(
                health_local_position_ok=event.health.local_position_ok,
                health_global_position_ok=event.health.global_position_ok,
                health_home_position_ok=event.health.home_position_ok,
                health_armable=event.health.armable,
            )
        elif event.type == EventType.ARMED_CHANGED:
            changes["armed"] = bool(getattr(event, "change").current)
        elif event.type == EventType.IN_AIR_CHANGED:
            changes["in_air"] = bool(getattr(event, "change").current)
        self.state._update(**changes)


class Drone(BaseDevice):
    """标准无人机设备对象。

    面向 PX4、DJI 等无人机产品提供解锁、起飞、降落、返航、悬停和导航等统一方法；
    底层飞控或厂商 SDK 差异由适配器处理。
    """

    def unlock(self, force: bool = False) -> StandardResponse:
        """解锁无人机并立即返回标准响应。

        `force=False` 时执行标准解锁流程，由飞控或厂商 SDK 自行决定是否通过预检；`force=True`
        时显式请求底层适配器执行强制解锁，仅适用于已采取安全措施的台架测试或仿真诊断。强制解锁不能
        让未满足的飞行条件变为有效，飞控仍可能立即自动上锁，因此不能替代正常起飞预检。返回值中的
        `ok`、`code`、`message` 和 `data` 可用于判断失败原因，并由调用方决定是否重试或改用强制解锁。
        """
        return self.execute(CommandName.UNLOCK, {"force": force})

    def lock(self) -> StandardResponse:
        """锁定无人机，立即执行并返回标准响应。"""
        return self.execute(CommandName.LOCK)

    def takeoff(self, altitude: Optional[float] = 5.0) -> StandardResponse:
        """发送起飞目标并立即返回标准响应。

        ``altitude`` 是相对 Home 的目标高度，单位为米；默认 5 米。显式传 ``None`` 时不覆盖
        厂商 SDK 或飞控中已有的起飞高度，适合禁止参数写入的台架验证；适配器不支持该语义时会返回
        失败响应。成功仅表示命令已被底层接受，不表示已经离地或到达目标高度。
        """
        return self.execute(CommandName.TAKEOFF, {"altitude": altitude})

    def land(self) -> StandardResponse:
        """发送降落命令并立即返回；成功不表示无人机已经着陆。"""
        return self.execute(CommandName.LAND)

    def return_to_launch(self) -> StandardResponse:
        """触发返航并立即返回；成功不表示无人机已经到达 Home 或完成降落。"""
        return self.execute(CommandName.RETURN_TO_LAUNCH)

    def hover(self) -> StandardResponse:
        """让无人机悬停或保持当前位置，立即执行并返回标准响应。"""
        return self.execute(CommandName.HOVER)

    def goto_global(
        self,
        latitude_deg: float,
        longitude_deg: float,
        altitude_m: float,
        yaw_deg: float = 0.0,
    ) -> StandardResponse:
        """通过标准 ``MOVE`` 命令飞向 GPS 经纬度目标点。

        `latitude_deg`、`longitude_deg` 单位为度，`altitude_m` 是 AMSL 绝对海拔米数，
        `yaw_deg` 是目标偏航角且单位为度；
        本方法是 ``move(GpsPoint(...))`` 的便捷入口，返回标准命令响应；成功仅表示位置目标已被接受。
        """
        return self.move(GpsPoint(latitude_deg, longitude_deg, altitude_m, yaw_deg))

    def goto_local(self, x_m: float, y_m: float, z_m: float) -> StandardResponse:
        """通过标准 ``MOVE`` 命令飞向任务局部坐标目标点。

        ``x_m/y_m/z_m`` 单位为米，x/y 分别对应任务局部北向和东向，z 轴向上为正。本方法是
        ``move(RelativePoint3D(...))`` 的便捷入口，返回标准命令响应；成功仅表示位置目标已被接受。
        """
        return self.move(RelativePoint3D(x_m, y_m, z_m))


class RobotDog(BaseDevice):
    """标准机器狗设备对象。

    面向 Unitree 等足式机器人产品提供站立、坐下、移动、停止和急停等统一方法。
    """

    def stand(self) -> StandardResponse:
        """让机器狗站立，立即执行并返回标准响应。"""
        return self.execute(CommandName.STAND)

    def sit(self) -> StandardResponse:
        """让机器狗坐下，立即执行并返回标准响应。"""
        return self.execute(CommandName.SIT)
