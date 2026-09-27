/**
 * pre13-verify.spec.mjs — verify the pre13 hot-deploy on a router:
 *   1. admin board #/devices does NOT show SESSION_EXPIRED (ACL + classification)
 *   2. guest portal splash mounts with no page error (render-guard/ErrorBoundary)
 *
 *   ROUTER_IP=192.168.1.1 ROUTER_PASSWORD=... npx playwright test \
 *     --config tests/browser/pre13-verify.config.mjs
 */
import { test, expect } from '@playwright/test';

const IP = process.env.ROUTER_IP || '192.168.1.1';
const PW = process.env.ROUTER_PASSWORD || 'password';

test('board #/devices: no SESSION_EXPIRED on a fresh login', async ({ page }) => {
  await page.goto(`http://${IP}:8090/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('input#password', { timeout: 20000 });
  await page.fill('input#username', 'root');
  await page.fill('input#password', PW);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2000);

  await page.goto(`http://${IP}:8090/#/devices`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  const body = (await page.textContent('body')) || '';
  expect(body, 'raw SESSION_EXPIRED must not appear').not.toContain('SESSION_EXPIRED');
  // either leases or the empty state is acceptable
  expect(/connected|No connected devices/i.test(body), `devices page body: ${body.slice(0, 200)}`).toBeTruthy();
  await page.screenshot({ path: 'test-results/pre13-board-devices.png', fullPage: true });
});

test('guest portal: splash mounts without a render error', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`http://${IP}:2051/splash.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#cashu-token', { timeout: 25000 });
  await expect(page.locator('#cashu-token')).toBeVisible();
  expect(errors, `page errors:\n${errors.join('\n')}`).toEqual([]);
  await page.screenshot({ path: 'test-results/pre13-portal-splash.png', fullPage: true });
});
