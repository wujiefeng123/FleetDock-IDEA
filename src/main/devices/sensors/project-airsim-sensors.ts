// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Centralize Project AirSim sensor presets, migration, and validation for the global device library.

import {
  CameraCaptureType,
  IManagedSensorMount,
  ManagedSensorDefinition,
  ManagedSensorType,
  ProjectAirsimLidarType,
  SensorVector3
} from '../types';

export const PROJECT_AIRSIM_SENSOR_TYPE_OPTIONS: Array<{
  value: ManagedSensorType;
  label: string;
}> = [
  { value: 'camera', label: '相机' },
  { value: 'imu', label: 'IMU' },
  { value: 'gps', label: 'GPS' },
  { value: 'barometer', label: '气压计' },
  { value: 'magnetometer', label: '磁力计' },
  { value: 'airspeed', label: '空速计' },
  { value: 'lidar', label: '激光雷达' },
  { value: 'distance-sensor', label: '距离传感器' },
  { value: 'radar', label: '雷达' },
  { value: 'battery', label: '电池状态' }
];

export const PROJECT_AIRSIM_FLIGHT_SENSOR_IDS = [
  'IMU1',
  'GPS',
  'Barometer',
  'Magnetometer'
] as const;

const SENSOR_TYPES = new Set<ManagedSensorType>(
  PROJECT_AIRSIM_SENSOR_TYPE_OPTIONS.map(option => option.value)
);
const LIDAR_TYPES = new Set([
  'generic_cylindrical',
  'generic_rosette',
  'livox_mid70',
  'livox_avia',
  'gpu_cylindrical',
  'depth_lidar'
]);

function cloneSensor<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function mount(
  xyz: SensorVector3 = [0, 0, 0],
  rpyDeg: SensorVector3 = [0, 0, 0]
): IManagedSensorMount {
  return { parentLink: 'Frame', xyz, rpyDeg };
}

export function createProjectAirsimSensorPreset(
  type: ManagedSensorType,
  id?: string
): ManagedSensorDefinition {
  const sensorId = String(id || defaultId(type));
  const label = sensorTypeLabel(type);
  const base = {
    id: sensorId,
    name: label,
    enabled: true,
    mount: mount()
  };

  switch (type) {
    case 'camera':
      return {
        ...base,
        type,
        config: {
          captureInterval: 0.03,
          imageTypes: [0],
          width: 1280,
          height: 720,
          fovDegrees: 90,
          streamingEnabled: false,
          compress: false,
          targetGamma: 2.5,
          gimbal: {
            lockRoll: false,
            lockPitch: false,
            lockYaw: false
          }
        }
      };
    case 'imu':
      return {
        ...base,
        type,
        config: {
          accelerometer: {
            randomWalk: 2.353e-3,
            tau: 800,
            biasStability: 3.53e-4,
            turnOnBias: [0, 0, 0]
          },
          gyroscope: {
            randomWalk: 8.72644e-5,
            tau: 500,
            biasStability: 2.23014e-5,
            turnOnBias: [0, 0, 0]
          }
        }
      };
    case 'gps':
      return { ...base, type, config: {} };
    case 'barometer':
      return { ...base, type, config: {} };
    case 'magnetometer':
      return { ...base, type, config: {} };
    case 'airspeed':
      return {
        ...base,
        type,
        config: { forwardXyz: [1, 0, 0] }
      };
    case 'lidar':
      return {
        ...base,
        type,
        config: {
          lidarType: 'generic_cylindrical',
          numberOfChannels: 16,
          range: 100,
          pointsPerSecond: 100000,
          reportFrequency: 0,
          horizontalRotationFrequency: 10,
          horizontalFovStartDeg: 0,
          horizontalFovEndDeg: 360,
          verticalRotationFrequency: 0,
          verticalFovLowerDeg: -45,
          verticalFovUpperDeg: -15,
          disableSelfHits: true,
          drawDebugPoints: false
        }
      };
    case 'distance-sensor':
      return {
        ...base,
        type,
        config: {
          minDistance: 0.5,
          maxDistance: 50,
          drawDebugPoints: false
        }
      };
    case 'radar':
      return {
        ...base,
        type,
        config: {
          rangeMax: 500,
          rangeResolution: 1,
          velocityMax: 100,
          velocityResolution: 1,
          detectionInterval: 0.02,
          trackInterval: 0.2,
          rcsAdjustFactor: 0.1,
          drawDebugPoints: false,
          fov: {
            azimuthMin: -0.785,
            azimuthMax: 0.785,
            elevationMin: -0.524,
            elevationMax: 0.524,
            azimuthResolution: 0.0175,
            elevationResolution: 0.0175
          }
        }
      };
    case 'battery':
      return {
        ...base,
        type,
        config: {
          mode: 'rotor-power-discharge-mode',
          totalCapacity: 36000,
          capacityOnStart: 30000,
          rotorPowerCoefficient: 1
        }
      };
  }
}

