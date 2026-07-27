// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';
import { ensureFleetDockDemoNotebooks } from './fleetdock-demo-notebooks';

// [UAV-IDE MODIFY] Isolate default UAV application project files from SessionWindow lifecycle code.
export function ensureApplicationProjectFiles(workingDirectory: string) {
  const mainPath = path.join(workingDirectory, 'main.py');
  const requirementsPath = path.join(workingDirectory, 'requirements.txt');

  if (!fs.existsSync(mainPath)) {
    // [UAV-IDE MODIFY] Seed each new project with a single Python entrypoint so later run/publish flows can rely on `main.py`.
    fs.writeFileSync(
      mainPath,
      `# [UAV-IDE MODIFY] Default project entrypoint generated for new applications.
from fleetdock_runtime import FleetDock


def main():
    FleetDock.init()
    print("FleetDock runtime is ready:", FleetDock.info())


if __name__ == "__main__":
    main()
`,
      'utf8'
    );
  }

  if (!fs.existsSync(requirementsPath)) {
    // [UAV-IDE MODIFY] Seed each new project with a dependency manifest so later run/publish flows can install project requirements consistently.
    fs.writeFileSync(
      requirementsPath,
      '# [UAV-IDE MODIFY] Add project dependencies here, one package per line.\n',
      'utf8'
    );
  }

  // [UAV-IDE MODIFY] Seed non-destructive FleetDock teaching notebooks for every newly created application.
  ensureFleetDockDemoNotebooks(workingDirectory);
}
