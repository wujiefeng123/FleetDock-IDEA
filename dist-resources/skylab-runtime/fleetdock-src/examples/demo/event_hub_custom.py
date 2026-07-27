"""进程内 EventHub 自定义消息示例；不连接或控制真实设备。"""

from fleetdock import EventHub, MessageEvent, MessageEventType


def on_custom_event(event: MessageEvent) -> None:
    """接收并打印指定主题的进程内自定义消息。"""
    print(f"received {event.type.value}:{event.topic} -> {dict(event.payload)}")


def main() -> None:
    """发布一次自定义消息并展示 EventHub 的订阅结果。"""
    hub = EventHub()
    hub.subscribe(MessageEventType.CUSTOM, on_custom_event, topic="mission.created")
    hub.publish_custom("mission.created", {"mission_id": "mission-001", "device_count": 2})


if __name__ == "__main__":
    main()
