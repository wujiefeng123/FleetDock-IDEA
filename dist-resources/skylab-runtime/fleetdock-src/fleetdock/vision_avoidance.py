"""兼容旧版视觉避障导入路径；新代码请使用 :mod:`fleetdock.avoidance`。"""

from fleetdock.avoidance.vision import (
    DepthCameraObstacleProvider,
    DepthObstacleConfig,
    StereoObstacleConfig,
    StereoObstacleProvider,
    VisionAvoidancePreset,
    VisionObstacleProviderStatus,
)

__all__ = [
    "DepthCameraObstacleProvider",
    "DepthObstacleConfig",
    "StereoObstacleConfig",
    "StereoObstacleProvider",
    "VisionAvoidancePreset",
    "VisionObstacleProviderStatus",
]
