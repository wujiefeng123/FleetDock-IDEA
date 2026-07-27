// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { IApplicationEnvVar } from '../config/application';

// [UAV-IDE MODIFY] Keep UAV application creation payloads out of SessionWindow so workspace services own application data shape.
export interface ICreateApplicationRequest {
  appCode: string;
  version: string;
  name: string;
  description: string;
  entryFile: string;
  maxRunSeconds: number | string;
  changeLog: string;
  workingDirectory: string;
  runtimeLabel: string;
  pythonPath: string;
  envVars: IApplicationEnvVar[];
  aiGateway: {
    gatewayUrl: string;
    defaultModel: string;
    routeStrategy: string;
    timeoutMs: number | string;
    apiKey: string;
    ak: string;
    sk: string;
  };
  simulator: {
    engine: string;
    worldFile: string;
    realTimeFactor: string;
    wslDistro?: string;
    px4WorkingDirectory?: string;
    sitlCommand?: string;
    airSimExecutable?: string;
    airSimArgs?: string[];
    autoLaunchAirSim?: boolean;
    // [UAV-IDE MODIFY] Carry local Project AirSim scene loader settings from app creation into desktop-app.json.
    sceneFile?: string;
    sceneLoaderScript?: string;
  };
  groundStation: {
    stationType: string;
    connectionUrl: string;
    autoConnect: boolean;
    executablePath?: string;
    launchArgs?: string[];
  };
}