export function createDefaultProjectAirsimSensors(): ManagedSensorDefinition[] {
  const chase = createProjectAirsimSensorPreset('camera', 'Chase');
  chase.name = '追踪相机';
  chase.mount = mount([-10, 0, -1], [0, -11.46, 0]);
  if (chase.type === 'camera') {
    chase.config.streamingEnabled = true;
    chase.config.gimbal = {
      lockRoll: true,
      lockPitch: true,
      lockYaw: false
    };
  }

  const imu = createProjectAirsimSensorPreset('imu', 'IMU1');
  imu.name = '飞控 IMU';
  const gps = createProjectAirsimSensorPreset('gps', 'GPS');
  const barometer = createProjectAirsimSensorPreset('barometer', 'Barometer');
  const magnetometer = createProjectAirsimSensorPreset(
    'magnetometer',
    'Magnetometer'
  );
  return [chase, imu, gps, barometer, magnetometer];
}

export function sensorTypeLabel(type: ManagedSensorType): string {
  return (
    PROJECT_AIRSIM_SENSOR_TYPE_OPTIONS.find(option => option.value === type)
      ?.label || type
  );
}

export function normalizeManagedSensors(
  value: unknown,
  options: { includeDefaults: boolean; strict?: boolean }
): ManagedSensorDefinition[] {
  if (value == null) {
    return options.includeDefaults ? createDefaultProjectAirsimSensors() : [];
  }
  if (!Array.isArray(value)) {
    if (options.strict) {
      throw new Error('传感器配置必须是数组。');
    }
    return options.includeDefaults ? createDefaultProjectAirsimSensors() : [];
  }

  const sensors: ManagedSensorDefinition[] = [];
  for (const item of value) {
    try {
      const sensor = normalizeSensor(item);
      if (!options.strict) {
        assertValidManagedSensors([...sensors, sensor]);
      }
      sensors.push(sensor);
    } catch (error) {
      if (options.strict) {
        throw error;
      }
      console.warn('Ignored malformed managed sensor:', error);
    }
  }
  if (options.strict) {
    assertValidManagedSensors(sensors);
  }
  return sensors;
}

