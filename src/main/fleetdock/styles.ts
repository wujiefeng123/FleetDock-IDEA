// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';

// [UAV-IDE MODIFY] Keep desktop color tokens shared across isolated renderer assets.
export function readFleetDockCss(fileName: string): string {
  const assets = path.resolve(__dirname, '..', '..', '..', 'app-assets');
  return [
    fs.readFileSync(path.join(assets, 'fleetdock-ui-tokens.css'), 'utf8'),
    fs.readFileSync(path.join(assets, fileName), 'utf8')
  ].join('\n');
}
