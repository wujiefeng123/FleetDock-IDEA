"""Focused regression check for the IDE-owned FleetDock SDK sensor service."""

from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import threading
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_ROOT = os.path.join(ROOT, "dist-resources", "skylab-runtime")
SDK_ROOT = os.path.join(RUNTIME_ROOT, "fleetdock-src")
sys.path.insert(0, RUNTIME_ROOT)
sys.path.insert(0, SDK_ROOT)

from fleetdock import (  # noqa: E402
    CoordinateFrame,
    ExtensionSensorKind,
    ImageEncoding,
    ImageFrame,
    PointCloudEncoding,
    PointCloudFrame,
    SensorDescriptor,
    SensorKind,
    SensorSourceKind,
)
from fleetdock_ide_runtime.sensor_protocol import (  # noqa: E402
    encode_message,
    receive_message,
)
from fleetdock_ide_runtime.sensor_proxy import IdeSimulationSensorProxy  # noqa: E402
from fleetdock_ide_runtime.sensor_service import (  # noqa: E402
    SdkSensorService,
    create_projectairsim_backends,
)


class MockBackend:
    def __init__(self) -> None:
        self.descriptors = (
            SensorDescriptor(
                sensor_id="FrontCamera",
                kind=SensorKind.RGB_CAMERA,
                source=SensorSourceKind.SIMULATED,
                frame=CoordinateFrame.BODY,
                is_primary=True,
                metadata={"provider": "projectairsim"},
            ),
            SensorDescriptor(
                sensor_id="LidarFront",
                kind=SensorKind.LIDAR,
                source=SensorSourceKind.SIMULATED,
                frame=CoordinateFrame.BODY,
                is_primary=True,
                metadata={"provider": "projectairsim"},
            ),
        )
        self.handlers = []
        self.subscribe_count = 0
        self.unsubscribe_count = 0
        self.closed = False

    def list_sensors(self):
        return self.descriptors

    def subscribe(self, sensor_id, handler):
        assert sensor_id in {descriptor.sensor_id for descriptor in self.descriptors}
        self.subscribe_count += 1
        self.handlers.append(handler)
        active = True

        def unsubscribe():
            nonlocal active
            if not active:
                return
            active = False
            self.unsubscribe_count += 1
            self.handlers.remove(handler)

        return unsubscribe

    def emit(self, sample):
        for handler in tuple(self.handlers):
            handler(sample)

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for sensor service result.")


def main() -> None:
    shared_client = object()
    sdk_backends = create_projectairsim_backends(
        shared_client, ["Drone1", "Drone2"]
    )
    assert sdk_backends["Drone1"]._client is shared_client
    assert sdk_backends["Drone2"]._client is shared_client
    assert sdk_backends["Drone1"]._owns_client is False

    backend = MockBackend()
    with tempfile.TemporaryDirectory() as temporary:
        manifest = os.path.join(temporary, "runtime.json")
        camera_streams = [
            {
                "id": "Drone1:FrontCamera",
                "vehicleName": "Drone1",
                "sensorId": "FrontCamera",
                "label": "Front camera",
                "uri": "rtsp://127.0.0.1:8554/Drone1_camera_front",
            }
        ]
        service = SdkSensorService(
            "run-test", {"Drone1": backend}, manifest, rtsp_streams=camera_streams
        )
        status = service.start()
        assert status["available"] is True
        assert status["rtspStreams"] == camera_streams

        first = IdeSimulationSensorProxy("Drone1", manifest)
        second = IdeSimulationSensorProxy("Drone1", manifest)
        descriptors = first.list_sensors()
        assert len(descriptors) == 2
        assert descriptors == backend.descriptors
        media_proxy = IdeSimulationSensorProxy(
            "Drone1", manifest, excluded_kinds=("rgb_camera",)
        )
        assert media_proxy.list_sensors() == (backend.descriptors[1],)
        media_proxy.close()

        first_samples = []
        second_samples = []
        first_event = threading.Event()
        second_event = threading.Event()
        unsubscribe_first = first.subscribe(
            "FrontCamera", lambda sample: (first_samples.append(sample), first_event.set())
        )
        unsubscribe_second = second.subscribe(
            "FrontCamera", lambda sample: (second_samples.append(sample), second_event.set())
        )
        assert backend.subscribe_count == 1

        pixels = bytes(range(12))
        backend.emit(
            ImageFrame(
                sensor_id="FrontCamera",
                kind=SensorKind.RGB_CAMERA,
                sequence=1,
                frame=CoordinateFrame.BODY,
                clock_domain="projectairsim",
                width=2,
                height=2,
                encoding=ImageEncoding.RGB8,
                data=memoryview(pixels),
                stride_bytes=6,
            )
        )
        assert first_event.wait(3.0)
        assert second_event.wait(3.0)
        assert bytes(first_samples[0].data) == pixels
        assert bytes(second_samples[0].data) == pixels
        assert first_samples[0].kind is SensorKind.RGB_CAMERA

        point_data = struct.pack("<ffffff", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        point_cloud = PointCloudFrame(
            sensor_id="LidarFront",
            kind=SensorKind.LIDAR,
            sequence=2,
            frame=CoordinateFrame.BODY,
            clock_domain="projectairsim",
            data=memoryview(point_data),
            point_count=2,
            encoding=PointCloudEncoding.XYZ_F32,
        )
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(encode_message({"sample": point_cloud}))
            restored = receive_message(receiver)["sample"]
        finally:
            sender.close()
            receiver.close()
        assert restored == point_cloud
        assert bytes(restored.data) == point_data

        custom_kind = ExtensionSensorKind("vendor.fleetdock.test")
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(encode_message({"kind": custom_kind}))
            restored_kind = receive_message(receiver)["kind"]
        finally:
            sender.close()
            receiver.close()
        assert restored_kind == custom_kind
        assert type(restored_kind) is ExtensionSensorKind

        unsubscribe_first()
        assert backend.unsubscribe_count == 0
        unsubscribe_second()
        wait_for(lambda: backend.unsubscribe_count == 1)
        first.close()
        second.close()
        service.close()
        assert backend.closed is True
        with open(manifest, "r", encoding="utf-8") as source:
            assert '"active": false' in source.read().lower()
        stopped = IdeSimulationSensorProxy("Drone1", manifest)
        try:
            stopped.list_sensors()
            raise AssertionError("Stopped simulation manifest was accepted.")
        except RuntimeError as exc:
            assert "not running" in str(exc)

    print("FleetDock simulation sensor service checks passed.")


if __name__ == "__main__":
    main()