export function assertValidManagedSensors(
  sensors: ManagedSensorDefinition[]
): void {
  const ids = new Set<string>();
  for (const sensor of sensors) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(sensor.id)) {
      throw new Error(
        `传感器 ID“${sensor.id}”无效，只能以字母开头并包含字母、数字、下划线或连字符。`
      );
    }
    const normalizedId = sensor.id.toLowerCase();
    if (ids.has(normalizedId)) {
      throw new Error(`传感器 ID 重复：${sensor.id}`);
    }
    ids.add(normalizedId);
    if (!sensor.name.trim()) {
      throw new Error(`传感器 ${sensor.id} 缺少名称。`);
    }
    if (!sensor.mount.parentLink.trim()) {
      throw new Error(`传感器 ${sensor.id} 缺少父 Link。`);
    }
    assertVector(sensor.mount.xyz, `${sensor.id} 安装位置`);
    assertVector(sensor.mount.rpyDeg, `${sensor.id} 安装角度`);

    switch (sensor.type) {
      case 'camera':
        assertPositive(sensor.config.captureInterval, `${sensor.id} 采集周期`);
        assertIntegerRange(sensor.config.width, 1, 8192, `${sensor.id} 宽度`);
        assertIntegerRange(sensor.config.height, 1, 8192, `${sensor.id} 高度`);
        assertRange(sensor.config.fovDegrees, 5, 170, `${sensor.id} FOV`);
        if (!sensor.config.imageTypes.length) {
          throw new Error(`相机 ${sensor.id} 至少需要一种图像输出。`);
        }
        for (const imageType of sensor.config.imageTypes) {
          assertIntegerRange(imageType, 0, 6, `${sensor.id} 图像类型`);
        }
        break;
      case 'imu':
        assertNonNegative(
          sensor.config.accelerometer.randomWalk,
          `${sensor.id} 加速度计随机游走`
        );
        assertPositive(
          sensor.config.accelerometer.tau,
          `${sensor.id} 加速度计时间常数`
        );
        assertNonNegative(
          sensor.config.gyroscope.randomWalk,
          `${sensor.id} 陀螺仪随机游走`
        );
        assertPositive(
          sensor.config.gyroscope.tau,
          `${sensor.id} 陀螺仪时间常数`
        );
        assertNonNegative(
          sensor.config.accelerometer.biasStability,
          `${sensor.id} 加速度计偏置稳定性`
        );
        assertNonNegative(
          sensor.config.gyroscope.biasStability,
          `${sensor.id} 陀螺仪偏置稳定性`
        );
        break;
      case 'gps':
        assertOptionalFinite(
          [
            sensor.config.ephTimeConstant,
            sensor.config.epvTimeConstant,
            sensor.config.ephInitial,
            sensor.config.epvInitial,
            sensor.config.ephFinal,
            sensor.config.epvFinal
          ],
          `${sensor.id} GPS 参数`
        );
        break;
      case 'barometer':
        assertOptionalFinite(
          [
            sensor.config.qnh,
            sensor.config.pressureFactorSigma,
            sensor.config.pressureFactorTau,
            sensor.config.uncorrelatedNoiseSigma,
            sensor.config.updateLatency,
            sensor.config.updateFrequency,
            sensor.config.startupDelay
          ],
          `${sensor.id} 气压计参数`
        );
        break;
      case 'magnetometer':
        if (sensor.config.noiseSigma) {
          assertVector(sensor.config.noiseSigma, `${sensor.id} 磁力计噪声`);
        }
        if (sensor.config.noiseBias) {
          assertVector(sensor.config.noiseBias, `${sensor.id} 磁力计偏置`);
        }
        assertOptionalFinite(
          [sensor.config.scaleFactor],
          `${sensor.id} 磁力计比例因子`
        );
        break;
      case 'airspeed':
        assertVector(sensor.config.forwardXyz, `${sensor.id} 前向轴`);
        assertOptionalFinite(
          [
            sensor.config.pressureFactorSigma,
            sensor.config.pressureFactorTau,
            sensor.config.uncorrelatedNoiseSigma
          ],
          `${sensor.id} 空速计参数`
        );
        break;
      case 'lidar':
        if (!LIDAR_TYPES.has(sensor.config.lidarType)) {
          throw new Error(
            `激光雷达 ${sensor.id} 使用了不支持的类型：${sensor.config.lidarType}`
          );
        }
        assertIntegerRange(
          sensor.config.numberOfChannels,
          1,
          256,
          `${sensor.id} 通道数`
        );
        assertPositive(sensor.config.range, `${sensor.id} 量程`);
        assertIntegerRange(
          sensor.config.pointsPerSecond,
          1,
          10000000,
          `${sensor.id} 点频`
        );
        assertNonNegative(
          sensor.config.reportFrequency,
          `${sensor.id} 上报频率`
        );
        assertRange(
          sensor.config.horizontalFovStartDeg,
          -360,
          360,
          `${sensor.id} 水平 FOV 起点`
        );
        assertRange(
          sensor.config.horizontalFovEndDeg,
          -360,
          360,
          `${sensor.id} 水平 FOV 终点`
        );
        assertRange(
          sensor.config.verticalFovLowerDeg,
          -90,
          90,
          `${sensor.id} 垂直 FOV 下限`
        );
        assertRange(
          sensor.config.verticalFovUpperDeg,
          -90,
          90,
          `${sensor.id} 垂直 FOV 上限`
        );
        if (
          sensor.config.verticalFovLowerDeg >= sensor.config.verticalFovUpperDeg
        ) {
          throw new Error(
            `激光雷达 ${sensor.id} 的垂直 FOV 上限必须大于下限。`
          );
        }
        break;
      case 'distance-sensor':
        assertNonNegative(sensor.config.minDistance, `${sensor.id} 最小距离`);
        assertPositive(sensor.config.maxDistance, `${sensor.id} 最大距离`);
        if (sensor.config.minDistance >= sensor.config.maxDistance) {
          throw new Error(
            `距离传感器 ${sensor.id} 的最大距离必须大于最小距离。`
          );
        }
        break;
      case 'radar':
        assertPositive(sensor.config.rangeMax, `${sensor.id} 最大距离`);
        assertPositive(
          sensor.config.rangeResolution,
          `${sensor.id} 距离分辨率`
        );
        assertPositive(sensor.config.velocityMax, `${sensor.id} 最大速度`);
        assertPositive(
          sensor.config.velocityResolution,
          `${sensor.id} 速度分辨率`
        );
        assertPositive(
          sensor.config.detectionInterval,
          `${sensor.id} 检测周期`
        );
        assertPositive(sensor.config.trackInterval, `${sensor.id} 跟踪周期`);
        assertPositive(
          sensor.config.rcsAdjustFactor,
          `${sensor.id} RCS 调整系数`
        );
        assertPositive(
          sensor.config.fov.azimuthResolution,
          `${sensor.id} 方位角分辨率`
        );
        assertPositive(
          sensor.config.fov.elevationResolution,
          `${sensor.id} 俯仰角分辨率`
        );
        if (sensor.config.fov.azimuthMin >= sensor.config.fov.azimuthMax) {
          throw new Error(`雷达 ${sensor.id} 的方位角上限必须大于下限。`);
        }
        if (sensor.config.fov.elevationMin >= sensor.config.fov.elevationMax) {
          throw new Error(`雷达 ${sensor.id} 的俯仰角上限必须大于下限。`);
        }
        break;
      case 'battery':
        assertPositive(sensor.config.totalCapacity, `${sensor.id} 总容量`);
        assertRange(
          sensor.config.capacityOnStart,
          0,
          sensor.config.totalCapacity,
          `${sensor.id} 初始容量`
        );
        if (sensor.config.mode === 'simple-discharge-mode') {
          assertNonNegative(
            Number(sensor.config.drainRateOnStart),
            `${sensor.id} 初始放电速率`
          );
        } else {
          assertPositive(
            Number(sensor.config.rotorPowerCoefficient),
            `${sensor.id} 旋翼功率系数`
          );
        }
        break;
      default:
        break;
    }
  }
}

