// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { UavAirframeType } from './types';

// [UAV-IDE MODIFY] Centralize the PX4 and Project AirSim fields that both sides can represent today.
export type UavAirframeProfile = {
  type: UavAirframeType;
  label: string;
  px4AutostartId: number;
  projectAirsimAirframe: string;
  robotTemplate: string;
};

export const UAV_AIRFRAME_PROFILES: Record<
  UavAirframeType,
  UavAirframeProfile
> = {
  quadrotor: {
    type: 'quadrotor',
    label: 'Quadrotor',
    px4AutostartId: 10016,
    projectAirsimAirframe: 'quadrotor-x',
    robotTemplate: 'robot_quadrotor_px4.jsonc'
  },
  hexarotor: {
    type: 'hexarotor',
    label: 'Hexarotor',
    px4AutostartId: 6001,
    projectAirsimAirframe: 'hexarotor-x',
    robotTemplate: 'robot_quadrotor_px4.jsonc'
  },
  vtol_tailsitter: {
    type: 'vtol_tailsitter',
    label: 'VTOL Tailsitter',
    px4AutostartId: 13200,
    projectAirsimAirframe: 'vtol-quad-x-tailsitter',
    robotTemplate: 'robot_quadrotor_px4.jsonc'
  },
  vtol_tiltrotor: {
    type: 'vtol_tiltrotor',
    label: 'VTOL Tiltrotor',
    px4AutostartId: 13030,
    projectAirsimAirframe: 'vtol-quad-tiltrotor',
    robotTemplate: 'robot_quadrotor_px4.jsonc'
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
