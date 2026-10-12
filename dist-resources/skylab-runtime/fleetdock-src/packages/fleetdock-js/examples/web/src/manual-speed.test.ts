import { describe, expect, it } from 'vitest';
import {
  adjustManualSpeed,
  formatManualSpeed,
  MANUAL_SPEED_LIMITS
} from './manual-speed';

describe('Web 手动控制速度', () => {
  it('按各自步长调整并限制水平速度', () => {
    expect(adjustManualSpeed('horizontal', 3, 1)).toBe(3.5);
    expect(adjustManualSpeed('horizontal', 0.5, -1)).toBe(0.5);
    expect(adjustManualSpeed('horizontal', 20, 1)).toBe(20);
  });

  it('按 0.1 m/s 调整并限制升降速度', () => {
    expect(adjustManualSpeed('vertical', 0.6, 1)).toBe(0.7);
    expect(adjustManualSpeed('vertical', 0.1, -1)).toBe(0.1);
    expect(adjustManualSpeed('vertical', 2, 1)).toBe(2);
  });

  it('提供默认值和统一显示格式', () => {
    expect(MANUAL_SPEED_LIMITS.horizontal.initial).toBe(3);
    expect(MANUAL_SPEED_LIMITS.vertical.initial).toBe(0.6);
    expect(formatManualSpeed(3)).toBe('3.0 m/s');
  });
});
