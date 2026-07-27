// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import {
  ISessionExtension,
  ISessionExtensionContext,
  ISessionExtensionFactory
} from '../extensions/session-extension';

/**
 * FleetDock features are attached here as migration layers are enabled.
 * Keeping the adapter valid while initially empty lets every layer compile and
 * be reviewed independently.
 */
class FleetDockSessionExtension implements ISessionExtension {
  readonly id = 'fleetdock';
}

export const fleetDockSessionExtensionFactory: ISessionExtensionFactory = (
  _context: ISessionExtensionContext
) => new FleetDockSessionExtension();
