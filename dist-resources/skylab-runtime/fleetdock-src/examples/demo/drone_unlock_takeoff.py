"""PX4 最小解锁起飞示例；展示遥测高度确认、返航和资源释放。"""

import time

from fleetdock import (
    DeviceFactory,
    DeviceMetadata,
    Drone,
    EventType,
    GlobalPositionUpdatedEvent,
)


TELEMETRY_PRINT_INTERVAL_S = 5.0


def main() -> None:
    """连接、解锁并起飞到目标高度，确认后触发返航并释放设备。"""
    drone: Drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint="udpin://0.0.0.0:14555", timeout_s=30.0)
        .metadata(DeviceMetadata(device_id="drone-001", name="演示无人机"))
        .build()
    )

    last_global_print_at = 0.0

    def on_position(event: GlobalPositionUpdatedEvent) -> None:
        """记录最新相对 Home 高度，供起飞完成确认循环读取。"""
        nonlocal last_global_print_at
        now = time.monotonic()
        if now - last_global_print_at < TELEMETRY_PRINT_INTERVAL_S:
            return
        last_global_print_at = now

        position = event.position
        absolute_altitude = position.absolute_altitude_m
        altitude = event.position.relative_altitude_m
        altitude_text = "未知" if altitude is None else f"{altitude:.2f} m"
        state = drone.state
        print(
            "[遥测] "
            f"lat={position.latitude_deg:.7f}, lon={position.longitude_deg:.7f}, "
            f"absolute_alt={absolute_altitude:.2f} m, relative_alt={altitude_text}, "
            f"local=({state.local_x_m}, {state.local_y_m}, {state.local_z_m})",
            flush=True,
        )

    unsubscribe_global = drone.events.on(EventType.GLOBAL_POSITION_UPDATED, on_position)
    should_rtl = False
    try:
        connect_response = drone.connect()
        print(connect_response)
        if not connect_response.ok:
            return

        unlock_response = drone.unlock()
        print(unlock_response)
        if not unlock_response.ok:
            return

        takeoff_response = drone.takeoff(altitude=12.0)
        print(takeoff_response)
        if not takeoff_response.ok:
            return

        should_rtl = True
        time.sleep(10.0)
    finally:
        try:
            if should_rtl:
                rtl_response = drone.return_to_launch()
                print(rtl_response)
                if not rtl_response.ok:
                    print(drone.land())
        finally:
            unsubscribe_global()
            drone.close()


if __name__ == "__main__":
    main()
