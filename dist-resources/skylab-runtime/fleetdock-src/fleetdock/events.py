"""FleetDock 标准设备事件流及兼容的进程内消息总线。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import logging
from queue import Full, Queue
from threading import RLock
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type, Union, cast

from fleetdock.runtime import ManagedWorker, WorkerStatus
from fleetdock.message_bus import Event, EventHub, MessageBus, MessageEvent, MessageEventHandler, MessageEventType

logger = logging.getLogger(__name__)


from fleetdock.event_models import *  # noqa: F403
from fleetdock.event_models import _coerce_standard_payload
DeviceEventHandler = Callable[[DeviceEvent], None]
DeviceEventClass = Union[Type[DeviceEvent], Tuple[Type[DeviceEvent], ...]]
DeviceEventFilter = Union[DeviceEventClass, StandardEventType]


class HandlerErrorPolicy(str, Enum):
    """设备事件回调异常策略；异常始终隔离，不会中断其他订阅者。"""

    LOG = "log"  # 记录 DEBUG 日志并继续分发。
    PUBLISH = "publish"  # 额外发布 ``EventType.HANDLER_FAILED`` 诊断事件。
    DISABLE = "disable"  # 首次失败后自动取消该订阅，并发布诊断事件。
    RAISE_ON_DRAIN = "raise_on_drain"  # 后台继续分发，在调用 ``drain()`` 时向调用方抛出。


class DeviceEventHandlerError(RuntimeError):
    """事件回调失败异常；仅由 ``RAISE_ON_DRAIN`` 策略在 ``drain()`` 调用线程抛出。"""


@dataclass
class _DeviceEventSubscription:
    handler: DeviceEventHandler
    on_error: Optional[Callable[[EventHandlerFailure], None]]
    error_policy: HandlerErrorPolicy
    consecutive_failures: int = 0
    release_demand: Optional[Callable[[], None]] = None


@dataclass(frozen=True)
class DeviceEventStreamStatus:
    """单设备事件流状态快照。

    ``pending_events`` 是尚未完成分发的事件数量；``dropped_events`` 是有界队列为保护内存而
    丢弃或合并的累计事件数；``queue_capacity`` 和 ``queue_high_watermark`` 分别表示容量与历史
    最高积压。``handler_error_count`` 为流生命周期内累计回调错误数。
    """

    closed: bool
    subscriber_count: int
    pending_events: int
    handler_error_count: int
    last_handler_error: Optional[EventHandlerFailure]
    worker: WorkerStatus
    dropped_events: int
    queue_capacity: int
    queue_high_watermark: int


class DeviceEventStream:
    """单设备有序事件流；与后续多设备消息总线保持独立。"""

    _STOP = object()

    def __init__(self, source: EventSource, *, queue_capacity: int = 256) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be greater than zero.")
        self.source = source
        self._lock = RLock()
        self._handlers: List[_DeviceEventSubscription] = []
        self._sequence = 0
        self._closed = False
        self._queue: Queue[object] = Queue(maxsize=queue_capacity)
        self._dropped_events = 0
        self._queue_high_watermark = 0
        self._demand_handler: Optional[Callable[[Any], Callable[[], None]]] = None
        self._handler_error_count = 0
        self._last_handler_error: Optional[EventHandlerFailure] = None
        self._drain_error: Optional[EventHandlerFailure] = None
        self._worker = ManagedWorker(
            f"fleetdock-events-{source.device_id}",
            self._dispatch,
            request_stop=self._request_stop,
            # 事件流仍要求 device.close() 确定性释放；daemon 仅避免调用方遗漏关闭时阻塞解释器退出。
            daemon=True,
        ).start()

    def _request_stop(self) -> None:
        while True:
            try:
                self._queue.put_nowait(self._STOP)
                return
            except Full:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self._dropped_events += 1
                except Exception:
                    return

    def subscribe(
        self,
        event_class_or_handler: Union[DeviceEventFilter, DeviceEventHandler],
        handler: Optional[DeviceEventHandler] = None,
        *,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """订阅单设备事件流。

        只传 `event_class_or_handler` 时，该参数必须是接收 `DeviceEvent` 的处理函数，用于接收
        全部事件；同时传 `handler` 时，第一个参数可为事件类、事件类元组或标准事件枚举，用于
        过滤后再异步调用 `handler`。``on_error`` 在该回调失败时接收强类型诊断信息；
        ``error_policy`` 决定记录、发布、禁用订阅或在 ``drain()`` 时抛出。返回取消订阅函数。
        """
        if handler is not None:
            return self.on(cast(DeviceEventFilter, event_class_or_handler), handler, on_error=on_error, error_policy=error_policy)
        possible_handler = event_class_or_handler
        if self._is_event_class_filter(cast(Any, possible_handler)):
            raise TypeError("Device event handler is required for event class subscriptions.")
        event_handler = cast(DeviceEventHandler, possible_handler)
        if not callable(event_handler):
            raise TypeError("Device event handler must be callable.")
        return self._subscribe_handler(event_handler, on_error=on_error, error_policy=error_policy)

    def _subscribe_handler(
        self,
        event_handler: DeviceEventHandler,
        *,
        on_error: Optional[Callable[[EventHandlerFailure], None]],
        error_policy: HandlerErrorPolicy,
        demand_filter: Any = None,
    ) -> Callable[[], None]:
        release_demand = None
        demand_handler = self._demand_handler
        if demand_filter is not None and demand_handler is not None:
            release_demand = demand_handler(demand_filter)
        subscription = _DeviceEventSubscription(
            handler=event_handler,
            on_error=on_error,
            error_policy=HandlerErrorPolicy(error_policy),
            release_demand=release_demand,
        )
        with self._lock:
            if self._closed:
                if release_demand is not None:
                    release_demand()
                raise RuntimeError("Device event stream is closed.")
            self._handlers.append(subscription)
        logger.debug("设备事件订阅完成: device_id=%s", self.source.device_id)

        def unsubscribe() -> None:
            removed = False
            with self._lock:
                try:
                    self._handlers.remove(subscription)
                    removed = True
                except ValueError:
                    pass
            if removed and subscription.release_demand is not None:
                subscription.release_demand()
                subscription.release_demand = None
            logger.debug("设备事件订阅取消: device_id=%s", self.source.device_id)

        return unsubscribe

    def on(
        self,
        event_filter: DeviceEventFilter,
        handler: DeviceEventHandler,
        *,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """按强类型事件类或标准事件枚举订阅设备事件。

        ``event_filter`` 可传入 GlobalPositionUpdatedEvent 等事件类，或
        EventType.ROUTE_STARTED 等标准事件枚举；事件发布后异步分发时执行
        ``handler``。``on_error`` 在处理器抛异常时接收诊断载荷；``error_policy`` 决定只记录、
        发布诊断事件、禁用订阅或在 ``drain()`` 中抛出。返回值是用于取消订阅的函数。
        """
        if isinstance(event_filter, STANDARD_EVENT_ENUMS):
            return self.on_name(event_filter, handler, on_error=on_error, error_policy=error_policy)
        if not self._is_event_class_filter(event_filter):
            raise TypeError("Device event filter must be a DeviceEvent class or standard event enum.")

        def filtered_handler(event: DeviceEvent) -> None:
            if isinstance(event, event_filter):
                handler(event)

        return self._subscribe_handler(
            filtered_handler,
            on_error=on_error,
            error_policy=error_policy,
            demand_filter=event_filter,
        )

    def on_name(
        self,
        event_name: DeviceEventName,
        handler: DeviceEventHandler,
        *,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """按标准事件枚举或事件名订阅设备事件。

        ``event_name`` 表示要匹配的事件类型，可传入标准事件枚举（如
        EventType.ROUTE_STARTED）或厂商、自定义、扩展事件名字符串；事件发布后异步
        分发时执行 ``handler``。``on_error`` 和 ``error_policy`` 的语义与 ``subscribe(...)``
        一致。返回值是用于取消订阅的函数。
        """
        if not event_name:
            raise ValueError("Device event name must not be empty.")
        normalized_event_name = event_name.value if isinstance(event_name, Enum) else str(event_name)

        def filtered_handler(event: DeviceEvent) -> None:
            if event.type.value == normalized_event_name:
                handler(event)

        return self._subscribe_handler(
            filtered_handler,
            on_error=on_error,
            error_policy=error_policy,
            demand_filter=event_name,
        )

    def set_demand_handler(self, handler: Optional[Callable[[Any], Callable[[], None]]]) -> None:
        """设置强类型事件订阅对应的底层数据需求处理器。

        ``handler`` 接收事件类、标准事件枚举或事件名，并返回幂等释放函数。设备适配器用它把
        ``events.on(...)`` 透明映射为厂商遥测租约；传 ``None`` 取消后续映射。已经存在的订阅不会
        被追溯修改，普通全事件 ``subscribe(handler)`` 也不会隐式开启全部高频数据。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Device event stream is closed.")
            self._demand_handler = handler

    def publish(
        self,
        event_type: DeviceEventType,
        category: EventCategory,
        payload: Any = None,
        *,
        extensions: Optional[Mapping[str, Mapping[str, Any]]] = None,
        correlation_id: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
        original: Optional[OriginalEvent] = None,
    ) -> DeviceEvent:
        """发布单设备事件并加入异步分发队列。

        ``event_type`` 必须是标准事件枚举或 ``ExtensionEventType``；``category`` 表示事件领域；
        标准事件会把 ``payload`` 转换成对应强类型载荷。``extensions`` 保存厂商补充字段，
        ``correlation_id`` 用于关联命令或上游请求，``original`` 保留厂商原始事件；
        ``occurred_at`` 表示事件在设备或适配器侧发生的 UTC 时间。返回创建好的事件对象。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Device event stream is closed.")
            self._sequence += 1
            sequence = self._sequence
        if not isinstance(event_type, (Enum, ExtensionEventType)):
            raise TypeError("Standard events must use an event enum; extensions must use ExtensionEventType.")
        normalized_type = event_type.value
        if isinstance(event_type, Enum):
            event_class = STANDARD_EVENT_CLASSES[event_type]
            event_payload = _coerce_standard_payload(event_type, payload)
        elif event_type.startswith(("vendor.", "extension.")):
            event_class = VendorEvent
            event_payload = payload
        else:
            event_class = CustomEvent
            event_payload = payload
        event = event_class(
            type=event_type,
            category=category,
            source=self.source,
            sequence=sequence,
            _data=event_payload,
            extensions={key: dict(value) for key, value in (extensions or {}).items()},
            original=original,
            correlation_id=correlation_id,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        self._offer_event(event)
        logger.debug(
            "设备事件入队: device_id=%s sequence=%d type=%s",
            self.source.device_id,
            sequence,
            normalized_type,
        )
        return event

    def _offer_event(self, event: DeviceEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except Full:
            replaced = False
            # Queue 没有公开的按类型替换接口；在其互斥锁内只替换可丢弃遥测，可靠事件不被遥测挤出。
            with self._queue.mutex:
                queued = self._queue.queue
                candidate_index = next(
                    (
                        index
                        for index, item in enumerate(queued)
                        if isinstance(item, DeviceEvent)
                        and item.category is EventCategory.TELEMETRY
                        and item.type == event.type
                    ),
                    None,
                )
                if candidate_index is None and event.category is not EventCategory.TELEMETRY:
                    candidate_index = next(
                        (
                            index
                            for index, item in enumerate(queued)
                            if isinstance(item, DeviceEvent) and item.category is EventCategory.TELEMETRY
                        ),
                        None,
                    )
                if candidate_index is not None:
                    del queued[candidate_index]
                    queued.append(event)
                    self._dropped_events += 1
                    replaced = True
                    self._queue.not_empty.notify()
                else:
                    self._dropped_events += 1
            if not replaced:
                logger.debug(
                    "设备事件队列已满，丢弃新事件: device_id=%s type=%s capacity=%d",
                    self.source.device_id,
                    event.type.value,
                    self._queue.maxsize,
                )
                return
            logger.debug(
                "设备事件队列已满，合并旧遥测: device_id=%s type=%s capacity=%d",
                self.source.device_id,
                event.type.value,
                self._queue.maxsize,
            )
        with self._lock:
            self._queue_high_watermark = max(self._queue_high_watermark, self._queue.qsize())

    def publish_vendor(
        self,
        event_type: str,
        category: EventCategory,
        payload: Any = None,
        *,
        native_data: Any = None,
    ) -> DeviceEvent:
        """发布厂商透传事件。

        ``event_type`` 必须以 ``vendor.`` 开头，``category`` 表示事件领域；``payload`` 会被转换为
        可序列化 ``raw_data``，``native_data`` 可保存原生 SDK 对象但不保证可序列化。
        返回入队后的 ``VendorEvent``。
        """
        if not event_type.startswith("vendor."):
            raise ValueError("Vendor event type must start with 'vendor.'.")
        raw_data = serialize_event_data(payload)
        provider = event_type.split(".", 2)[1]
        return self.publish(
            ExtensionEventType(event_type),
            category,
            raw_data,
            original=OriginalEvent(provider, event_type, raw_data, native_data),
        )

    def publish_custom(self, event_type: str, payload: Any = None) -> CustomEvent:
        """把设备相关的应用自定义事件发布到当前单设备事件流。

        ``event_type`` 必须使用 ``custom.<应用>.<事件>`` 命名空间；``payload`` 是调用方定义的
        事件数据，会原样保存在 ``CustomEvent.data``，并同步生成可序列化的
        ``OriginalEvent.raw_data``。方法在事件进入异步分发队列后返回对应 ``CustomEvent``；
        事件流已关闭时抛出 ``RuntimeError``，命名空间不合法时抛出 ``ValueError``。
        """
        if not event_type.startswith("custom."):
            raise ValueError("Custom event type must start with 'custom.'.")
        data = payload
        raw_data = serialize_event_data(data)
        return cast(
            CustomEvent,
            self.publish(
                ExtensionEventType(event_type),
                EventCategory.CUSTOM,
                data,
                original=OriginalEvent("application", event_type, raw_data),
            ),
        )

    def drain(self, timeout_s: float = 2.0) -> bool:
        """等待当前已入队事件完成分发。

        ``timeout_s`` 单位为秒，返回是否在超时前排空；若某订阅使用
        ``HandlerErrorPolicy.RAISE_ON_DRAIN``，排空后会在当前调用线程抛出
        ``DeviceEventHandlerError``。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if getattr(self._queue, "unfinished_tasks", 0) == 0:
                return self._finish_drain()
            time.sleep(0.01)
        drained = getattr(self._queue, "unfinished_tasks", 0) == 0
        return self._finish_drain() if drained else False

    def status(self) -> DeviceEventStreamStatus:
        """返回事件流、订阅数量、积压事件、回调错误和后台线程状态快照。"""
        with self._lock:
            return DeviceEventStreamStatus(
                closed=self._closed,
                subscriber_count=len(self._handlers),
                pending_events=int(getattr(self._queue, "unfinished_tasks", 0)),
                handler_error_count=self._handler_error_count,
                last_handler_error=self._last_handler_error,
                worker=self._worker.status(),
                dropped_events=self._dropped_events,
                queue_capacity=self._queue.maxsize,
                queue_high_watermark=self._queue_high_watermark,
            )

    def close(self) -> None:
        """关闭事件流并等待后台分发线程退出；关闭后不能继续发布或订阅事件，无返回值。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(self._handlers)
            self._handlers.clear()
            self._demand_handler = None
        for subscription in subscriptions:
            if subscription.release_demand is not None:
                subscription.release_demand()
                subscription.release_demand = None
        if not self._worker.stop(timeout_s=2.0):
            logger.debug("设备事件流后台线程未在期限内退出: device_id=%s", self.source.device_id)
        logger.debug("设备事件流已关闭: device_id=%s", self.source.device_id)

    def _dispatch(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._STOP:
                    return
                if not isinstance(item, DeviceEvent):
                    continue
                event = item
                with self._lock:
                    subscriptions = list(self._handlers)
                for subscription in subscriptions:
                    try:
                        subscription.handler(event)
                        subscription.consecutive_failures = 0
                    except Exception as exc:
                        logger.debug(
                            "设备事件回调异常: device_id=%s type=%s",
                            self.source.device_id,
                            event.type.value,
                            exc_info=True,
                        )
                        self._handle_handler_error(subscription, event, exc)
            finally:
                self._queue.task_done()

    def _handle_handler_error(
        self,
        subscription: _DeviceEventSubscription,
        event: DeviceEvent,
        exc: Exception,
    ) -> None:
        subscription.consecutive_failures += 1
        failure = EventHandlerFailure(
            event_id=event.event_id,
            event_type=event.type.value,
            handler_name=getattr(subscription.handler, "__qualname__", repr(subscription.handler)),
            exception_type=type(exc).__name__,
            message=str(exc),
            consecutive_failures=subscription.consecutive_failures,
        )
        with self._lock:
            self._handler_error_count += 1
            self._last_handler_error = failure
            if subscription.error_policy is HandlerErrorPolicy.RAISE_ON_DRAIN:
                self._drain_error = failure
            if subscription.error_policy is HandlerErrorPolicy.DISABLE:
                try:
                    self._handlers.remove(subscription)
                except ValueError:
                    pass
                if subscription.release_demand is not None:
                    subscription.release_demand()
                    subscription.release_demand = None
        if subscription.on_error is not None:
            try:
                subscription.on_error(failure)
            except Exception:
                logger.debug("设备事件错误处理器异常: device_id=%s", self.source.device_id, exc_info=True)
        should_publish = subscription.error_policy in (HandlerErrorPolicy.PUBLISH, HandlerErrorPolicy.DISABLE)
        if should_publish and event.type is not EventType.HANDLER_FAILED:
            try:
                self.publish(EventType.HANDLER_FAILED, EventCategory.ALERT, failure)
            except RuntimeError:
                logger.debug("事件流关闭期间无法发布回调失败事件: device_id=%s", self.source.device_id)

    def _finish_drain(self) -> bool:
        with self._lock:
            failure = self._drain_error
            self._drain_error = None
        if failure is not None:
            raise DeviceEventHandlerError(
                f"Event handler '{failure.handler_name}' failed for {failure.event_type}: {failure.message}"
            )
        return True

    @staticmethod
    def _is_event_class_filter(event_class: DeviceEventClass) -> bool:
        if isinstance(event_class, tuple):
            return bool(event_class) and all(
                isinstance(item, type) and issubclass(item, DeviceEvent) for item in event_class
            )
        return isinstance(event_class, type) and issubclass(event_class, DeviceEvent)
