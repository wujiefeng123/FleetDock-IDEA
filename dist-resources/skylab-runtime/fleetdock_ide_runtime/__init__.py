"""FleetDock IDEA-owned Python runtime helpers.

This package contains transport and lifecycle glue only. Device and sensor
adaptation remains in the bundled FleetDock SDK.
"""

from .sensor_proxy import IdeSimulationSensorProxy

__all__ = ["IdeSimulationSensorProxy"]
