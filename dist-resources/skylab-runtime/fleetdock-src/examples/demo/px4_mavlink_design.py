"""PX4 托管客户端设计示例；展示如何把已有 MAVSDK 兼容对象交给 FleetDock。"""

from fleetdock import (
    DeviceFactory,
    DeviceMetadata,
    Drone,
    GpsPoint,
    RelativeMove3D,
    RelativePoint3D,
    Route,
)


class DemoMavsdkClient:
    """最小 MAVSDK 兼容托管对象，仅用于展示适配器注入所需的方法形状。"""
    def __init__(self) -> None:
        self.calls = []

    def connect(self) -> None:
        """模拟建立连接；真实实现应在此等待 MAVSDK 连接状态。"""
        self.calls.append(("connect",))

    def arm(self, force: bool = False) -> None:
        """模拟解锁；``force`` 表示是否请求调试环境强制解锁。"""
        if force:
            self.calls.append(("arm", True))
        else:
            self.calls.append(("arm",))

    def takeoff(self, altitude: float) -> None:
        """记录相对 Home 起飞高度，单位为米。"""
        self.calls.append(("takeoff", altitude))

    def land(self) -> None:
        """模拟发送降落命令。"""
        self.calls.append(("land",))

    def set_velocity(self, x: float, y: float, z: float, speed: float) -> None:
        """模拟发送本地速度方向和米/秒速度目标。"""
        self.calls.append(("set_velocity", x, y, z, speed))

    def set_position(self, x_m: float, y_m: float, z_m: float, yaw_deg=None) -> None:
        """模拟发送本地 NED 位置米数和可选偏航角目标。"""
        self.calls.append(("set_position", x_m, y_m, z_m, yaw_deg))

    def goto_location(self, latitude_deg: float, longitude_deg: float, altitude_m: float, yaw_deg: float) -> None:
        """模拟发送 WGS84 经纬度、AMSL 米制高度和偏航角目标。"""
        self.calls.append(("goto_location", latitude_deg, longitude_deg, altitude_m, yaw_deg))

    def stop(self) -> None:
        """模拟停止当前运动。"""
        self.calls.append(("stop",))

    def emergency_stop(self) -> None:
        """模拟紧急停止；真实设备必须由应用建立额外安全边界。"""
        self.calls.append(("emergency_stop",))

    def get_status(self):
        """返回演示客户端的连接、解锁和高度状态快照。"""
        return {"calls": list(self.calls)}


def main() -> None:
    """把演示客户端托管给 PX4 设备对象，并调用标准 SDK 命令。"""
    mavsdk_client = DemoMavsdkClient()
    drone: Drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint="udpin://0.0.0.0:14555")
        .managed_object(mavsdk_client)
        .metadata(DeviceMetadata(device_id="px4-001", name="PX4 托管示例"))
        .build()
    )

    print(drone.connect())
    print(drone.unlock())
    print(drone.takeoff(altitude=12))
    print(drone.move(RelativePoint3D(x_m=10.0, y_m=0.0, z_m=12.0)))
    print(drone.move(RelativeMove3D(forward_m=5.0, up_m=2.0)))
    gps_center = GpsPoint(latitude_deg=31.2304, longitude_deg=121.4737, altitude_m=30.0)
    print(drone.move(gps_center))
    gps_spiral = Route.spiral(
        max_radius_m=20.0,
        spacing_m=5.0,
        gps_origin=gps_center,
        altitude_m=30.0,
    )
    print(f"GPS 螺旋航点数: {len(gps_spiral.targets)}")
    print(drone.get_status())


if __name__ == "__main__":
    main()
