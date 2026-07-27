"""兼容旧版 LiDAR 避障导入路径；新代码请使用 :mod:`fleetdock.avoidance`。"""

from fleetdock.avoidance.lidar import (
    DepartureDirection,
    DepartureDirectionPolicy,
    LidarAvoidancePreset,
    LidarObstacleConfig,
    LidarObstacleProvider,
    LidarObstacleProviderStatus,
    LidarProviderReadiness,
)

__all__ = [
    "DepartureDirection",
    "DepartureDirectionPolicy",
    "LidarAvoidancePreset",
    "LidarObstacleConfig",
    "LidarObstacleProvider",
    "LidarObstacleProviderStatus",
    "LidarProviderReadiness",
]