function normalizeSensor(value: unknown): ManagedSensorDefinition {
  const item = asRecord(value);
  const type = String(item.type || '') as ManagedSensorType;
  if (!SENSOR_TYPES.has(type)) {
    throw new Error(`不支持的传感器类型：${String(item.type || '')}`);
  }
  const preset = createProjectAirsimSensorPreset(type);
  const config = asRecord(item.config);
  const incomingMount = asRecord(item.mount);
  const common = {
    id: String(item.id || preset.id).trim(),
    name: String(item.name || sensorTypeLabel(type)).trim(),
    enabled: item.enabled !== false,
    mount: {
      parentLink: String(
        incomingMount.parentLink || preset.mount.parentLink || 'Frame'
      ).trim(),
      xyz: vector(incomingMount.xyz, preset.mount.xyz),
      rpyDeg: vector(incomingMount.rpyDeg, preset.mount.rpyDeg)
    }
  };

  switch (type) {
    case 'camera': {
      const defaults = preset.type === type ? preset.config : undefined;
      const imageTypes = Array.isArray(config.imageTypes)
        ? Array.from(
            new Set(
              config.imageTypes.map(value => Number(value) as CameraCaptureType)
            )
          ).sort((left, right) => left - right)
        : defaults?.imageTypes || [0];
      const incomingGimbal = asRecord(config.gimbal);
      return {
        ...common,
        type,
        config: {
          captureInterval: number(
            config.captureInterval,
            defaults?.captureInterval || 0.03
          ),
          imageTypes,
          width: number(config.width, defaults?.width || 1280),
          height: number(config.height, defaults?.height || 720),
          fovDegrees: number(config.fovDegrees, defaults?.fovDegrees || 90),
          streamingEnabled: boolean(
            config.streamingEnabled,
            defaults?.streamingEnabled || false
          ),
          compress: boolean(config.compress, defaults?.compress || false),
          targetGamma: number(config.targetGamma, defaults?.targetGamma || 2.5),
          gimbal: {
            lockRoll: boolean(
              incomingGimbal.lockRoll,
              defaults?.gimbal?.lockRoll || false
            ),
            lockPitch: boolean(
              incomingGimbal.lockPitch,
              defaults?.gimbal?.lockPitch || false
            ),
            lockYaw: boolean(
              incomingGimbal.lockYaw,
              defaults?.gimbal?.lockYaw || false
            )
          }
        }
      };
    }
    case 'imu': {
      const defaults = preset.type === type ? preset.config : undefined;
      const accelerometer = asRecord(config.accelerometer);
      const gyroscope = asRecord(config.gyroscope);
      return {
        ...common,
        type,
        config: {
          accelerometer: {
            randomWalk: number(
              accelerometer.randomWalk,
              defaults?.accelerometer.randomWalk || 0
            ),
            tau: number(accelerometer.tau, defaults?.accelerometer.tau || 800),
            biasStability: number(
              accelerometer.biasStability,
              defaults?.accelerometer.biasStability || 0
            ),
            turnOnBias: vector(
              accelerometer.turnOnBias,
              defaults?.accelerometer.turnOnBias || [0, 0, 0]
            )
          },
          gyroscope: {
            randomWalk: number(
              gyroscope.randomWalk,
              defaults?.gyroscope.randomWalk || 0
            ),
            tau: number(gyroscope.tau, defaults?.gyroscope.tau || 500),
            biasStability: number(
              gyroscope.biasStability,
              defaults?.gyroscope.biasStability || 0
            ),
            turnOnBias: vector(
              gyroscope.turnOnBias,
              defaults?.gyroscope.turnOnBias || [0, 0, 0]
            )
          }
        }
      };
    }
    case 'gps':
      return {
        ...common,
        type,
        config: {
          ephTimeConstant: optionalNumber(config.ephTimeConstant),
          epvTimeConstant: optionalNumber(config.epvTimeConstant),
          ephInitial: optionalNumber(config.ephInitial),
          epvInitial: optionalNumber(config.epvInitial),
          ephFinal: optionalNumber(config.ephFinal),
          epvFinal: optionalNumber(config.epvFinal)
        }
      };
    case 'barometer':
      return {
        ...common,
        type,
        config: {
          qnh: optionalNumber(config.qnh),
          pressureFactorSigma: optionalNumber(config.pressureFactorSigma),
          pressureFactorTau: optionalNumber(config.pressureFactorTau),
          uncorrelatedNoiseSigma: optionalNumber(config.uncorrelatedNoiseSigma),
          updateLatency: optionalNumber(config.updateLatency),
          updateFrequency: optionalNumber(config.updateFrequency),
          startupDelay: optionalNumber(config.startupDelay)
        }
      };
    case 'magnetometer':
      return {
        ...common,
        type,
        config: {
          scaleFactor: optionalNumber(config.scaleFactor),
          noiseSigma: optionalVector(config.noiseSigma),
          noiseBias: optionalVector(config.noiseBias)
        }
      };
    case 'airspeed':
      return {
        ...common,
        type,
        config: {
          forwardXyz: vector(config.forwardXyz, [1, 0, 0]),
          pressureFactorSigma: optionalNumber(config.pressureFactorSigma),
          pressureFactorTau: optionalNumber(config.pressureFactorTau),
          uncorrelatedNoiseSigma: optionalNumber(config.uncorrelatedNoiseSigma)
        }
      };
    case 'lidar': {
      const defaults = preset.type === type ? preset.config : undefined;
      return {
        ...common,
        type,
        config: {
          lidarType: String(
            config.lidarType || defaults?.lidarType || 'generic_cylindrical'
          ) as ProjectAirsimLidarType,
          numberOfChannels: number(
            config.numberOfChannels,
            defaults?.numberOfChannels || 16
          ),
          range: number(config.range, defaults?.range || 100),
          pointsPerSecond: number(
            config.pointsPerSecond,
            defaults?.pointsPerSecond || 100000
          ),
          reportFrequency: number(
            config.reportFrequency,
            defaults?.reportFrequency || 0
          ),
          horizontalRotationFrequency: number(
            config.horizontalRotationFrequency,
            defaults?.horizontalRotationFrequency || 10
          ),
          horizontalFovStartDeg: number(
            config.horizontalFovStartDeg,
            defaults?.horizontalFovStartDeg || 0
          ),
          horizontalFovEndDeg: number(
            config.horizontalFovEndDeg,
            defaults?.horizontalFovEndDeg || 360
          ),
          verticalRotationFrequency: number(
            config.verticalRotationFrequency,
            defaults?.verticalRotationFrequency || 0
          ),
          verticalFovLowerDeg: number(
            config.verticalFovLowerDeg,
            defaults?.verticalFovLowerDeg || -45
          ),
          verticalFovUpperDeg: number(
            config.verticalFovUpperDeg,
            defaults?.verticalFovUpperDeg || -15
          ),
          disableSelfHits: boolean(
            config.disableSelfHits,
            defaults?.disableSelfHits || false
          ),
          drawDebugPoints: boolean(
            config.drawDebugPoints,
            defaults?.drawDebugPoints || false
          )
        }
      };
    }
    case 'distance-sensor': {
      const defaults = preset.type === type ? preset.config : undefined;
      return {
        ...common,
        type,
        config: {
          minDistance: number(config.minDistance, defaults?.minDistance || 0.5),
          maxDistance: number(config.maxDistance, defaults?.maxDistance || 50),
          drawDebugPoints: boolean(
            config.drawDebugPoints,
            defaults?.drawDebugPoints || false
          )
        }
      };
    }
    case 'radar': {
      const defaults = preset.type === type ? preset.config : undefined;
      const fov = asRecord(config.fov);
      return {
        ...common,
        type,
        config: {
          rangeMax: number(config.rangeMax, defaults?.rangeMax || 500),
          rangeResolution: number(
            config.rangeResolution,
            defaults?.rangeResolution || 1
          ),
          velocityMax: number(config.velocityMax, defaults?.velocityMax || 100),
          velocityResolution: number(
            config.velocityResolution,
            defaults?.velocityResolution || 1
          ),
          detectionInterval: number(
            config.detectionInterval,
            defaults?.detectionInterval || 0.02
          ),
          trackInterval: number(
            config.trackInterval,
            defaults?.trackInterval || 0.2
          ),
          rcsAdjustFactor: number(
            config.rcsAdjustFactor,
            defaults?.rcsAdjustFactor || 0.1
          ),
          drawDebugPoints: boolean(
            config.drawDebugPoints,
            defaults?.drawDebugPoints || false
          ),
          fov: {
            azimuthMin: number(
              fov.azimuthMin,
              defaults?.fov.azimuthMin || -0.785
            ),
            azimuthMax: number(
              fov.azimuthMax,
              defaults?.fov.azimuthMax || 0.785
            ),
            elevationMin: number(
              fov.elevationMin,
              defaults?.fov.elevationMin || -0.524
            ),
            elevationMax: number(
              fov.elevationMax,
              defaults?.fov.elevationMax || 0.524
            ),
            azimuthResolution: number(
              fov.azimuthResolution,
              defaults?.fov.azimuthResolution || 0.0175
            ),
            elevationResolution: number(
              fov.elevationResolution,
              defaults?.fov.elevationResolution || 0.0175
            )
          }
        }
      };
    }
    case 'battery': {
      const defaults = preset.type === type ? preset.config : undefined;
      const mode =
        config.mode === 'simple-discharge-mode'
          ? 'simple-discharge-mode'
          : 'rotor-power-discharge-mode';
      return {
        ...common,
        type,
        config: {
          mode,
          totalCapacity: number(
            config.totalCapacity,
            defaults?.totalCapacity || 36000
          ),
          capacityOnStart: number(
            config.capacityOnStart,
            defaults?.capacityOnStart || 30000
          ),
          drainRateOnStart: number(config.drainRateOnStart, 1),
          rotorPowerCoefficient: number(config.rotorPowerCoefficient, 1)
        }
      };
    }
  }
}

