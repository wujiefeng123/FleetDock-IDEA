// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Expose a focused UAV device registry API so dialogs, orchestration, and future panels share one source of truth.

import { DeviceStore } from './storage/device-store';
import { DroneDevice } from './types';

export class DeviceRegistry {
  constructor(private _store: DeviceStore) {}

  async addDevice(device: Partial<DroneDevice>): Promise<DroneDevice> {
    return this._store.upsert(device);
  }

  async removeDevice(id: string): Promise<boolean> {
    // [UAV-IDE MODIFY] Return the deletion outcome for inline command-center device management.
    return this._store.remove(id);
  }

  async updateDevice(device: Partial<DroneDevice>): Promise<DroneDevice> {
    return this._store.upsert(device);
  }

  async listDevices(): Promise<DroneDevice[]> {
    return this._store.list();
  }
}
