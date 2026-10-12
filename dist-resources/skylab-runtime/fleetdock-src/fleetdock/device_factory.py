"""Typed user-facing device builders."""

from __future__ import annotations

from typing import Any, Dict, Optional, TypeVar

from fleetdock.devices import BaseDevice, Drone, RobotDog
from fleetdock.events import EventHub
from fleetdock.factory import AdapterFactory, default_factory
from fleetdock.models import DeviceMetadata, FirmwareKey, FirmwareVersion, ProductType
from fleetdock.sensors import SensorSourceKind

BuilderT = TypeVar("BuilderT", bound="_DeviceBuilder")


class _DeviceBuilder:
    def __init__(
        self,
        product: ProductType,
        device_type: type[BaseDevice],
        adapter_factory: AdapterFactory,
        firmware_version: Optional[FirmwareKey] = None,
    ) -> None:
        self._product = product
        self._device_type = device_type
        self._adapter_factory = adapter_factory
        self._firmware_version = firmware_version
        self._params: Dict[str, Any] = {}
        self._metadata: Optional[DeviceMetadata] = None
        self._event_hub: Optional[EventHub] = None
        self._managed_object: Any = None
        self._close_managed_object = False

    def firmware(self: BuilderT, version: FirmwareKey) -> BuilderT:
        """选择产品支持的固件或厂商 SDK 版本并返回当前构造器；不立即创建设备。"""
        self._firmware_version = version
        return self

    def metadata(self: BuilderT, metadata: DeviceMetadata) -> BuilderT:
        """设置业务设备标识、名称和标签并返回当前构造器；这些信息不参与适配器选择。"""
        self._metadata = metadata
        return self

    def event_hub(self: BuilderT, event_hub: EventHub) -> BuilderT:
        """注入应用级消息总线并返回当前构造器；普通单设备场景无需设置。"""
        self._event_hub = event_hub
        return self

    def managed_object(
        self: BuilderT,
        managed_object: Any,
        *,
        close_on_close: bool = False,
    ) -> BuilderT:
        """复用调用方创建的厂商 client 并返回当前构造器。

        默认由调用方继续负责关闭 ``managed_object``；``close_on_close=True`` 表示把所有权交给 FleetDock，
        此后设备关闭或连接失败时 SDK 可以关闭该对象。该高级入口不改变标准设备调用方式。
        """
        self._managed_object = managed_object
        self._close_managed_object = close_on_close
        return self

    def build(self) -> BaseDevice:
        """根据已选择的产品、连接参数和适配器创建标准设备；校验失败时抛出公共 FleetDock 异常。"""
        return self._device_type(
            product=self._product,
            firmware_version=self._firmware_version,
            params=self._params,
            metadata=self._metadata,
            managed_object=self._managed_object,
            event_hub=self._event_hub,
            adapter_factory=self._adapter_factory,
            close_managed_object=self._close_managed_object,
        )


class Px4GenericBuilder(_DeviceBuilder):
    """通用 PX4 无人机构造器。

    该构造器面向使用 MAVLink/MAVSDK 接入的 PX4 产品，负责收集连接端点、
    超时和可选 MAVSDK server 端口等初始化参数，最终通过 ``build()`` 返回
    标准 ``Drone`` 对象。
    """

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(ProductType.PX4_GENERIC, Drone, adapter_factory, FirmwareVersion.PX4_V0_1)

    def params(
        self,
        endpoint: str,
        timeout_s: float = 10.0,
        mavsdk_server_port: Optional[int] = None,
        camera_stream_uri: Optional[str] = None,
        camera_stream_source: SensorSourceKind = SensorSourceKind.UNKNOWN,
        link_profile: str = "standard",
    ) -> "Px4GenericBuilder":
        """配置 PX4 MAVSDK 连接参数。

        `endpoint` 是 MAVLink 监听地址，`timeout_s` 是连接超时秒数；同一 Python 进程连接多架
        PX4 时可用 `mavsdk_server_port` 指定不同本地 gRPC 端口，未指定时 SDK 自动分配；
        ``camera_stream_uri`` 是可选 RTSP 等视频地址，只用于把已经确认存在的视频源注册为标准 RGB
        相机，不会写入日志；``camera_stream_source`` 声明该流来自物理设备、仿真器或未知来源，默认不
        猜测。``link_profile`` 选择 `simulation`、`standard` 或 `low_bandwidth` 遥测预算；室内低速
        数传使用 `low_bandwidth`，只影响 SDK 内部按需流和频率，不改变公共设备调用。是否强制解锁
        属于运行期决策，应在调用 `drone.unlock(force=True)` 时显式指定，而不是在建连参数中预设。
        返回当前 builder。
        """
        selected_link_profile = str(link_profile).strip().lower()
        if selected_link_profile not in {"simulation", "standard", "low_bandwidth"}:
            raise ValueError("link_profile must be simulation, standard, or low_bandwidth.")
        self._params = {
            "endpoint": endpoint,
            "timeout_s": timeout_s,
            "link_profile": selected_link_profile,
        }
        if mavsdk_server_port is not None:
            self._params["mavsdk_server_port"] = int(mavsdk_server_port)
        if camera_stream_uri:
            self._params["camera_stream_uri"] = str(camera_stream_uri)
            self._params["camera_stream_source"] = camera_stream_source
        return self

    def build(self) -> Drone:
        """创建并返回标准无人机对象；此时会完成产品 profile 校验和适配器实例化。"""
        return super().build()  # type: ignore[return-value]


