/**
 * Generic 7-step wizard flow — E2E contract spec.
 *
 * Drives the operator-agnostic wizard:
 *
 *   Discover -> Connect -> Uplink -> Payouts -> Advanced (optional) -> Deploy -> Live
 *
 * The step list, its order and its optionality come from ./wizard-flow.mjs —
 * they are DATA. This file contains no hardcoded step array.
 *
 * Every case is independently runnable via --grep on its title. The two
 * Advanced cases use these exact filters:
 *
 *   --grep "advanced SKIP path"
 *   --grep "advanced VISITED path"
 *
 * Determinism rules enforced here:
 *   - no page.waitForTimeout / sleeps; no wall-clock arithmetic
 *   - every wait is an explicit condition on UI state (expect().toBeVisible,
 *     expect(locator).toHaveCount, locator.waitFor)
 *   - selectors are getByTestId / getByRole first; no CSS descendant chains
 *     (the only CSS used is `[data-testid=...]`, which IS the testid hook)
 *   - the backend HTTP API is intercepted, so no router, no LAN, and no
 *     network latency participates in the result. Cases cannot leak state
 *     because each Playwright test gets a fresh context + fresh page and the
 *     API doubles are installed per test in beforeEach.
 *
 * Run:  npx playwright test --config=generic-wizard-7step.config.mjs
 */
import { test, expect } from '@playwright/test';

import {
	WIZARD_STEPS,
	STEP_COUNT,
	STEP_ORDER,
	STEP_TITLES,
	OPTIONAL_STEP,
	TERMINAL_STEP,
	LIVE_EVIDENCE,
	stepSelector,
} from './wizard-flow.mjs';

const WIZARD_URL = process.env.WIZARD_URL || 'http://127.0.0.1:8099';

/** Test-only router identity used by the stubbed discovery response. */
const FIXTURE_ROUTER = Object.freeze({
	ip: '192.0.2.1',
	vendor: 'TestVendor',
	model: 'FixtureRouter',
});

/** Test-only payout address. */
const FIXTURE_LNURL = 'operator@wallet.example';

/** Test-only deploy job id used by the stubbed deploy/status responses. */
const FIXTURE_JOB_ID = 'red-fixture-job';

/**
 * Stub the wizard's backend HTTP API so the spec measures the UI contract
 * only. Without this, discovery would scan the real LAN and the run would
 * fail for a network reason instead of the wizard-behaviour reason under test.
 * @param {import('@playwright/test').Page} page
 */
async function installApiDoubles(page) {
	await page.route('**/api/scan', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ routers: [FIXTURE_ROUTER] }),
		}),
	);

	await page.route('**/api/wifi-scan', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ ssids: [{ name: 'FixtureUplink', encryption: 'wpa2' }] }),
		}),
	);

	await page.route('**/api/deploy', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ job_id: FIXTURE_JOB_ID }),
		}),
	);

	// A completed deployment: every reported step done, job status done.
	await page.route(`**/api/status/${FIXTURE_JOB_ID}`, (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				ip: FIXTURE_ROUTER.ip,
				status: 'done',
				step: 11,
				steps: [
					{ name: 'verify', desc: 'Verifying SSH access to router...', status: 'done' },
					{ name: 'firmware', desc: 'Checking firmware version...', status: 'done' },
					{ name: 'password', desc: 'Setting root password...', status: 'done' },
					{ name: 'upstream', desc: 'Configuring upstream connection...', status: 'done' },
					{ name: 'install', desc: 'Installing net4sats package', status: 'done' },
					{ name: 'brand', desc: 'Branding captive portal', status: 'done' },
					{ name: 'portal', desc: 'Deploying captive portal...', status: 'done' },
					{ name: 'admin', desc: 'Installing admin panel...', status: 'done' },
					{ name: 'lnurl', desc: 'Configuring Lightning address...', status: 'done' },
					{ name: 'services', desc: 'Restarting services...', status: 'done' },
					{ name: 'health', desc: 'Running health check...', status: 'done' },
				],
				log: [],
			}),
		}),
	);
}

/**
 * Fresh-state reset + navigation. Called in beforeEach so no case can inherit
 * DOM, storage or routing state from a previous case.
 * @param {import('@playwright/test').Page} page
 */
