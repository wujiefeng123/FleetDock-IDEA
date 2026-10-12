"""标准传感器 HUD 数据接收端与显示生命周期。"""

from __future__ import annotations

import logging
from threading import Event, RLock, Thread, current_thread
from typing import Callable, Mapping, Optional, Protocol, TYPE_CHECKING

from fleetdock.sensors import SensorHandler, SensorSample

if TYPE_CHECKING:
    from fleetdock.debug.device import DeviceDebug

logger = logging.getLogger(__name__)


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
