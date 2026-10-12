// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Lock the FleetDock PX4/Project AirSim profile and packaged-template contract together.

import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';
import {
  getUavAirframeProfile,
  UAV_AIRFRAME_PROFILES
} from '../../src/main/devices/uav-airframes';
import { DroneDevice, UavAirframeType } from '../../src/main/devices/types';
import { SimulationConfigGenerator } from '../../src/main/uavsimulation/config/simulation-config-generator';

const TEMPLATE_DIRECTORY = path.join(
  process.cwd(),
  'dist-resources',
  'skylab-simulation',
  'templates'
);

describe('UAV airframe profiles', () => {
  it('uses POSIX SITL airframes and runtime overrides without a PX4 rebuild', () => {
    expect(UAV_AIRFRAME_PROFILES.quadrotor.px4AutostartId).toBe(10016);
    expect(UAV_AIRFRAME_PROFILES.hexarotor.px4AutostartId).toBe(10016);
    expect(UAV_AIRFRAME_PROFILES.hexarotor.px4ParameterOverrides).toMatchObject(
      {
        MAV_TYPE: 13,
        CA_ROTOR_COUNT: 6,
        PWM_MAIN_FUNC5: 105,
        PWM_MAIN_FUNC6: 106
      }
    );
    expect(UAV_AIRFRAME_PROFILES.vtol_tailsitter.px4AutostartId).toBe(1041);
    expect(UAV_AIRFRAME_PROFILES.vtol_tiltrotor.px4AutostartId).toBe(1042);
    expect(
      UAV_AIRFRAME_PROFILES.vtol_tiltrotor.px4ParameterOverrides
    ).toMatchObject({
      CA_SV_TL0_CT: 0,
      CA_SV_TL1_CT: 0,
      VT_FWD_THRUST_EN: 0
    });
  });

  it.each(Object.values(UAV_AIRFRAME_PROFILES))(
    'matches $type to its packaged Project AirSim actuator contract',
    profile => {
      const source = fs.readFileSync(
        path.join(TEMPLATE_DIRECTORY, profile.robotTemplate),
        'utf8'
      );
      const actuatorOrder = /"actuator-order"\s*:\s*\[([\s\S]*?)\]/.exec(
        source
      )?.[1];
      expect(actuatorOrder).toBeTruthy();
      const ids = Array.from(
        String(actuatorOrder).matchAll(/"id"\s*:\s*"([^"]+)"/g)
      ).map(match => match[1]);
      expect(ids).toEqual(profile.actuatorContract.order);
      expect(source.match(/"type"\s*:\s*"rotor"/g) || []).toHaveLength(
        profile.actuatorContract.rotorCount
      );
      for (const id of profile.actuatorContract.order) {
        expect(source).toContain(`"name": "${id}"`);
      }
      expect(source).toContain(
        `"airframe-setup": "${profile.projectAirsimAirframe}"`
      );
    }
  );

  it('packages the complete AirTaxi rotor and shroud visual assembly', () => {
    const source = fs.readFileSync(
      path.join(TEMPLATE_DIRECTORY, 'robot_vtol_tiltrotor_px4.jsonc'),
      'utf8'
    );
    expect(source.match(/\/Drone\/AirTaxi\/AirTaxi_Rotor/g) || []).toHaveLength(
      4
    );
    expect(
      source.match(/\/Drone\/AirTaxi\/AirTaxi_Shroud/g) || []
    ).toHaveLength(4);
    for (const suffix of ['FL', 'FR', 'RL', 'RR']) {
      expect(source).toContain(`"name": "Prop_${suffix}"`);
      expect(source).toContain(`"name": "Shroud_${suffix}"`);
    }
    expect(source.match(/"type"\s*:\s*"tilt"/g) || []).toHaveLength(2);
    expect(source).toContain('"name": "Prop_FR_tilt_actuator"');
    expect(source).toContain('"name": "Prop_FL_tilt_actuator"');
  });

  it('normalizes unknown stored values to the quadrotor profile', () => {
    expect(getUavAirframeProfile('legacy-airframe').type).toBe('quadrotor');
  });

  it('generates all four robot files from canonical profiles instead of stale stored fields', () => {
    const workingDirectory = fs.mkdtempSync(
      path.join(process.cwd(), 'tmp-uav-airframes-')
    );
    const types = Object.keys(UAV_AIRFRAME_PROFILES) as UavAirframeType[];
    const runtimeIndexes = [7, 12, 20, 30];
    const drones = types.map(
      (type, index) =>
        (({
          id: `device-${index + 1}`,
          name: `drone${runtimeIndexes[index]}`,
          envVarName: `DEVICE_DRONE_${runtimeIndexes[index]}`,
          sensors: [],
          uav: {
            airframeType: type,
            px4AutostartId: 99999,
            projectAirsimAirframe: 'quadrotor-x',
            robotTemplate: 'robot_quadrotor_px4.jsonc'
          },
          simulation: { model: type },
          fleetdock: { params: {} }
        } as unknown) as DroneDevice)
    );
    try {
      const plan = new SimulationConfigGenerator().generate({
        workingDirectory,
        scene: {
          id: 'airframe-contracts',
          name: 'Airframe contracts',
          rootDirectory: workingDirectory,
          executablePath: path.join(workingDirectory, 'scene.exe'),
          workingDirectory,
          engine: 'project-airsim',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        },
        config: { airsim: { mode: 'local' }, drones },
        scenePackageDirectory: path.join(
          process.cwd(),
          'dist-resources',
          'skylab-simulation'
        ),
        windowsHostAddress: '127.0.0.1',
        pythonExecutable: process.execPath
      });
      expect(plan.drones).toHaveLength(types.length);
      expect(plan.drones.map(drone => drone.px4Instance)).toEqual([
        6,
        11,
        19,
        29
      ]);
      expect(plan.drones.map(drone => drone.origin.xyz)).toEqual([
        '0.0 0.0 -4.0',
        '8.0 0.0 -4.0',
        '16.0 0.0 -4.0',
        '0.0 8.0 -4.0'
      ]);
      plan.drones.forEach((drone, index) => {
        const profile = UAV_AIRFRAME_PROFILES[types[index]];
        const robotSource = fs.readFileSync(drone.robotFile, 'utf8');
        expect(drone.px4AutostartId).toBe(profile.px4AutostartId);
        expect(drone.robotTemplate).toBe(profile.robotTemplate);
        expect(drone.actuatorOrder).toEqual(profile.actuatorContract.order);
        expect(robotSource).not.toMatch(/"__[A-Z0-9_]+__"/);
        if (types[index].startsWith('vtol_')) {
          expect(robotSource).toContain('"type": "airspeed"');
          expect(robotSource).toContain('"forward-xyz": "1 0 0"');
        } else {
          expect(robotSource).not.toContain('"type": "airspeed"');
        }
      });
    } finally {
      fs.rmSync(workingDirectory, { recursive: true, force: true });
    }
  });
});
