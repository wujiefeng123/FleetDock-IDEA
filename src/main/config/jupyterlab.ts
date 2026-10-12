// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Persist JupyterLab shell configuration for the desktop app.

import * as fs from 'fs';
import * as path from 'path';

import { getUserDataDir } from '../utils';

const MAIN_MENU_SETTINGS_DIR = path.join(
  getUserDataDir(),
  'lab',
  'user-settings',
  '@jupyterlab',
  'mainmenu-extension'
);

const MAIN_MENU_SETTINGS_FILE = path.join(
  MAIN_MENU_SETTINGS_DIR,
  'plugin.jupyterlab-settings'
);

const MAIN_MENU_SETTINGS = {
  menus: [
    {
      id: 'jp-mainmenu-help',
      disabled: true
    }
  ]
};

const JUPYTERLAB_PAGE_CONFIG_DIR = path.join(getUserDataDir(), 'labconfig');

const JUPYTERLAB_PAGE_CONFIG_FILE = path.join(
  JUPYTERLAB_PAGE_CONFIG_DIR,
  'page_config.json'
);

const JUPYTERLAB_PAGE_CONFIG = {
  disabledExtensions: {
    // [UAV-IDE MODIFY] Disable Anaconda Toolbox panels so FleetDock owns the product workspace chrome.
    '@anaconda/assistant': true,
    '@anaconda/core': true,
    '@anaconda/panels': true,
    '@anaconda/share_notebook': true,
    '@anaconda/toolbox': true,
    '@jupyterlab/apputils-extension:announcements': true
  },
  lockedExtensions: {
    // [UAV-IDE MODIFY] Keep upstream Anaconda extensions disabled even if a user environment exposes them.
    '@anaconda/assistant': true,
    '@anaconda/core': true,
    '@anaconda/panels': true,
    '@anaconda/share_notebook': true,
    '@anaconda/toolbox': true,
    '@jupyterlab/apputils-extension:announcements': true
  }
};

export function ensureDesktopMainMenuSettings(): void {
  try {
    if (!fs.existsSync(MAIN_MENU_SETTINGS_DIR)) {
      fs.mkdirSync(MAIN_MENU_SETTINGS_DIR, { recursive: true });
    }

    const nextContents = JSON.stringify(MAIN_MENU_SETTINGS, null, 2);
    if (
      fs.existsSync(MAIN_MENU_SETTINGS_FILE) &&
      fs.readFileSync(MAIN_MENU_SETTINGS_FILE, 'utf8') === nextContents
    ) {
      return;
    }

    fs.writeFileSync(MAIN_MENU_SETTINGS_FILE, nextContents);
  } catch (error) {
    console.error('Failed to persist JupyterLab main menu settings:', error);
  }
}

export function ensureDesktopJupyterLabPageConfig(): void {
  try {
    if (!fs.existsSync(JUPYTERLAB_PAGE_CONFIG_DIR)) {
      fs.mkdirSync(JUPYTERLAB_PAGE_CONFIG_DIR, { recursive: true });
    }

    const nextContents = JSON.stringify(JUPYTERLAB_PAGE_CONFIG, null, 2);
    if (
      fs.existsSync(JUPYTERLAB_PAGE_CONFIG_FILE) &&
      fs.readFileSync(JUPYTERLAB_PAGE_CONFIG_FILE, 'utf8') === nextContents
    ) {
      return;
    }

    fs.writeFileSync(JUPYTERLAB_PAGE_CONFIG_FILE, nextContents);
  } catch (error) {
    console.error('Failed to persist JupyterLab page config:', error);
  }
}
