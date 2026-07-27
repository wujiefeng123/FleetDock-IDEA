"""FleetDock 精简稳定 API；高级领域类型请从对应子模块导入。"""

from fleetdock.device_factory import DeviceFactory
from fleetdock.devices import BaseDevice, DeviceLifecycleState, DeviceState, Drone, RobotDog
from fleetdock.errors import FleetDockError, FleetDockValidationError
from fleetdock.fleet import DroneFleet, Fleet, RobotDogFleet
from fleetdock.models import (
    CapabilityName,
    CommandName,
    DeviceMetadata,
    ErrorCode,
    IntegrationMaturity,
    ProductType,
    StandardResponse,
)
from fleetdock.movement import GpsPoint, RelativeMove2D, RelativeMove3D, RelativePoint2D, RelativePoint3D
from fleetdock.plans import Plan
from fleetdock.readiness import DeviceDiagnostic, ReadinessReport
from fleetdock.sensors import (
    ImageFrame,
    ImuSample,
    PointCloudFrame,
    SensorDeliveryMode,
    SensorDescriptor,
    SensorKind,
    SensorOverflowPolicy,
    SensorStreamOptions,
    StereoFrame,
)

__all__ = [
    "BaseDevice",
    "CapabilityName",
    "CommandName",
    "DeviceDiagnostic",
    "DeviceFactory",
    "DeviceLifecycleState",
    "DeviceMetadata",
    "DeviceState",
    "Drone",
    "DroneFleet",
    "ErrorCode",
    "Fleet",
    "FleetDockError",
    "FleetDockValidationError",
    "GpsPoint",
    "IntegrationMaturity",
    "ImageFrame",
    "ImuSample",
    "Plan",
    "ProductType",
    "PointCloudFrame",
    "ReadinessReport",
    "RelativeMove2D",
    "RelativeMove3D",
    "RelativePoint2D",
    "RelativePoint3D",
    "RobotDog",
    "RobotDogFleet",
    "SensorDeliveryMode",
    "SensorDescriptor",
    "SensorKind",
    "SensorOverflowPolicy",
    "SensorStreamOptions",
    "StandardResponse",
    "StereoFrame",
]
