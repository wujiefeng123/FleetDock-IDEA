// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable @typescript-eslint/naming-convention -- [UAV-IDE MODIFY] Preserve the FleetDock executor contract name. */

// [UAV-IDE MODIFY] Define a shared execution contract so local, SSH, and probe behaviors can be composed in the orchestrator.

export interface RuntimeExecutor<TTarget = any> {
  probe(target: TTarget): Promise<boolean>;
  start(target: TTarget): Promise<void>;
  stop(target: TTarget): Promise<void>;
}
