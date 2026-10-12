import { EventEmitter } from 'events';
import { describe, expect, it, vi } from 'vitest';
import type { ChildProcess } from 'child_process';
import { QgcManager } from '../../src/main/groundstation/qgc-manager';

describe('QGroundControl process ownership', () => {
  it('closes only the process tracked by the manager', async () => {
    const manager = new QgcManager();
    const process = new EventEmitter() as ChildProcess;
    process.exitCode = null;
    process.kill = vi.fn(() => {
      process.exitCode = 0;
      queueMicrotask(() => process.emit('exit', 0, null));
      return true;
    });
    Reflect.set(manager, '_child', process);

    const status = await manager.close();

    expect(process.kill).toHaveBeenCalledTimes(1);
    expect(status.running).toBe(false);
    await manager.close();
    expect(process.kill).toHaveBeenCalledTimes(1);
  });
});
