/**
 * Playwright config: TollGate ADMIN CONFIG SPA — live end-to-end drive.
 * Drives the deployed admin SPA on the beta GL-MT3000 lab router (192.168.8.1:8090).
 *
 * Run: TOLLGATE_ADMIN_URL=http://192.168.8.1:8090/ TOLLGATE_ADMIN_PASSWORD=test123 \
 *        npx playwright test --config=tests/browser/admin-spa-live.config.mjs
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'admin-spa-live.spec.mjs',
	retries: 0,
	timeout: 120000,
	workers: 1, // single serial drive of the SPA; do not parallelize against the router daemon
	reporter: [
		['list'],
		['json', { outputFile: 'admin-spa-live-report/report.json' }],
	],
	use: {
		headless: true,
		// Playwright 1.60 does NOT support `chromium` on this host (ubuntu26.04-x64).
		// Use the system google-chrome via channel:'chrome'.
		channel: 'chrome',
		viewport: { width: 1280, height: 900 },
		screenshot: 'on',
		trace: 'on-first-retry',
		actionTimeout: 20000,
	},
	outputDir: 'admin-spa-live-report/output',
});
