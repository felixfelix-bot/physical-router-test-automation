import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'feed-per-arch-e2e.spec.mjs',
  retries: 0,
  timeout: 600000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    channel: 'chrome',
    screenshot: 'off',
    video: 'off',
    trace: 'off',
    ignoreHTTPSErrors: true,
    actionTimeout: 60000,
    navigationTimeout: 60000,
  },
  outputDir: '../../test-results/feed-per-arch-e2e',
});
