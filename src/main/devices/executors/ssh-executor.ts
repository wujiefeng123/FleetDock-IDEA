// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Keep remote PX4 control behind an SSH executor abstraction so orchestration can support local and remote targets uniformly.

import { spawn } from 'child_process';
import { RuntimeExecutor } from './runtime-executor';

export type SshTarget = {
  host: string;
  port?: number;
  username?: string;
  command?: string;
};

function buildSshCommand(target: SshTarget): string | null {
  if (!target.command || !target.host) {
    return null;
  }

  const portPart = target.port ? `-p ${target.port}` : '';
  const userHost = target.username
    ? `${target.username}@${target.host}`
    : target.host;
  return `ssh ${portPart} ${userHost} "${target.command}"`;
}

export class SshExecutor implements RuntimeExecutor<SshTarget> {
  async probe(_target: SshTarget): Promise<boolean> {
    return false;
  }

  async start(target: SshTarget): Promise<void> {
    const command = buildSshCommand(target);
    if (!command) {
      return;
    }

    await new Promise<void>((resolve, reject) => {
      const child = spawn(command, {
        shell: true,
        detached: false,
        stdio: 'ignore'
      });
      child.once('error', reject);
      child.once('spawn', () => resolve());
    });
  }

  async stop(_target: SshTarget): Promise<void> {
    // [UAV-IDE MODIFY] Remote stop commands depend on deployment conventions, so keep stop as a no-op until explicit remote shutdown rules are defined.
  }
}
