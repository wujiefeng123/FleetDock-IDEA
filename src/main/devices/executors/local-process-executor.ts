// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Provide a local executor for AirSim or PX4 processes while keeping start/stop/probe logic isolated from UI code.

import { ChildProcess, spawn } from 'child_process';
import { RuntimeExecutor } from './runtime-executor';

export type LocalProcessTarget = {
  id: string;
  command?: string;
  workdir?: string;
};

export class LocalProcessExecutor
  implements RuntimeExecutor<LocalProcessTarget> {
  private _processes = new Map<string, ChildProcess>();

  async probe(target: LocalProcessTarget): Promise<boolean> {
    const child = this._processes.get(target.id);
    return !!child && !child.killed;
  }

  async start(target: LocalProcessTarget): Promise<void> {
    if (!target.command || (await this.probe(target))) {
      return;
    }

    const child = spawn(target.command, {
      cwd: target.workdir,
      shell: true,
      detached: false,
      stdio: 'ignore'
    });

    this._processes.set(target.id, child);
    child.once('exit', () => {
      this._processes.delete(target.id);
    });
  }

  async stop(target: LocalProcessTarget): Promise<void> {
    const child = this._processes.get(target.id);
    if (!child) {
      return;
    }
    child.kill();
    this._processes.delete(target.id);
  }
}
