// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Translate device-owned sensors into Project AirSim JSONC without coupling the device store to simulation files.

import { ManagedSensorDefinition, SensorVector3 } from '../../devices/types';

export function injectProjectAirsimSensors(
  robotJsonc: string,
  sensors: ManagedSensorDefinition[]
): string {
  const range = findTopLevelArrayProperty(robotJsonc, 'sensors');
  if (!range) {
    throw new Error(
      'Project AirSim 机器人模板缺少顶层 sensors 数组，无法应用设备传感器配置。'
    );
  }
  const propertyIndent = lineIndentAt(robotJsonc, range.start);
  const serialized = JSON.stringify(
    sensors.map(sensor => projectAirsimSensorJson(sensor)),
    null,
    2
  )
    .split('\n')
    .map((line, index) => (index === 0 ? line : propertyIndent + line))
    .join('\n');
  return `${robotJsonc.slice(0, range.start)}${serialized}${robotJsonc.slice(
    range.end
  )}`;
}

export function projectAirsimSensorJson(
  sensor: ManagedSensorDefinition
): Record<string, unknown> {
  const result: Record<string, unknown> = {
    id: sensor.id,
    type: sensor.type,
    enabled: sensor.enabled,
    'parent-link': sensor.mount.parentLink || 'Frame'
  };
  if (
    sensor.type === 'camera' ||
    sensor.mount.xyz.some(value => value !== 0) ||
    sensor.mount.rpyDeg.some(value => value !== 0)
  ) {
    result.origin = {
      xyz: vectorString(sensor.mount.xyz),
      'rpy-deg': vectorString(sensor.mount.rpyDeg)
    };
  }

  switch (sensor.type) {
    case 'camera':
      result['capture-interval'] = sensor.config.captureInterval;
      result['capture-settings'] = sensor.config.imageTypes.map(imageType => ({
        'image-type': imageType,
        width: sensor.config.width,
        height: sensor.config.height,
        'fov-degrees': sensor.config.fovDegrees,
        'capture-enabled': true,
        'streaming-enabled': sensor.config.streamingEnabled,
        'pixels-as-float': imageType === 1 || imageType === 2,
        compress: sensor.config.compress,
        ...(imageType === 0 && sensor.config.targetGamma != null
          ? { 'target-gamma': sensor.config.targetGamma }
          : {})
      }));
      if (sensor.config.gimbal) {
        result.gimbal = {
          'lock-roll': sensor.config.gimbal.lockRoll,
          'lock-pitch': sensor.config.gimbal.lockPitch,
          'lock-yaw': sensor.config.gimbal.lockYaw
        };
      }
      break;
    case 'imu':
      result.accelerometer = {
        'velocity-random-walk': sensor.config.accelerometer.randomWalk,
        tau: sensor.config.accelerometer.tau,
        'bias-stability': sensor.config.accelerometer.biasStability,
        'turn-on-bias': vectorString(sensor.config.accelerometer.turnOnBias)
      };
      result.gyroscope = {
        'angle-random-walk': sensor.config.gyroscope.randomWalk,
        tau: sensor.config.gyroscope.tau,
        'bias-stability': sensor.config.gyroscope.biasStability,
        'turn-on-bias': vectorString(sensor.config.gyroscope.turnOnBias)
      };
      break;
    case 'gps':
      assignIfDefined(
        result,
        'eph-time-constant',
        sensor.config.ephTimeConstant
      );
      assignIfDefined(
        result,
        'epv-time-constant',
        sensor.config.epvTimeConstant
      );
      assignIfDefined(result, 'eph-initial', sensor.config.ephInitial);
      assignIfDefined(result, 'epv-initial', sensor.config.epvInitial);
      assignIfDefined(result, 'eph-final', sensor.config.ephFinal);
      assignIfDefined(result, 'epv-final', sensor.config.epvFinal);
      break;
    case 'barometer':
      assignIfDefined(result, 'qnh', sensor.config.qnh);
      assignIfDefined(
        result,
        'pressure-factor-sigma',
        sensor.config.pressureFactorSigma
      );
      assignIfDefined(
        result,
        'pressure-factor-tau',
        sensor.config.pressureFactorTau
      );
      assignIfDefined(
        result,
        'uncorrelated-noise-sigma',
        sensor.config.uncorrelatedNoiseSigma
      );
      assignIfDefined(result, 'update-latency', sensor.config.updateLatency);
      assignIfDefined(
        result,
        'update-frequency',
        sensor.config.updateFrequency
      );
      assignIfDefined(result, 'startup-delay', sensor.config.startupDelay);
      break;
    case 'magnetometer':
      assignIfDefined(result, 'scale-factor', sensor.config.scaleFactor);
      if (sensor.config.noiseSigma) {
        result['noise-sigma'] = vectorString(sensor.config.noiseSigma);
      }
      if (sensor.config.noiseBias) {
        result['noise-bias'] = vectorString(sensor.config.noiseBias);
      }
      break;
    case 'airspeed':
      result['forward-xyz'] = vectorString(sensor.config.forwardXyz);
      assignIfDefined(
        result,
        'pressure-factor-sigma',
        sensor.config.pressureFactorSigma
      );
      assignIfDefined(
        result,
        'pressure-factor-tau',
        sensor.config.pressureFactorTau
      );
      assignIfDefined(
        result,
        'uncorrelated-noise-sigma',
        sensor.config.uncorrelatedNoiseSigma
      );
      break;
    case 'lidar':
      result['lidar-type'] = sensor.config.lidarType;
      result['number-of-channels'] = sensor.config.numberOfChannels;
      result.range = sensor.config.range;
      result['points-per-second'] = sensor.config.pointsPerSecond;
      result['report-frequency'] = sensor.config.reportFrequency;
      result['horizontal-rotation-frequency'] =
        sensor.config.horizontalRotationFrequency;
      result['horizontal-fov-start-deg'] = sensor.config.horizontalFovStartDeg;
      result['horizontal-fov-end-deg'] = sensor.config.horizontalFovEndDeg;
      result['vertical-rotation-frequency'] =
        sensor.config.verticalRotationFrequency;
      result['vertical-fov-lower-deg'] = sensor.config.verticalFovLowerDeg;
      result['vertical-fov-upper-deg'] = sensor.config.verticalFovUpperDeg;
      result['disable-self-hits'] = sensor.config.disableSelfHits;
      result['draw-debug-points'] = sensor.config.drawDebugPoints;
      break;
    case 'distance-sensor':
      result['min-distance'] = sensor.config.minDistance;
      result['max-distance'] = sensor.config.maxDistance;
      result['draw-debug-points'] = sensor.config.drawDebugPoints;
      break;
    case 'radar':
      result.fov = {
        'azimuth-min': sensor.config.fov.azimuthMin,
        'azimuth-max': sensor.config.fov.azimuthMax,
        'elevation-min': sensor.config.fov.elevationMin,
        'elevation-max': sensor.config.fov.elevationMax,
        'azimuth-resolution': sensor.config.fov.azimuthResolution,
        'elevation-resolution': sensor.config.fov.elevationResolution
      };
      result['range-max'] = sensor.config.rangeMax;
      result['range-resolution'] = sensor.config.rangeResolution;
      result['velocity-max'] = sensor.config.velocityMax;
      result['velocity-resolution'] = sensor.config.velocityResolution;
      result['detection-interval'] = sensor.config.detectionInterval;
      result['track-interval'] = sensor.config.trackInterval;
      result['rcs-adjust-factor'] = sensor.config.rcsAdjustFactor;
      result['draw-debug-points'] = sensor.config.drawDebugPoints;
      break;
    case 'battery':
      result['battery-mode'] = sensor.config.mode;
      result['total-battery-capacity'] = sensor.config.totalCapacity;
      result['battery-capacity-on-start'] = sensor.config.capacityOnStart;
      if (sensor.config.mode === 'simple-discharge-mode') {
        result['battery-drain-rate-on-start'] = sensor.config.drainRateOnStart;
      } else {
        result['rotor-power-coefficient'] = sensor.config.rotorPowerCoefficient;
      }
      break;
  }
  return result;
}

