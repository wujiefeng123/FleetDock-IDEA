"""适配器层公共接口与能力协议。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Protocol, cast, runtime_checkable

from fleetdock._exception_mapping import command_failure
from fleetdock.events import DeviceEventStream, EventCategory, EventHub, EventType, OriginalEvent, StandardEventType
from fleetdock.models import CommandName, ConnectionInfo, ErrorCode, ProductKey, ProductProfile, StandardCommand, StandardResponse, normalize_product
from fleetdock.takeoff import TakeoffAdapter as TakeoffAdapter

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fleetdock.devices import DeviceState
    from fleetdock.tasking.models import HeadingMode
    from fleetdock.tasking.routes import Route, Waypoint


NativeWaypointCallback = Callable[[Any, "Waypoint", int, Optional[float]], None]
NativeRouteEventCallback = Callable[[Any], None]
NativeRouteCompletionCallback = Callable[[str], None]
NativeWaypointActionCallback = Callable[["Waypoint", int, Optional[float]], None]


@dataclass(frozen=True)
class NativeRouteCallbacks:
    """原生航线执行期间的 SDK 回调集合。

    ``guard`` 在适配器等待期间周期执行并可抛出异常中止任务；其余回调分别报告航点事件、
    航线事件和航点动作，均在执行原生任务的调用线程中触发。``completion`` 仅在原生航线
    正常结束时调用，参数是公开的 ``RouteCompletionReason`` 字符串值；异常失败不得调用。
    """

    guard: Optional[Callable[[], None]] = None
    waypoint_event: Optional[NativeWaypointCallback] = None
    route_event: Optional[NativeRouteEventCallback] = None
    completion: Optional[NativeRouteCompletionCallback] = None
    waypoint_action: Optional[NativeWaypointActionCallback] = None


@dataclass(frozen=True)
class NativeRouteRequest:
    """适配器原生任务系统的强类型航线请求。

    ``altitude_m`` 和航点高度单位为米，``speed_m_s`` 单位为米/秒，``timeout_s`` 单位为秒；
    ``max_duration_s`` 是可选正常执行上限，达到后适配器应安全停止并返回成功，而不是报告超时；
    ``state`` 是提交任务时的设备状态快照，回调由适配器按执行进度调用。
    """

    route: "Route"
    state: Optional["DeviceState"] = None
    altitude_m: Optional[float] = None
    speed_m_s: float = 0.0
    heading: Optional["HeadingMode"] = None
    timeout_s: float = 60.0
    max_duration_s: Optional[float] = None
    callbacks: NativeRouteCallbacks = NativeRouteCallbacks()


# 新代码使用跨设备命名；旧名称保留以兼容现有适配器。
RouteExecutionCallbacks = NativeRouteCallbacks
RouteExecutionRequest = NativeRouteRequest


@runtime_checkable
class LifecycleAdapter(Protocol):
    """适配器生命周期能力协议；实现方必须能确定性释放后台资源。"""

    def close(self) -> None:
        """停止后台活动并释放连接资源；无返回值，重复调用应保持安全。"""


@runtime_checkable
class MotionAdapter(Protocol):
    """标准运动命令能力协议；输入输出保持 FleetDock 标准模型。"""

    def execute(self, command: StandardCommand) -> StandardResponse:
        """执行 FleetDock 标准命令；``command`` 携带命令名、设备 ID、参数和命令 ID，返回标准响应。"""


@runtime_checkable
class TelemetryAdapter(Protocol):
    """标准遥测事件能力协议；适配器通过 ``device_events`` 发布强类型事件。"""

    device_events: DeviceEventStream


@runtime_checkable
class MissionAdapter(Protocol):
    """厂商或飞控原生任务能力协议。"""

    def supports_native_route(self, route: "Route", *, state: Optional["DeviceState"] = None) -> bool:
        """判断航线是否可交给原生任务系统执行。

        ``route`` 是待执行航线，``state`` 是可选设备状态快照；返回 True 表示适配器可直接执行，
        False 表示任务层应回退到 SDK 侧运动控制。
        """

    def execute_mission(self, request: NativeRouteRequest) -> StandardResponse:
        """执行强类型原生航线请求；``request`` 包含航线、速度、超时和回调，返回标准响应。"""


@runtime_checkable
class RouteAdapter(Protocol):
    """跨设备标准路线执行协议。

    无人机适配器可映射到 PX4/DJI Mission，足式设备可映射到厂商导航任务；业务层始终使用
    FleetDock ``Route`` 和标准响应，不依赖厂商任务对象。
    """

    def supports_route(self, request: NativeRouteRequest) -> bool:
        """判断当前适配器能否原生执行标准路线请求。

        ``request`` 包含待执行 ``Route``、设备状态快照、速度、高度、朝向、超时和回调；
        返回值为 ``True`` 时任务层可交给适配器原生执行，返回 ``False`` 时可回退 SDK 侧连续控制。
        """

    def execute_route(self, request: NativeRouteRequest) -> StandardResponse:
        """同步执行标准路线请求并返回标准响应。

        ``request`` 携带路线、速度、高度、超时、最大持续时间和回调；实现应在执行期间响应
        守卫、取消和进度回调。返回值表示路线是否被原生任务系统接受并完成。
        """

    def cancel_route(self) -> None:
        """请求取消当前原生路线并进入设备安全停止状态。

        没有活动路线时应保持幂等；该方法无返回值，失败时由具体适配器转换为日志或后续响应。
        """


class DeviceAdapter(ABC):
    """厂商 SDK/API 适配器基类。

    厂商协议对象被限制在适配器内部；公共设备对象只通过 ``StandardCommand`` 和
    ``StandardResponse`` 与适配器交互，事件通过 ``device_events`` 转换为 FleetDock 标准事件。
    """

    def __init__(
        self,
        device_id: str,
        product: ProductKey,
        connection: ConnectionInfo,
        profile: ProductProfile,
        event_hub: EventHub,
        device_events: DeviceEventStream,
    ) -> None:
        self.device_id = device_id
        self.product = normalize_product(product)
        self.connection = connection
        self.profile = profile
        self.event_hub = event_hub
        self.device_events = device_events
        self.connected = False

    def execute(self, command: StandardCommand) -> StandardResponse:
        """执行标准命令并返回标准响应。

        ``command`` 包含命令名、命令 ID、设备 ID 和参数；方法会在执行前后发布标准命令事件，
        并把不支持、未连接、参数错误和未知异常统一转换为 ``StandardResponse``。
        """
        logger.debug(
            "适配器执行命令: device_id=%s adapter=%s command=%s command_id=%s",
            self.device_id,
            type(self).__name__,
            command.command.value,
            command.command_id,
        )
        self.publish_standard_event(
            EventType.COMMAND_STARTED,
            EventCategory.COMMAND,
            {"command": command.command.value, "command_id": command.command_id},
            correlation_id=command.command_id,
        )
        if not self.profile.supports(command.command):
            logger.debug("命令被拒绝: 产品不支持 command=%s", command.command.value)
            response = StandardResponse.failure(
                command,
                ErrorCode.UNSUPPORTED_COMMAND,
                f"Command '{command.command.value}' is not supported by product '{self.product}'.",
            )
            self._publish_command_completed(command, response)
            return response

        if command.command != CommandName.CONNECT and not self.connected:
            logger.debug("命令被拒绝: 设备未连接 command=%s", command.command.value)
            response = StandardResponse.failure(command, ErrorCode.NOT_CONNECTED, "Device is not connected.")
            self._publish_command_completed(command, response)
            return response

        handler = getattr(self, f"_handle_{command.command.value}", None)
        if handler is None:
            logger.debug("命令被拒绝: 适配器未实现 command=%s", command.command.value)
            response = StandardResponse.failure(
                command,
                ErrorCode.INVALID_COMMAND,
                f"Adapter does not implement command '{command.command.value}'.",
            )
            self._publish_command_completed(command, response)
            return response

        try:
            with self._command_scope(command):
                response = handler(command)
        except Exception as exc:
            logger.debug("命令执行异常: command=%s error=%s", command.command.value, exc, exc_info=True)
            response = command_failure(command, exc)

        self._publish_command_completed(command, response)
        logger.debug(
            "适配器命令完成: device_id=%s command=%s command_id=%s ok=%s code=%s",
            self.device_id,
            command.command.value,
            command.command_id,
            response.ok,
            response.code.value,
        )
        return response

    def _command_scope(self, command: StandardCommand) -> Any:
        """返回当前命令的适配器内部执行上下文；默认不附加行为。"""
        del command
        return nullcontext()

    def _publish_command_completed(self, command: StandardCommand, response: StandardResponse) -> None:
        payload = {
            "command": command.command.value,
            "command_id": command.command_id,
            "ok": response.ok,
            "code": response.code.value,
            "message": response.message,
        }
        self.event_hub.publish_device(
            "command.executed",
            payload,
            source=self.device_id,
        )
        self.publish_standard_event(
            EventType.COMMAND_COMPLETED,
            EventCategory.COMMAND,
            payload,
            correlation_id=command.command_id,
        )

    @abstractmethod
    def get_vendor_state(self) -> Dict[str, Any]:
        """返回适配器内部诊断状态；键值应可序列化，不能包含密码、令牌或密钥。"""

    def get_managed_object(self) -> Any:
        """返回调用方托管或适配器暴露的原生 SDK 对象；无对象时返回 None。"""
        return self.connection.managed_object

    def sensor_backends(self) -> tuple[Any, ...]:
        """返回与当前控制连接组合使用的标准传感器后端。

        默认没有内置后端；PX4、DJI、Unitree 等适配器可返回复用同一厂商连接的 ``SensorBackend``。
        返回对象由设备 ``SensorHub`` 统一订阅和关闭，不得要求业务代码读取厂商原生流。
        """
        return ()

    def additional_readiness_issues(self, capability: Any, state: Any) -> tuple[Any, ...]:
        """返回厂商针对 ``capability`` 追加的未就绪条件。

        ``state`` 是 FleetDock 标准设备状态快照；默认不追加条件。适配器实现应返回公开的
        ``ReadinessIssue`` 元组，不得要求上层读取厂商原生对象才能判断标准能力是否可用。
        """
        return ()

    def supports_native_route(self, route: Any, *, state: Any = None) -> bool:
        """判断适配器是否能用飞控或厂商 SDK 的原生任务系统执行指定航线。

        ``route`` 是标准航线对象，``state`` 是可选设备状态快照；默认返回 False。
        """
        return False

    def supports_route(self, request: NativeRouteRequest) -> bool:
        """判断是否支持原生执行标准路线请求。

        ``request`` 是任务层传入的强类型路线请求，包含 ``Route``、状态快照、速度、高度、
        超时和回调。返回值为 ``True`` 表示可由适配器原生执行，``False`` 表示任务层应回退。
        默认桥接旧 ``supports_native_route`` 扩展点，便于现有 PX4 和第三方适配器平滑迁移；
        新适配器应优先覆写本方法。
        """
        return self.supports_native_route(request.route, state=request.state)

    def execute_route(self, request: NativeRouteRequest) -> StandardResponse:
        """执行标准路线请求并返回标准响应。

        ``request`` 携带路线、状态、速度、高度、超时和回调；默认桥接旧 ``execute_mission`` 实现。
        返回值为标准响应，调用方可通过 ``ok``、``code``、``message`` 和 ``data`` 判断路线结果。
        """
        return self.execute_mission(request)

    def cancel_route(self) -> None:
        """取消当前原生路线并执行通用停止动作。

        默认调用标准 ``stop``，适配器可覆写以暂停或清理厂商任务；该方法无返回值，没有活动路线时应幂等。
        """
        stop_command = StandardCommand(CommandName.STOP, self.device_id, self.product)
        self.execute(stop_command)

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
        """执行原生航线任务。

        ``route`` 是标准航线对象，``state`` 是可选设备状态快照，``heading`` 是任务层解析后的航向策略；
        ``altitude_m`` 单位为米，``speed_m_s`` 单位为米/秒，``timeout_s`` 单位为秒；
        ``max_duration_s`` 是达到后按成功结束的可选秒数上限。
        ``guard_callback`` 用于周期性中止检查，``event_callback`` 报告航点事件，
        ``route_event_callback`` 报告航线事件，``completion_callback`` 报告正常结束原因，
        ``action_callback`` 请求到点动作。
        默认返回不支持响应。
        """
        return StandardResponse(
            ok=False,
            command_id="native-route",
            device_id=self.device_id,
            product=self.product,
            code=ErrorCode.UNSUPPORTED_COMMAND,
            message="Native route execution is not supported by this adapter.",
        )

    def execute_mission(self, request: NativeRouteRequest) -> StandardResponse:
        """执行强类型原生航线请求。

        ``request`` 汇总航线、状态、米制高度、米/秒速度、秒制超时和回调集合。默认桥接到兼容的
        ``execute_native_route(...)`` 覆写点；新适配器可直接覆写本方法。返回值是原生任务执行的
        标准响应，失败时应包含可读错误消息。
        """
        callbacks = request.callbacks
        execute_native_route = self.execute_native_route
        parameters = inspect.signature(execute_native_route).parameters
        accepts_extra_keywords = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        if request.max_duration_s is not None and not (
            accepts_extra_keywords or "max_duration_s" in parameters
        ):
            return StandardResponse(
                ok=False,
                command_id="native-route",
                device_id=self.device_id,
                product=self.product,
                code=ErrorCode.UNSUPPORTED_COMMAND,
                message="Native route adapter does not support duration-limited execution.",
            )
        kwargs = {
            "state": request.state,
            "altitude_m": request.altitude_m,
            "speed_m_s": request.speed_m_s,
            "heading": request.heading,
            "timeout_s": request.timeout_s,
            "guard_callback": callbacks.guard,
            "event_callback": callbacks.waypoint_event,
            "route_event_callback": callbacks.route_event,
            "action_callback": callbacks.waypoint_action,
        }
        if request.max_duration_s is not None:
            kwargs["max_duration_s"] = request.max_duration_s
        if callbacks.completion is not None and (accepts_extra_keywords or "completion_callback" in parameters):
            kwargs["completion_callback"] = callbacks.completion
        route_executor = cast(Callable[..., StandardResponse], execute_native_route)
        return route_executor(request.route, **kwargs)

    def publish_standard_event(
        self,
        event_type: StandardEventType,
        category: EventCategory,
        payload: Any,
        *,
        extensions: Dict[str, Dict[str, Any]] | None = None,
        correlation_id: str | None = None,
        original: OriginalEvent | None = None,
    ) -> None:
        """发布 FleetDock 标准设备事件。

        ``event_type`` 必须是标准事件枚举，``category`` 表示事件领域；``payload`` 会按标准事件
        模型转换，``extensions`` 保存厂商补充字段，``correlation_id`` 可关联命令，``original``
        用于保留厂商来源数据。方法无返回值，事件会进入设备事件流异步分发。
        """
        self.device_events.publish(
            event_type,
            category,
            payload,
            extensions=extensions,
            correlation_id=correlation_id,
            original=original,
        )

    def publish_vendor_event(
        self,
        event_type: str,
        category: EventCategory,
        payload: Any,
        *,
        native_data: Any = None,
    ) -> None:
        """发布厂商扩展事件。

        ``event_type`` 必须以 ``vendor.`` 开头，``category`` 表示事件领域，``payload`` 会保留为
        可序列化原始数据；``native_data`` 仅用于同进程高级接入，不保证可序列化。
        方法无返回值，事件会进入设备事件流异步分发。
        """
        self.device_events.publish_vendor(event_type, category, payload, native_data=native_data)

    def close(self) -> None:
        """释放适配器持有的后台资源；默认无操作，子类可停止遥测线程或关闭底层连接，无返回值。"""
