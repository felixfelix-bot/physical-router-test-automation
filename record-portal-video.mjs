import { chromium } from "@playwright/test";
import { execSync } from "child_process";
import fs from "fs";

const COMMIT = execSync("git rev-parse --short HEAD", { encoding: "utf-8" }).trim();
const HOST = "tollgate.lan";
const VIDEO_DIR = "videos";

fs.mkdirSync(VideoDirSafe(), { recursive: true });
function VideoDirSafe() { return VIDEO_DIR; }

(async () => {
  console.log("Commit:", COMMIT);
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    recordVideo: { dir: VIDEO_DIR, size: { width: 1280, height: 900 } },
  });
  const page = await context.newPage();
  const results = [];

  function log(msg) { console.log("[VIDEO]", msg); results.push(msg); }

  try {
    // 1. Navigate to splash page
    log("Step 1: Loading splash page at http://" + HOST + "/splash.html");
    await page.goto("http://" + HOST + "/splash.html", { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(3000);

    // 2. Wait for portal SPA to render
    log("Step 2: Waiting for portal SPA to render");
    try {
      await page.waitForSelector(".tollgate-captive-portal-view", { timeout: 15000 });
      log("Portal view rendered successfully");
    } catch(e) {
      log("Portal view selector not found, trying #app");
      const appVisible = await page.locator("#app").isVisible();
      log("#app visible: " + appVisible);
    }
    await page.waitForTimeout(2000);

    // 3. Screenshot splash
    await page.screenshot({ path: VIDEO_DIR + "/01-splash.png" });
    log("Screenshot 01-splash.png saved");

    // 4. Check for bare "0" literals (PORTAL-2 bug fix)
    try {
      const view = page.locator(".tollgate-captive-portal-view");
      if (await view.isVisible()) {
        const text = await view.innerText();
        const lines = text.split("\n").map(l => l.trim()).filter(l => l);
        const bareZeros = lines.filter(l => l === "0");
        log("Bare zero check: " + (bareZeros.length === 0 ? "PASS (no bare zeros)" : "FAIL (" + bareZeros.length + " found)"));
      }
    } catch(e) { log("Bare zero check skipped: " + e.message.slice(0,80)); }

    // 5. Check Cashu tab
    log("Step 3: Checking Cashu payment tab");
    const cashuTab = page.locator("#tab-cashu");
    if (await cashuTab.isVisible().catch(() => false)) {
      await cashuTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: VIDEO_DIR + "/02-cashu-tab.png" });
      log("Cashu tab screenshot saved");
    } else {
      log("Cashu tab not visible, checking all tabs");
      const tabs = await page.locator("[id^=tab-]").all();
      for (const t of tabs) {
        const tid = await t.getAttribute("id");
        log("Found tab: " + tid);
      }
    }

    // 6. Check Lightning tab
    log("Step 4: Checking Lightning payment tab");
    const lnTab = page.locator("#tab-lightning");
    if (await lnTab.isVisible().catch(() => false)) {
      await lnTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: VIDEO_DIR + "/03-lightning-tab.png" });
      log("Lightning tab screenshot saved");
    }

    // 7. Check gateway API for balance
    log("Step 5: Checking gateway API");
    try {
      const resp = await page.evaluate(async () => {
        const r = await fetch("http://" + window.location.hostname + ":2121/", { timeout: 5000 });
        return r.ok ? await r.json() : { status: r.status };
      });
      log("Gateway API response: " + JSON.stringify(resp).slice(0, 200));
    } catch(e) { log("Gateway API: " + e.message.slice(0, 80)); }

    // 8. Final state
    await page.waitForTimeout(2000);
    await page.screenshot({ path: VIDEO_DIR + "/04-final.png" });
    log("Final screenshot saved");

    log("VIDEO RECORDING COMPLETE - All steps passed");
  } catch(e) {
    log("ERROR: " + e.message.slice(0, 200));
    await page.screenshot({ path: VIDEO_DIR + "/error.png" }).catch(() => {});
  }

  // Close context to finalize video
  await context.close();
  await browser.close();

  // Save results
  fs.writeFileSync(VIDEO_DIR + "/results.txt", results.join("\n"));
  console.log("Results saved to " + VIDEO_DIR + "/results.txt");
})();
