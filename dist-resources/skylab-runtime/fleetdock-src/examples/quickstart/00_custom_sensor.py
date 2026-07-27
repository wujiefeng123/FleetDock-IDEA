"""使用 Mock 设备演示应用自定义传感器。"""

from threading import Event

from fleetdock.adapters import MockSensorBackend
from fleetdock.api import DeviceFactory
from fleetdock.sensors import ExtensionSensorKind, GenericSensorSample, SensorDescriptor


def main() -> None:
    """定义气体浓度传感器，订阅一条样本后关闭设备。"""
    gas_kind = ExtensionSensorKind("custom.inspection.gas_detector")
    backend = MockSensorBackend((SensorDescriptor("gas-front", gas_kind),))
    drone = DeviceFactory.create().mock_drone().build()
    drone.sensors.register_backend(backend)
    received = Event()

    def on_gas(sample: GenericSensorSample) -> None:
        """打印气体浓度，单位由应用自定义模型约定为 ppm。"""
        print("气体浓度：", sample.values["concentration_ppm"], "ppm")
        received.set()

    unsubscribe = drone.sensors.subscribe(gas_kind, on_gas)
    try:
        backend.publish(
            GenericSensorSample(
                sensor_id="gas-front",
                kind=gas_kind,
                sequence=1,
                values={"concentration_ppm": 12.5},
            )
        )
        received.wait(1.0)
    finally:
        unsubscribe()
        drone.close()


if __name__ == "__main__":
    main()
