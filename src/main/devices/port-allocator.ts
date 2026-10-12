// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Keep managed simulation MAVLink ports in the reserved FleetDock range.

export const SIMULATION_MAVLINK_PORT_MIN = 14600;
export const SIMULATION_MAVLINK_PORT_MAX = 14699;

export function allocatePort(index: number): number {
  return SIMULATION_MAVLINK_PORT_MIN + index;
}

export function allocateUniqueSimulationMavlinkPort(
  usedPorts: Iterable<number>,
  requested?: number
): number {
  const used = new Set(
    Array.from(usedPorts).filter(
      port =>
        Number.isInteger(port) &&
        port >= SIMULATION_MAVLINK_PORT_MIN &&
        port <= SIMULATION_MAVLINK_PORT_MAX
    )
  );

  if (
    Number.isInteger(requested) &&
    requested >= SIMULATION_MAVLINK_PORT_MIN &&
    requested <= SIMULATION_MAVLINK_PORT_MAX &&
    !used.has(requested)
  ) {
    return requested;
  }

  for (
    let port = SIMULATION_MAVLINK_PORT_MIN;
    port <= SIMULATION_MAVLINK_PORT_MAX;
    port += 1
  ) {
    if (!used.has(port)) {
      return port;
    }
  }

  throw new Error('仿真设备 MAVLink 端口 14600-14699 已全部占用。');
}
