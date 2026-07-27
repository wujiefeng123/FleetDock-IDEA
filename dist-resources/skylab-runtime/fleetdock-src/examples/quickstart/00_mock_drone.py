"""零厂商依赖学习标准无人机连接、起飞、移动、返航和资源释放。"""

from fleetdock.api import DeviceFactory, DeviceMetadata, Plan, RelativePoint3D


def main() -> None:
    """创建内置模拟无人机并执行短计划；不需要 PX4、DJI SDK、真机或仿真器。"""
    with (
        DeviceFactory.create()
        .mock_drone()
        .metadata(DeviceMetadata(device_id="mock-drone-001", name="入门模拟无人机"))
        .build()
    ) as drone:
        print(drone.connect())
        result = (
            Plan(drone)
            .unlock()
            .takeoff(altitude=3.0)
            .move(RelativePoint3D(x_m=2.0, y_m=0.0, z_m=3.0))
            .return_to_launch()
            .run()
        )
        print(result)


if __name__ == "__main__":
    main()
