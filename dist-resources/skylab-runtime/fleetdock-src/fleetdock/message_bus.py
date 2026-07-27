"""FleetDock 进程内兼容消息总线。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
from threading import RLock
from typing import Callable, DefaultDict, List, Mapping, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class MessageEventType(str, Enum):
    """进程内消息总线事件类型；设备标准事件应使用 ``EventType``。"""

    SYSTEM = "system"  # SDK 生命周期或后台服务状态等系统级消息。
    DEVICE = "device"  # 设备连接、命令或状态变化等设备级消息。
    TELEMETRY = "telemetry"  # 位置、速度、电池等周期性遥测消息。
    CUSTOM = "custom"  # 上层应用自行定义的进程内消息。


@dataclass(frozen=True)
class MessageEvent:
    """进程内消息总线事件。

    ``topic`` 是调用方定义的主题，``payload`` 应尽量保持可序列化，``timestamp`` 使用 UTC。
    该类型不等同于带设备来源和强类型数据的标准 ``DeviceEvent``。
    """

    type: MessageEventType
    topic: str
    payload: Mapping[str, object] = field(default_factory=dict)
    source: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


MessageEventHandler = Callable[[MessageEvent], None]


class MessageBus:
    """线程安全的同步进程内发布订阅总线。

    处理器在发布线程中按事件类型和可选主题同步执行；``subscribe`` 返回的取消函数可重复调用。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: DefaultDict[MessageEventType, List[MessageEventHandler]] = defaultdict(list)
        self._topic_handlers: DefaultDict[str, List[MessageEventHandler]] = defaultdict(list)

    def subscribe(
        self,
        event_type: MessageEventType,
        handler: MessageEventHandler,
        topic: Optional[str] = None,
    ) -> Callable[[], None]:
        """订阅消息并返回取消函数。

        ``event_type`` 指定消息大类；``topic=None`` 接收该类全部主题，否则只接收指定主题。
        ``handler`` 在发布线程同步执行，调用方应避免长时间阻塞。
        """
        with self._lock:
            handlers = self._handlers[event_type] if topic is None else self._topic_handlers[self._topic_key(event_type, topic)]
            handlers.append(handler)
        logger.debug("事件订阅完成: type=%s topic=%s", event_type.value, topic or "*")

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers[event_type] if topic is None else self._topic_handlers[self._topic_key(event_type, topic)]
                self._remove_handler(handlers, handler)
            logger.debug("事件订阅取消: type=%s topic=%s", event_type.value, topic or "*")

        return unsubscribe

    def publish(self, event: MessageEvent) -> MessageEvent:
        """同步发布 ``event`` 并返回原事件对象。

        所有匹配事件类型及主题的处理器都会在当前线程执行；处理器异常直接向发布方传播。
        """
        with self._lock:
            handlers = list(self._handlers[event.type])
            handlers.extend(self._topic_handlers[self._topic_key(event.type, event.topic)])
        logger.debug(
            "事件发布: event_id=%s type=%s topic=%s source=%s handler_count=%d",
            event.event_id, event.type.value, event.topic, event.source, len(handlers),
        )
        for handler in handlers:
            handler(event)
        return event

    @staticmethod
    def _topic_key(event_type: MessageEventType, topic: str) -> str:
        return f"{event_type.value}:{topic}"

    @staticmethod
    def _remove_handler(handlers: List[MessageEventHandler], handler: MessageEventHandler) -> None:
        try:
            handlers.remove(handler)
        except ValueError:
            return


class EventHub:
    """为常见消息类别提供简短发布入口的兼容门面。"""

    def __init__(self, bus: Optional[MessageBus] = None) -> None:
        self.bus = bus or MessageBus()

    def subscribe(self, event_type: MessageEventType, handler: MessageEventHandler, topic: Optional[str] = None) -> Callable[[], None]:
        """订阅进程内消息并返回取消函数；``event_type``、``handler``、``topic`` 语义同总线入口。"""
        return self.bus.subscribe(event_type, handler, topic)

    def publish(
        self,
        event_type: MessageEventType,
        topic: str,
        payload: Optional[Mapping[str, object]] = None,
        source: Optional[str] = None,
    ) -> MessageEvent:
        """发布进程内消息并返回事件。

        ``event_type`` 和 ``topic`` 确定分发范围，``payload`` 默认为空映射，``source`` 可记录
        设备实例或应用来源；处理器在当前线程同步执行。
        """
        return self.bus.publish(MessageEvent(event_type, topic, payload or {}, source))

    def publish_system(self, topic: str, payload: Optional[Mapping[str, object]] = None) -> MessageEvent:
        """发布系统消息并返回事件；``topic`` 表示主题，``payload`` 是可序列化业务字段。"""
        return self.publish(MessageEventType.SYSTEM, topic, payload)

    def publish_device(self, topic: str, payload: Optional[Mapping[str, object]] = None, source: Optional[str] = None) -> MessageEvent:
        """发布设备消息并返回事件；``topic`` 是主题，``payload`` 是字段，``source`` 可填设备 ID。"""
        return self.publish(MessageEventType.DEVICE, topic, payload, source)

    def publish_telemetry(self, topic: str, payload: Optional[Mapping[str, object]] = None, source: Optional[str] = None) -> MessageEvent:
        """发布遥测消息并返回事件；``topic`` 是主题，``payload`` 是字段，``source`` 可填设备 ID。"""
        return self.publish(MessageEventType.TELEMETRY, topic, payload, source)

    def publish_custom(self, topic: str, payload: Optional[Mapping[str, object]] = None, source: Optional[str] = None) -> MessageEvent:
        """发布自定义消息并返回事件；``topic`` 是主题，``payload`` 是字段，``source`` 可填应用来源。"""
        return self.publish(MessageEventType.CUSTOM, topic, payload, source)


Event = MessageEvent
