// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as ejs from 'ejs';
import * as path from 'path';
import * as fs from 'fs';
import { ThemedWindow } from '../dialog/themedwindow';
import { app } from 'electron';
import {
  PRODUCT_NAME,
  PRODUCT_TEAM_NAME,
  UPSTREAM_PRODUCT_NAME
} from '../branding';

export class AboutDialog {
  constructor(options: AboutDialog.IOptions) {
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      // [UAV-IDE MODIFY] Keep the upstream dialog behavior and only replace product identity.
      title: `About ${PRODUCT_NAME}`,
      width: 400,
      height: 320,
      preload: path.join(__dirname, './preload.js')
    });

    const logoSrc = fs.readFileSync(
      path.join(__dirname, '../../../app-assets/icon.svg')
    );

    const template = `
      <style>
        .dialog-container {
          display: flex;
          flex-direction: column;
          padding: 10px;
        }
        .dialog-container a {
          color: #222222;
          outline: none;
        }
        .app-ui-dark .dialog-container a {
          color: #5d96ed;
        }
        .row {
          display: flex;
          flex-direction: row;
        }
        .logo svg {
          width: 80px;
          height: 80px;
        }
        .logo-row {
          align-items: center;
        }
        .app-title-version {
          padding: 20px;
        }
        .app-title {
          font-size: 20px;
          margin-bottom: 10px;
        }
        .app-version {
          color: var(--neutral-foreground-hint);
        }
        .product-copyright-row {
          margin: 20px 0 8px;
        }
        .upstream-row {
          margin: 4px 0;
          color: var(--neutral-foreground-hint);
        }
      </style>
      <div class="dialog-container">
        <div class="row logo-row">
          <div class="logo">
            ${logoSrc}
          </div>
          <div class="app-title-version">
            <div class="app-title">
              ${PRODUCT_NAME}
            </div>
            <div class="app-version">
              Version <%= version %>
            </div>
          </div>
        </div>
        <div class="row product-copyright-row">
          © <%= thisYear %> ${PRODUCT_TEAM_NAME}
        </div>
        <div class="row upstream-row">
          Built on ${UPSTREAM_PRODUCT_NAME}
        </div>
        <div class="row upstream-row">
          © 2015-<%= thisYear %>  Project Jupyter Contributors
        </div>
        <div class="row upstream-row">
          <a href="javascript:void(0)" onclick='handleAboutProjectJupyterLink(this);'>About Project Jupyter</a>
        </div>

        <script>
          function handleAboutProjectJupyterLink(el) {
            window.electronAPI.launchAboutJupyterPage();
          }
        </script>
      </div>
    `;
    this._pageBody = ejs.render(template, {
      version: app.getVersion(),
      thisYear: new Date().getFullYear()
    });
  }

  get window(): ThemedWindow {
    return this._window;
  }

  load() {
    this._window.loadDialogContent(this._pageBody);
  }

  private _window: ThemedWindow;
  private _pageBody: string;
}

export namespace AboutDialog {
  export interface IOptions {
    isDarkTheme: boolean;
  }
}
