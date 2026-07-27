// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { performance } from 'perf_hooks';

type TimingEntry = {
  label: string;
  durationMs: number;
  outcome: 'success' | 'failed';
};

// [UAV-IDE MODIFY] Record simulation lifecycle timings with a monotonic clock.
export class SimulationTimingRecorder {
  constructor(private _scope: 'startup' | 'shutdown') {}

  async measure<T>(label: string, action: () => T | Promise<T>): Promise<T> {
    const startedAt = performance.now();
    try {
      const result = await action();
      this._entries.push({
        label,
        durationMs: this._elapsed(startedAt),
        outcome: 'success'
      });
      return result;
    } catch (error) {
      this._entries.push({
        label,
        durationMs: this._elapsed(startedAt),
        outcome: 'failed'
      });
      throw error;
    }
  }

  async finish(
    outcome: 'success' | 'failed',
    appendLog: (line: string) => Promise<void>
  ): Promise<void> {
    for (const entry of this._entries) {
      await appendLog(
        `[simulation-timing] ${this._scope}.${entry.label} durationMs=${entry.durationMs} outcome=${entry.outcome}`
      ).catch((): void => undefined);
    }
    await appendLog(
      `[simulation-timing] ${this._scope}.total durationMs=${this._elapsed(
        this._startedAt
      )} outcome=${outcome}`
    ).catch((): void => undefined);
  }

  private _elapsed(startedAt: number): number {
    return Math.max(0, Math.round(performance.now() - startedAt));
  }

  private _startedAt = performance.now();
  private _entries: TimingEntry[] = [];
}
