"""跨品牌设备的标准传感器发现、数据模型与流式订阅运行时。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from queue import Empty, Full, Queue
from threading import Event as ThreadEvent, RLock, Thread
import time
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from fleetdock.events import CoordinateFrame, DeviceEventStream, EventCategory, EventType, Vector3
from fleetdock._frozen import freeze_mapping

logger = logging.getLogger(__name__)


class SensorKind(str, Enum):
    """FleetDock 跨无人机、足式设备和仿真器复用的标准传感器类型。"""

    IMU = "imu"  # 角速度、线加速度和可选姿态等惯性测量数据。
    RGB_CAMERA = "rgb_camera"  # 普通彩色图像流；具体像素编码由帧对象声明。
    DEPTH_CAMERA = "depth_camera"  # 每像素深度图；距离单位和编码由帧对象声明。
    STEREO_CAMERA = "stereo_camera"  # 已完成时间配对的左右双目图像流。
    LIDAR = "lidar"  # 二维或三维激光雷达点云/扫描数据。
    RADAR = "radar"  # 雷达检测、距离或速度观测数据。
    GNSS = "gnss"  # 原始或设备侧处理后的卫星定位传感器数据。
    MAGNETOMETER = "magnetometer"  # 磁场测量数据，标准单位为特斯拉。
    BAROMETER = "barometer"  # 气压与可选气压高度数据。
    DISTANCE = "distance"  # 单向或扇区式测距数据，标准单位为米。
    JOINT_STATE = "joint_state"  # 足式或机械臂设备的关节位置和速度数据。
    FORCE_TORQUE = "force_torque"  # 六维力/力矩传感器数据。
    FOOT_FORCE = "foot_force"  # 足端压力、支撑力或接触载荷数据。
    OPTICAL_FLOW = "optical_flow"  # 图像平面角位移与质量观测，用于无 GPS 环境的运动估计。
    AIRSPEED = "airspeed"  # 固定翼或复合翼设备的指示空速和可选真空速，单位为米/秒。
    CONTACT = "contact"  # 足端、轮胎或机体触点的接触状态与可选接触位置。


class ExtensionSensorKind(str):
    """厂商或应用自定义传感器类型。

    类型名必须使用 ``vendor.<厂商>.<名称>`` 或 ``custom.<应用>.<名称>`` 命名空间；该类型用于扩展
    ``SensorKind`` 未覆盖的专有观测，同时让字符串继续只表示具体 ``sensor_id``。
    """

    def __new__(cls, value: str) -> "ExtensionSensorKind":
        normalized = str(value).strip().lower()
        parts = normalized.split(".")
        if len(parts) < 3 or parts[0] not in {"vendor", "custom"} or any(not part for part in parts):
            raise ValueError(
                "Extension sensor kind must use vendor.<vendor>.<name> or custom.<application>.<name>."
            )
        return str.__new__(cls, normalized)

    @property
    def value(self) -> str:
        """返回完整命名空间字符串，便于与标准 ``SensorKind.value`` 采用相同读取方式。"""
        return str(self)


SensorType = SensorKind | ExtensionSensorKind


class SensorSourceKind(str, Enum):
    """传感器数据来源性质，用于区分物理观测、仿真观测和未知来源。"""

    PHYSICAL = "physical"  # 来自真机物理传感器或其厂商驱动。
    SIMULATED = "simulated"  # 来自 Project AirSim 等仿真环境。
    UNKNOWN = "unknown"  # 后端无法可靠判断数据来源。


class SensorDeliveryMode(str, Enum):
    """传感器样本向订阅者交付时的完整性与实时性策略。"""

    LATEST_ONLY = "latest_only"  # 丢弃尚未处理的旧样本，只保留最新样本，适合实时感知。
    BUFFERED = "buffered"  # 使用有界队列按顺序交付，适合 IMU、SLAM 和短时抖动吸收。
    RELIABLE = "reliable"  # 队列满时阻塞生产者，适合可承受反压的录制链路。


class SensorOverflowPolicy(str, Enum):
    """有界传感器队列满载时的处理策略。"""

    DROP_OLDEST = "drop_oldest"  # 丢弃最旧样本，为新样本腾出空间。
    DROP_NEWEST = "drop_newest"  # 保留当前队列，丢弃刚到达的新样本。
    BLOCK = "block"  # 阻塞生产者直到队列有空间；不得用于飞控关键回调线程。
    ERROR = "error"  # 记录溢出并拒绝新样本，供严格诊断场景使用。


class ImageEncoding(str, Enum):
    """标准图像帧的像素编码；无法标准化的厂商格式使用 ``NATIVE``。"""

    RGB8 = "rgb8"  # 每像素按 R、G、B 顺序存放三个 8 位通道。
    BGR8 = "bgr8"  # 每像素按 B、G、R 顺序存放三个 8 位通道。
    RGBA8 = "rgba8"  # 每像素存放四个 8 位 RGBA 通道。
    MONO8 = "mono8"  # 每像素一个 8 位灰度通道。
    DEPTH_U16_MM = "depth_u16_mm"  # 每像素一个 uint16 深度值，单位为毫米。
    DEPTH_F32_M = "depth_f32_m"  # 每像素一个 float32 深度值，单位为米。
    NATIVE = "native"  # 后端保留的原生编码，具体格式见 descriptor 或 extensions。


class PointCloudEncoding(str, Enum):
    """标准点云连续缓冲区的字段布局。"""

    XYZ_F32 = "xyz_f32"  # 每点依次存放 x、y、z 三个 float32，坐标单位为米。


@dataclass(frozen=True)
class SensorStreamOptions:
    """单个传感器订阅的交付和资源上限配置。

    ``delivery`` 未指定时由 SDK 根据传感器类型选择安全默认值；图像和点云采用只保留最新样本，
    IMU、关节与力传感器采用有界顺序缓冲。``capacity`` 仅对 ``BUFFERED`` 和 ``RELIABLE``
    生效，单位为样本数；``max_rate_hz`` 是交付给该订阅者的最大频率，``None`` 表示不额外限频。
    """

    delivery: Optional[SensorDeliveryMode] = None
    capacity: Optional[int] = None
    overflow: Optional[SensorOverflowPolicy] = None
    max_rate_hz: Optional[float] = None

    def resolved(self, kind: SensorType) -> "SensorStreamOptions":
        """根据传感器类型补齐默认值并返回新的不可变配置。

        ``kind`` 决定实时型或有序型默认值；返回值可直接用于运行时队列。参数组合不合法时抛出
        ``ValueError``，原对象不会被修改。
        """

        high_rate_ordered = {
            SensorKind.IMU,
            SensorKind.JOINT_STATE,
            SensorKind.FORCE_TORQUE,
            SensorKind.FOOT_FORCE,
        }
        delivery = self.delivery or (
            SensorDeliveryMode.BUFFERED if kind in high_rate_ordered else SensorDeliveryMode.LATEST_ONLY
        )
        if self.max_rate_hz is not None and self.max_rate_hz <= 0:
            raise ValueError("max_rate_hz must be greater than 0.")
        if delivery is SensorDeliveryMode.LATEST_ONLY:
            if self.capacity not in (None, 1):
                raise ValueError("LATEST_ONLY does not accept capacity other than 1.")
            if self.overflow not in (None, SensorOverflowPolicy.DROP_OLDEST):
                raise ValueError("LATEST_ONLY always drops the previous sample.")
            return SensorStreamOptions(delivery, 1, SensorOverflowPolicy.DROP_OLDEST, self.max_rate_hz)
        capacity = self.capacity if self.capacity is not None else (128 if kind is SensorKind.IMU else 16)
        if capacity <= 0:
            raise ValueError("capacity must be greater than 0.")
        overflow = self.overflow or (
            SensorOverflowPolicy.BLOCK
            if delivery is SensorDeliveryMode.RELIABLE
            else SensorOverflowPolicy.DROP_OLDEST
        )
        if delivery is SensorDeliveryMode.RELIABLE and overflow is not SensorOverflowPolicy.BLOCK:
            raise ValueError("RELIABLE delivery requires BLOCK overflow policy.")
        return SensorStreamOptions(delivery, capacity, overflow, self.max_rate_hz)


@dataclass(frozen=True)
class SensorMount:
    """传感器相对父坐标系的米制安装外参。

    ``position_m`` 是传感器原点相对 ``parent_frame`` 原点的 x/y/z 位移，单位为米；
    ``rotation_rpy_deg`` 是传感器坐标轴到父坐标轴的 Roll/Pitch/Yaw 旋转角，单位为度。
    该对象只描述标定结果，不负责运行期姿态变换；缺少可靠标定时应使用 ``None``，不得伪造零外参。
    """

    position_m: Vector3
    rotation_rpy_deg: Vector3 = Vector3(0.0, 0.0, 0.0)
    parent_frame: CoordinateFrame = CoordinateFrame.BODY


@dataclass(frozen=True)
class CameraIntrinsics:
    """针孔相机内参，所有焦距与主点坐标单位均为像素。

    ``width_px``、``height_px`` 是标定分辨率；``fx_px``、``fy_px`` 是水平与垂直焦距，
    ``cx_px``、``cy_px`` 是主点。对象不包含畸变参数，输入图像应已校正或由视觉后端负责校正。
    """

    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float

    def __post_init__(self) -> None:
        if min(self.width_px, self.height_px) <= 0 or min(self.fx_px, self.fy_px) <= 0:
            raise ValueError("Camera dimensions and focal lengths must be greater than 0.")

    @classmethod
    def from_horizontal_fov(cls, width_px: int, height_px: int, fov_deg: float) -> "CameraIntrinsics":
        """按水平视场角生成居中针孔内参。

        ``width_px``、``height_px`` 是图像像素尺寸，``fov_deg`` 是水平视场角且必须位于 0 到 180 度之间；
        返回假定方形像素、主点位于图像中心的近似内参，真机应优先使用标定结果。
        """

        import math

        if min(width_px, height_px) <= 0 or not 0 < fov_deg < 180:
            raise ValueError("Camera dimensions and horizontal FOV are invalid.")
        focal_px = width_px / (2.0 * math.tan(math.radians(fov_deg) * 0.5))
        return cls(width_px, height_px, focal_px, focal_px, (width_px - 1) * 0.5, (height_px - 1) * 0.5)


@dataclass(frozen=True)
class StereoCalibration:
    """已校正双目相机标定参数。

    ``left``、``right`` 是左右相机内参，``baseline_m`` 是两个光心之间的米制基线；当前标准 Provider
    假定输入图像已经极线校正且左右光轴平行，复杂畸变和外参校正由可替换视觉后端负责。
    """

    left: CameraIntrinsics
    right: CameraIntrinsics
    baseline_m: float

    def __post_init__(self) -> None:
        if self.baseline_m <= 0:
            raise ValueError("Stereo baseline must be greater than 0.")


@dataclass(frozen=True)
class SensorDescriptor:
    """连接后发现的标准传感器能力描述。

    ``sensor_id`` 在单个设备内唯一；``kind`` 用于枚举订阅；``frame`` 声明样本坐标系；
    ``is_primary`` 指示按枚举订阅时应选择的默认传感器。分辨率、量程、内外参和厂商字段放在
    ``mount`` 是可选强类型安装外参；分辨率、量程和厂商补充字段放在 ``metadata`` 中，缺失字段表示
    后端没有可靠信息，不得伪造为零。
    """

    sensor_id: str
    kind: SensorType
    source: SensorSourceKind = SensorSourceKind.UNKNOWN
    frame: CoordinateFrame = CoordinateFrame.UNKNOWN
    is_primary: bool = False
    nominal_rate_hz: Optional[float] = None
    mount: Optional[SensorMount] = None
    camera_intrinsics: Optional[CameraIntrinsics] = None
    stereo_calibration: Optional[StereoCalibration] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        if not self.sensor_id:
            raise ValueError("sensor_id must not be empty.")
        if self.nominal_rate_hz is not None and self.nominal_rate_hz <= 0:
            raise ValueError("nominal_rate_hz must be greater than 0.")


@dataclass(frozen=True, kw_only=True)
class SensorSample:
    """所有标准传感器样本共享的不可变元数据。

    ``captured_at_s`` 是来源时钟域中的采集秒数，``None`` 表示来源未提供；``received_at_s`` 是
    FleetDock 进程单调时钟秒数；``sequence`` 在同一传感器流内递增。``native_data`` 只供同进程
    高级集成使用，不保证可序列化，也不得成为公共功能的必要依赖。
    """

    sensor_id: str
    kind: SensorType
    sequence: int
    frame: CoordinateFrame = CoordinateFrame.UNKNOWN
    captured_at_s: Optional[float] = None
    received_at_s: float = field(default_factory=time.monotonic)
    clock_domain: str = "unknown"
    extensions: Mapping[str, Any] = field(default_factory=dict)
    native_data: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", freeze_mapping(self.extensions))


@dataclass(frozen=True, kw_only=True)
class GenericSensorSample(SensorSample):
    """尚无专用强类型模型时使用的标准键值样本。

    ``values`` 必须使用标准单位和稳定字段名；厂商补充字段放入 ``extensions``。该类型用于平滑
    扩展 Radar、GNSS、气压计等能力，不应用来替代已经定义的 IMU、图像或点云模型。
    """

    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "values", freeze_mapping(self.values))


@dataclass(frozen=True, kw_only=True)
class ImuSample(SensorSample):
    """标准 IMU 样本。

    ``angular_velocity_rad_s`` 和 ``linear_acceleration_m_s2`` 是 ``frame`` 坐标系内的三轴角速度与
    线加速度；``orientation_rpy_deg`` 的 x/y/z 依次表示机体 FRD 坐标系相对本地 NED 导航坐标系的
    Roll、Pitch、Yaw，单位为度，来源未提供绝对姿态时为 ``None``，不得通过角速度静默推算。
    """

    angular_velocity_rad_s: Vector3
    linear_acceleration_m_s2: Vector3
    orientation_rpy_deg: Optional[Vector3] = None


@dataclass(frozen=True, kw_only=True)
class GnssSample(SensorSample):
    """标准 GNSS 观测。

    经纬度单位为度，``absolute_altitude_m`` 是相对平均海平面的米制高度；精度、速度和航向字段为
    ``None`` 时表示来源未提供，``fix_type`` 保留跨厂商可读的定位级别名称。
    """

    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: Optional[float] = None
    horizontal_accuracy_m: Optional[float] = None
    vertical_accuracy_m: Optional[float] = None
    velocity_m_s: Optional[Vector3] = None
    ground_speed_m_s: Optional[float] = None
    heading_deg: Optional[float] = None
    satellite_count: Optional[int] = None
    fix_type: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class MagnetometerSample(SensorSample):
    """标准磁力计观测；``magnetic_field_t`` 为样本坐标系中的三轴磁感应强度，单位为特斯拉。"""

    magnetic_field_t: Vector3
    temperature_c: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class BarometerSample(SensorSample):
    """标准气压计观测；绝对和差分气压单位为帕，可选气压高度单位为米、温度单位为摄氏度。"""

    absolute_pressure_pa: float
    differential_pressure_pa: Optional[float] = None
    pressure_altitude_m: Optional[float] = None
    temperature_c: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class OpticalFlowSample(SensorSample):
    """标准光流积分观测。

    ``integrated_flow_rad`` 是采样窗口内图像 x/y 方向角位移，单位为弧度；``integration_time_s``
    单位为秒，``quality`` 归一化到 0 至 1，缺失距离时 ``ground_distance_m`` 为 ``None``。
    """

    integrated_flow_rad: tuple[float, float]
    integration_time_s: float
    quality: Optional[float] = None
    ground_distance_m: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class AirspeedSample(SensorSample):
    """标准空速观测；指示空速和可选真空速、地速均以米/秒表示。"""

    indicated_airspeed_m_s: float
    true_airspeed_m_s: Optional[float] = None
    groundspeed_m_s: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class ImageFrame(SensorSample):
    """单路标准图像帧。

    ``width``、``height`` 单位为像素；``stride_bytes`` 是相邻图像行起点间隔；``data`` 是只读或
    调用方不得修改的连续字节视图。数据只在回调持有期间保证有效，长期保存时调用方应显式复制。
    """

    width: int
    height: int
    encoding: ImageEncoding
    data: memoryview
    stride_bytes: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class StereoFrame(SensorSample):
    """已由后端按采集时间配对的左右双目图像帧。"""

    left: ImageFrame
    right: ImageFrame
    baseline_m: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class PointCloudFrame(SensorSample):
    """标准连续点云帧。

    当前 ``XYZ_F32`` 编码按每点三个 float32 顺序存储，坐标单位为米；``point_count`` 是有效点数。
    ``data`` 采用缓冲区视图以避免把十万级点展开成 Python 对象。调用方若跨回调保存数据应显式复制。
    """

    data: memoryview
    point_count: int
    encoding: PointCloudEncoding = PointCloudEncoding.XYZ_F32
    point_stride_bytes: int = 12

    def xyz_points(self) -> Sequence[tuple[float, float, float]]:
        """把 ``XYZ_F32`` 缓冲区解析为米制 XYZ 点序列并返回。

        该便利方法会创建 Python 元组，适合调试和小规模处理；高吞吐算法应直接使用 ``data`` 缓冲区
        或转换为 NumPy 视图。非 ``XYZ_F32`` 编码会抛出 ``ValueError``。
        """

        if self.encoding is not PointCloudEncoding.XYZ_F32:
            raise ValueError(f"Unsupported point cloud encoding: {self.encoding.value}")
        values = self.data.cast("B").cast("f")
        return tuple(
            (float(values[index]), float(values[index + 1]), float(values[index + 2]))
            for index in range(0, min(len(values), self.point_count * 3), 3)
        )


@dataclass(frozen=True)
class RadarDetection:
    """单个 Radar 极坐标检测。

    ``range_m`` 是距离，``azimuth_rad``、``elevation_rad`` 是相对传感器前向轴的方位角和俯仰角，
    ``radial_velocity_m_s`` 为沿波束方向的相对速度（负值接近），``rcs_m2`` 是可空雷达散射截面积。
    """

    range_m: float
    azimuth_rad: float
    elevation_rad: float
    radial_velocity_m_s: float = 0.0
    rcs_m2: Optional[float] = None


@dataclass(frozen=True)
class RadarTrack:
    """Radar 输出的稳定目标轨迹。

    ``track_id`` 在同一传感器流中标识目标；``position_m``、``velocity_m_s`` 和可选
    ``acceleration_m_s2`` 均位于样本声明坐标系，单位分别为米、米/秒和米/秒²。
    """

    track_id: str
    position_m: Vector3
    velocity_m_s: Vector3
    acceleration_m_s2: Optional[Vector3] = None
    rcs_m2: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class RadarFrame(SensorSample):
    """同一采集时刻的 Radar 检测或轨迹集合；两者都可以为空但不得被调用方修改。"""

    detections: tuple[RadarDetection, ...] = ()
    tracks: tuple[RadarTrack, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DistanceSample(SensorSample):
    """沿传感器前向轴测得的单向距离样本。

    ``distance_m`` 单位为米，``min_distance_m``、``max_distance_m`` 是可空有效量程；方向由
    ``SensorDescriptor.mount`` 决定。该模型适合近场停止或悬停保护，不表达完整障碍物外形。
    """

    distance_m: float
    min_distance_m: Optional[float] = None
    max_distance_m: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class JointStateSample(SensorSample):
    """足式机器人、机械臂等设备的标准关节状态。

    ``joint_names`` 与位置、速度及可选力矩元组按索引一一对应；位置单位为弧度，速度单位为弧度/秒，
    力矩单位为牛·米。来源缺少某组数据时使用空元组，不得填充伪造的零值。
    """

    joint_names: tuple[str, ...]
    position_rad: tuple[float, ...] = ()
    velocity_rad_s: tuple[float, ...] = ()
    effort_nm: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        count = len(self.joint_names)
        if any(values and len(values) != count for values in (self.position_rad, self.velocity_rad_s, self.effort_nm)):
            raise ValueError("Joint state value tuples must match joint_names length.")


@dataclass(frozen=True, kw_only=True)
class ForceTorqueSample(SensorSample):
    """六维力/力矩观测；``force_n`` 单位为牛，``torque_nm`` 单位为牛·米，方向由样本坐标系声明。"""

    force_n: Vector3
    torque_nm: Vector3


@dataclass(frozen=True, kw_only=True)
class FootForceSample(SensorSample):
    """单个足端的标准载荷观测；``force_n`` 是样本坐标系中的三轴牛顿力，``in_contact`` 可空。"""

    foot_name: str
    force_n: Vector3
    in_contact: Optional[bool] = None


@dataclass(frozen=True, kw_only=True)
class ContactSample(SensorSample):
    """足端、轮胎或机体触点的标准接触观测；位置单位为米，法向量位于样本坐标系。"""

    contact_name: str
    in_contact: bool
    position_m: Optional[Vector3] = None
    normal: Optional[Vector3] = None
    normal_force_n: Optional[float] = None


SensorHandler = Callable[[SensorSample], None]
SensorUnsubscribe = Callable[[], None]


@runtime_checkable
class SensorBackend(Protocol):
    """厂商 SDK、仿真器或中间件实现标准传感器流时遵循的后端协议。"""

    def list_sensors(self) -> Sequence[SensorDescriptor]:
        """发现并返回当前设备可订阅的传感器描述。

        支持原生枚举的后端必须返回实际实例；PX4 等只能枚举协议流的后端可返回可订阅通道，但必须把
        来源标记为 ``UNKNOWN``，并由 ``SensorStreamStatus`` 的最近接收时间明确反映运行期是否有数据。
        """

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        """启动 ``sensor_id`` 原生数据流，把标准样本交给 ``handler``，并返回幂等取消函数。"""

    def close(self) -> None:
        """取消全部原生订阅并释放连接；重复调用应保持安全，无返回值。"""


@dataclass(frozen=True)
class SensorStreamStatus:
    """单个标准传感器通道的运行状态和累计性能计数。"""

    sensor_id: str
    subscriber_count: int
    received_samples: int
    delivered_samples: int
    dropped_samples: int
    handler_error_count: int
    last_received_at_s: Optional[float]

    def age_s(self, *, now_s: Optional[float] = None) -> Optional[float]:
        """返回最近样本距 ``now_s`` 的单调时钟秒数；尚未收到样本时返回 ``None``。"""
        if self.last_received_at_s is None:
            return None
        return max(0.0, (time.monotonic() if now_s is None else now_s) - self.last_received_at_s)

    def is_stale(self, max_age_s: float, *, now_s: Optional[float] = None) -> bool:
        """判断最近样本是否超过 ``max_age_s`` 秒；未收到样本也视为失效。

        ``max_age_s`` 必须大于零；``now_s`` 是可选进程单调时钟秒值，默认读取当前时刻，主要用于
        确定性测试。返回值只反映数据新鲜度，不代表传感器测量值本身可信。
        """
        if max_age_s <= 0:
            raise ValueError("max_age_s must be greater than 0.")
        age = self.age_s(now_s=now_s)
        return age is None or age > max_age_s


class _Subscriber:
    _STOP = object()

    def __init__(self, descriptor: SensorDescriptor, handler: SensorHandler, options: SensorStreamOptions) -> None:
        self.descriptor = descriptor
        self.handler = handler
        self.options = options.resolved(descriptor.kind)
        self.queue: Queue[object] = Queue(maxsize=int(self.options.capacity or 1))
        self.closed = False
        self.delivered = 0
        self.dropped = 0
        self.errors = 0
        self._last_enqueued_at_s: Optional[float] = None
        self._thread = Thread(
            target=self._dispatch,
            name=f"fleetdock-sensor-{descriptor.sensor_id}",
            daemon=True,
        )
        self._thread.start()

    def offer(self, sample: SensorSample) -> None:
        if self.closed:
            return
        if self.options.max_rate_hz is not None and self._last_enqueued_at_s is not None:
            if sample.received_at_s - self._last_enqueued_at_s < 1.0 / self.options.max_rate_hz:
                self.dropped += 1
                return
        overflow = self.options.overflow
        try:
            if overflow is SensorOverflowPolicy.BLOCK:
                while not self.closed:
                    try:
                        self.queue.put(sample, timeout=0.1)
                        break
                    except Full:
                        continue
                if self.closed:
                    return
            else:
                self.queue.put_nowait(sample)
        except Full:
            if overflow is SensorOverflowPolicy.DROP_OLDEST:
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                except Empty:
                    pass
                self.dropped += 1
                try:
                    self.queue.put_nowait(sample)
                except Full:
                    self.dropped += 1
                    return
            else:
                self.dropped += 1
                if overflow is SensorOverflowPolicy.ERROR:
                    logger.debug("传感器订阅队列溢出: sensor_id=%s", self.descriptor.sensor_id)
                return
        self._last_enqueued_at_s = sample.received_at_s

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        while True:
            try:
                self.queue.put_nowait(self._STOP)
                break
            except Full:
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                    self.dropped += 1
                except Empty:
                    break
        self._thread.join(timeout=2.0)

    def _dispatch(self) -> None:
        while True:
            item = self.queue.get()
            try:
                if item is self._STOP:
                    return
                try:
                    self.handler(item)  # type: ignore[arg-type]
                    self.delivered += 1
                except Exception:
                    self.errors += 1
                    logger.debug(
                        "传感器订阅回调异常: sensor_id=%s",
                        self.descriptor.sensor_id,
                        exc_info=True,
                    )
            finally:
                self.queue.task_done()


class _SensorChannel:
    def __init__(self, descriptor: SensorDescriptor, backend: SensorBackend) -> None:
        self.descriptor = descriptor
        self.backend = backend
        self.lock = RLock()
        self.subscribers: list[_Subscriber] = []
        self.backend_unsubscribe: Optional[SensorUnsubscribe] = None
        self.received = 0
        self.completed_delivered = 0
        self.completed_dropped = 0
        self.completed_errors = 0
        self.last_received_at_s: Optional[float] = None
        self.latest_sample: Optional[SensorSample] = None

    def _retain_stats(self, subscriber: _Subscriber) -> None:
        self.completed_delivered += subscriber.delivered
        self.completed_dropped += subscriber.dropped
        self.completed_errors += subscriber.errors

    def add(self, handler: SensorHandler, options: SensorStreamOptions) -> SensorUnsubscribe:
        subscriber = _Subscriber(self.descriptor, handler, options)
        with self.lock:
            self.subscribers.append(subscriber)
            if self.backend_unsubscribe is None:
                try:
                    self.backend_unsubscribe = self.backend.subscribe(self.descriptor.sensor_id, self._publish)
                except Exception:
                    self.subscribers.remove(subscriber)
                    subscriber.close()
                    raise

        def unsubscribe() -> None:
            backend_unsubscribe: Optional[SensorUnsubscribe] = None
            with self.lock:
                try:
                    self.subscribers.remove(subscriber)
                except ValueError:
                    return
                if not self.subscribers:
                    backend_unsubscribe = self.backend_unsubscribe
                    self.backend_unsubscribe = None
            subscriber.close()
            with self.lock:
                self._retain_stats(subscriber)
            if backend_unsubscribe is not None:
                backend_unsubscribe()

        return unsubscribe

    def close(self) -> None:
        with self.lock:
            subscribers = list(self.subscribers)
            self.subscribers.clear()
            backend_unsubscribe = self.backend_unsubscribe
            self.backend_unsubscribe = None
        for subscriber in subscribers:
            subscriber.close()
            with self.lock:
                self._retain_stats(subscriber)
        if backend_unsubscribe is not None:
            backend_unsubscribe()

    def status(self) -> SensorStreamStatus:
        with self.lock:
            subscribers = list(self.subscribers)
        return SensorStreamStatus(
            sensor_id=self.descriptor.sensor_id,
            subscriber_count=len(subscribers),
            received_samples=self.received,
            delivered_samples=self.completed_delivered + sum(item.delivered for item in subscribers),
            dropped_samples=self.completed_dropped + sum(item.dropped for item in subscribers),
            handler_error_count=self.completed_errors + sum(item.errors for item in subscribers),
            last_received_at_s=self.last_received_at_s,
        )

    def _publish(self, sample: SensorSample) -> None:
        if sample.sensor_id != self.descriptor.sensor_id:
            logger.debug(
                "忽略传感器 ID 不匹配的样本: expected=%s actual=%s",
                self.descriptor.sensor_id,
                sample.sensor_id,
            )
            return
        if sample.kind != self.descriptor.kind:
            logger.debug(
                "忽略传感器类型不匹配的样本: sensor_id=%s expected=%s actual=%s",
                self.descriptor.sensor_id,
                self.descriptor.kind.value,
                sample.kind.value,
            )
            return
        with self.lock:
            self.received += 1
            self.last_received_at_s = sample.received_at_s
            self.latest_sample = sample
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            subscriber.offer(sample)


SensorIdentifier = str | SensorType


class SensorHub:
    """单设备标准传感器入口，负责发现、枚举订阅、流隔离和资源释放。

    调用方通常通过 ``device.sensors`` 使用本对象。``subscribe(SensorKind.LIDAR, handler)`` 会选择该
    类型唯一或标记为 primary 的传感器；同类型全部订阅需显式调用 ``subscribe_all``，避免意外启动
    多路高带宽摄像头。后端只在首次发现或订阅时访问底层协议，最后一个订阅取消后停止原生数据流。
    """

    def __init__(
        self,
        device_id: str,
        *,
        event_stream: Optional[DeviceEventStream] = None,
    ) -> None:
        """创建单设备传感器入口。

        ``device_id`` 是业务设备 ID；``event_stream`` 由 ``BaseDevice`` 注入时用于发布强类型发现、流生命周期和错误事件，
        独立使用 SensorHub 时可省略。外部传感器后端必须在对象创建后通过 ``register_backend()`` 注册，构造过程不发现、
        不订阅也不启动任何传感器数据流。
        """
        self.device_id = device_id
        self._lock = RLock()
        self._backends: list[SensorBackend] = []
        self._channels: dict[str, _SensorChannel] = {}
        self._discovered = False
        self._closed = False
        self._event_stream = event_stream

    def register_backend(self, backend: SensorBackend) -> "SensorHub":
        """为高级扩展注册应用自定义传感器后端并返回自身。

        ``backend`` 必须实现 ``SensorBackend``。注册本身不调用 ``list_sensors()`` 或 ``subscribe()``；
        没有活动流时延迟到下一次发现或订阅才访问后端。已有活动流时会立即刷新能力，以保持现有通道
        路由一致。关闭后的 Hub 不允许注册；后端资源由 ``SensorHub.close()`` 统一释放。FleetDock 内置的
        真机、仿真器和摄像头接入由设备适配器自动装配，业务代码不应通过本方法重复注册。
        """

        if not isinstance(backend, SensorBackend):
            raise TypeError("backend must implement SensorBackend.")
        with self._lock:
            if self._closed:
                raise RuntimeError("Sensor hub is closed.")
            self._backends.append(backend)
            should_refresh = self._discovered and any(
                channel.status().subscriber_count for channel in self._channels.values()
            )
            if not should_refresh:
                self._discovered = False
        logger.debug(
            "传感器后端已注册 device_id=%s backend=%s refresh=%s",
            self.device_id,
            type(backend).__name__,
            should_refresh,
        )
        if should_refresh:
            self.refresh()
        return self

    def _register_camera(
        self,
        source: str | int,
        *,
        sensor_id: str = "camera.front",
        frame: CoordinateFrame = CoordinateFrame.BODY,
        intrinsics: Optional[CameraIntrinsics] = None,
    ) -> SensorDescriptor:
        """为独立摄像头调试入口挂载 OpenCV 后端并返回内部标准描述。"""
        from fleetdock.adapters.opencv_camera import _OpenCvCameraBackend

        backend = _OpenCvCameraBackend(
            source,
            sensor_id=sensor_id,
            frame=frame,
            intrinsics=intrinsics,
        )
        self.register_backend(backend)
        return self.get(sensor_id)

    def refresh(self) -> tuple[SensorDescriptor, ...]:
        """重新发现所有后端并返回当前传感器描述。

        已有活动订阅的传感器必须继续存在；重复 ``sensor_id`` 或活动传感器消失时抛出 ``ValueError``，
        防止数据被静默路由到错误设备。该方法不会自动启动任何传感器流。
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("Sensor hub is closed.")
            backends = list(self._backends)
            active = {key for key, channel in self._channels.items() if channel.status().subscriber_count}
            previous = {key: channel.descriptor for key, channel in self._channels.items()}
        discovered: dict[str, _SensorChannel] = {}
        for backend in backends:
            for descriptor in backend.list_sensors():
                if descriptor.sensor_id in discovered:
                    raise ValueError(f"Duplicate sensor_id: {descriptor.sensor_id}")
                discovered[descriptor.sensor_id] = _SensorChannel(descriptor, backend)
        missing = active.difference(discovered)
        if missing:
            raise ValueError(f"Active sensors disappeared during refresh: {', '.join(sorted(missing))}")
        with self._lock:
            for sensor_id in active:
                discovered[sensor_id] = self._channels[sensor_id]
            self._channels = discovered
            self._discovered = True
        for sensor_id in sorted(set(discovered).difference(previous)):
            self._publish_diagnostic(EventType.SENSOR_DISCOVERED, discovered[sensor_id].descriptor)
        for sensor_id in sorted(set(previous).difference(discovered)):
            self._publish_diagnostic(EventType.SENSOR_LOST, previous[sensor_id])
        logger.debug("传感器发现完成: device_id=%s count=%d", self.device_id, len(discovered))
        return self.list()

    def list(self, kind: Optional[SensorType] = None) -> tuple[SensorDescriptor, ...]:
        """返回当前设备发现的传感器描述，可按 ``SensorKind`` 枚举过滤。

        ``kind`` 为 ``None`` 时返回全部描述，传入枚举时只返回该类型。首次调用会触发后端发现，但
        不会启动图像或点云数据流。返回值按传感器 ID 排序且不可变。
        """

        self._ensure_discovered()
        with self._lock:
            descriptors = [channel.descriptor for channel in self._channels.values()]
        if kind is not None:
            descriptors = [item for item in descriptors if item.kind == kind]
        return tuple(sorted(descriptors, key=lambda item: item.sensor_id))

    def get(self, identifier: SensorIdentifier) -> SensorDescriptor:
        """按传感器 ID 或 ``SensorKind`` 枚举返回一个传感器描述。

        ``identifier`` 可以是设备内唯一 ID 或公开枚举。枚举查询优先选择唯一的 ``is_primary`` 描述；
        没有 primary 且仅有一个候选时返回该候选。
        不存在或多个候选无法判定时抛出 ``LookupError``，避免静默启动错误摄像头。
        """

        self._ensure_discovered()
        if isinstance(identifier, (SensorKind, ExtensionSensorKind)):
            matches = list(self.list(identifier))
            primary = [item for item in matches if item.is_primary]
            if len(primary) == 1:
                return primary[0]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise LookupError(f"Sensor kind is not available: {identifier.value}")
            raise LookupError(f"Multiple sensors match {identifier.value}; select sensor_id explicitly.")
        with self._lock:
            channel = self._channels.get(str(identifier))
        if channel is None:
            raise LookupError(f"Sensor is not available: {identifier}")
        return channel.descriptor

    def supports(self, kind: SensorType) -> bool:
        """返回运行时发现结果中是否至少存在一个标准或扩展 ``kind`` 类型的传感器。"""

        return bool(self.list(kind))

    def subscribe(
        self,
        identifier: SensorIdentifier,
        handler: SensorHandler,
        *,
        options: Optional[SensorStreamOptions] = None,
    ) -> SensorUnsubscribe:
        """按传感器 ID 或 ``SensorKind`` 枚举订阅标准样本并返回取消函数。

        ``identifier`` 可以是设备内 ID 或公开枚举；``handler`` 在该订阅独立的后台线程中执行；
        ``options`` 未传时按传感器类型选择安全默认值。第一个订阅会启动原生流，最后一个订阅取消后
        停止原生流。枚举存在歧义时要求显式传入 ID。
        """

        if not callable(handler):
            raise TypeError("handler must be callable.")
        descriptor = self.get(identifier)
        with self._lock:
            channel = self._channels[descriptor.sensor_id]
        was_inactive = channel.status().subscriber_count == 0
        logger.debug("传感器订阅开始: device_id=%s sensor_id=%s", self.device_id, descriptor.sensor_id)
        try:
            unsubscribe_channel = channel.add(handler, options or SensorStreamOptions())
        except Exception as exc:
            self._publish_diagnostic(EventType.SENSOR_STREAM_ERROR, descriptor, message=str(exc))
            raise
        if was_inactive:
            self._publish_diagnostic(EventType.SENSOR_STREAM_STARTED, descriptor)
        closed = False

        def unsubscribe() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            unsubscribe_channel()
            if channel.status().subscriber_count == 0:
                self._publish_diagnostic(EventType.SENSOR_STREAM_STOPPED, descriptor)

        return unsubscribe

    def subscribe_all(
        self,
        kind: SensorType,
        handler: SensorHandler,
        *,
        options: Optional[SensorStreamOptions] = None,
    ) -> SensorUnsubscribe:
        """订阅指定枚举类型的全部传感器并返回统一取消函数。

        ``handler`` 接收标准样本，每个样本通过 ``sensor_id`` 区分来源；``options`` 对每一路使用相同
        的交付配置。该入口可能启动多路高带宽流，调用方应先通过 ``list(kind)`` 检查数量。任一订阅
        建立失败时会回滚已经建立的订阅。
        """

        unsubscribes: list[SensorUnsubscribe] = []
        try:
            for descriptor in self.list(kind):
                unsubscribes.append(self.subscribe(descriptor.sensor_id, handler, options=options))
        except Exception:
            for unsubscribe in reversed(unsubscribes):
                unsubscribe()
            raise
        if not unsubscribes:
            raise LookupError(f"Sensor kind is not available: {kind.value}")

        def unsubscribe_all() -> None:
            while unsubscribes:
                unsubscribes.pop()()

        return unsubscribe_all

    def latest(self, identifier: SensorIdentifier) -> Optional[SensorSample]:
        """返回 ``identifier`` 最近接收的标准样本，没有样本时返回 ``None``。

        该方法不会隐式启动原生数据流；调用方应先建立订阅，或使用 ``read_once()`` 等待下一条样本。
        返回对象不可变，但图像和点云缓冲区若需跨回调长期保存仍应按具体样本说明显式复制。
        """

        descriptor = self.get(identifier)
        with self._lock:
            channel = self._channels[descriptor.sensor_id]
        with channel.lock:
            return channel.latest_sample

    def read_once(self, identifier: SensorIdentifier, *, timeout_s: float = 5.0) -> SensorSample:
        """订阅并等待 ``identifier`` 的下一条样本，收到后取消临时订阅并返回样本。

        ``timeout_s`` 单位为秒且必须大于零；超时抛出 ``TimeoutError``。该入口适合诊断和低频读取，
        连续 IMU、图像或点云处理应使用 ``subscribe()``，避免反复启动原生流。
        """

        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0.")
        received = ThreadEvent()
        samples: list[SensorSample] = []

        def on_sample(sample: SensorSample) -> None:
            samples.append(sample)
            received.set()

        unsubscribe = self.subscribe(identifier, on_sample)
        try:
            if not received.wait(timeout_s):
                descriptor = self.get(identifier)
                raise TimeoutError(f"Timed out waiting for sensor sample: {descriptor.sensor_id}")
            return samples[0]
        finally:
            unsubscribe()

    def status(self, identifier: SensorIdentifier) -> SensorStreamStatus:
        """按 ID 或枚举 ``identifier`` 返回订阅数、收发、丢弃、回调错误和最新接收时间。"""

        descriptor = self.get(identifier)
        with self._lock:
            return self._channels[descriptor.sensor_id].status()

    def close(self) -> None:
        """取消全部订阅并关闭所有后端；重复调用安全且无返回值。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            channels = list(self._channels.values())
            backends = list(self._backends)
            self._channels.clear()
        for channel in channels:
            was_active = channel.status().subscriber_count > 0
            channel.close()
            if was_active:
                self._publish_diagnostic(EventType.SENSOR_STREAM_STOPPED, channel.descriptor)
        for backend in backends:
            try:
                backend.close()
            except Exception:
                logger.debug("关闭传感器后端失败: device_id=%s", self.device_id, exc_info=True)
        logger.debug("传感器 Hub 已关闭: device_id=%s", self.device_id)

    def _publish_diagnostic(
        self,
        event_type: EventType,
        descriptor: SensorDescriptor,
        *,
        message: str = "",
    ) -> None:
        if self._event_stream is None:
            return
        self._event_stream.publish(
            event_type,
            EventCategory.SENSOR,
            {
                "sensor_id": descriptor.sensor_id,
                "sensor_kind": descriptor.kind.value,
                "message": message,
            },
        )

    def _ensure_discovered(self) -> None:
        with self._lock:
            discovered = self._discovered
            closed = self._closed
        if closed:
            raise RuntimeError("Sensor hub is closed.")
        if not discovered:
            self.refresh()
