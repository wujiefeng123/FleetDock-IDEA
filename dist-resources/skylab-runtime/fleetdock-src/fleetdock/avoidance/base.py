"""传感器障碍物 Provider 的公共协议。"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from fleetdock.obstacles import ObstacleProvider
from fleetdock.sensors import SensorDescriptor, SensorSample


SampleT = TypeVar("SampleT", bound=SensorSample, contravariant=True)


@runtime_checkable
class ObstacleProviderStatus(Protocol):
    """障碍物 Provider 状态的最小公共视图。

    ``ready`` 表示至少成功生成过一次障碍物快照；``stale`` 表示最近成功观测已超过查询时指定的
    秒级超时；``obstacle_count`` 是当前快照内障碍物数量；``latest_sequence`` 是最近处理的传感器
    序号，尚未收到样本时为 ``None``；``last_error`` 为空表示最近处理没有诊断错误。
    """

    ready: bool
    stale: bool
    obstacle_count: int
    latest_sequence: int | None
    last_error: str | None


@runtime_checkable
class SensorObstacleProvider(ObstacleProvider, Protocol[SampleT]):
    """把一种标准传感器样本转换为 ``ObstacleSnapshot`` 的公共协议。

    ``descriptor`` 标识唯一传感器和坐标语义；调用方通过
    ``device.sensors.subscribe(descriptor.sensor_id, provider.observe)`` 显式送入样本，再把 Provider
    注册到 ``device.avoidance``。实现不得自行发现或订阅传感器；取消订阅后由调用方调用 ``close()``。
    """

    descriptor: SensorDescriptor

    def observe(self, sample: SampleT) -> None:
        """接收一条标准样本并更新或排队生成障碍物快照。

        ``sample`` 必须与 ``descriptor.sensor_id`` 和传感器类型匹配，坐标系、单位及缓冲区生命周期由
        对应强类型样本声明；高带宽实现应快速返回并在内部后台线程处理。本方法无返回值。
        """

    def status(self, *, stale_timeout_s: float = 2.0) -> ObstacleProviderStatus:
        """按 ``stale_timeout_s`` 秒阈值返回就绪、新鲜度、障碍物数量和错误状态。"""
