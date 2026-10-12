"""独立摄像机调试入口与标准传感器 OpenCV 渲染。"""

from __future__ import annotations

from dataclasses import fields
import logging
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from fleetdock.debug._ui import (
    _DebugTextRenderer,
    _restore_dpi_context_on_windows,
    _set_unicode_window_title_on_windows,
    _use_physical_pixels_on_windows,
)
from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.sensors import (
    AirspeedSample,
    BarometerSample,
    CameraIntrinsics,
    ContactSample,
    DistanceSample,
    FootForceSample,
    ForceTorqueSample,
    GenericSensorSample,
    GnssSample,
    ImageEncoding,
    ImageFrame,
    ImuSample,
    JointStateSample,
    MagnetometerSample,
    OpticalFlowSample,
    SensorHub,
    SensorSample,
    StereoFrame,
)

logger = logging.getLogger(__name__)


def camera(
    source: str | int = 0,
    handler: Optional[Callable[[ImageFrame], None]] = None,
    *,
    sensor_id: str = "camera.front",
    frame: CoordinateFrame = CoordinateFrame.BODY,
    intrinsics: Optional[CameraIntrinsics] = None,
    title: Optional[str] = None,
) -> None:
    """直接显示普通摄像头、RTSP 或视频文件，不创建设备对象。

    ``source`` 是整数摄像头索引、视频 URI 或文件路径；``handler`` 可选接收与窗口相同的标准
    ``ImageFrame``；``sensor_id`` 是临时 SensorHub 内的唯一 ID；``frame`` 声明安装坐标系；
    ``intrinsics`` 是可选像素制针孔内参；``title`` 是可选窗口标题。调用时注册并订阅一路临时标准
    摄像头，按 Q、Esc 或关闭窗口后自动取消订阅并释放全部资源，方法无返回值。
    """
    from fleetdock.debug.device import DeviceDebug

    sensors = SensorHub("standalone-camera-debug")
    debug = DeviceDebug(sensors)
    try:
        sensors._register_camera(
            source,
            sensor_id=sensor_id,
            frame=frame,
            intrinsics=intrinsics,
        )

        def on_sample(sample: SensorSample) -> None:
            if handler is not None:
                if not isinstance(sample, ImageFrame):
                    raise TypeError("独立摄像头调试入口只接受 ImageFrame 样本。")
                handler(sample)

        debug.camera(sensor_id, on_sample if handler is not None else None, title=title)
    finally:
        debug.close()
        sensors.close()

