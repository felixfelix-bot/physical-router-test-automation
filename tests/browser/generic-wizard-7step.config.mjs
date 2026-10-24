/**
 * Playwright config for the generic 7-step wizard flow.
 *
 * Deterministic on purpose: no video (this is a contract spec, not a demo
 * recording — the WZ1.8 hardware/video gate is a separate task), retries off
 * so a flake cannot masquerade as a red result, single worker so cases cannot
 * interleave, and `channel: 'chromium'` so the browser is the Playwright-
 * managed bundle on this host rather than a system Chrome that may not exist.
 *
 * Structure adapted from endo-onboarding.config.mjs (upstream f095444).
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'generic-wizard-7step.spec.mjs',
	retries: 0,
	timeout: 60_000,
	workers: 1,
	reporter: [['list']],
	use: {
		baseURL: process.env.WIZARD_URL || 'http://127.0.0.1:8099',
		headless: true,
		channel: 'chromium',
		viewport: { width: 1280, height: 900 },
		actionTimeout: 10_000,
		screenshot: 'only-on-failure',
		trace: 'off',
		video: 'off',
	},
	projects: [
		{
			name: 'generic-wizard-7step',
			use: {},
		},
	],
});