async function reset(page) {
	await installApiDoubles(page);
	await page.goto(WIZARD_URL, { waitUntil: 'domcontentloaded' });
	// Explicit wait on UI state: the wizard root must be interactive.
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

/** The step container for a declared step, by testid. */
function stepLocator(page, step) {
	return page.locator(stepSelector(step));
}

/** Forward-navigation control for the current step. */
function nextButton(page) {
	return page.getByRole('button', { name: /next|continue/i });
}

/**
 * The declared step whose container is currently visible. Resolves on UI state
 * (waits until exactly one declared step container is visible), never on a
 * timer.
 * @returns {Promise<string>} the step id
 */
async function currentStepId(page) {
	const visible = [];
	for (const step of WIZARD_STEPS) {
		if (await stepLocator(page, step).isVisible().catch(() => false)) {
			visible.push(step.id);
		}
	}
	if (visible.length !== 1) {
		throw new Error(
			`expected exactly one declared step container visible, found ${visible.length} ` +
				`(${visible.join(', ') || 'none'}) — the wizard declares ${STEP_COUNT} steps: ` +
				STEP_ORDER.join(' -> '),
		);
	}
	return visible[0];
}

/**
 * Fill the minimum fields a non-optional step requires, using role/label
 * selectors only. Kept per-step so a step that gains a new requirement shows
 * up as a spec-side conversation, not a silent pass.
 * @param {import('@playwright/test').Page} page
 * @param {string} stepId
 */
async function satisfyStep(page, stepId) {
	switch (stepId) {
		case 'discover':
			// Discover resolves on its own once the (stubbed) scan returns.
			await expect(page.getByRole('option').first()).toBeVisible();
			return;
		case 'connect': {
			const router = page.getByRole('combobox', { name: /router/i });
			await router.selectOption({ index: 0 });
			return;
		}
		case 'uplink': {
			const wan = page.getByRole('radio', { name: /wan|ethernet/i });
			await wan.check();
			return;
		}
		case 'payouts': {
			const ln = page.getByRole('textbox', { name: /lightning/i });
			await ln.fill(FIXTURE_LNURL);
			return;
		}
		case 'advanced':
			// Optional — nothing is required to advance.
			return;
		default:
			throw new Error(`satisfyStep: no requirements declared for step "${stepId}"`);
	}
}

/**
 * Advance through steps until `targetId` is the current step, satisfying each
 * step's requirements on the way. Bounded by the declared step count, so a
 * wizard that never advances fails the test instead of looping.
 * @param {import('@playwright/test').Page} page
 * @param {string} targetId
 */
async function walkTo(page, targetId) {
	const target = WIZARD_STEPS.find((s) => s.id === targetId);
	if (!target) throw new Error(`walkTo: "${targetId}" is not a declared step`);

	for (let guard = 0; guard <= STEP_COUNT; guard++) {
		if ((await currentStepId(page)) === targetId) return;
		await satisfyStep(page, await currentStepId(page));
		const next = nextButton(page);
		await expect(next).toBeEnabled();
		await next.click();
	}
	throw new Error(
		`walkTo("${targetId}") did not reach the step within ${STEP_COUNT} advances — ` +
			`declared order is ${STEP_ORDER.join(' -> ')}`,
	);
}

test.describe('generic 7-step wizard flow', () => {
	test.beforeEach(async ({ page }) => {
		await reset(page);
	});

	test.afterEach(async ({ page }) => {
		// Cleanup: drop any interception and return to a blank document so no
		// in-flight poll or DOM state can bleed into the next case.
		await page.unrouteAll({ behavior: 'ignoreErrors' });
		await page.goto('about:blank');
	});

	// ---------------------------------------------------------------------
	// (a) all 7 steps appear in the declared order
	// ---------------------------------------------------------------------
	test('step order: all 7 steps appear in the declared order', async ({ page }) => {
		expect(STEP_COUNT, 'the flow definition must declare exactly 7 steps').toBe(7);
		expect(STEP_ORDER).toEqual([
			'discover',
			'connect',
			'uplink',
			'payouts',
			'advanced',
			'deploy',
			'live',
		]);

		// Every declared step must exist in the DOM as a testid-stable node.
		for (const step of WIZARD_STEPS) {
			await expect(
				stepLocator(page, step),
				`step "${step.title}" is missing: expected a container with ${stepSelector(step)}`,
			).toBeAttached();
		}

		// DOM order must match declared order — compare the sequence of testids
		// actually present, not just their existence.
		const renderedOrder = await page
			.locator('[data-testid^="step-"]')
			.evaluateAll((nodes) => nodes.map((n) => n.getAttribute('data-testid')));
		expect(renderedOrder).toEqual(WIZARD_STEPS.map((s) => s.testId));

		// And the visible titles, read through the accessibility tree.
		const headings = await page.getByRole('heading', { level: 2 }).allInnerTexts();
		expect(headings.map((h) => h.trim())).toEqual([...STEP_TITLES]);
	});

	// ---------------------------------------------------------------------
	// (b) Advanced SKIP path  — filter: --grep "advanced SKIP path"
	// ---------------------------------------------------------------------
	test('advanced SKIP path: skipping Advanced still reaches Deploy', async ({ page }) => {
		if (!OPTIONAL_STEP) throw new Error('flow definition declares no optional step to skip');

		await walkTo(page, OPTIONAL_STEP.id);

		// The optional step is present but must not gate forward navigation.
		await expect(stepLocator(page, OPTIONAL_STEP)).toBeVisible();
		const skip = page.getByRole('button', { name: /skip/i });
		await expect(
			skip,
			'the optional step must offer an explicit Skip control',
		).toBeVisible();

		await skip.click();

		// After skipping, the next declared step is current.
		const advancedIndex = WIZARD_STEPS.indexOf(OPTIONAL_STEP);
		const nextStep = WIZARD_STEPS[advancedIndex + 1];
		await expect(stepLocator(page, nextStep)).toBeVisible();
		expect(await currentStepId(page)).toBe(nextStep.id);

		// Skipping must not have applied any advanced values.
		await expect(
			page.getByTestId('advanced-summary'),
			'a skipped Advanced step must be reported as skipped, not configured',
		).toHaveAttribute('data-status', 'skipped');
	});

	// ---------------------------------------------------------------------
	// (c) Advanced VISITED path — filter: --grep "advanced VISITED path"
	// ---------------------------------------------------------------------
	test('advanced VISITED path: visiting Advanced applies and reports its values', async ({ page }) => {
		if (!OPTIONAL_STEP) throw new Error('flow definition declares no optional step to visit');

		await walkTo(page, OPTIONAL_STEP.id);
		await expect(stepLocator(page, OPTIONAL_STEP)).toBeVisible();

		const next = nextButton(page);
		await expect(next).toBeEnabled();

		// Visit: change an advanced value, then advance normally.
		const devSplit = page.getByRole('slider', { name: /payout share/i });
		await devSplit.fill('10');
		await expect(page.getByTestId('devsplit-value')).toHaveText('10%');

		await next.click();

		const advancedIndex = WIZARD_STEPS.indexOf(OPTIONAL_STEP);
		const nextStep = WIZARD_STEPS[advancedIndex + 1];
		await expect(stepLocator(page, nextStep)).toBeVisible();

		// Visiting must be reported distinctly from skipping, with the value kept.
		await expect(page.getByTestId('advanced-summary')).toHaveAttribute('data-status', 'configured');
		await expect(page.getByTestId('advanced-summary')).toContainText('10%');
	});

	// ---------------------------------------------------------------------
	// (d) forward-navigation gating
	// ---------------------------------------------------------------------
	test('gating: cannot advance past a step while its requirements are unmet', async ({ page }) => {
		// Discover must satisfy on its own, then Connect's requirement (a
		// selected router) must gate the forward control.
		await walkTo(page, 'connect');
		await expect(stepLocator(page, 'connect')).toBeVisible();

		// Requirement unmet: Next must be disabled, and clicking must not move.
		await expect(
			nextButton(page),
			'Next must be disabled on Connect until a router is selected',
		).toBeDisabled();
		expect(await currentStepId(page)).toBe('connect');

		// A disabled control cannot be activated — the step must not change.
		await nextButton(page).click({ force: true }).catch(() => {});
		expect(await currentStepId(page), 'a disabled Next must not advance the flow').toBe('connect');

		// Requirement met: Next enables and advances exactly one declared step.
		await satisfyStep(page, 'connect');
		await expect(nextButton(page)).toBeEnabled();
		await nextButton(page).click();
		await expect(stepLocator(page, 'uplink')).toBeVisible();
		expect(await currentStepId(page)).toBe('uplink');

		// Payouts gates on a valid Lightning address specifically.
		await walkTo(page, 'payouts');
		const ln = page.getByRole('textbox', { name: /lightning/i });
		await ln.fill('not-a-lightning-address');
		await expect(
			nextButton(page),
			'Next must stay disabled on Payouts for a malformed Lightning address',
		).toBeDisabled();
		await ln.fill(FIXTURE_LNURL);
		await expect(nextButton(page)).toBeEnabled();
	});

	// ---------------------------------------------------------------------
	// (e) terminal Live state + post-deploy evidence
	// ---------------------------------------------------------------------
	test('live: terminal Live step reached with post-deploy evidence', async ({ page }) => {
		await walkTo(page, 'deploy');
		await expect(stepLocator(page, 'deploy')).toBeVisible();

		// Deploy step exposes progress for the steps the backend reports.
		const deployNext = page.getByRole('button', { name: /deploy|start/i });
		await deployNext.click();

		// Terminal state must be reached by UI state, not by elapsed time: the
		// Live container appearing IS the completion signal.
		await expect(
			stepLocator(page, TERMINAL_STEP),
			`the flow must terminate on "${TERMINAL_STEP.title}"`,
		).toBeVisible({ timeout: 30_000 });
		expect(await currentStepId(page)).toBe(TERMINAL_STEP.id);
		expect(TERMINAL_STEP.optional).toBe(false);

		// Post-deploy evidence must be present on the terminal step.
		for (const item of LIVE_EVIDENCE) {
			await expect(
				page.getByTestId(item),
				`Live must present post-deploy evidence "${item}"`,
			).toBeVisible();
		}

		// The terminal step is terminal: no forward control remains.
		await expect(
			page.getByRole('button', { name: /next|continue/i }),
			'Live is terminal — no forward control may remain',
		).toHaveCount(0);
	});
});
