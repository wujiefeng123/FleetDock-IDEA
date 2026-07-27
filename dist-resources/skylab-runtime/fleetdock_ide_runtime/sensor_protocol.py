"""Authenticated local wire protocol for SDK-standard sensor objects."""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
import socket
import struct
from collections.abc import Mapping
from typing import Any


# [UAV-IDE MODIFY] Version the IDE transport independently from Project AirSim and FleetDock APIs.
PROTOCOL_VERSION = 1
MAX_HEADER_BYTES = 2 * 1024 * 1024
MAX_BUFFER_BYTES = 128 * 1024 * 1024
_TAG = "__fleetdock_ide_wire__"


class SensorProtocolError(RuntimeError):
    """Raised when a local sensor-service message violates the wire contract."""


def encode_message(message: Any) -> bytes:
    buffers: list[bytes] = []
    value = _encode_value(message, buffers)
    header = json.dumps(
        {"value": value, "buffers": [len(item) for item in buffers]},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > MAX_HEADER_BYTES:
        raise SensorProtocolError("Sensor protocol header is too large.")
    total_buffers = sum(len(item) for item in buffers)
    if total_buffers > MAX_BUFFER_BYTES:
        raise SensorProtocolError("Sensor protocol payload is too large.")
    return struct.pack("!I", len(header)) + header + b"".join(buffers)


def send_message(sock: socket.socket, message: Any) -> None:
    sock.sendall(encode_message(message))


def receive_message(sock: socket.socket) -> Any:
    header_size = struct.unpack("!I", _receive_exact(sock, 4))[0]
    if header_size <= 0 or header_size > MAX_HEADER_BYTES:
        raise SensorProtocolError("Invalid sensor protocol header size.")
    header = json.loads(_receive_exact(sock, header_size).decode("utf-8"))
    lengths = header.get("buffers", [])
    if not isinstance(lengths, list) or any(
        not isinstance(length, int) or length < 0 for length in lengths
    ):
        raise SensorProtocolError("Invalid sensor protocol buffer lengths.")
    if sum(lengths) > MAX_BUFFER_BYTES:
        raise SensorProtocolError("Sensor protocol payload is too large.")
    buffers = [_receive_exact(sock, length) for length in lengths]
    return _decode_value(header.get("value"), buffers)


def _receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("Sensor service connection closed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _encode_value(value: Any, buffers: list[bytes]) -> Any:
    if isinstance(value, enum.Enum):
        return {
            _TAG: "enum",
            "type": _type_name(type(value)),
            "value": _encode_value(value.value, buffers),
        }
    if isinstance(value, str) and type(value) is not str:
        return {
            _TAG: "scalar",
            "type": _type_name(type(value)),
            "value": str(value),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (memoryview, bytes, bytearray)):
        index = len(buffers)
        buffers.append(bytes(value))
        kind = "memoryview" if isinstance(value, memoryview) else type(value).__name__
        return {_TAG: "buffer", "index": index, "kind": kind}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            _TAG: "dataclass",
            "type": _type_name(type(value)),
            "fields": {
                field.name: _encode_value(getattr(value, field.name), buffers)
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, tuple):
        return {_TAG: "tuple", "items": [_encode_value(item, buffers) for item in value]}
    if isinstance(value, list):
        return [_encode_value(item, buffers) for item in value]
    if isinstance(value, Mapping):
        return {
            _TAG: "mapping",
            "items": [
                [_encode_value(key, buffers), _encode_value(item, buffers)]
                for key, item in value.items()
            ],
        }
    raise SensorProtocolError(f"Unsupported sensor wire value: {type(value).__name__}")


def _decode_value(value: Any, buffers: list[bytes]) -> Any:
    if isinstance(value, list):
        return [_decode_value(item, buffers) for item in value]
    if not isinstance(value, dict) or _TAG not in value:
        return value
    kind = value[_TAG]
    if kind == "buffer":
        index = int(value.get("index", -1))
        if index < 0 or index >= len(buffers):
            raise SensorProtocolError("Sensor protocol buffer index is invalid.")
        buffer = buffers[index]
        buffer_kind = value.get("kind")
        if buffer_kind == "memoryview":
            return memoryview(buffer)
        if buffer_kind == "bytearray":
            return bytearray(buffer)
        return buffer
    if kind == "enum":
        cls = _resolve_type(str(value.get("type", "")))
        return cls(_decode_value(value.get("value"), buffers))
    if kind == "scalar":
        cls = _resolve_type(str(value.get("type", "")))
        return cls(value.get("value"))
    if kind == "dataclass":
        cls = _resolve_type(str(value.get("type", "")))
        fields = value.get("fields")
        if not dataclasses.is_dataclass(cls) or not isinstance(fields, dict):
            raise SensorProtocolError("Invalid sensor dataclass payload.")
        return cls(**{name: _decode_value(item, buffers) for name, item in fields.items()})
    if kind == "tuple":
        return tuple(_decode_value(item, buffers) for item in value.get("items", []))
    if kind == "mapping":
        return {
            _decode_value(item[0], buffers): _decode_value(item[1], buffers)
            for item in value.get("items", [])
        }
    raise SensorProtocolError(f"Unsupported sensor wire tag: {kind}")


def _type_name(cls: type[Any]) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def _resolve_type(name: str) -> type[Any]:
    try:
        module_name, qualname = name.split(":", 1)
    except ValueError as exc:
        raise SensorProtocolError("Invalid sensor wire type name.") from exc
    # [UAV-IDE MODIFY] Only SDK model modules may be reconstructed from the authenticated local channel.
    if not module_name.startswith("fleetdock.") or "<locals>" in qualname:
        raise SensorProtocolError(f"Sensor wire type is not allowed: {name}")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not isinstance(target, type):
        raise SensorProtocolError(f"Sensor wire target is not a type: {name}")
    return target
