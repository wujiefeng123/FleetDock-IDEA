import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('Web 视频覆盖层样式', () => {
  it('在线状态设置 hidden 后不会继续显示等待提示', () => {
    const css = readFileSync(new URL('./style.css', import.meta.url), 'utf8');

    expect(css).toMatch(/\.video-copy\[hidden\]\s*\{[^}]*display:\s*none\s*;/);
  });
});