function assignIfDefined(
  target: Record<string, unknown>,
  key: string,
  value: unknown
): void {
  if (value != null) {
    target[key] = value;
  }
}

function vectorString(vector: SensorVector3): string {
  return vector.map(value => Number(value)).join(' ');
}

function lineIndentAt(source: string, index: number): string {
  const lineStart = source.lastIndexOf('\n', index - 1) + 1;
  return /^\s*/.exec(source.slice(lineStart, index))?.[0] || '';
}

function findTopLevelArrayProperty(
  source: string,
  propertyName: string
): { start: number; end: number } | undefined {
  let index = 0;
  let objectDepth = 0;
  let arrayDepth = 0;
  while (index < source.length) {
    const character = source[index];
    if (character === '"') {
      const stringEnd = skipString(source, index);
      if (objectDepth === 1 && arrayDepth === 0) {
        const token = JSON.parse(source.slice(index, stringEnd)) as string;
        if (token === propertyName) {
          let cursor = skipTrivia(source, stringEnd);
          if (source[cursor] === ':') {
            cursor = skipTrivia(source, cursor + 1);
            if (source[cursor] === '[') {
              return {
                start: cursor,
                end: findMatchingArrayEnd(source, cursor)
              };
            }
          }
        }
      }
      index = stringEnd;
      continue;
    }
    if (character === '/' && source[index + 1] === '/') {
      index = skipLineComment(source, index + 2);
      continue;
    }
    if (character === '/' && source[index + 1] === '*') {
      index = skipBlockComment(source, index + 2);
      continue;
    }
    if (character === '{') {
      objectDepth += 1;
    } else if (character === '}') {
      objectDepth -= 1;
    } else if (character === '[') {
      arrayDepth += 1;
    } else if (character === ']') {
      arrayDepth -= 1;
    }
    index += 1;
  }
  return undefined;
}

