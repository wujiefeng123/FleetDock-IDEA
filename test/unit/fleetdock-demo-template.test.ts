// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, describe, expect, it } from 'vitest';
import { ensureFleetDockDemoNotebooks } from '../../src/main/application/fleetdock-demo-notebooks';

describe('FleetDock new-project demo template', () => {
  const temporaryDirectories: string[] = [];

  afterEach(() => {
    for (const directory of temporaryDirectories.splice(0)) {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('creates the packaged demos without demo_bootstrap.py or saved outputs', () => {
    const workingDirectory = fs.mkdtempSync(
      path.join(os.tmpdir(), 'fleetdock-demo-template-')
    );
    temporaryDirectories.push(workingDirectory);

    ensureFleetDockDemoNotebooks(workingDirectory);

    const demoDirectory = path.join(workingDirectory, 'demo');
    const notebooks = fs
      .readdirSync(demoDirectory)
      .filter(fileName => fileName.endsWith('.ipynb'));
    expect(notebooks).toHaveLength(13);
    expect(fs.existsSync(path.join(demoDirectory, 'demo_bootstrap.py'))).toBe(
      false
    );

    const notebookText = notebooks
      .map(fileName =>
        fs.readFileSync(path.join(demoDirectory, fileName), 'utf8')
      )
      .join('\n');
    expect(notebookText).not.toContain('demo_bootstrap');
    expect(notebookText).toContain('from fleetdock_runtime import FleetDock');

    for (const fileName of notebooks) {
      const notebook = JSON.parse(
        fs.readFileSync(path.join(demoDirectory, fileName), 'utf8')
      );
      for (const cell of notebook.cells) {
        if (cell.cell_type === 'code') {
          expect(cell.execution_count).toBeNull();
          expect(cell.outputs).toEqual([]);
        }
      }
    }
  });

  it('does not overwrite an existing project demo file', () => {
    const workingDirectory = fs.mkdtempSync(
      path.join(os.tmpdir(), 'fleetdock-demo-template-')
    );
    temporaryDirectories.push(workingDirectory);
    const demoDirectory = path.join(workingDirectory, 'demo');
    const notebookPath = path.join(
      demoDirectory,
      '01_三行开始_FleetDock运行时.ipynb'
    );
    fs.mkdirSync(demoDirectory, { recursive: true });
    fs.writeFileSync(notebookPath, 'user-edited-demo', 'utf8');

    ensureFleetDockDemoNotebooks(workingDirectory);

    expect(fs.readFileSync(notebookPath, 'utf8')).toBe('user-edited-demo');
  });
});
