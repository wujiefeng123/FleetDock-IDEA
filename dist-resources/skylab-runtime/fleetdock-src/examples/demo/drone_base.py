"""PX4 单机即时命令示例；展示连接、事件、安全检查、移动和异常返航。"""

from __future__ import annotations

from fleetdock import DeviceEvent, DeviceFactory, DeviceMetadata, Drone, RelativePoint3D


TAKEOFF_ALTITUDE_M = 10.0


def print_event(event: DeviceEvent) -> None:
    """打印标准设备事件的类型和来源，便于观察命令与遥测流程。"""
    event_type = getattr(event.type, "value", str(event.type))
    payload = event.to_dict()["data"]
    print(f"[设备事件] 序号={event.sequence} 类别={event.category.value} 类型={event_type} 数据={payload}")


def raise_if_low_battery(drone) -> None:
    """电量低于 25% 时抛出异常，演示应用侧最小安全检查。"""
    battery = drone.state.snapshot().battery_remaining_fraction
    if battery is not None and battery < 0.25:
        raise RuntimeError("电量低，基础指令流程中断并返航。")


def run_or_raise(response) -> None:
    """检查标准响应；失败时抛出异常以进入统一返航清理流程。"""
    print(response)
    if not response.ok:
        raise RuntimeError(response.message)


def main() -> None:
    """执行连接、起飞、移动和返航流程，并在 finally 中关闭设备。"""
    drone: Drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint="udpin://0.0.0.0:14555", timeout_s=30.0)
        .metadata(DeviceMetadata(device_id="drone-001", name="基础指令演示无人机"))
        .build()
    )
    unsubscribe = drone.events.subscribe(print_event)
    should_rtl = False

    try:
        run_or_raise(drone.connect())
        run_or_raise(drone.unlock())
        run_or_raise(drone.takeoff(altitude=TAKEOFF_ALTITUDE_M))
        should_rtl = True

        raise_if_low_battery(drone)
        run_or_raise(drone.move(RelativePoint3D(x_m=15.0, y_m=0.0, z_m=TAKEOFF_ALTITUDE_M)))
        run_or_raise(drone.move(RelativePoint3D(x_m=15.0, y_m=10.0, z_m=TAKEOFF_ALTITUDE_M)))

        run_or_raise(drone.return_to_launch())
        should_rtl = False
        print("基础指令流程执行完成")
    except Exception as exc:
        print(f"基础指令流程失败：{exc}")
        if should_rtl:
            rtl_response = drone.return_to_launch()
            print(rtl_response)
            if not rtl_response.ok:
                print(drone.land())
    finally:
        unsubscribe()
        drone.close()


if __name__ == "__main__":
    main()
