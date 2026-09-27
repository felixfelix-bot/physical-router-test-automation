/**
 * Generic 7-step wizard flow — full walkthrough E2E with video.
 *
 * Adapted from endo-onboarding.spec.mjs (upstream f095444) into an
 * operator-agnostic, fully env-parameterized 7-step flow:
 *
 *   1. Discover  — wizard auto-detects routers on the network
 *   2. Connect   — select router, enter router password
 *   3. Uplink    — choose upstream mode (WAN ethernet default / STA repeater)
 *   4. Payouts   — enter Lightning address / LNURL
 *   5. Advanced  — optional: dev split, margin, Cashu mint (kept optional,
 *                  verified not to block deploy readiness)
 *   6. Deploy    — click Deploy, watch all deployment steps complete
 *   7. Live      — verify success view + TollGate API & portal on the router
 *
 * Environment variables (all optional):
 *   WIZARD_URL       default http://localhost:8099
 *   ROUTER_IP        default 192.168.1.1          (must appear in scan results)
 *   ROUTER_PASSWORD  default ''                   (fresh-reset routers have none)
 *   LNURL            default generic@wallet.app   (payouts Lightning address)
 *   DEPLOY_MODE      'wan' (default) or 'sta'
 *   WIFI_SSID        STA mode: upstream WiFi SSID
 *   WIFI_PASSWORD    STA mode: upstream WiFi password
 *   DEV_SPLIT        advanced: 0-50 (default: leave wizard default 10)
 *   MARGIN           advanced: 0-100 (default: leave wizard default 0)
 *   STRICT_DEPLOY    '1' (default) fail on wizard error view;
 *                    '0' lenient like the endo spec (log + screenshot, pass)
 *   DEPLOY_TIMEOUT   ms to wait for deploy result (default 180000)
 *   TOLLGATE_PORT    default 2121
 *   PORTAL_PORT      default 2050
 *
 * Prerequisites:
 *  - net4sats-wizard binary serving WIZARD_URL
 *  - target router (GL-MT6000 at ROUTER_IP) reachable with the given password
 *
 * Video recording is enabled via generic-wizard-7step.config.mjs (video: 'on').
 * Gate 9 for the issuing task = the recorded video of a green run.
 */
import { test, expect } from '@playwright/test';

const WIZARD_URL = process.env.WIZARD_URL || 'http://localhost:8099';
const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
const ROUTER_PASSWORD = process.env.ROUTER_PASSWORD || '';
const LNURL = process.env.LNURL || 'generic@wallet.app';
const DEPLOY_MODE = (process.env.DEPLOY_MODE || 'wan').toLowerCase();
const WIFI_SSID = process.env.WIFI_SSID || '';
const WIFI_PASSWORD = process.env.WIFI_PASSWORD || '';
const DEV_SPLIT = process.env.DEV_SPLIT === undefined ? null : parseInt(process.env.DEV_SPLIT, 10);
const MARGIN = process.env.MARGIN === undefined ? null : parseInt(process.env.MARGIN, 10);
const STRICT_DEPLOY = (process.env.STRICT_DEPLOY || '1') !== '0';
const DEPLOY_TIMEOUT = parseInt(process.env.DEPLOY_TIMEOUT || '180000', 10);
const TOLLGATE_PORT = process.env.TOLLGATE_PORT || '2121';
const PORTAL_PORT = process.env.PORTAL_PORT || '2050';

const SHOT = (name) => `test-results/generic-wizard-7step/${name}.png`;

test.describe.configure({ mode: 'serial' });

/** Navigate to the wizard and wait for the Discover scan to finish
 *  (select view becomes visible once the scan completes). */
async function discoverAndWait(page) {
	await page.goto(WIZARD_URL);
	await page.waitForSelector('#scan-view', { timeout: 15000 });
	await page.waitForSelector('#select-view:not(.hidden)', { timeout: 45000 });
}

/** Connect: pick the target router by IP, falling back to the first found. */
async function selectRouter(page) {
	const select = page.locator('#router-select');
	const options = await select.locator('option').allTextContents();
	const target = options.find((o) => o.includes(ROUTER_IP));
	if (target) {
		await select.selectOption({ label: target });
	} else {
		// Generic fallback: first discovered router.
		await select.selectOption({ index: 0 });
	}
}

/** Steps 2–4 prefix: Connect (router + password), Uplink (mode), Payouts (LNURL). */
async function configureThroughPayouts(page) {
	await selectRouter(page);
	await page.fill('#password', ROUTER_PASSWORD);

	if (DEPLOY_MODE === 'sta') {
		await page.click('#mode-sta');
		await expect(page.locator('#sta-fields')).toBeVisible();
		if (WIFI_SSID) await page.fill('#ssid', WIFI_SSID);
		if (WIFI_PASSWORD) await page.fill('#wifi-pass', WIFI_PASSWORD);
	} else {
		await page.click('#mode-wan');
		await expect(page.locator('#sta-fields')).toBeHidden();
	}

	await page.fill('#lnurl', LNURL);
}

test('Step 1: Discover — auto-detect routers on the network', async ({ page }) => {
	await page.goto(WIZARD_URL);

	// Scan view shows first, then select view appears when the scan completes
	await page.waitForSelector('#scan-view', { timeout: 15000 });
	await page.waitForSelector('#select-view:not(.hidden)', { timeout: 45000 });

	const options = await page.locator('#router-select option').count();
	expect(options).toBeGreaterThan(0);

	await page.screenshot({ path: SHOT('step1-discover') });
});

