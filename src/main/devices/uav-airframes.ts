// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable camelcase -- [UAV-IDE MODIFY] Keep stable UAV airframe identifiers aligned with workspace configuration values. */

import { UavAirframeType } from './types';

// [UAV-IDE MODIFY] Centralize the PX4 and Project AirSim fields that both sides can represent today.
export type UavAirframeProfile = {
  type: UavAirframeType;
  label: string;
  px4AutostartId: number;
  px4ParameterOverrides: Readonly<Record<string, number>>;
  projectAirsimAirframe: string;
  robotTemplate: string;
  actuatorContract: {
    order: readonly string[];
    rotorCount: number;
  };
  capabilities: {
    verticalTakeoff: boolean;
    fixedWingTransition: boolean;
  };
};

export const UAV_AIRFRAME_PROFILES: Record<
  UavAirframeType,
  UavAirframeProfile
> = {
  quadrotor: {
    type: 'quadrotor',
    label: 'Quadrotor',
    px4AutostartId: 10016,
    px4ParameterOverrides: {},
    projectAirsimAirframe: 'quadrotor-x',
    robotTemplate: 'robot_quadrotor_px4.jsonc',
    actuatorContract: {
      order: [
        'Prop_FR_actuator',
        'Prop_RL_actuator',
        'Prop_FL_actuator',
        'Prop_RR_actuator'
      ],
      rotorCount: 4
    },
    capabilities: { verticalTakeoff: true, fixedWingTransition: false }
  },
  hexarotor: {
    type: 'hexarotor',
    label: 'Hexarotor',
    // [UAV-IDE MODIFY] Reuse the packaged generic SITL startup and override only the allocator geometry; hardware airframe 6001 is not present in init.d-posix.
    px4AutostartId: 10016,
    px4ParameterOverrides: {
      MAV_TYPE: 13,
      CA_AIRFRAME: 0,
      CA_ROTOR_COUNT: 6,
      CA_ROTOR0_PX: 0.7,
      CA_ROTOR0_PY: -0.4,
      CA_ROTOR0_KM: -0.05,
      CA_ROTOR1_PX: 0.7,
      CA_ROTOR1_PY: 0.4,
      CA_ROTOR1_KM: 0.05,
      CA_ROTOR2_PX: 0,
      CA_ROTOR2_PY: 0.82,
      CA_ROTOR2_KM: -0.05,
      CA_ROTOR3_PX: -0.7,
      CA_ROTOR3_PY: 0.4,
      CA_ROTOR3_KM: 0.05,
      CA_ROTOR4_PX: -0.7,
      CA_ROTOR4_PY: -0.4,
      CA_ROTOR4_KM: -0.05,
      CA_ROTOR5_PX: 0,
      CA_ROTOR5_PY: -0.82,
      CA_ROTOR5_KM: 0.05,
      PWM_MAIN_FUNC1: 101,
      PWM_MAIN_FUNC2: 102,
      PWM_MAIN_FUNC3: 103,
      PWM_MAIN_FUNC4: 104,
      PWM_MAIN_FUNC5: 105,
      PWM_MAIN_FUNC6: 106
    },
    projectAirsimAirframe: 'hexarotor-x',
    robotTemplate: 'robot_hexarotor_px4.jsonc',
    actuatorContract: {
      order: [
        'Prop_0_actuator',
        'Prop_1_actuator',
        'Prop_2_actuator',
        'Prop_3_actuator',
        'Prop_4_actuator',
        'Prop_5_actuator'
      ],
      rotorCount: 6
    },
    capabilities: { verticalTakeoff: true, fixedWingTransition: false }
  },
  vtol_tailsitter: {
    type: 'vtol_tailsitter',
    label: 'VTOL Tailsitter',
    px4AutostartId: 1041,
    px4ParameterOverrides: {},
    projectAirsimAirframe: 'vtol-quad-x-tailsitter',
    robotTemplate: 'robot_vtol_tailsitter_px4.jsonc',
    actuatorContract: {
      order: [
        'Prop_FR_actuator',
        'Prop_RL_actuator',
        'Prop_FL_actuator',
        'Prop_RR_actuator',
        'Unused_actuator',
        'Elevon_L_actuator',
        'Elevon_R_actuator'
      ],
      rotorCount: 4
    },
    capabilities: { verticalTakeoff: true, fixedWingTransition: true }
  },
  vtol_tiltrotor: {
    type: 'vtol_tiltrotor',
    label: 'VTOL Tiltrotor',
    px4AutostartId: 1042,
    // [UAV-IDE MODIFY] Keep AirTaxi tilt pods vertical in multicopter flight because its large FastPhysics model cannot safely use the Gazebo 1042 vectored-thrust tuning.
    px4ParameterOverrides: {
      CA_SV_TL0_CT: 0,
      CA_SV_TL1_CT: 0,
      VT_FWD_THRUST_EN: 0
    },
    projectAirsimAirframe: 'vtol-quad-tiltrotor',
    robotTemplate: 'robot_vtol_tiltrotor_px4.jsonc',
    actuatorContract: {
      // [UAV-IDE MODIFY] Match the nine effective outputs of PX4 POSIX airframe 1042.
      order: [
        'Prop_FR_actuator',
        'Prop_RL_actuator',
        'Prop_FL_actuator',
        'Prop_RR_actuator',
        'Prop_FR_tilt_actuator',
        'Prop_FL_tilt_actuator',
        'Aileron_L_actuator',
        'Aileron_R_actuator',
        'Elevator_actuator'
      ],
      rotorCount: 4
    },
    capabilities: { verticalTakeoff: true, fixedWingTransition: true }
  }
};

export function normalizeUavAirframeType(value: unknown): UavAirframeType {
  if (value === 'hexarotor') {
    return 'hexarotor';
  }
  if (value === 'vtol_tailsitter') {
    return 'vtol_tailsitter';
  }
  if (value === 'vtol_tiltrotor') {
    return 'vtol_tiltrotor';
  }
  return 'quadrotor';
}

export function getUavAirframeProfile(value: unknown): UavAirframeProfile {
  return UAV_AIRFRAME_PROFILES[normalizeUavAirframeType(value)];
}
