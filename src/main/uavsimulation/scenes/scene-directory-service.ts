// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';
import { UavSimulationScene } from '../types';

// [UAV-IDE MODIFY] Scan a user-selected Project AirSim package root and register every packaged scene executable under it.
export class UavSimulationSceneDirectoryService {
  scanPackageDirectory(rootDirectory: string): Partial<UavSimulationScene>[] {
    const normalizedRoot = this._validateDirectory(rootDirectory);
    const executables = this._findSceneExecutables(normalizedRoot);
    if (!executables.length) {
      throw new Error(
        'No Project AirSim scene executable was found under the selected directory.'
      );
    }
    return executables.map(executablePath => ({
      name: path.basename(executablePath, '.exe'),
      rootDirectory: normalizedRoot,
      executablePath,
      workingDirectory: path.dirname(executablePath),
      engine: 'project-airsim'
    }));
  }

  inspectDirectory(rootDirectory: string): Partial<UavSimulationScene> {
    const scenes = this.scanPackageDirectory(rootDirectory);
    if (scenes.length > 1) {
      throw new Error(
        'The selected directory contains multiple scenes; scan the package root from Simulation Config.'
      );
    }
    return scenes[0];
  }

  private _validateDirectory(rootDirectory: string): string {
    const normalizedRoot = path.normalize(String(rootDirectory || '').trim());
    if (!normalizedRoot || !fs.existsSync(normalizedRoot)) {
      throw new Error('Scene directory does not exist.');
    }
    if (!fs.statSync(normalizedRoot).isDirectory()) {
      throw new Error('Please select a scene directory instead of a file.');
    }
    return normalizedRoot;
  }

  private _findSceneExecutables(rootDirectory: string): string[] {
    const results: string[] = [];
    const visit = (directory: string, depth: number) => {
      if (depth > 4) {
        return;
      }
      const entries = fs.readdirSync(directory, { withFileTypes: true });
      const hasEngine = entries.some(
        entry => entry.isDirectory() && entry.name === 'Engine'
      );
      for (const entry of entries) {
        if (!entry.isFile() || !/.exe$/i.test(entry.name)) {
          continue;
        }
        const appName = path.basename(entry.name, '.exe');
        const appContentDirectory = path.join(directory, appName);
        if (
          hasEngine &&
          fs.existsSync(appContentDirectory) &&
          fs.statSync(appContentDirectory).isDirectory()
        ) {
          results.push(path.join(directory, entry.name));
        }
      }
      for (const entry of entries) {
        if (entry.isDirectory()) {
          visit(path.join(directory, entry.name), depth + 1);
        }
      }
    };
    visit(rootDirectory, 0);
    return results.sort((left, right) => left.localeCompare(right));
  }
}
