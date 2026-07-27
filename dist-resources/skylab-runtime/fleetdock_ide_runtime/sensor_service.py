"""IDE-owned service that exposes FleetDock SDK sensor backends locally."""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .sensor_protocol import PROTOCOL_VERSION, encode_message, receive_message


logger = logging.getLogger(__name__)
_MAX_SESSION_QUEUE = 16


def fleetdock_sdk_version() -> str:
    import fleetdock

    return str(getattr(fleetdock, "__version__", "unknown"))


def create_projectairsim_backends(
    client: Any, vehicle_names: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Create one SDK adapter per robot while retaining one IDE-owned client."""

    try:
        from fleetdock.adapters.projectairsim_sensors import (
            _ProjectAirSimSensorBackend,
        )
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "The bundled FleetDock SDK does not expose the compatible Project AirSim sensor backend."
        ) from exc
    return {
        name: _ProjectAirSimSensorBackend(name, client=client)
        for name in vehicle_names
        if name
    }


@dataclass
class _SharedStream:
    targets: dict[tuple[int, str], "_ClientSession"] = field(default_factory=dict)
    unsubscribe: Callable[[], None] | None = None


class _ClientSession:
    def __init__(self, service: "SdkSensorService", sock: socket.socket) -> None:
        self.service = service
        self.sock = sock
        self.session_id = id(self)
        self._closed = False
        self._condition = threading.Condition()
        self._outgoing: deque[tuple[bool, str, bytes]] = deque()
        self._writer = threading.Thread(
            target=self._write_loop,
            name=f"fleetdock-sensor-writer-{self.session_id}",
            daemon=True,
        )

    def run(self) -> None:
        self._writer.start()
        try:
            hello = receive_message(self.sock)
            if not isinstance(hello, dict) or hello.get("op") != "hello":
                raise RuntimeError("Sensor service HELLO is required.")
            if int(hello.get("protocolVersion", 0)) != PROTOCOL_VERSION:
                raise RuntimeError("Sensor service protocol version mismatch.")
            if not secrets.compare_digest(str(hello.get("token", "")), self.service.token):
                raise RuntimeError("Sensor service authentication failed.")
            if str(hello.get("runId", "")) != self.service.run_id:
                raise RuntimeError("Sensor service runId mismatch.")
            self.enqueue(
                {
                    "op": "hello",
                    "ok": True,
                    "protocolVersion": PROTOCOL_VERSION,
                    "runId": self.service.run_id,
                    "sdkVersion": self.service.sdk_version,
                }
            )
            while not self._closed:
                message = receive_message(self.sock)
                if not isinstance(message, dict):
                    raise RuntimeError("Sensor service request must be an object.")
                if message.get("op") == "close":
                    break
                self._handle_request(message)
        except (EOFError, OSError):
            pass
        except Exception as exc:
            logger.warning("FleetDock sensor client session failed: %s", exc)
            self.enqueue({"op": "error", "ok": False, "error": str(exc)})
        finally:
            self.close()

    def _handle_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("id", ""))
        operation = str(message.get("op", ""))
        try:
            if not request_id:
                raise ValueError("Sensor service request id is required.")
            vehicle_name = str(message.get("vehicleName", ""))
            if operation == "list":
                result = self.service.list_sensors(vehicle_name)
            elif operation == "subscribe":
                subscription_id = str(message.get("subscriptionId", ""))
                sensor_id = str(message.get("sensorId", ""))
                self.service.subscribe(self, vehicle_name, sensor_id, subscription_id)
                result = {"subscriptionId": subscription_id}
            elif operation == "unsubscribe":
                subscription_id = str(message.get("subscriptionId", ""))
                self.service.unsubscribe(self, subscription_id)
                result = {"subscriptionId": subscription_id}
            else:
                raise ValueError("Unsupported sensor service operation.")
            self.enqueue({"op": "result", "id": request_id, "ok": True, "data": result})
        except Exception as exc:
            self.enqueue(
                {"op": "result", "id": request_id, "ok": False, "error": str(exc)}
            )

    def enqueue(self, message: dict[str, Any], *, sample: bool = False) -> None:
        if self._closed:
            return
        subscription_id = str(message.get("subscriptionId", "")) if sample else ""
        try:
            frame = encode_message(message)
        except Exception as exc:
            logger.warning("Unable to encode FleetDock sensor message: %s", exc)
            return
        with self._condition:
            if self._closed:
                return
            if sample and len(self._outgoing) >= _MAX_SESSION_QUEUE:
                drop_index = next(
                    (
                        index
                        for index, item in enumerate(self._outgoing)
                        if item[0] and item[1] == subscription_id
                    ),
                    None,
                )
                if drop_index is None:
                    drop_index = next(
                        (index for index, item in enumerate(self._outgoing) if item[0]),
                        None,
                    )
                if drop_index is None:
                    return
                del self._outgoing[drop_index]
            self._outgoing.append((sample, subscription_id, frame))
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self.service.detach(self)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _write_loop(self) -> None:
        while True:
            with self._condition:
                while not self._outgoing and not self._closed:
                    self._condition.wait()
                if self._closed and not self._outgoing:
                    return
                _sample, _subscription_id, frame = self._outgoing.popleft()
            try:
                self.sock.sendall(frame)
            except OSError:
                self.close()
                return


class SdkSensorService:
    """Fan out SDK-standard samples from per-robot backends to local kernels."""

    def __init__(
        self,
        run_id: str,
        backends: dict[str, Any],
        manifest_path: str,
        rtsp_streams: list[dict[str, str]] | None = None,
    ) -> None:
        if not run_id:
            raise ValueError("Sensor service run_id is required.")
        self.run_id = run_id
        self.backends = dict(backends)
        self.manifest_path = manifest_path
        # [UAV-IDE MODIFY] Publish a camera catalog instead of decoded preview endpoints.
        self.rtsp_streams = list(rtsp_streams or [])
        self.token = secrets.token_hex(32)
        self.sdk_version = fleetdock_sdk_version()
        self._lock = threading.RLock()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._sessions: set[_ClientSession] = set()
        self._streams: dict[tuple[str, str], _SharedStream] = {}
        self._session_subscriptions: dict[_ClientSession, dict[str, tuple[str, str]]] = {}
        self._closed = False

    def start(self) -> dict[str, Any]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        self._listener = listener
        host, port = listener.getsockname()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="fleetdock-sensor-accept",
            daemon=True,
        )
        self._accept_thread.start()
        manifest = {
            "active": True,
            "protocolVersion": PROTOCOL_VERSION,
            "runId": self.run_id,
            "endpoint": {"host": host, "port": port},
            "token": self.token,
            "sdkVersion": self.sdk_version,
            "vehicles": sorted(self.backends),
            "rtspStreams": self.rtsp_streams,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(self.manifest_path, manifest)
        return {
            "available": True,
            "protocolVersion": PROTOCOL_VERSION,
            "endpoint": f"{host}:{port}",
            "sdkVersion": self.sdk_version,
            "vehicles": sorted(self.backends),
            "rtspStreams": self.rtsp_streams,
        }

    def list_sensors(self, vehicle_name: str) -> tuple[Any, ...]:
        backend = self._backend(vehicle_name)
        return tuple(backend.list_sensors())

    def subscribe(
        self,
        session: _ClientSession,
        vehicle_name: str,
        sensor_id: str,
        subscription_id: str,
    ) -> None:
        if not sensor_id or not subscription_id:
            raise ValueError("sensorId and subscriptionId are required.")
        backend = self._backend(vehicle_name)
        key = (vehicle_name, sensor_id)
        target_key = (session.session_id, subscription_id)
        with self._lock:
            subscriptions = self._session_subscriptions.setdefault(session, {})
            if subscription_id in subscriptions:
                raise ValueError("Duplicate sensor subscription id.")
            stream = self._streams.get(key)
            if stream is None:
                stream = _SharedStream()
                self._streams[key] = stream

                def publish(sample: Any, stream_key: tuple[str, str] = key) -> None:
                    with self._lock:
                        active = self._streams.get(stream_key)
                        targets = list(active.targets.items()) if active is not None else []
                    for (_session_id, target_subscription_id), target in targets:
                        target.enqueue(
                            {
                                "op": "sample",
                                "subscriptionId": target_subscription_id,
                                "sample": sample,
                            },
                            sample=True,
                        )

                try:
                    stream.unsubscribe = backend.subscribe(sensor_id, publish)
                except Exception:
                    self._streams.pop(key, None)
                    raise
            stream.targets[target_key] = session
            subscriptions[subscription_id] = key

    def unsubscribe(self, session: _ClientSession, subscription_id: str) -> None:
        unsubscribe: Callable[[], None] | None = None
        with self._lock:
            subscriptions = self._session_subscriptions.get(session, {})
            key = subscriptions.pop(subscription_id, None)
            if key is None:
                return
            stream = self._streams.get(key)
            if stream is not None:
                stream.targets.pop((session.session_id, subscription_id), None)
                if not stream.targets:
                    self._streams.pop(key, None)
                    unsubscribe = stream.unsubscribe
            if not subscriptions:
                self._session_subscriptions.pop(session, None)
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                logger.debug("FleetDock native sensor unsubscribe failed.", exc_info=True)

    def detach(self, session: _ClientSession) -> None:
        with self._lock:
            subscription_ids = tuple(self._session_subscriptions.get(session, {}))
            self._sessions.discard(session)
        for subscription_id in subscription_ids:
            self.unsubscribe(session, subscription_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            listener = self._listener
            self._listener = None
            sessions = tuple(self._sessions)
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        for session in sessions:
            session.close()
        with self._lock:
            streams = tuple(self._streams.values())
            self._streams.clear()
            self._session_subscriptions.clear()
        for stream in streams:
            if stream.unsubscribe is not None:
                try:
                    stream.unsubscribe()
                except Exception:
                    logger.debug("FleetDock native sensor unsubscribe failed.", exc_info=True)
        for backend in self.backends.values():
            try:
                backend.close()
            except Exception:
                logger.debug("FleetDock Project AirSim sensor backend close failed.", exc_info=True)
        _write_json_atomic(
            self.manifest_path,
            {
                "active": False,
                "runId": self.run_id,
                "protocolVersion": PROTOCOL_VERSION,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _backend(self, vehicle_name: str) -> Any:
        backend = self.backends.get(vehicle_name)
        if backend is None:
            raise LookupError(f"Project AirSim vehicle is not available: {vehicle_name}")
        return backend

    def _accept_loop(self) -> None:
        while not self._closed:
            listener = self._listener
            if listener is None:
                return
            try:
                client, _address = listener.accept()
            except OSError:
                return
            session = _ClientSession(self, client)
            with self._lock:
                if self._closed:
                    session.close()
                    return
                self._sessions.add(session)
            threading.Thread(
                target=session.run,
                name=f"fleetdock-sensor-session-{session.session_id}",
                daemon=True,
            ).start()


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
