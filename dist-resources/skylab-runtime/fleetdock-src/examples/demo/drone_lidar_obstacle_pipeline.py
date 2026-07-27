"""显式订阅 LiDAR 点云，并把标准点云交给障碍物 Provider。"""

import os
import time

from fleetdock import (
    DeviceFactory,
    PointCloudFrame,
    SensorKind,
    avoidance,
)


def main() -> None:
    """展示“发现传感器—订阅点云—生成障碍物快照—释放资源”的完整数据流。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint, timeout_s=30.0).build()
    provider = None
    unsubscribe = None
    try:
        response = drone.connect()
        if not response.ok:
            raise RuntimeError(response.message)

        lidar = drone.sensors.get(SensorKind.LIDAR)
        provider = avoidance.LidarObstacleProvider(
            lidar,
            pose_provider=drone.state.snapshot,
            config=avoidance.LidarObstacleConfig.for_preset(avoidance.LidarAvoidancePreset.LOW_ALTITUDE),
        )
        drone.avoidance.add_provider(provider)

        def on_lidar(frame: PointCloudFrame) -> None:
            """把米制标准点云交给长期存活的 Provider，并打印转换结果。"""
            provider.observe(frame)
            print(f"点云帧 {frame.sequence}：{frame.point_count} 点")

        unsubscribe = drone.sensors.subscribe(lidar.sensor_id, on_lidar)
        time.sleep(10.0)
        print("障碍物数量：", len(provider.snapshot().obstacles))
    finally:
        if unsubscribe is not None:
            unsubscribe()
        if provider is not None:
            drone.avoidance.remove_provider(provider)
        drone.close()


if __name__ == "__main__":
    main()
