"""Adapter public API."""

from fleetdock.adapters.base import (
    DeviceAdapter,
    LifecycleAdapter,
    MissionAdapter,
    MotionAdapter,
    NativeRouteCallbacks,
    NativeRouteRequest,
    RouteAdapter,
    RouteExecutionCallbacks,
    RouteExecutionRequest,
    TelemetryAdapter,
)
from fleetdock.adapters.mock import MockDroneAdapter, MockRobotDogAdapter
from fleetdock.adapters.mock_sensors import MockSensorBackend
from fleetdock.adapters.px4_mavlink import MavsdkClient, Px4MavlinkAdapter

__all__ = [
    "DeviceAdapter",
    "LifecycleAdapter",
    "MavsdkClient",
    "MockDroneAdapter",
    "MockRobotDogAdapter",
    "MockSensorBackend",
    "MissionAdapter",
    "MotionAdapter",
    "NativeRouteCallbacks",
    "NativeRouteRequest",
    "RouteAdapter",
    "RouteExecutionCallbacks",
    "RouteExecutionRequest",
    "Px4MavlinkAdapter",
    "TelemetryAdapter",
]
