import { defineConfig } from 'vitest/config';

// [UAV-IDE MODIFY] Keep standalone SDK tests independent of the desktop test configuration.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['examples/web/src/**/*.test.ts']
  }
});
