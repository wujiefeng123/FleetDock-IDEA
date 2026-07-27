"""Kernel-side FleetDock SensorBackend proxy for the IDE sensor service."""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import uuid
from typing import Any, Callable, Sequence

from .sensor_protocol import PROTOCOL_VERSION, receive_message, send_message


class IdeSimulationSensorProxy:
    """Expose SDK-standard remote samples through the public SensorBackend contract."""

    def __init__(
        self,
        vehicle_name: str,
        manifest_path: str | None = None,
        *,
        request_timeout_s: float = 5.0,
        excluded_kinds: Sequence[str] = (),
    ) -> None:
        if not vehicle_name:
            raise ValueError("vehicle_name is required.")
        self.vehicle_name = vehicle_name
        self.manifest_path = manifest_path or os.getenv(
            "FLEETDOCK_SIMULATION_MANIFEST", ""
        )
        self.request_timeout_s = request_timeout_s
        self.excluded_kinds = {str(kind) for kind in excluded_kinds}
        self._excluded_sensor_ids: set[str] = set()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._run_id = ""
        self._pending: dict[str, queue.Queue[Any]] = {}
        self._handlers: dict[str, Callable[[Any], None]] = {}
        self._closed = False
        self._connection_error = ""

    def list_sensors(self) -> Sequence[Any]:
        response = self._request("list")
        data = response.get("data", ())
        descriptors = tuple(data or ())
        if not self.excluded_kinds:
            return descriptors
        excluded = {
            str(descriptor.sensor_id)
            for descriptor in descriptors
            if str(getattr(getattr(descriptor, "kind", None), "value", ""))
            in self.excluded_kinds
        }
        with self._lock:
            self._excluded_sensor_ids = excluded
        return tuple(
            descriptor
            for descriptor in descriptors
            if str(descriptor.sensor_id) not in excluded
        )

    def subscribe(self, sensor_id: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        if not sensor_id or not callable(handler):
            raise ValueError("sensor_id and handler are required.")
        with self._lock:
            if sensor_id in self._excluded_sensor_ids:
                raise LookupError(
                    f"Sensor is provided through the device media adapter: {sensor_id}"
                )
        subscription_id = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                raise RuntimeError("Simulation sensor proxy is closed.")
            self._handlers[subscription_id] = handler
        try:
            self._request(
                "subscribe",
                sensorId=sensor_id,
                subscriptionId=subscription_id,
            )
        except Exception:
            with self._lock:
                self._handlers.pop(subscription_id, None)
            raise
        unsubscribed = False
        unsubscribe_lock = threading.Lock()

        def unsubscribe() -> None:
            nonlocal unsubscribed
            with unsubscribe_lock:
                if unsubscribed:
                    return
                unsubscribed = True
            with self._lock:
                self._handlers.pop(subscription_id, None)
            try:
                self._request("unsubscribe", subscriptionId=subscription_id)
            except Exception:
                pass

        return unsubscribe

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sock = self._socket
            self._socket = None
            self._handlers.clear()
            self._fail_pending_locked(RuntimeError("Simulation sensor proxy closed."))
        if sock is not None:
            try:
                with self._send_lock:
                    send_message(sock, {"op": "close"})
            except Exception:
                pass
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _request(self, operation: str, **payload: Any) -> dict[str, Any]:
        self._ensure_connected()
        request_id = uuid.uuid4().hex
        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._lock:
            if self._socket is None:
                raise RuntimeError(self._connection_error or "Simulation sensor service is unavailable.")
            self._pending[request_id] = result_queue
            sock = self._socket
        try:
            with self._send_lock:
                send_message(
                    sock,
                    {
                        "op": operation,
                        "id": request_id,
                        "vehicleName": self.vehicle_name,
                        **payload,
                    },
                )
            response = result_queue.get(timeout=self.request_timeout_s)
        except queue.Empty as exc:
            raise TimeoutError("Timed out waiting for simulation sensor service.") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if isinstance(response, BaseException):
            raise response
        if not isinstance(response, dict) or not response.get("ok"):
            error = response.get("error", "Simulation sensor request failed.") if isinstance(response, dict) else "Invalid simulation sensor response."
            raise RuntimeError(str(error))
        return response

    def _ensure_connected(self) -> None:
        manifest = self._load_manifest()
        with self._lock:
            if self._closed:
                raise RuntimeError("Simulation sensor proxy is closed.")
            if self._socket is not None and self._run_id == manifest["runId"]:
                return
            self._disconnect_locked()
            endpoint = manifest["endpoint"]
            sock = socket.create_connection(
                (str(endpoint["host"]), int(endpoint["port"])),
                timeout=self.request_timeout_s,
            )
            sock.settimeout(None)
            try:
                send_message(
                    sock,
                    {
                        "op": "hello",
                        "protocolVersion": PROTOCOL_VERSION,
                        "runId": manifest["runId"],
                        "token": manifest["token"],
                    },
                )
                hello = receive_message(sock)
                if not isinstance(hello, dict) or not hello.get("ok"):
                    raise RuntimeError("Simulation sensor service handshake failed.")
                if str(hello.get("sdkVersion", "")) != self._local_sdk_version():
                    raise RuntimeError(
                        "FleetDock SDK version mismatch between simulation and kernel."
                    )
            except Exception:
                sock.close()
                raise
            self._socket = sock
            self._run_id = str(manifest["runId"])
            self._connection_error = ""
            self._reader = threading.Thread(
                target=self._read_loop,
                args=(sock,),
                name=f"fleetdock-sensor-proxy-{self.vehicle_name}",
                daemon=True,
            )
            self._reader.start()

    def _read_loop(self, sock: socket.socket) -> None:
        error: BaseException = RuntimeError("Simulation sensor service disconnected.")
        try:
            while True:
                message = receive_message(sock)
                if not isinstance(message, dict):
                    continue
                if message.get("op") == "sample":
                    with self._lock:
                        handler = self._handlers.get(str(message.get("subscriptionId", "")))
                    if handler is not None:
                        try:
                            handler(message.get("sample"))
                        except Exception:
                            pass
                    continue
                request_id = str(message.get("id", ""))
                with self._lock:
                    target = self._pending.get(request_id)
                if target is not None:
                    target.put(message)
        except BaseException as exc:
            error = exc
        finally:
            with self._lock:
                if self._socket is sock:
                    self._socket = None
                    self._connection_error = str(error)
                    self._fail_pending_locked(RuntimeError(str(error)))
            try:
                sock.close()
            except OSError:
                pass

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path:
            raise RuntimeError("FleetDock simulation manifest path is not configured.")
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as source:
                manifest = json.load(source)
        except FileNotFoundError as exc:
            raise RuntimeError("Project AirSim simulation is not running.") from exc
        if not manifest.get("active"):
            raise RuntimeError("Project AirSim simulation is not running.")
        if int(manifest.get("protocolVersion", 0)) != PROTOCOL_VERSION:
            raise RuntimeError("Simulation sensor protocol version mismatch.")
        if self.vehicle_name not in manifest.get("vehicles", []):
            raise LookupError(
                f"Project AirSim vehicle is not active: {self.vehicle_name}"
            )
        endpoint = manifest.get("endpoint")
        if not isinstance(endpoint, dict) or not endpoint.get("host") or not endpoint.get("port"):
            raise RuntimeError("Simulation sensor endpoint is invalid.")
        if not manifest.get("token") or not manifest.get("runId"):
            raise RuntimeError("Simulation sensor manifest is incomplete.")
        return manifest

    def _disconnect_locked(self) -> None:
        sock = self._socket
        self._socket = None
        self._run_id = ""
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _fail_pending_locked(self, error: BaseException) -> None:
        for target in self._pending.values():
            try:
                target.put_nowait(error)
            except queue.Full:
                pass

    @staticmethod
    def _local_sdk_version() -> str:
        import fleetdock

        return str(getattr(fleetdock, "__version__", "unknown"))