class _OpenCvDebugDisplay:
    def __init__(self) -> None:
        try:
            import cv2
            import numpy
        except ImportError as exc:
            raise RuntimeError(
                '传感器调试窗口需要可选依赖，请使用 "fleetdock[vision]" 安装。'
            ) from exc
        self._cv2 = cv2
        self._np = numpy
        self._text_renderer = _DebugTextRenderer(numpy)
        self._text_items: list[tuple[str, int, int, tuple[int, int, int], float]] = []
        self._title = ""
        self._fps: dict[str, tuple[float, float]] = {}
        self._previous_dpi_context: Any = None

    def open(self, title: str) -> None:
        self._title = title
        try:
            self._previous_dpi_context = _use_physical_pixels_on_windows()
            self._cv2.namedWindow(title, self._cv2.WINDOW_NORMAL)
            self._cv2.resizeWindow(title, 640, 320)
            _set_unicode_window_title_on_windows(title)
        except Exception as exc:
            raise RuntimeError("无法打开本地调试窗口，请确认当前环境具有图形桌面。") from exc

    def draw(self, title: str, samples: Mapping[str, SensorSample]) -> None:
        canvas = self._compose(samples)
        try:
            self._cv2.imshow(title, canvas)
        except Exception as exc:
            raise RuntimeError("无法绘制本地传感器调试窗口。") from exc

    def should_close(self, delay_ms: int) -> bool:
        key = int(self._cv2.waitKey(delay_ms)) & 0xFF
        if key in {27, ord("q"), ord("Q")}:
            return True
        try:
            return float(self._cv2.getWindowProperty(self._title, self._cv2.WND_PROP_VISIBLE)) < 1.0
        except Exception:
            return True

    def close(self) -> None:
        if not self._title:
            return
        try:
            self._cv2.destroyWindow(self._title)
        except Exception:
            logger.debug("关闭传感器调试窗口失败: title=%s", self._title, exc_info=True)
        finally:
            _restore_dpi_context_on_windows(self._previous_dpi_context)
            self._previous_dpi_context = None
        self._title = ""

    def _compose(self, samples: Mapping[str, SensorSample]) -> Any:
        self._text_items.clear()
        camera = next(
            (sample for sample in samples.values() if isinstance(sample, (ImageFrame, StereoFrame))),
            None,
        )
        imu = next((sample for sample in samples.values() if isinstance(sample, ImuSample)), None)
        height, width = 320, 640
        canvas = self._np.zeros((height, width, 3), dtype=self._np.uint8)
        canvas[:] = (18, 22, 26)
        panel_left = 24
        if camera is not None:
            try:
                image = self._camera_image(camera)
                image = self._fit(image, width, height)
                y = (height - image.shape[0]) // 2
                x = (width - image.shape[1]) // 2
                canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
            except Exception as exc:
                self._put(f"相机画面渲染失败：{exc}", 24, 60, (0, 80, 255), 0.45)
            self._render_text(canvas)
            return canvas
        elif imu is not None:
            panel_left = 300
            self._draw_attitude(canvas, imu, center=(150, 150), radius=110)
        if panel_left > 24:
            self._cv2.line(canvas, (panel_left, 24), (panel_left, height - 24), (65, 75, 82), 1)
        lines = ["FleetDock 标准传感器 HUD", "Q / Esc：关闭"]
        for sample in samples.values():
            lines.extend(self._sample_lines(sample))
        self._draw_lines(canvas, lines, panel_left + 12, 26)
        self._render_text(canvas)
        return canvas

    def _draw_attitude(
        self,
        canvas: Any,
        sample: ImuSample,
        *,
        center: tuple[int, int],
        radius: int,
    ) -> None:
        center_x, center_y = center
        orientation = sample.orientation_rpy_deg
        self._put("姿态 / 人工地平仪", center_x - radius, 34, (225, 235, 240), 0.46)
        if orientation is None:
            self._cv2.circle(canvas, center, radius, (70, 80, 88), 3)
            self._draw_aircraft_symbol(canvas, center)
            self._put("姿态未知", center_x - 36, center_y + radius + 32, (80, 170, 255), 0.46)
            return

        roll_deg = float(orientation.x)
        pitch_deg = float(orientation.y)
        yaw_deg = float(orientation.z) % 360.0
        diameter = radius * 2
        attitude: Any = self._np.empty((diameter, diameter, 3), dtype=self._np.uint8)
        horizon_y = int(radius + max(-45.0, min(45.0, pitch_deg)) * radius / 45.0)
        attitude[: max(0, min(diameter, horizon_y))] = (150, 92, 38)
        attitude[max(0, min(diameter, horizon_y)) :] = (52, 88, 132)
        rotation = self._cv2.getRotationMatrix2D((radius, radius), -roll_deg, 1.0)
        attitude = self._cv2.warpAffine(
            attitude,
            rotation,
            (diameter, diameter),
            flags=self._cv2.INTER_LINEAR,
            borderMode=self._cv2.BORDER_REPLICATE,
        )
        yy, xx = self._np.ogrid[:diameter, :diameter]
        mask = (xx - radius) ** 2 + (yy - radius) ** 2 <= radius**2
        top, left = center_y - radius, center_x - radius
        region = canvas[top : top + diameter, left : left + diameter]
        region[mask] = attitude[mask]

        self._cv2.circle(canvas, center, radius, (210, 220, 225), 3)
        self._cv2.line(
            canvas,
            (center_x, center_y - radius),
            (center_x, center_y - radius + 14),
            (0, 220, 255),
            2,
        )
        self._draw_aircraft_symbol(canvas, center)
        self._put(
            f"横滚 {roll_deg:+06.1f}°",
            center_x - radius,
            center_y + radius + 30,
            (220, 230, 235),
            0.36,
        )
        self._put(
            f"俯仰 {pitch_deg:+06.1f}°",
            center_x - 42,
            center_y + radius + 30,
            (220, 230, 235),
            0.36,
        )
        self._put(
            f"航向 {yaw_deg:05.1f}°",
            center_x + 82,
            center_y + radius + 30,
            (0, 220, 255),
            0.36,
        )

    def _draw_aircraft_symbol(self, canvas: Any, center: tuple[int, int]) -> None:
        center_x, center_y = center
        points = self._np.array(
            (
                (center_x, center_y - 42),
                (center_x + 11, center_y - 8),
                (center_x + 105, center_y + 8),
                (center_x + 105, center_y + 20),
                (center_x + 14, center_y + 14),
                (center_x + 12, center_y + 45),
                (center_x + 42, center_y + 58),
                (center_x + 42, center_y + 67),
                (center_x, center_y + 59),
                (center_x - 42, center_y + 67),
                (center_x - 42, center_y + 58),
                (center_x - 12, center_y + 45),
                (center_x - 14, center_y + 14),
                (center_x - 105, center_y + 20),
                (center_x - 105, center_y + 8),
                (center_x - 11, center_y - 8),
            ),
            dtype=self._np.int32,
        )
        self._cv2.fillPoly(canvas, (points,), (0, 215, 255), lineType=self._cv2.LINE_AA)
        self._cv2.polylines(canvas, (points,), True, (20, 35, 40), 2, self._cv2.LINE_AA)

    def _camera_image(self, sample: ImageFrame | StereoFrame) -> Any:
        if isinstance(sample, StereoFrame):
            left = self._image_frame(sample.left)
            right = self._image_frame(sample.right)
            target_height = min(left.shape[0], right.shape[0])
            if left.shape[0] != target_height:
                left = self._fit(left, left.shape[1], target_height)
            if right.shape[0] != target_height:
                right = self._fit(right, right.shape[1], target_height)
            return self._np.hstack((left, right))
        return self._image_frame(sample)

    def _image_frame(self, frame: ImageFrame) -> Any:
        channels_by_encoding = {
            ImageEncoding.RGB8: (self._np.uint8, 3),
            ImageEncoding.BGR8: (self._np.uint8, 3),
            ImageEncoding.RGBA8: (self._np.uint8, 4),
            ImageEncoding.MONO8: (self._np.uint8, 1),
            ImageEncoding.DEPTH_U16_MM: (self._np.uint16, 1),
            ImageEncoding.DEPTH_F32_M: (self._np.float32, 1),
        }
        if frame.encoding not in channels_by_encoding:
            raise ValueError(f"不支持的图像编码：{frame.encoding.value}")
        dtype, channels = channels_by_encoding[frame.encoding]
        item_size = int(self._np.dtype(dtype).itemsize)
        row_bytes = frame.stride_bytes or frame.width * channels * item_size
        if row_bytes % item_size:
            raise ValueError("图像步长未与像素数据类型对齐")
        values_per_row = row_bytes // item_size
        required_values = frame.height * values_per_row
        raw = self._np.frombuffer(frame.data, dtype=dtype, count=required_values)
        rows = raw.reshape(frame.height, values_per_row)
        pixels = rows[:, : frame.width * channels]
        if channels > 1:
            pixels = pixels.reshape(frame.height, frame.width, channels)
        if frame.encoding is ImageEncoding.RGB8:
            return self._cv2.cvtColor(pixels, self._cv2.COLOR_RGB2BGR)
        if frame.encoding is ImageEncoding.RGBA8:
            return self._cv2.cvtColor(pixels, self._cv2.COLOR_RGBA2BGR)
        if frame.encoding is ImageEncoding.MONO8:
            return self._cv2.cvtColor(pixels, self._cv2.COLOR_GRAY2BGR)
        if frame.encoding in {ImageEncoding.DEPTH_U16_MM, ImageEncoding.DEPTH_F32_M}:
            return self._colorize_depth(pixels)
        return pixels

    def _colorize_depth(self, depth: Any) -> Any:
        finite = self._np.isfinite(depth) & (depth > 0)
        normalized = self._np.zeros(depth.shape, dtype=self._np.uint8)
        if bool(finite.any()):
            valid = depth[finite]
            low, high = float(valid.min()), float(valid.max())
            if high > low:
                normalized[finite] = self._np.clip((depth[finite] - low) * 255.0 / (high - low), 0, 255)
            else:
                normalized[finite] = 255
        return self._cv2.applyColorMap(normalized, self._cv2.COLORMAP_TURBO)

    def _fit(self, image: Any, max_width: int, max_height: int) -> Any:
        scale = min(max_width / image.shape[1], max_height / image.shape[0])
        width = max(1, int(image.shape[1] * scale))
        height = max(1, int(image.shape[0] * scale))
        return self._cv2.resize(image, (width, height), interpolation=self._cv2.INTER_AREA)

    def _sample_lines(self, sample: SensorSample) -> list[str]:
        now = time.monotonic()
        previous_at, previous_fps = self._fps.get(sample.sensor_id, (sample.received_at_s, 0.0))
        interval = sample.received_at_s - previous_at
        fps = (1.0 / interval) if interval > 0 else previous_fps
        self._fps[sample.sensor_id] = (sample.received_at_s, fps)
        lines = [
            "",
            f"[{sample.sensor_id}] {sample.kind.value}",
            f"序号={sample.sequence}  坐标系={sample.frame.value}",
            f"频率={fps:.1f} Hz  延迟={max(0.0, now - sample.received_at_s) * 1000.0:.0f} ms",
        ]
        if isinstance(sample, ImageFrame):
            lines.append(f"{sample.width}x{sample.height}  {sample.encoding.value}")
        elif isinstance(sample, StereoFrame):
            lines.append(f"双目 {sample.left.width}x{sample.left.height}")
        elif isinstance(sample, ImuSample):
            lines.extend(
                (
                    f"角速度 rad/s：{self._vector(sample.angular_velocity_rad_s)}",
                    f"加速度 m/s²：{self._vector(sample.linear_acceleration_m_s2)}",
                    f"姿态角 °：{self._optional_vector(sample.orientation_rpy_deg)}",
                )
            )
        elif isinstance(sample, GnssSample):
            lines.extend(
                (
                    f"纬度/经度 °：{sample.latitude_deg:.7f}, {sample.longitude_deg:.7f}",
                    f"绝对高度 m：{self._optional_number(sample.absolute_altitude_m)}",
                    f"定位={sample.fix_type or '未知'}  卫星数="
                    f"{sample.satellite_count if sample.satellite_count is not None else '未知'}",
                )
            )
        elif isinstance(sample, MagnetometerSample):
            lines.append(f"磁场 T：{self._scientific_vector(sample.magnetic_field_t)}")
        elif isinstance(sample, BarometerSample):
            lines.append(f"气压 Pa：{sample.absolute_pressure_pa:.2f}")
            lines.append(f"气压高度 m：{self._optional_number(sample.pressure_altitude_m)}")
        elif isinstance(sample, DistanceSample):
            lines.append(f"距离 m：{sample.distance_m:.3f}")
        elif isinstance(sample, AirspeedSample):
            lines.append(f"空速 m/s：{sample.indicated_airspeed_m_s:.3f}")
        elif isinstance(sample, OpticalFlowSample):
            lines.append(f"光流 rad：{sample.integrated_flow_rad[0]:.4f}, {sample.integrated_flow_rad[1]:.4f}")
        elif isinstance(sample, JointStateSample):
            lines.append(f"关节数：{len(sample.joint_names)}")
            lines.append(f"关节位置 rad：{self._short_sequence(sample.position_rad)}")
        elif isinstance(sample, ForceTorqueSample):
            lines.append(f"力 N：{self._vector(sample.force_n)}")
            lines.append(f"力矩 N·m：{self._vector(sample.torque_nm)}")
        elif isinstance(sample, FootForceSample):
            lines.append(f"足端={sample.foot_name}  力 N：{self._vector(sample.force_n)}")
        elif isinstance(sample, ContactSample):
            lines.append(f"接触点={sample.contact_name}：{'是' if sample.in_contact else '否'}")
        elif isinstance(sample, GenericSensorSample):
            lines.extend(f"{key}: {self._short_value(value)}" for key, value in list(sample.values.items())[:6])
        else:
            lines.extend(self._generic_lines(sample))
        return lines

    def _generic_lines(self, sample: SensorSample) -> list[str]:
        excluded = {
            "sensor_id",
            "kind",
            "sequence",
            "frame",
            "captured_at_s",
            "received_at_s",
            "clock_domain",
            "extensions",
            "native_data",
            "data",
        }
        result = []
        for item in fields(sample):
            if item.name not in excluded:
                result.append(f"{item.name}: {self._short_value(getattr(sample, item.name))}")
            if len(result) >= 6:
                break
        return result

    def _draw_lines(self, canvas: Any, lines: Sequence[str], x: int, y: int) -> None:
        for line in lines:
            if y > canvas.shape[0] - 12:
                break
            color = (210, 230, 210) if line else (120, 120, 120)
            self._put(line, x, y, color, 0.40)
            y += 18

    def _put(self, text: str, x: int, y: int, color: tuple[int, int, int], scale: float) -> None:
        self._text_items.append((text, x, y, color, scale))

    def _render_text(self, canvas: Any) -> None:
        self._text_renderer.render(canvas, self._text_items)

    @staticmethod
    def _vector(value: Vector3) -> str:
        return f"{value.x:.3f}, {value.y:.3f}, {value.z:.3f}"

    @staticmethod
    def _scientific_vector(value: Vector3) -> str:
        return f"{value.x:.2e}, {value.y:.2e}, {value.z:.2e}"

    def _optional_vector(self, value: Optional[Vector3]) -> str:
        return "未知" if value is None else self._vector(value)

    @staticmethod
    def _optional_number(value: Optional[float]) -> str:
        return "未知" if value is None else f"{value:.3f}"

    @staticmethod
    def _short_sequence(value: Sequence[Any]) -> str:
        displayed = ", ".join(str(item) for item in value[:4])
        return displayed + (" ..." if len(value) > 4 else "")

    @staticmethod
    def _short_value(value: Any) -> str:
        text = str(value)
        return text if len(text) <= 56 else text[:53] + "..."