function defaultId(type: ManagedSensorType): string {
  switch (type) {
    case 'camera':
      return 'Camera';
    case 'imu':
      return 'IMU';
    case 'gps':
      return 'GPS';
    case 'barometer':
      return 'Barometer';
    case 'magnetometer':
      return 'Magnetometer';
    case 'airspeed':
      return 'Airspeed';
    case 'lidar':
      return 'Lidar';
    case 'distance-sensor':
      return 'DistanceSensor';
    case 'radar':
      return 'Radar';
    case 'battery':
      return 'Battery';
  }
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, any>)
    : {};
}

function number(value: unknown, fallback: number): number {
  return value === '' || value == null ? fallback : Number(value);
}

function optionalNumber(value: unknown): number | undefined {
  return value === '' || value == null ? undefined : Number(value);
}

function boolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function vector(value: unknown, fallback: SensorVector3): SensorVector3 {
  if (!Array.isArray(value) || value.length !== 3) {
    return cloneSensor(fallback);
  }
  return [Number(value[0]), Number(value[1]), Number(value[2])];
}

function optionalVector(value: unknown): SensorVector3 | undefined {
  return value == null ? undefined : vector(value, [NaN, NaN, NaN]);
}

function assertVector(value: SensorVector3, label: string): void {
  if (
    !Array.isArray(value) ||
    value.length !== 3 ||
    value.some(item => !Number.isFinite(Number(item)))
  ) {
    throw new Error(`${label}必须包含三个有效数字。`);
  }
}

function assertPositive(value: number, label: string): void {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${label}必须大于 0。`);
  }
}

function assertNonNegative(value: number, label: string): void {
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${label}不能小于 0。`);
  }
}

function assertOptionalFinite(
  values: Array<number | undefined>,
  label: string
): void {
  if (values.some(value => value != null && !Number.isFinite(value))) {
    throw new Error(`${label}必须使用有效数字。`);
  }
}

function assertRange(
  value: number,
  minimum: number,
  maximum: number,
  label: string
): void {
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${label}必须在 ${minimum} 到 ${maximum} 之间。`);
  }
}

function assertIntegerRange(
  value: number,
  minimum: number,
  maximum: number,
  label: string
): void {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label}必须是 ${minimum} 到 ${maximum} 之间的整数。`);
  }
}
