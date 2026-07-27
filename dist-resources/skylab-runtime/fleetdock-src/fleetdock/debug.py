"""基于标准传感器样本的跨品牌本地调试窗口。"""

from __future__ import annotations

from dataclasses import fields
import logging
from threading import Event, RLock, Thread, current_thread
import time
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from fleetdock._debug_ui import (
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
    SensorHandler,
    SensorHub,
    SensorIdentifier,
    SensorKind,
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


class _DebugDisplay(Protocol):
    def open(self, title: str) -> None: ...

    def draw(self, title: str, samples: Mapping[str, SensorSample]) -> None: ...

    def should_close(self, delay_ms: int) -> bool: ...

    def close(self) -> None: ...


DisplayFactory = Callable[[], _DebugDisplay]


class _LatestSampleBuffer:
    """按传感器 ID 只保留最新样本，避免高频通道挤掉低频通道。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._samples: dict[str, SensorSample] = {}

    def offer(self, sample: SensorSample) -> None:
        with self._lock:
            self._samples[sample.sensor_id] = sample

    def drain(self) -> dict[str, SensorSample]:
        with self._lock:
            samples = self._samples
            self._samples = {}
        return samples


class SensorDashboard:
    """在后台窗口中显示调用方主动提交的标准传感器样本。

    本对象由 ``device.debug.dashboard()`` 创建，不发现也不订阅传感器。调用方应把对象本身、
    ``update()`` 或 ``handler()`` 返回的代理传给 ``device.sensors.subscribe()``，并负责在
    ``finally`` 中取消订阅和调用 ``close()``。仪表盘按 ``sensor_id`` 保留各路最新样本；样本中的
    SI 单位、坐标系和时间语义保持不变。窗口在独立显示线程中刷新，按 Q、Esc 或关闭窗口可停止显示。
    """

    def __init__(self, owner: "DeviceDebug", title: str) -> None:
        self._owner = owner
        self._title = title
        self._samples = _LatestSampleBuffer()
        self._stop = Event()
        self._started = Event()
        self._finished = Event()
        self._error: Optional[BaseException] = None
        self._thread = Thread(
            target=self._display,
            name=f"fleetdock-dashboard-{owner._sensors.device_id}",
            daemon=True,
        )

    @property
    def closed(self) -> bool:
        """返回仪表盘窗口是否已经停止；关闭后提交的新样本会被安全忽略。"""
        return self._finished.is_set()

    def update(self, *samples: SensorSample) -> None:
        """把一个或多个标准传感器样本提交给仪表盘，无返回值。

        ``samples`` 必须是 ``SensorSample`` 或其强类型子类；每次提交只更新对应 ``sensor_id`` 的最新值，
        不改变样本的单位、坐标系或内容，也不会建立传感器订阅。窗口已经关闭时调用安全无效。
        """
        if not samples:
            raise ValueError("dashboard.update 至少需要一个传感器样本。")
        for sample in samples:
            if not isinstance(sample, SensorSample):
                raise TypeError("dashboard 仅接受 SensorSample 实例。")
        if self.closed:
            return
        for sample in samples:
            self._samples.offer(sample)

    def __call__(self, sample: SensorSample) -> None:
        """接收一个标准样本，便于将仪表盘直接用作 ``SensorHub.subscribe()`` 回调。"""
        self.update(sample)

    def handler(self, callback: Optional[SensorHandler] = None) -> SensorHandler:
        """返回“业务处理后送入仪表盘”的传感器订阅回调。

        ``callback`` 可选接收未经修改的标准样本；代理先调用业务回调，再把同一对象提交给 HUD。
        即使业务回调抛出异常，样本仍会进入仪表盘，异常继续交由 ``SensorHub`` 统计和隔离。
        返回值可直接传给 ``device.sensors.subscribe()``。
        """

        def proxy(sample: SensorSample) -> None:
            try:
                if callback is not None:
                    callback(sample)
            finally:
                self.update(sample)

        return proxy

    def close(self) -> None:
        """请求后台窗口停止并等待显示线程退出；重复调用安全且不取消传感器订阅，无返回值。"""
        self._stop.set()
        if self._thread.is_alive() and current_thread() is not self._thread:
            self._thread.join(timeout=5.0)

    def _start(self) -> None:
        self._thread.start()
        if not self._started.wait(timeout=5.0):
            self.close()
            raise RuntimeError("传感器仪表盘未能在 5 秒内启动。")
        if self._error is not None:
            self.close()
            raise RuntimeError("无法启动传感器仪表盘。") from self._error

    def _display(self) -> None:
        display: Optional[_DebugDisplay] = None
        try:
            display = self._owner._display_factory()
            display.open(self._title)
            self._started.set()
            latest: dict[str, SensorSample] = {}
            while not self._stop.is_set():
                latest.update(self._samples.drain())
                display.draw(self._title, latest)
                if display.should_close(20):
                    break
        except BaseException as exc:
            self._error = exc
            self._started.set()
            logger.debug("传感器仪表盘显示异常: title=%s", self._title, exc_info=True)
        finally:
            if display is not None:
                display.close()
            self._finished.set()
            self._owner._dashboard_finished(self)


class DeviceDebug:
    """设备级标准传感器调试入口。

    调用方通常通过 ``device.debug`` 使用。调试窗口只调用 ``device.sensors`` 的发现和订阅接口，
    因此 PX4、其他品牌无人机、机械狗、仿真器和自定义后端只要提供标准样本即可复用。窗口使用可选
    ``opencv-python`` 和 NumPy；未调用调试入口时不会导入 GUI 依赖。
    """

    def __init__(
        self,
        sensors: SensorHub,
        *,
        device: Any = None,
        _display_factory: Optional[DisplayFactory] = None,
        _controller_display_factory: Any = None,
    ) -> None:
        """绑定一个设备的 ``SensorHub``。

        ``sensors`` 是正式传感器数据入口；``device`` 是可选标准设备对象，仅用于
        ``controller()`` 调用公共 SDK，不会访问厂商适配器。两个 ``_...factory`` 仅用于无图形环境
        测试，不属于稳定公共参数。一个 ``DeviceDebug`` 同一时刻只允许运行一个窗口。
        """
        self._sensors = sensors
        self._device = device
        self._display_factory = _display_factory or _OpenCvDebugDisplay
        self._controller_display_factory = _controller_display_factory
        self._proxy_samples = _LatestSampleBuffer()
        self._stop = Event()
        self._lock = RLock()
        self._running = False
        self._closed = False
        self._dashboard: Optional[SensorDashboard] = None
        self._controller: Any = None

    def camera(
        self,
        sensor: Optional[SensorIdentifier | SensorHandler] = None,
        handler: Optional[SensorHandler] = None,
        *,
        title: Optional[str] = None,
    ) -> None:
        """自动订阅一路标准相机，在本地窗口显示画面并阻塞至窗口关闭。

        ``sensor`` 可使用设备内 ``sensor_id`` 或相机 ``SensorKind``；仅传一个可调用对象时将其视为
        默认相机的 ``handler``。``title`` 是可选窗口标题，省略时包含实际传感器 ID。省略传感器时
        依次选择主 RGB、深度或
        双目相机。每个 ``ImageFrame`` 或 ``StereoFrame`` 会先送入只保留最新帧的显示队列，再把同一个
        未修改的标准样本交给可选 ``handler``。按 Q、Esc 或关闭窗口后自动取消本方法创建的订阅；
        方法无返回值，也不会关闭设备本身。
        """
        identifier: SensorIdentifier
        selected_handler = handler
        if callable(sensor):
            if handler is not None:
                raise ValueError("摄像头处理函数被重复传入。")
            identifier = self._default_camera_identifier()
            selected_handler = sensor
        else:
            identifier = sensor if sensor is not None else self._default_camera_identifier()
        descriptor = self._sensors.get(identifier)
        if descriptor.kind not in {
            SensorKind.RGB_CAMERA,
            SensorKind.DEPTH_CAMERA,
            SensorKind.STEREO_CAMERA,
        }:
            raise ValueError(f"该传感器不是标准摄像头：{descriptor.sensor_id}")
        self._run_subscribed(
            (descriptor.sensor_id,),
            handler=selected_handler,
            handlers=None,
            title=title or f"FleetDock 摄像头 - {descriptor.sensor_id}",
        )

    def dashboard(self, *, title: str = "FleetDock 传感器仪表盘") -> SensorDashboard:
        """创建后台 HUD 数据接收端并返回 ``SensorDashboard``。

        ``title`` 是本地窗口标题。调用本方法只启动显示窗口，不发现、不订阅传感器；调用方应显式使用
        ``device.sensors.subscribe()`` 把标准样本交给返回对象，并在 ``finally`` 中取消订阅后调用其
        ``close()``。同一个 ``DeviceDebug`` 同时只允许一个窗口，缺少 ``vision`` 可选依赖或窗口创建失败
        时抛出 ``RuntimeError``。
        """
        self._begin_run()
        dashboard = SensorDashboard(self, title)
        with self._lock:
            self._dashboard = dashboard
        try:
            dashboard._start()
        except Exception:
            self._dashboard_finished(dashboard)
            raise
        return dashboard

    def camera_handler(self, handler: Optional[SensorHandler] = None) -> SensorHandler:
        """返回可传给 ``device.sensors.subscribe`` 的摄像头调试代理回调。

        代理把收到的标准相机帧放入调试队列，并把同一个未修改样本继续传给可选 ``handler``；它不创建
        订阅、不启动 GUI，也不复制大图像缓冲区。建立订阅后调用 ``device.debug.run()`` 可在当前线程
        显示队列内容。
        """

        def proxy(sample: SensorSample) -> None:
            if not isinstance(sample, (ImageFrame, StereoFrame)):
                raise TypeError("camera_handler 仅接受 ImageFrame 或 StereoFrame。")
            self._proxy_samples.offer(sample)
            if handler is not None:
                handler(sample)

        return proxy

    def hud_handler(self, handler: Optional[SensorHandler] = None) -> SensorHandler:
        """返回可传给现有传感器订阅的通用 HUD 代理回调。

        代理将标准样本送入调试队列后，把原对象继续传给可选 ``handler``。它不创建第二条订阅流；调用方
        仍负责保存并在 ``finally`` 中执行 ``device.sensors.subscribe`` 返回的取消函数。
        """

        def proxy(sample: SensorSample) -> None:
            self._proxy_samples.offer(sample)
            if handler is not None:
                handler(sample)

        return proxy

    def run(self, *, title: str = "FleetDock 传感器调试") -> None:
        """在当前线程显示代理回调送入的样本，并阻塞到窗口关闭。

        ``title`` 是本地窗口标题。本方法不创建或取消传感器订阅，适用于 ``camera_handler``、
        ``hud_handler`` 与既有
        ``device.sensors.subscribe`` 组合。按 Q、Esc 或关闭窗口后返回；调用方仍应在 ``finally`` 中
        取消自己创建的订阅。
        """
        self._begin_run()
        try:
            self._display_loop(self._proxy_samples, title)
        finally:
            self._end_run()

    def controller(
        self,
        *,
        title: str = "FleetDock 调试控制器",
        camera: bool = True,
        camera_subscribe: Optional[Callable[[SensorHandler], Callable[[], None]]] = None,
    ) -> None:
        """打开单设备图形调试控制器并阻塞到窗口关闭，无返回值。

        ``title`` 是窗口标题；``camera=True`` 时控制器尝试通过 ``device.sensors`` 订阅主 RGB、深度
        或双目相机；``camera_subscribe`` 可传入“接收标准相机回调并返回取消函数”的订阅入口，用于由
        调用方明确选择 ``SensorKind`` 或具体 ``sensor_id``，省略时才自动选择相机。发现或订阅失败只
        显示提示，不影响控制按钮。控制器根据标准能力和状态提供连接、解锁、起飞、悬停、降落、返航、
        急停及机体系方向控制；所有动作只调用设备公共 SDK。设备必须通过正常构造流程绑定本调试入口，
        独立 ``DeviceDebug(SensorHub)`` 不具备控制能力。窗口关闭时自动调用订阅入口返回的取消函数并
        结束手动控制，但不会自动返航、降落或关闭设备。
        """
        if self._device is None:
            raise RuntimeError("图形调试控制器必须绑定到标准设备对象。")
        from fleetdock.debug_controller import _DebugController

        self._begin_run()
        controller = _DebugController(
            self._device,
            title=title,
            camera=camera,
            camera_subscribe=camera_subscribe,
            display_factory=self._controller_display_factory,
        )
        with self._lock:
            self._controller = controller
        try:
            controller.run()
        finally:
            with self._lock:
                if self._controller is controller:
                    self._controller = None
            self._end_run()

    def close(self) -> None:
        """请求当前调试窗口退出并永久关闭调试入口；设备关闭时由 SDK 自动调用，无返回值。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            dashboard = self._dashboard
            controller = self._controller
        if dashboard is not None:
            dashboard.close()
        if controller is not None:
            controller.close()
        logger.debug("设备传感器调试入口已关闭: device_id=%s", self._sensors.device_id)

    def _default_camera_identifier(self) -> SensorIdentifier:
        for kind in (SensorKind.RGB_CAMERA, SensorKind.DEPTH_CAMERA, SensorKind.STEREO_CAMERA):
            if self._sensors.supports(kind):
                return kind
        raise LookupError("当前设备没有可用的标准摄像头传感器。")

    def _run_subscribed(
        self,
        identifiers: Sequence[str],
        *,
        handler: Optional[SensorHandler],
        handlers: Optional[Mapping[str, SensorHandler]],
        title: str,
    ) -> None:
        self._begin_run()
        samples = _LatestSampleBuffer()
        unsubscribes: list[Callable[[], None]] = []
        try:
            for sensor_id in identifiers:
                selected_handler = handler if len(identifiers) == 1 else (handlers or {}).get(sensor_id)

                def on_sample(
                    sample: SensorSample,
                    callback: Optional[SensorHandler] = selected_handler,
                ) -> None:
                    samples.offer(sample)
                    if callback is not None:
                        callback(sample)

                unsubscribes.append(self._sensors.subscribe(sensor_id, on_sample))
            self._display_loop(samples, title)
        finally:
            for unsubscribe in reversed(unsubscribes):
                unsubscribe()
            self._end_run()

    def _begin_run(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("设备调试入口已关闭。")
            if self._running:
                raise RuntimeError("A device debug window is already running.")
            self._running = True
            self._stop.clear()
        logger.debug("设备传感器调试窗口启动: device_id=%s", self._sensors.device_id)

    def _end_run(self) -> None:
        with self._lock:
            self._running = False
            if not self._closed:
                self._stop.clear()
        logger.debug("设备传感器调试窗口结束: device_id=%s", self._sensors.device_id)

    def _dashboard_finished(self, dashboard: SensorDashboard) -> None:
        with self._lock:
            if self._dashboard is not dashboard:
                return
            self._dashboard = None
        self._end_run()

    def _display_loop(self, samples: _LatestSampleBuffer, title: str) -> None:
        display = self._display_factory()
        latest: dict[str, SensorSample] = {}
        display.open(title)
        try:
            while not self._stop.is_set():
                latest.update(samples.drain())
                display.draw(title, latest)
                if display.should_close(20):
                    break
        finally:
            display.close()


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
