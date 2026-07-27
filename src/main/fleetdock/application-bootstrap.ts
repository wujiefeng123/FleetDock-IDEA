// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app } from 'electron';
import {
  ensureDesktopJupyterLabPageConfig,
  ensureDesktopMainMenuSettings
} from '../config/jupyterlab';
import { PRODUCT_NAME } from '../branding';

/**
 * FleetDock application-wide setup kept behind two explicit lifecycle hooks.
 */
export function configureFleetDockApplicationBrand(): void {
  const thisYear = new Date().getFullYear();
  app.setAboutPanelOptions({
    applicationName: PRODUCT_NAME,
    applicationVersion: app.getVersion(),
    version: app.getVersion(),
    website: 'https://jupyter.org/about.html',
    copyright: `© 2015-${thisYear} Project Jupyter Contributors`
  });
}

export function prepareFleetDockUserConfiguration(): void {
  ensureDesktopMainMenuSettings();
  ensureDesktopJupyterLabPageConfig();
}
