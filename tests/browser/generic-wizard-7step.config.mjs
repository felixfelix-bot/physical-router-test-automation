/**
 * Playwright config for the generic 7-step wizard flow — video recording
 * enabled for the full walkthrough (Discover → Live). Adapted from
 * endo-onboarding.config.mjs (upstream f095444).
 */
import { defineConfig } from '@playwright/test';

const viewport = process.env.TOLLGATE_VIEWPORT || 'desktop';
const viewports = {
	desktop: { width: 1280, height: 900 },
	mobile: { width: 375, height: 812 },
};

export default defineConfig({
	testDir: '.',
	testMatch: 'generic-wizard-7step.spec.mjs',
	retries: 0,
	timeout: 240000, // 4 min — deploy step can take a while
	workers: 1,
	reporter: [
		['html', { outputFolder: 'generic-wizard-report', open: 'never' }],
		['list'],
	],
	use: {
		baseURL: 'http://localhost:8099',
		headless: true,
		channel: 'chrome',
		viewport: viewports[viewport] || viewports.desktop,
		screenshot: 'on',
		trace: 'on',
		video: 'on',
		actionTimeout: 30000,
	},
	projects: [
		{
			name: `${viewport}-generic-wizard-7step`,
			use: { viewport: viewports[viewport] || viewports.desktop },
		},
	],
});