function findMatchingArrayEnd(source: string, start: number): number {
  let depth = 0;
  let index = start;
  while (index < source.length) {
    const character = source[index];
    if (character === '"') {
      index = skipString(source, index);
      continue;
    }
    if (character === '/' && source[index + 1] === '/') {
      index = skipLineComment(source, index + 2);
      continue;
    }
    if (character === '/' && source[index + 1] === '*') {
      index = skipBlockComment(source, index + 2);
      continue;
    }
    if (character === '[') {
      depth += 1;
    } else if (character === ']') {
      depth -= 1;
      if (depth === 0) {
        return index + 1;
      }
    }
    index += 1;
  }
  throw new Error('Project AirSim 机器人模板的 sensors 数组没有正确结束。');
}

function skipTrivia(source: string, start: number): number {
  let index = start;
  while (index < source.length) {
    if (/\s/.test(source[index])) {
      index += 1;
    } else if (source[index] === '/' && source[index + 1] === '/') {
      index = skipLineComment(source, index + 2);
    } else if (source[index] === '/' && source[index + 1] === '*') {
      index = skipBlockComment(source, index + 2);
    } else {
      break;
    }
  }
  return index;
}

function skipString(source: string, start: number): number {
  let index = start + 1;
  while (index < source.length) {
    if (source[index] === '\\') {
      index += 2;
    } else if (source[index] === '"') {
      return index + 1;
    } else {
      index += 1;
    }
  }
  throw new Error('Project AirSim 机器人模板包含未结束的字符串。');
}

function skipLineComment(source: string, start: number): number {
  const end = source.indexOf('\n', start);
  return end < 0 ? source.length : end + 1;
}

function skipBlockComment(source: string, start: number): number {
  const end = source.indexOf('*/', start);
  if (end < 0) {
    throw new Error('Project AirSim 机器人模板包含未结束的块注释。');
  }
  return end + 2;
}
