// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';

// [UAV-IDE MODIFY] Keep the packaged FleetDock teaching template isolated from
// project creation so individual demos can evolve without embedding notebook
// JSON in Electron main-process code.
const FLEETDOCK_DEMO_TEMPLATE_DIRECTORY = 'fleetdock-demo-template';

export function ensureFleetDockDemoNotebooks(workingDirectory: string): void {
  const demoDirectory = path.join(workingDirectory, 'demo');
  const templateDirectory = resolveFleetDockDemoTemplateDirectory();
  copyTemplateDirectory(templateDirectory, demoDirectory);
}

function resolveFleetDockDemoTemplateDirectory(): string {
  const candidates = [
    path.resolve(
      __dirname,
      '..',
      '..',
      '..',
      'app-assets',
      FLEETDOCK_DEMO_TEMPLATE_DIRECTORY
    ),
    path.resolve(
      process.cwd(),
      'src',
      'assets',
      FLEETDOCK_DEMO_TEMPLATE_DIRECTORY
    )
  ];
  const templateDirectory = candidates.find(candidate =>
    fs.existsSync(candidate)
  );
  if (!templateDirectory) {
    throw new Error(
      `FleetDock Demo 模板未随应用安装：${FLEETDOCK_DEMO_TEMPLATE_DIRECTORY}`
    );
  }
  return templateDirectory;
}

function copyTemplateDirectory(source: string, target: string): void {
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const sourcePath = path.join(source, entry.name);
    const targetPath = path.join(target, entry.name);
    if (entry.isDirectory()) {
      copyTemplateDirectory(sourcePath, targetPath);
    } else if (entry.isFile() && !fs.existsSync(targetPath)) {
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
}
