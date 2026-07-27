"""兼容旧版测距避障导入路径；新代码请使用 :mod:`fleetdock.avoidance`。"""

from fleetdock.avoidance.ranging import (
    DistanceObstacleConfig,
    DistanceObstacleProvider,
    RadarObstacleConfig,
    RadarObstacleProvider,
    RangingObstacleProviderStatus,
)

__all__ = [
    "DistanceObstacleConfig",
    "DistanceObstacleProvider",
    "RadarObstacleConfig",
    "RadarObstacleProvider",
    "RangingObstacleProviderStatus",
]