class MockDroneBuilder(_DeviceBuilder):
    """零厂商依赖的标准模拟无人机构造器；用于学习、单元测试和业务流程演练。"""

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(ProductType.MOCK_DRONE, Drone, adapter_factory, FirmwareVersion.MOCK_V1)

    def build(self) -> Drone:
        """创建并返回模拟 ``Drone``；不连接 PX4、DJI、真机或外部仿真器。"""
        return super().build()  # type: ignore[return-value]


class MockRobotDogBuilder(_DeviceBuilder):
    """零厂商依赖的标准模拟机器狗构造器；用于学习、单元测试和业务流程演练。"""

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(ProductType.MOCK_ROBOT_DOG, RobotDog, adapter_factory, FirmwareVersion.MOCK_V1)

    def build(self) -> RobotDog:
        """创建并返回模拟 ``RobotDog``；不连接 Unitree 真机或外部仿真器。"""
        return super().build()  # type: ignore[return-value]


class UnitreeGo2Builder(_DeviceBuilder):
    """Unitree Go2 机器狗构造器。

    用于声明 Unitree Go2 的网络接口、可选机器人 IP 和托管对象等初始化信息，
    ``build()`` 返回标准 ``RobotDog`` 对象。
    """

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(ProductType.UNITREE_GO2, RobotDog, adapter_factory, FirmwareVersion.UNITREE_SDK2)

    def params(self, network_interface: str, robot_ip: Optional[str] = None) -> "UnitreeGo2Builder":
        """配置 Unitree SDK2 网络参数。

        ``network_interface`` 是本机用于连接机器狗的网卡名；``robot_ip`` 为可选
        机器人地址，未传入时由底层适配器按厂商 SDK 默认行为处理。返回当前构造器。
        """
        self._params = {"network_interface": network_interface}
        if robot_ip is not None:
            self._params["robot_ip"] = robot_ip
        return self

    def build(self) -> RobotDog:
        """创建并返回标准机器狗对象；此时会完成产品 profile 校验和适配器实例化。"""
        return super().build()  # type: ignore[return-value]


class UnitreeG1Builder(_DeviceBuilder):
    """Unitree G1 人形机器人构造器。

    当前 G1 先复用标准 ``RobotDog`` 设备对象承载足式/移动能力，后续可在产品
    profile 和设备类成熟后替换为更专门的人形机器人对象。
    """

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(ProductType.UNITREE_G1, RobotDog, adapter_factory, FirmwareVersion.UNITREE_SDK2)

    def params(self, network_interface: str, robot_ip: Optional[str] = None) -> "UnitreeG1Builder":
        """配置 Unitree SDK2 网络参数。

        ``network_interface`` 是本机连接设备的网卡名；``robot_ip`` 为可选设备地址。
        返回当前构造器，便于继续链式设置 metadata、firmware 或托管对象。
        """
        self._params = {"network_interface": network_interface}
        if robot_ip is not None:
            self._params["robot_ip"] = robot_ip
        return self

    def build(self) -> RobotDog:
        """创建并返回当前 G1 profile 对应的标准设备对象。"""
        return super().build()  # type: ignore[return-value]


