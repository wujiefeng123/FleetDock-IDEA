"""用于测试、教学和第三方适配器契约验证的内存传感器后端。"""

from __future__ import annotations

from threading import RLock
from typing import Sequence

from fleetdock.sensors import SensorDescriptor, SensorHandler, SensorSample, SensorUnsubscribe


class MockSensorBackend:
    """由调用方主动发布标准样本的内存传感器后端。

    ``descriptors`` 声明该 Mock 设备实际存在的传感器；``publish`` 同步把样本交给对应原生通道，
    SensorHub 随后按每个订阅的 QoS 异步分发。该类不模拟厂商协议，仅用于测试公共契约和简单 demo。
    """

    def __init__(self, descriptors: Sequence[SensorDescriptor]) -> None:
        self._descriptors = tuple(descriptors)
        self._lock = RLock()
        self._handlers: dict[str, list[SensorHandler]] = {
            descriptor.sensor_id: [] for descriptor in self._descriptors
        }
        self._closed = False

    def list_sensors(self) -> tuple[SensorDescriptor, ...]:
        """返回构造时声明的传感器描述；关闭后仍可读取静态描述用于诊断。"""

        return self._descriptors

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        """订阅指定 Mock 传感器的标准样本并返回幂等取消函数。

        ``sensor_id`` 必须存在于构造描述中；``handler`` 同步接收后端发布的标准样本，随后由
        SensorHub 隔离到用户订阅线程。关闭后订阅会抛出 ``RuntimeError``。
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("Mock sensor backend is closed.")
            if sensor_id not in self._handlers:
                raise LookupError(f"Mock sensor is not available: {sensor_id}")
            self._handlers[sensor_id].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._handlers[sensor_id].remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, sample: SensorSample) -> None:
        """同步发布一个标准样本给 SensorHub 原生通道。

        ``sample.sensor_id`` 必须存在；该方法不等待用户回调处理完成，因为每个公共订阅都有独立队列。
        发布完成后无返回值。
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("Mock sensor backend is closed.")
            if sample.sensor_id not in self._handlers:
                raise LookupError(f"Mock sensor is not available: {sample.sensor_id}")
            handlers = list(self._handlers[sample.sensor_id])
        for handler in handlers:
            handler(sample)

    def close(self) -> None:
        """清空全部订阅并关闭后端；重复调用安全且无返回值。"""

        with self._lock:
            self._closed = True
            for handlers in self._handlers.values():
                handlers.clear()
