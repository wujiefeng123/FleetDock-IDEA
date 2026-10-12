import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './test/e2e',
  timeout: 60000,
  retries: process.env.CI ? 2 : 0,
  // Electron E2E instances share one bundled Python environment. Serializing
  // them prevents concurrent Jupyter server discovery and runtime-file races.
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    trace: 'on-first-retry',
    video: 'on-first-retry'
  },
  projects: [
    {
      name: 'electron',
      testMatch: '**/*.test.ts'
    }
  ],
  outputDir: 'test-results/'
});