class DjiMatrice350RtkBuilder(_DeviceBuilder):
    """DJI Matrice 350 RTK 无人机构造器。

    用于收集 DJI MSDK、PSDK 或 Cloud API 接入所需的模式和鉴权参数。敏感参数
    仅作为适配器初始化输入传递，不应写入日志或标准响应。
    """

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        super().__init__(
            ProductType.DJI_MATRICE_350_RTK,
            Drone,
            adapter_factory,
            FirmwareVersion.DJI_MSDK_V5,
        )

    def params(
        self,
        sdk_mode: str,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        mqtt_host: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> "DjiMatrice350RtkBuilder":
        """配置 DJI 接入参数。

        ``sdk_mode`` 表示使用 MSDK、PSDK 或 Cloud API 等接入模式；``app_key``、
        ``app_secret``、``mqtt_host`` 和 ``workspace_id`` 按具体 DJI 接入方式选填。
        返回当前构造器。
        """
        self._params = {
            key: value
            for key, value in {
                "sdk_mode": sdk_mode,
                "app_key": app_key,
                "app_secret": app_secret,
                "mqtt_host": mqtt_host,
                "workspace_id": workspace_id,
            }.items()
            if value is not None
        }
        return self

    def build(self) -> Drone:
        """创建并返回 DJI Matrice 350 RTK 对应的标准无人机对象。"""
        return super().build()  # type: ignore[return-value]


class DjiMavic3EnterpriseBuilder(DjiMatrice350RtkBuilder):
    """DJI Mavic 3 Enterprise 系列无人机构造器。

    参数语义与 Matrice 350 RTK 构造器一致，但产品 profile 指向轻量行业无人机
    系列，便于后续按产品差异选择不同适配器或能力声明。
    """

    def __init__(self, adapter_factory: AdapterFactory) -> None:
        _DeviceBuilder.__init__(
            self,
            ProductType.DJI_MAVIC_3_ENTERPRISE,
            Drone,
            adapter_factory,
            FirmwareVersion.DJI_MSDK_V5,
        )

    def params(
        self,
        sdk_mode: str,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        mqtt_host: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> "DjiMavic3EnterpriseBuilder":
        """配置 DJI Mavic 3 Enterprise 系列接入参数。

        ``sdk_mode`` 表示使用 MSDK 或 Cloud API 等接入模式；``app_key``、
        ``app_secret``、``mqtt_host`` 和 ``workspace_id`` 按具体 DJI 接入方式选填。
        返回当前构造器，便于继续链式设置 metadata 或托管对象。
        """
        super().params(sdk_mode, app_key, app_secret, mqtt_host, workspace_id)
        return self


class DeviceFactory:
    """面向用户的产品专属设备构造器入口。

    调用方通过 ``DeviceFactory.create().px4_generic().params(...).build()`` 先选择
    产品，再填写该产品明确需要的初始化参数，避免直接传入松散字典造成参数语义不清。
    """

    def __init__(self, adapter_factory: AdapterFactory = default_factory) -> None:
        self._adapter_factory = adapter_factory

    @classmethod
    def create(cls, adapter_factory: AdapterFactory = default_factory) -> "DeviceFactory":
        """创建设备构造器入口。

        ``adapter_factory`` 用于测试或高级扩展场景下注入自定义适配器工厂；普通调用方
        使用默认值即可。返回新的 ``DeviceFactory`` 实例。
        """
        return cls(adapter_factory=adapter_factory)

    def px4_generic(self) -> Px4GenericBuilder:
        """返回通用 PX4 无人机构造器，用于 MAVLink/MAVSDK 接入。"""
        return Px4GenericBuilder(self._adapter_factory)

    def mock_drone(self) -> MockDroneBuilder:
        """返回零厂商依赖的模拟无人机构造器，适合首次学习和业务流程测试。"""
        return MockDroneBuilder(self._adapter_factory)

    def mock_robot_dog(self) -> MockRobotDogBuilder:
        """返回零厂商依赖的模拟机器狗构造器，适合首次学习和业务流程测试。"""
        return MockRobotDogBuilder(self._adapter_factory)

    def dji_matrice_350_rtk(self) -> DjiMatrice350RtkBuilder:
        """返回 DJI Matrice 350 RTK 无人机构造器。"""
        return DjiMatrice350RtkBuilder(self._adapter_factory)

    def dji_mavic_3_enterprise(self) -> DjiMavic3EnterpriseBuilder:
        """返回 DJI Mavic 3 Enterprise 系列无人机构造器。"""
        return DjiMavic3EnterpriseBuilder(self._adapter_factory)

    def unitree_go2(self) -> UnitreeGo2Builder:
        """返回 Unitree Go2 机器狗构造器。"""
        return UnitreeGo2Builder(self._adapter_factory)

    def unitree_g1(self) -> UnitreeG1Builder:
        """返回 Unitree G1 设备构造器。"""
        return UnitreeG1Builder(self._adapter_factory)
