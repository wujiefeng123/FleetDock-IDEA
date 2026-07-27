"""零厂商依赖学习标准机器狗连接、站立、移动、坐下和资源释放。"""

from fleetdock.api import DeviceFactory, DeviceMetadata, Plan, RelativeMove2D


def main() -> None:
    """创建内置模拟机器狗并执行短计划；不需要 Unitree SDK、真机或仿真器。"""
    with (
        DeviceFactory.create()
        .mock_robot_dog()
        .metadata(DeviceMetadata(device_id="mock-dog-001", name="入门模拟机器狗"))
        .build()
    ) as dog:
        print(dog.connect())
        result = Plan(dog).stand().move(RelativeMove2D(forward_m=1.0)).sit().run()
        print(result)


if __name__ == "__main__":
    main()
