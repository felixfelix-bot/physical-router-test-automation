import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'pre13-verify.spec.mjs',
	retries: 0,
	timeout: 90 * 1000,
	workers: 1,
	reporter: [['list'], ['html', { outputFolder: 'pre13-verify-report', open: 'never' }]],
	use: {
		headless: true,
		channel: 'chrome',
		viewport: { width: 1280, height: 900 },
		screenshot: 'on',
		trace: 'retain-on-failure',
		video: 'off',
		ignoreHTTPSErrors: true,
		actionTimeout: 20000,
		navigationTimeout: 30000,
	},
});
