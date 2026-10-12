"""FleetDock 服务端使用的独立媒体链路能力。"""

from fleetdock_server.media.gstreamer_whip_relay import (
    GSTREAMER_WHIP_RELAY_ELEMENTS,
    GStreamerWhipRelay,
)
from fleetdock_server.media.rtsp_relay import (
    GSTREAMER_RTSP_RELAY_ELEMENTS,
    RtspRelay,
    RtspRelaySnapshot,
    RtspRelayStatus,
)
from fleetdock_server.media.whip_relay import (
    FfmpegWhipRelay,
    WhipRelay,
    WhipRelaySnapshot,
    WhipRelayStatus,
)

__all__ = [
    "GSTREAMER_RTSP_RELAY_ELEMENTS",
    "GSTREAMER_WHIP_RELAY_ELEMENTS",
    "FfmpegWhipRelay",
    "GStreamerWhipRelay",
    "RtspRelay",
    "RtspRelaySnapshot",
    "RtspRelayStatus",
    "WhipRelay",
    "WhipRelaySnapshot",
    "WhipRelayStatus",
]
