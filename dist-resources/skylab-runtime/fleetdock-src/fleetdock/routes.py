"""航线与航点公共模型入口。

本模块承载任务编排中的路线生成相关公共 API，具体实现位于
``fleetdock.tasking.routes``。
"""

from fleetdock.tasking.routes import Route, RouteGeometry, RoutePattern, RouteTransform2D, Waypoint, WaypointAction
from fleetdock.movement import GpsPoint, RelativePoint2D, RelativePoint3D

__all__ = [
    "Route",
    "RouteGeometry",
    "RoutePattern",
    "RouteTransform2D",
    "GpsPoint",
    "RelativePoint2D",
    "RelativePoint3D",
    "Waypoint",
    "WaypointAction",
]
