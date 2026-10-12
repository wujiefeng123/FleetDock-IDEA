"""跨品牌本地调试工具的稳定公共入口。"""

from fleetdock.debug.camera import camera
from fleetdock.debug.device import DeviceDebug
from fleetdock.debug.hud import SensorDashboard

__all__ = ["DeviceDebug", "SensorDashboard", "camera"]