test('Step 2: Connect — select router and enter password', async ({ page }) => {
	await discoverAndWait(page);

	const options = await page.locator('#router-select option').count();
	expect(options).toBeGreaterThan(0);
	await selectRouter(page);

	// Password may be empty (fresh reset) — fill whatever is configured
	await page.fill('#password', ROUTER_PASSWORD);

	await page.screenshot({ path: SHOT('step2-connect') });
});

test('Step 3: Uplink — choose upstream mode', async ({ page }) => {
	await discoverAndWait(page);
	await selectRouter(page);
	await page.fill('#password', ROUTER_PASSWORD);

	if (DEPLOY_MODE === 'sta') {
		await page.click('#mode-sta');
		await expect(page.locator('#sta-fields')).toBeVisible();
		if (WIFI_SSID) await page.fill('#ssid', WIFI_SSID);
		if (WIFI_PASSWORD) await page.fill('#wifi-pass', WIFI_PASSWORD);
	} else {
		await page.click('#mode-wan');
		await expect(page.locator('#sta-fields')).toBeHidden();
	}

	await page.screenshot({ path: SHOT('step3-uplink') });
});

test('Step 4: Payouts — enter Lightning address', async ({ page }) => {
	await discoverAndWait(page);
	await configureThroughPayouts(page);

	await expect(page.locator('#lnurl')).toHaveValue(LNURL);

	await page.screenshot({ path: SHOT('step4-payouts') });
});

test('Step 5: Advanced (optional) — open advanced settings, defaults intact', async ({ page }) => {
	await discoverAndWait(page);
	await configureThroughPayouts(page);

	// The advanced section is an optional <details> — closed by default, and
	// NOT required for deploy readiness (checkReady: ip && validLightning(ln)).
	await expect(page.locator('details.advanced')).not.toHaveAttribute('open', 'open');
	await expect(page.locator('#deploy-btn')).toBeEnabled(); // advanced not needed

	await page.click('details.advanced > summary');
	await expect(page.locator('details.advanced')).toHaveAttribute('open', 'open');

	// Verify wizard defaults render
	await expect(page.locator('#devsplit')).toHaveValue('10');
	await expect(page.locator('#margin')).toHaveValue('0');
	await expect(page.locator('#mint')).toBeVisible();

	// Optional overrides from env
	if (DEV_SPLIT !== null && !Number.isNaN(DEV_SPLIT)) {
		await page.locator('#devsplit').fill(String(DEV_SPLIT));
		await expect(page.locator('#devsplit-val')).toHaveText(`${DEV_SPLIT}%`);
	}
	if (MARGIN !== null && !Number.isNaN(MARGIN)) {
		await page.locator('#margin').fill(String(MARGIN));
		await expect(page.locator('#margin-val')).toHaveText(`${MARGIN}%`);
	}

	await page.screenshot({ path: SHOT('step5-advanced') });
});

test('Step 6: Deploy — run the deployment to completion', async ({ page }) => {
	await discoverAndWait(page);
	await configureThroughPayouts(page);

	// LNURL makes the wizard ready — deploy button must be enabled
	await expect(page.locator('#deploy-btn')).toBeEnabled();

	await page.click('#deploy-btn');
	await page.waitForSelector('#deploy-view:not(.hidden)', { timeout: 15000 });

	const maxWait = DEPLOY_TIMEOUT;
	const pollInterval = 2000;
	let elapsed = 0;

	while (elapsed < maxWait) {
		const successVisible = await page
			.locator('#success-view:not(.hidden)')
			.isVisible()
			.catch(() => false);
		const errorVisible = await page
			.locator('#error-view:not(.hidden)')
			.isVisible()
			.catch(() => false);

		if (successVisible) {
			await expect(page.locator('#success-view h2')).toContainText('live');
			await page.screenshot({ path: SHOT('step6-deploy-success') });
			return;
		}

		if (errorVisible) {
			const errorDetail = await page.locator('#error-detail').textContent();
			await page.screenshot({ path: SHOT('step6-deploy-error') });
			if (STRICT_DEPLOY) {
				throw new Error(`Wizard reported deployment failure: ${errorDetail}`);
			}
			console.log(`Deployment reported (lenient mode): ${errorDetail}`);
			return;
		}

		if (elapsed % 8000 === 0) {
			const stepCount = await page.locator('#steps-list .step').count();
			const doneCount = await page.locator('#steps-list .step-icon.done').count();
			console.log(`Deployment progress: ${doneCount}/${stepCount} steps done (${elapsed / 1000}s)`);
		}

		await page.waitForTimeout(pollInterval);
		elapsed += pollInterval;
	}

	await page.screenshot({ path: SHOT('step6-deploy-timeout') });
	throw new Error(`Deployment still running after ${maxWait / 1000}s`);
});

test('Step 7: Live — verify TollGate API and portal on the router', async ({ page }) => {
	// TollGate API on the router
	const apiResponse = await page.goto(`http://${ROUTER_IP}:${TOLLGATE_PORT}/`, {
		waitUntil: 'domcontentloaded',
		timeout: 15000,
	});
	expect(apiResponse, `TollGate API http://${ROUTER_IP}:${TOLLGATE_PORT}/ must respond`).not.toBeNull();
	expect(apiResponse.status()).toBeLessThan(500);

	const body = await page.content();
	expect(body).toMatch(/kind|metric|pubkey|price_per_step/i);
	await page.screenshot({ path: SHOT('step7-live-tollgate') });

	// Captive portal
	await page.goto(`http://${ROUTER_IP}:${PORTAL_PORT}/`, {
		waitUntil: 'domcontentloaded',
		timeout: 15000,
	});
	await page.screenshot({ path: SHOT('step7-live-portal') });

	// Final wizard summary
	await page.goto(WIZARD_URL);
	await page.waitForTimeout(2000);
	await page.screenshot({ path: SHOT('step7-final'), fullPage: true });
});
