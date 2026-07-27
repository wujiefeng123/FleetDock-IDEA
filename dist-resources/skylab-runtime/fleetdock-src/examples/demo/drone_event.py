"""PX4 单设备事件示例；展示标准遥测事件订阅、过滤和确定性释放。"""

from __future__ import annotations

import random
import time

from fleetdock import (
    DeviceEvent,
    DeviceFactory,
    DeviceMetadata,
    Drone,
    EventType,
    EventHandlerFailure,
    GlobalPositionUpdatedEvent,
    HandlerErrorPolicy,
    Plan,
    Route,
    RouteMotion,
    UnlockStrategy,
)
from drone_plan import print_route_parameters


MIN_SAFE_ALTITUDE_M = 14.0
TAKEOFF_ALTITUDE_M = 10.0
CLIMB_SPEED_M_S = 1.0
ROUTE_SPEED_M_S = 3.0
TELEMETRY_PRINT_INTERVAL_S = 5.0
VERBOSE_ALL_EVENTS = False


def print_event(event: DeviceEvent) -> None:
    """打印标准事件的类型、来源和事件对象。"""
    event_type = getattr(event.type, "value", str(event.type))
    payload = event.to_dict()["data"]
    print(f"[event] #{event.sequence} {event.category.value} {event_type} data={payload}")


def print_handler_error(failure: EventHandlerFailure) -> None:
    """打印事件回调失败诊断；异常已被事件流隔离，不会阻断其他订阅者。"""
    print(f"[event-handler-error] {failure.handler_name}: {failure.exception_type}: {failure.message}")


def main() -> None:
    """订阅 PX4 标准事件，执行短航线后取消订阅并关闭设备。"""
    drone: Drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint="udpin://0.0.0.0:14555", timeout_s=30.0)
        .metadata(DeviceMetadata(device_id="drone-001", name="事件订阅演示无人机"))
        .build()
    )

    last_position_print_at = 0.0

    def on_position(event: GlobalPositionUpdatedEvent) -> None:
        """接收全球位置事件，并按节流周期打印经纬度和相对高度。"""
        nonlocal last_position_print_at
        position = event.position
        altitude = position.relative_altitude_m
        if altitude is None:
            return
        now = time.monotonic()
        if now - last_position_print_at < TELEMETRY_PRINT_INTERVAL_S:
            return
        last_position_print_at = now
        print(
            "[遥测] "
            f"lat={position.latitude_deg:.7f}, lon={position.longitude_deg:.7f}, "
            f"absolute_alt={position.absolute_altitude_m:.2f} m, relative_alt={altitude:.2f} m",
            flush=True,
        )

    unsubscribe_all_events = (
        drone.events.subscribe(
            print_event,
            on_error=print_handler_error,
            error_policy=HandlerErrorPolicy.PUBLISH,
        )
        if VERBOSE_ALL_EVENTS
        else lambda: None
    )
    unsubscribe_position = drone.events.on(EventType.GLOBAL_POSITION_UPDATED, on_position)
    should_rtl = False

    try:
        connect_response = drone.connect()
        print(connect_response)
        if not connect_response.ok:
            return
        should_rtl = True
        radius_m = random.uniform(20.0, 30.0)
        route = Route.circle(radius_m=radius_m, segments=24, name="event-demo-circle")
        print_route_parameters(
            route,
            label="事件圆形航线",
            altitude_m=MIN_SAFE_ALTITUDE_M,
            speed_m_s=ROUTE_SPEED_M_S,
            motion_mode="auto",
        )
        result = (
            Plan(drone)
            .climb_speed(CLIMB_SPEED_M_S)
            .wait_until_ready(timeout_s=60.0, require_armable=False)
            .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
            .takeoff(altitude=MIN_SAFE_ALTITUDE_M)
            .route(
                route,
                altitude=MIN_SAFE_ALTITUDE_M,
                speed=ROUTE_SPEED_M_S,
                motion=RouteMotion(mode="auto"),
                timeout_s=120.0,
            )
            .run(step_timeout_s=90.0)
        )
        print(result)
    finally:
        try:
            if should_rtl:
                rtl_response = drone.return_to_launch()
                print(rtl_response)
                if not rtl_response.ok:
                    print(drone.land())
        finally:
            unsubscribe_position()
            unsubscribe_all_events()
            drone.close()


if __name__ == "__main__":
    main()
