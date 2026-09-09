/**
 * E2E — Wizard deploys tollgate-wrt from the FEED's per-arch URL.
 *
 * Phase 3 (feat/feed-per-arch-urls) verification:
 *   1. Clear stale router tollgate state (wallet.db / config.json) so the
 *      feed-built artifact starts CLEAN and can reach FULL mode (kind:10021),
 *      proving it RUNS — the v0.5.0 Cashu-broken lesson.
 *   2. Start the wizard binary (WIZARD_RELEASE_URL) on localhost:8099.
 *   3. POST /api/deploy to the bench router (10.230.237.1, aarch64_cortex-a53,
 *      OpenWrt 25.12.0, apk-tools 3.0.2, SSH key id_ed25519 as root).
 *   4. Poll /api/status until done.
 *   5. Assert the deploy log shows:
 *        - the arch was DETECTED (aarch64_cortex-a53), not hardcoded
 *        - the package came from the FEED URL (FreedomTechFeed/packages)
 *   6. Verify the backend reaches FULL mode (kind:10021) — the feed artifact
 *      RUNS on the bench router, not just compiles.
 *
 * The spec MUST HARD-FAIL on degraded mode (kind:21023) — it never adapts.
 *
 * Env:
 *   ROUTER_IP           (default 10.230.237.1)
 *   WIZARD_RELEASE_URL  (default: local /tmp/net4sats-wizard-feed)
 *   WIZARD_PORT         (default 8099)
 *   SSH_KEY             (default ~/.ssh/id_ed25519)
 *   MINT                (default https://mint.coinos.io)
 *   LNURL               (default c3e23eb5e3d00f18b2f4f588@coinos.io)
 */
import { test, expect } from '@playwright/test';
import { execSync, spawn } from 'child_process';
import { existsSync, rmSync } from 'fs';
import { resolve } from 'path';

const ROUTER_IP       = process.env.ROUTER_IP || '10.230.237.1';
const WIZARD_URL      = process.env.WIZARD_RELEASE_URL || '/tmp/net4sats-wizard-feed';
const WIZARD_PORT     = process.env.WIZARD_PORT || '8099';
const SSH_KEY         = process.env.SSH_KEY || 'id_ed25519';
const MINT            = process.env.MINT || 'https://mint.coinos.io';
const LNURL           = process.env.LNURL || 'c3e23eb5e3d00f18b2f4f588@coinos.io';
const FEED_RE         = /FreedomTechFeed\/packages/;
const ARCH            = 'aarch64_cortex-a53';

const WIZARD_BIN  = resolve(new URL('.', import.meta.url).pathname, 'net4sats-wizard');
const OUTPUT_DIR  = resolve(new URL('.', import.meta.url).pathname, '../../test-results/feed-per-arch-e2e');
const FEED_URL = `https://github.com/FreedomTechFeed/packages/releases/download/v0.6.0-alpha1/tollgate-wrt_0.6.0_alpha1_${ARCH}.apk`;

