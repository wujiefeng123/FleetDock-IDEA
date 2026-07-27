"""Fleet 聚合设备事件订阅。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, Type, TypeAlias, Union, cast

from fleetdock.devices import BaseDevice
from fleetdock.events import (
    DeviceEvent, DeviceEventFilter, DeviceEventHandler, DeviceEventHandlerError,
    DeviceEventName, EventCategory, EventHandlerFailure, EventType, HandlerErrorPolicy,
)
from fleetdock.models import DeviceCategory, ProductKey, normalize_product

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fleetdock.fleet import Fleet


def device_public_id(device: BaseDevice) -> str:
    """返回 ``device`` 的业务 ID；未配置时返回其 SDK 实例 ID。"""
    return device.metadata.device_id or device.instance_id


# 兼容旧类型名称；Fleet 现在直接转发与 device.events 相同的 DeviceEvent 实例。
FleetEvent = DeviceEvent
FleetEventHandler = DeviceEventHandler
FleetEventPredicate = Callable[[DeviceEvent], bool]
FleetEventDeviceSelector: TypeAlias = Union[BaseDevice, Sequence[BaseDevice], "Fleet"]


@dataclass
class _FleetEventSubscription:
    handler: FleetEventHandler
    device_keys: Optional[frozenset[int]] = None
    device_categories: Optional[frozenset[str]] = None
    products: Optional[frozenset[str]] = None
    event_names: Optional[frozenset[str]] = None
    event_classes: Optional[tuple[Type[DeviceEvent], ...]] = None
    predicate: Optional[FleetEventPredicate] = None
    on_error: Optional[Callable[[EventHandlerFailure], None]] = None
    error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG
    consecutive_failures: int = 0


class FleetEvents:
    """Fleet 聚合事件流。

    每台成员设备只建立一条底层 `device.events` 订阅，随后按设备、类型、产品、事件名和
    自定义条件分发同一个 `DeviceEvent` 实例。Device 与 Fleet 使用相同事件枚举和回调参数，
    Fleet 只增加来源设备过滤能力。
    """

    def __init__(self, fleet: "Fleet") -> None:
        self._fleet = fleet
        self._lock = RLock()
        self._subscriptions: list[_FleetEventSubscription] = []
        self._device_unsubscribes: dict[int, Callable[[], None]] = {}
        self._closed = False
        self._drain_error: Optional[EventHandlerFailure] = None

    def subscribe(
        self,
        event_filter_or_handler: DeviceEventFilter | DeviceEventName | FleetEventHandler,
        handler: Optional[FleetEventHandler] = None,
        *,
        device: Optional[FleetEventDeviceSelector] = None,
        device_type: Optional[DeviceCategory | str | Sequence[DeviceCategory | str]] = None,
        device_category: Optional[DeviceCategory | str | Sequence[DeviceCategory | str]] = None,
        product: Optional[ProductKey | Sequence[ProductKey]] = None,
        event_type: Optional[DeviceEventFilter] = None,
        event_name: Optional[DeviceEventName | Sequence[DeviceEventName]] = None,
        predicate: Optional[FleetEventPredicate] = None,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """订阅 Fleet 聚合事件并返回取消订阅函数。

        只传 `event_filter_or_handler` 时，该参数必须是接收 `DeviceEvent` 的回调，用于接收全部
        聚合事件；同时传 `handler` 时，第一个参数可为事件类、标准事件枚举或事件名字符串，用于
        按事件过滤后再调用回调。`device` 可传单个设备、设备序列或筛选后的 Fleet 视图；
        `device_type`/`device_category`、`product` 和 `predicate` 会在 Fleet 自己的消息总线内过滤，
        不会重复订阅底层设备。仅传回调时，`event_type` 和 `event_name` 可作为关键字过滤事件；
        二者分别接收标准事件枚举/事件类和标准、厂商或自定义事件名。
        `on_error` 和 `error_policy` 与 `device.events.subscribe(...)` 一致。
        """
        if handler is None:
            if self._is_positional_event_filter(event_filter_or_handler):
                raise TypeError("Fleet event handler is required when subscribing by event.")
            if callable(event_filter_or_handler):
                event_handler = cast(FleetEventHandler, event_filter_or_handler)
            else:
                raise TypeError("Fleet event handler is required when subscribing by event.")
        else:
            if event_type is not None or event_name is not None:
                raise TypeError("Do not pass event_type/event_name when the event filter is positional.")
            event_handler = handler
            event_type, event_name = self._split_positional_event_filter(event_filter_or_handler)

        selected_devices = self._select_devices(device)
        subscription = _FleetEventSubscription(
            handler=event_handler,
            device_keys=None if device is None else frozenset(id(item) for item in selected_devices),
            device_categories=self._normalize_categories(device_category or device_type),
            products=self._normalize_products(product),
            event_names=self._event_names_from_filter(event_type, event_name),
            event_classes=self._event_classes_from_filter(event_type),
            predicate=predicate,
            on_error=on_error,
            error_policy=HandlerErrorPolicy(error_policy),
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("Fleet event bus is closed.")
            self._ensure_device_subscriptions_locked()
            self._subscriptions.append(subscription)
        logger.debug(
            "Fleet 事件订阅完成: fleet=%s device_count=%d",
            self._fleet.name or "-",
            len(selected_devices),
        )

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscriptions.remove(subscription)
                except ValueError:
                    pass
            logger.debug("Fleet 事件订阅取消: fleet=%s", self._fleet.name or "-")

        return unsubscribe

    def on(
        self,
        event_filter: DeviceEventFilter,
        handler: FleetEventHandler,
        *,
        device: Optional[FleetEventDeviceSelector] = None,
        device_type: Optional[DeviceCategory | str | Sequence[DeviceCategory | str]] = None,
        product: Optional[ProductKey | Sequence[ProductKey]] = None,
        predicate: Optional[FleetEventPredicate] = None,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """按标准事件枚举或强类型事件类订阅 Fleet 事件。

        `event_filter` 与 `device.events.on(...)` 含义一致，`handler` 接收匹配的 `DeviceEvent`；
        `device` 可传单个设备、设备序列或筛选后的 Fleet，`device_type` 和 `product` 用于限制
        来源，`predicate` 在异步分发时执行。
        `on_error` 与 `error_policy` 控制回调异常处理。返回取消订阅函数。
        """
        return self.subscribe(
            event_filter,
            handler,
            device=device,
            device_type=device_type,
            product=product,
            predicate=predicate,
            on_error=on_error,
            error_policy=error_policy,
        )

    def on_name(
        self,
        event_name: DeviceEventName,
        handler: FleetEventHandler,
        *,
        device: Optional[FleetEventDeviceSelector] = None,
        device_type: Optional[DeviceCategory | str | Sequence[DeviceCategory | str]] = None,
        product: Optional[ProductKey | Sequence[ProductKey]] = None,
        predicate: Optional[FleetEventPredicate] = None,
        on_error: Optional[Callable[[EventHandlerFailure], None]] = None,
        error_policy: HandlerErrorPolicy = HandlerErrorPolicy.LOG,
    ) -> Callable[[], None]:
        """按事件名订阅 Fleet 事件。

        `event_name` 可为标准事件枚举或厂商、自定义事件名，`handler` 接收匹配事件；`device`、
        `device_type` 和 `product` 限制来源，`predicate` 执行业务过滤，`on_error` 与
        `error_policy` 控制回调异常。返回取消订阅函数。
        """
        return self.subscribe(
            event_name,
            handler,
            device=device,
            device_type=device_type,
            product=product,
            predicate=predicate,
            on_error=on_error,
            error_policy=error_policy,
        )

    def drain(self, timeout_s: float = 2.0) -> bool:
        """等待 Fleet 内所有设备当前已入队事件完成分发。

        `timeout_s` 是每个单设备事件流的最长等待时间，单位为秒；返回 True 表示所有成员事件
        队列均已排空。
        """
        drained = all(device.events.drain(timeout_s=timeout_s) for device in self._fleet)
        if drained:
            with self._lock:
                failure = self._drain_error
                self._drain_error = None
            if failure is not None:
                raise DeviceEventHandlerError(
                    f"Event handler '{failure.handler_name}' failed for {failure.event_type}: {failure.message}"
                )
        return drained

    def close(self) -> None:
        """取消 Fleet 事件总线持有的底层设备订阅。

        关闭后不能继续新增 Fleet 事件订阅；该方法无返回值，通常由 `fleet.close()` 自动调用。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribes = tuple(self._device_unsubscribes.values())
            self._device_unsubscribes.clear()
            self._subscriptions.clear()
        for unsubscribe in unsubscribes:
            try:
                unsubscribe()
            except Exception:
                logger.debug("Fleet 底层事件订阅释放异常: fleet=%s", self._fleet.name or "-", exc_info=True)
        logger.debug("Fleet 事件总线关闭: fleet=%s", self._fleet.name or "-")

    def _select_devices(self, selection: Optional[FleetEventDeviceSelector]) -> tuple[BaseDevice, ...]:
        if selection is None:
            return tuple(self._fleet)
        if isinstance(selection, BaseDevice):
            devices: tuple[BaseDevice, ...] = (selection,)
        elif self._is_fleet(selection):
            devices = tuple(selection)
        else:
            devices = tuple(selection)
        allowed = {id(device) for device in self._fleet}
        return tuple(device for device in devices if id(device) in allowed)

    def _ensure_device_subscriptions_locked(self) -> None:
        for device in self._fleet:
            device_key = id(device)
            if device_key in self._device_unsubscribes:
                continue
            self._device_unsubscribes[device_key] = device.events.subscribe(self._wrap_device_handler(device))
            logger.debug("Fleet 绑定成员事件流: fleet=%s device_id=%s", self._fleet.name or "-", device_public_id(device))

    def _wrap_device_handler(self, device: BaseDevice) -> DeviceEventHandler:
        def wrapped(event: DeviceEvent) -> None:
            self._publish_from_device(device, event)

        return wrapped

    def _publish_from_device(self, device: BaseDevice, event: DeviceEvent) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions)
        if not subscriptions:
            return
        for subscription in subscriptions:
            if not self._matches(subscription, device, event):
                continue
            try:
                subscription.handler(event)
                subscription.consecutive_failures = 0
            except Exception as exc:
                self._handle_handler_error(device, subscription, event, exc)

    def _handle_handler_error(
        self,
        device: BaseDevice,
        subscription: _FleetEventSubscription,
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
        logger.debug(
            "Fleet 事件回调异常: fleet=%s source_device_id=%s type=%s",
            self._fleet.name or "-",
            event.source.device_id,
            event.type.value,
            exc_info=True,
        )
        if subscription.on_error is not None:
            try:
                subscription.on_error(failure)
            except Exception:
                logger.debug("Fleet 事件错误回调异常: fleet=%s", self._fleet.name or "-", exc_info=True)
        if subscription.error_policy is HandlerErrorPolicy.RAISE_ON_DRAIN:
            with self._lock:
                self._drain_error = failure
        should_publish = subscription.error_policy in (HandlerErrorPolicy.PUBLISH, HandlerErrorPolicy.DISABLE)
        if subscription.error_policy is HandlerErrorPolicy.DISABLE:
            with self._lock:
                try:
                    self._subscriptions.remove(subscription)
                except ValueError:
                    pass
        if should_publish and event.type is not EventType.HANDLER_FAILED:
            device.events.publish(EventType.HANDLER_FAILED, EventCategory.ALERT, failure)

    @staticmethod
    def _matches(subscription: _FleetEventSubscription, device: BaseDevice, event: DeviceEvent) -> bool:
        if subscription.device_keys is not None and id(device) not in subscription.device_keys:
            return False
        if subscription.device_categories is not None and event.source.device_category not in subscription.device_categories:
            return False
        if subscription.products is not None and event.source.product not in subscription.products:
            return False
        if subscription.event_names is not None and event.type.value not in subscription.event_names:
            return False
        if subscription.event_classes is not None and not isinstance(event, subscription.event_classes):
            return False
        if subscription.predicate is not None and not subscription.predicate(event):
            return False
        return True

    @staticmethod
    def _normalize_categories(
        value: Optional[DeviceCategory | str | Sequence[DeviceCategory | str]],
    ) -> Optional[frozenset[str]]:
        if value is None:
            return None
        values = (value,) if isinstance(value, (DeviceCategory, str)) else tuple(value)
        return frozenset(item.value if isinstance(item, DeviceCategory) else str(item) for item in values)

    @staticmethod
    def _normalize_products(value: Optional[ProductKey | Sequence[ProductKey]]) -> Optional[frozenset[str]]:
        if value is None:
            return None
        values = (value,) if isinstance(value, (Enum, str)) else tuple(value)
        return frozenset(normalize_product(item) for item in values)

    @staticmethod
    def _is_fleet(value: Any) -> bool:
        """避免模块循环导入，通过 Fleet 的稳定集合协议识别选择器。"""
        return hasattr(value, "device_ids") and callable(getattr(value, "by_id", None))

    @staticmethod
    def _is_positional_event_filter(value: Any) -> bool:
        if isinstance(value, (Enum, str)):
            return True
        if isinstance(value, tuple):
            return True
        return isinstance(value, type) and issubclass(value, DeviceEvent)

    @staticmethod
    def _split_positional_event_filter(
        event_filter: DeviceEventFilter | DeviceEventName | FleetEventHandler,
    ) -> tuple[Optional[DeviceEventFilter], Optional[DeviceEventName | Sequence[DeviceEventName]]]:
        if isinstance(event_filter, str):
            return None, event_filter
        if isinstance(event_filter, Enum):
            return event_filter, None
        if isinstance(event_filter, tuple):
            if all(isinstance(item, (Enum, str)) for item in event_filter):
                return None, cast(Sequence[DeviceEventName], event_filter)
            if all(isinstance(item, type) and issubclass(item, DeviceEvent) for item in event_filter):
                return cast(DeviceEventFilter, event_filter), None
            raise TypeError("Fleet event filter tuple must contain only event classes or only event names.")
        if isinstance(event_filter, type) and issubclass(event_filter, DeviceEvent):
            return cast(DeviceEventFilter, event_filter), None
        raise TypeError("Fleet event filter must be an event class, standard event enum, or event name string.")

    @staticmethod
    def _event_names_from_filter(
        event_type: Optional[DeviceEventFilter],
        event_name: Optional[DeviceEventName | Sequence[DeviceEventName]],
    ) -> Optional[frozenset[str]]:
        names: set[str] = set()
        if event_name is not None:
            values = (event_name,) if isinstance(event_name, (Enum, str)) else tuple(event_name)
            names.update(item.value if isinstance(item, Enum) else str(item) for item in values)
        if isinstance(event_type, Enum):
            names.add(event_type.value)
        return frozenset(names) if names else None

    @staticmethod
    def _event_classes_from_filter(event_type: Optional[DeviceEventFilter]) -> Optional[tuple[Type[DeviceEvent], ...]]:
        if event_type is None or isinstance(event_type, Enum):
            return None
        if isinstance(event_type, tuple):
            return event_type
        return (event_type,)
