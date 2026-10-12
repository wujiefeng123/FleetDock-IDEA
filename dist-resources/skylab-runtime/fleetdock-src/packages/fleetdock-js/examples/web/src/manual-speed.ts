export type ManualSpeedAxis = 'horizontal' | 'vertical';
export type SpeedDirection = -1 | 1;

export const MANUAL_SPEED_LIMITS = {
  horizontal: { minimum: 0.5, maximum: 20, step: 0.5, initial: 3 },
  vertical: { minimum: 0.1, maximum: 2, step: 0.1, initial: 0.6 }
} as const;

export function adjustManualSpeed(
  axis: ManualSpeedAxis,
  current: number,
  direction: SpeedDirection
): number {
  const limits = MANUAL_SPEED_LIMITS[axis];
  const adjusted = current + limits.step * direction;
  const clamped = Math.min(limits.maximum, Math.max(limits.minimum, adjusted));
  return Math.round(clamped * 10) / 10;
}

export function formatManualSpeed(value: number): string {
  return `${value.toFixed(1)} m/s`;
}
