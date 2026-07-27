// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Add a reusable TCP probe executor so orchestration can check remote AirSim or PX4 endpoints before deciding to start anything.

import * as net from 'net';
import { RuntimeExecutor } from './runtime-executor';

export type TcpProbeTarget = {
  host: string;
  port: number;
  timeoutMs?: number;
};

export class TcpProbeExecutor implements RuntimeExecutor<TcpProbeTarget> {
  async probe(target: TcpProbeTarget): Promise<boolean> {
    return new Promise(resolve => {
      const socket = net.createConnection(target.port, target.host);
      let resolved = false;
      const finish = (value: boolean) => {
        if (resolved) {
          return;
        }
        resolved = true;
        socket.destroy();
        resolve(value);
      };
      socket.setTimeout(target.timeoutMs ?? 1500);
      socket.once('connect', () => finish(true));
      socket.once('timeout', () => finish(false));
      socket.once('error', () => finish(false));
    });
  }

  async start(_target: TcpProbeTarget): Promise<void> {}

  async stop(_target: TcpProbeTarget): Promise<void> {}
}
