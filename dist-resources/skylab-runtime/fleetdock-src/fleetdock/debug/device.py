"""设备级调试入口。"""

from __future__ import annotations

import logging
from threading import Event, RLock
from typing import Callable, Mapping, Optional, Sequence

from fleetdock.debug.camera import _OpenCvDebugDisplay
from fleetdock.debug.hud import DisplayFactory, SensorDashboard, _LatestSampleBuffer
from fleetdock.sensors import (
    ImageFrame,
    SensorHandler,
    SensorHub,
    SensorIdentifier,
    SensorKind,
    SensorSample,
    StereoFrame,
)

logger = logging.getLogger(__name__)


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
        _display_factory: Optional[DisplayFactory] = None,
    ) -> None:
        """绑定一个设备的 ``SensorHub``。

        ``sensors`` 是正式传感器数据入口。``_display_factory`` 仅用于无图形环境测试，
        不属于稳定公共参数。一个 ``DeviceDebug`` 同一时刻只允许运行一个窗口。
        """
        self._sensors = sensors
        self._display_factory = _display_factory or _OpenCvDebugDisplay
        self._proxy_samples = _LatestSampleBuffer()
        self._stop = Event()
        self._lock = RLock()
        self._running = False
        self._closed = False
        self._dashboard: Optional[SensorDashboard] = None

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

    def close(self) -> None:
        """请求当前调试窗口退出并永久关闭调试入口；设备关闭时由 SDK 自动调用，无返回值。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            dashboard = self._dashboard
        if dashboard is not None:
            dashboard.close()
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