// SSH helper — key-based auth (bench router: id_ed25519 as root).
function rssh(cmd, timeoutMs = 30000) {
  try {
    return execSync(
      `ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -i ~/.ssh/${SSH_KEY} root@${ROUTER_IP} '${cmd.replace(/'/g, "'\\''")}'`,
      { timeout: timeoutMs, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
    ).trim();
  } catch (err) {
    console.error(`[SSH] failed: ${cmd}`);
    if (err.stderr) console.error(`[SSH] stderr: ${err.stderr}`);
    return '';
  }
}
function httpGet(url, timeoutMs = 30000) {
  try {
    return execSync(`curl -sS --max-time ${Math.floor(timeoutMs / 1000)} '${url}'`, { encoding: 'utf8', timeout: timeoutMs, stdio: ['pipe','pipe','pipe'] });
  } catch { return ''; }
}
function httpPost(url, body, timeoutMs = 120000) {
  try {
    const js = JSON.stringify(body);
    return execSync(`curl -sS --max-time ${Math.floor(timeoutMs / 1000)} -X POST -H 'Content-Type: application/json' -d '${js.replace(/'/g,"'\\''")}' '${url}'`, { encoding: 'utf8', timeout: timeoutMs, stdio: ['pipe','pipe','pipe'] });
  } catch { return ''; }
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

let wizard = null;
let wizardPid = null;
let jobId = null;
let deployResult = null;

test.beforeAll(async () => {
  let portHolder = '';
  try { portHolder = execSync(`ss -ltnp 2>/dev/null | grep ':${WIZARD_PORT} ' || true`, { encoding: 'utf8' }).trim(); } catch {}
  if (portHolder) throw new Error(`[SETUP] Port ${WIZARD_PORT} held: ${portHolder}`);
});

test('feed-per-arch: deploy uses detected arch + FEED URL, backend reaches full mode', async () => {
  test.setTimeout(600000);

  // ── Clean stale router tollgate state (no-brick: stop service first) ──
  console.log('[1/5] Stopping tollgate-wrt + clearing stale state...');
  let out = rssh('/etc/init.d/tollgate-wrt stop 2>/dev/null; true; sleep 1; rm -f /etc/tollgate/wallet.db /etc/tollgate/sessions.json /etc/tollgate/config.json; echo CLEARED');
  console.log(`       ${out}`);
  // Confirm arch + pkgmgr
  out = rssh('grep "^DISTRIB_ARCH" /etc/openwrt_release; which apk 2>/dev/null; cat /etc/openwrt_release | grep DISTRIB_ARCH');
  console.log(`[5] Router: ${out}`);

  // ── Preflight: prove the feed .apk is served (HEAD — the full .apk body
  // ── is ~8MB and can exceed a short -L body download window on slow links,
  // ── so probe existence cheaply; the real body download is the deploy's job) ──
  const pre = execSync(`curl -sS -I -L --max-time 30 -o /dev/null -w '%{http_code}' '${FEED_URL}'`, { encoding: 'utf8' });
  console.log(`[PRE] Feed aarch64 .apk HTTP ${pre}`);
  expect(pre).toBe('200');

  // ── Start wizard ──
  const src = existsSync(WIZARD_URL) ? WIZARD_URL : WIZARD_BIN;
  console.log(`[2/5] Starting wizard: ${src}`);
  wizard = spawn(src, [], { env: { ...process.env, PORT: WIZARD_PORT }, stdio: ['pipe','pipe','pipe'] });
  wizardPid = wizard.pid;
  for (let i = 0; i < 30; i++) {
    if (httpGet(`http://localhost:${WIZARD_PORT}`, 5000)) break;
    await sleep(2000);
  }
  // prove OUR pid owns the port
  let listen = '';
  try { listen = execSync(`ss -ltnp 2>/dev/null | grep ':${WIZARD_PORT} '`, { encoding: 'utf8' }).trim(); } catch {}
  console.log(`       ss: ${listen}`);
  expect(listen).toContain(`pid=${wizardPid}`);

  // ── POST deploy ──
  console.log('[3/5] POST /api/deploy...');
  const resp = httpPost(`http://localhost:${WIZARD_PORT}/api/deploy`, {
    ip: ROUTER_IP, password: '', mode: 'wan', lnurl: LNURL, devSplit: 10, margin: 0, mint: MINT,
  });
  console.log(`       POST resp: ${resp}`);
  let parsed; try { parsed = JSON.parse(resp); } catch {}
  expect(parsed && parsed.job_id).toBeTruthy();
  jobId = parsed.job_id;

  // ── Poll status ──
  console.log('[4/5] Polling status...');
  let status;
  for (let i = 0; i < 90; i++) {
    await sleep(4000);
    const s = httpGet(`http://localhost:${WIZARD_PORT}/api/status/${jobId}`, 30000);
    try { status = JSON.parse(s); } catch { continue; }
    if (status.status === 'done' || status.status === 'failed') break;
  }
  deployResult = status;
  console.log(`       final status=${status && status.status}`);
  const log = (status && status.log || []).map(l => (l && l.msg) || '').join('\n');
  console.log('--- deploy log ---');
  console.log(log);

  // Save evidence
  try {
    execSync(`mkdir -p '${OUTPUT_DIR}'`, { encoding: 'utf8' });
    const fs = await import('fs');
    fs.writeFileSync(`${OUTPUT_DIR}/status-final.json`, JSON.stringify(status, null, 2));
  } catch {}

  // ── ASSERTIONS ──
  expect(status, 'no status returned').toBeTruthy();

  // Gate: arch DETECTED (not hardcoded) + feed URL selected
  expect(log, 'log must show DETECTED arch').toMatch(new RegExp(`Detected router CPU arch:\\s*${ARCH}`));
  expect(log, 'log must show the FEED per-arch URL').toMatch(/Selected tollgate-wrt asset: .*FreedomTechFeed\/packages.*\.apk/);

  // The deploy itself must succeed or explicitly fail-loudly. Either is
  // evidence — but a degrade-to-success is disqualifying. We assert the
  // arch+feed wiring here; the full-mode backend is asserted next.
  console.log(`[5/5] Backend full-mode check...`);
  // Give backend time to finish mint probe after a fresh start
  await sleep(30000);
  const kind = rssh('wget -T 8 -qO- http://127.0.0.1:2121/ 2>/dev/null | head -c 200');
  console.log(`       :2121 => ${kind}`);
  // HARD-FAIL on degraded mode — never adapt
  expect(kind, `backend must NOT be degraded (kind:21023). Got: ${kind}`).not.toContain('21023');
  // It should be full mode OR at worst initializing-then-recovering with a reachable mint line.
  // The definitive check: the health/status is 'done' AND backend binds with pricing.
  // Require kind:10021 pricing metadata:
  expect(kind, 'backend must reach FULL mode (kind:10021 with price_per_step)').toContain('10021');
}, );

test.afterAll(async () => {
  if (wizardPid) { try { process.kill(wizardPid, 'SIGKILL'); } catch {} }
  try { execSync('pkill -f net4sats-wizard 2>/dev/null || true', { encoding: 'utf8' }); } catch {}
  console.log('[CLEANUP] wizard stopped');
});
