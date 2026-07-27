"""兼容旧版点云障碍物导入路径；新代码请使用 :mod:`fleetdock.avoidance`。"""

from fleetdock.avoidance.point_cloud import (
    PointCloudCorridorAnalyzer,
    PointCloudDirection,
    PointCloudObstacleTracker,
    PointCloudObstacleTrackerStatus,
)

__all__ = [
    "PointCloudCorridorAnalyzer",
    "PointCloudDirection",
    "PointCloudObstacleTracker",
    "PointCloudObstacleTrackerStatus",
]
