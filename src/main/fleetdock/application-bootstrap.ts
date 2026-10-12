// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app } from 'electron';
import {
  ensureDesktopJupyterLabPageConfig,
  ensureDesktopMainMenuSettings
} from '../config/jupyterlab';
import {
  PRODUCT_NAME,
  PRODUCT_TEAM_NAME,
  UPSTREAM_PRODUCT_NAME
} from '../branding';
import type { DesktopUpdateConfig } from '../app';

// [UAV-IDE MODIFY] FleetDock releases are distributed manually until a
// product-owned, signed update service is available.
export const fleetDockDesktopUpdateConfig: DesktopUpdateConfig = {
  enabled: false
};

/**
 * FleetDock application-wide setup kept behind two explicit lifecycle hooks.
 */
export function configureFleetDockApplicationBrand(): void {
  const thisYear = new Date().getFullYear();
  app.setAboutPanelOptions({
    applicationName: PRODUCT_NAME,
    applicationVersion: app.getVersion(),
    version: app.getVersion(),
    copyright: `© ${thisYear} ${PRODUCT_TEAM_NAME}. Built on ${UPSTREAM_PRODUCT_NAME}; © 2015-${thisYear} Project Jupyter Contributors.`
  });
}

export function prepareFleetDockUserConfiguration(): void {
  ensureDesktopMainMenuSettings();
  ensureDesktopJupyterLabPageConfig();
}
