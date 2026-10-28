"use strict";

/**
 * Synthetic/offline Chromium rendering contract for the real local Go
 * Operations HTML/CSS/JS. No actual Go server, Ollama, Mac or user data.
 * Intercepts every request on a fake loopback origin and never captures
 * screenshots or logs model outputs. NOT a P4 Mac/Safari acceptance.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const DIR = path.resolve(__dirname, "../../../services/inference-gateway/internal/httpapi/console");
const BASE = "http://127.0.0.1:8765";
const sizes = [
  { width: 320, height: 700 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1440, height: 900 },
];

const routes = {
  "/health/ready": { status: "ready" },
  "/health/upstream": { status: "reachable" },
  "/metrics/admission": {
    schema_version: 2, active_requests: 0, waiting_requests: 0,
    max_active_requests: 4, max_waiting_requests: 0,
    admitted_immediate_total: 0, admitted_queued_total: 0,
    rejected_full_total: 0, queue_timeout_total: 0,
    queue_canceled_total: 0, queue_wait_observations_total: 0,
  },
  "/metrics/inference": {
    schema_version: 3, attempts_total: 0, active_attempts: 0,
    nonstream_complete_total: 0, stream_clean_eof_total: 0,
    stream_done_total: 0, stream_missing_done_total: 0,
    pre_response_failure_total: 0, post_response_interrupted_total: 0,
    client_canceled_total: 0, duration_observations_total: 0,
    duration_total_us: 0, duration_bucket_counts: Array(9).fill(0),
    duration_bucket_upper_bounds_ms: [100,250,500,1000,2500,5000,10000,30000],
  },
  "/metrics/upstream": {
    schema_version: 1, connection_refused_total: 0,
    transport_errors_total: 0, timeouts_total: 0, http_rejected_total: 0,
    http_other_status_total: 0, protocol_errors_total: 0,
    response_too_large_total: 0, stream_read_errors_total: 0,
    stream_size_limit_total: 0,
  },
  "/metrics/usage": {
    schema_version: 2, classified_terminal_attempts_total: 0,
    nonstream_provider_plausible_total: 0, nonstream_provider_missing_total: 0,
    nonstream_provider_malformed_total: 0, nonstream_provider_inconsistent_total: 0,
    nonstream_provider_above_cap_total: 0, nonstream_no_completion_total: 0,
    stream_not_measured_total: 0, stream_provider_plausible_total: 0,
    stream_provider_missing_total: 0, stream_provider_malformed_total: 0,
    stream_provider_inconsistent_total: 0, stream_provider_above_cap_total: 0,
    stream_incomplete_total: 0, provider_prompt_tokens_reported_total: 0,
    provider_output_tokens_reported_total: 0, provider_all_tokens_reported_total: 0,
  },
};

async function intercept(route) {
  const url = new URL(route.request().url());
  if (url.origin !== BASE) {
    await route.abort("blockedbyclient");
    return;
  }
  const assets = {
    "/console": ["index.html", "text/html; charset=utf-8"],
    "/console/app.css": ["app.css", "text/css; charset=utf-8"],
    "/console/app.js": ["app.js", "text/javascript; charset=utf-8"],
  };
  if (Object.hasOwn(assets, url.pathname)) {
    const [file, contentType] = assets[url.pathname];
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": contentType },
      body: fs.readFileSync(path.join(DIR, file), "utf8"),
    });
  } else if (Object.hasOwn(routes, url.pathname)) {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(routes[url.pathname]),
    });
  } else {
    await route.abort("blockedbyclient");
  }
}

async function renderAt(browser, viewport) {
  const context = await browser.newContext({
    viewport, deviceScaleFactor: 1, reducedMotion: "reduce",
    serviceWorkers: "block", acceptDownloads: false,
  });
  const page = await context.newPage();
  const errors = [];
  const forbidden = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", request => {
    const u = new URL(request.url());
    if (u.origin !== BASE) forbidden.push(request.url());
  });
  try {
    await page.route("**/*", intercept);
    await page.goto(BASE + "/console", { waitUntil: "load", timeout: 15000 });
    await page.waitForFunction(() => document.querySelector("#data-state")?.textContent === "Gateway & upstream responding", null, {timeout:10000});

    const shape = await page.evaluate(() => {
      const main = document.querySelector("main");
      const nav = document.querySelector(".navigation");
      const pause = document.querySelector("#auto-refresh-toggle");
      const refresh = document.querySelector("#refresh");
      const cards = [...document.querySelectorAll(".health-card")];
      return {
        rootWidth: document.documentElement.scrollWidth,
        viewWidth: window.innerWidth,
        mainWidth: main.getBoundingClientRect().width,
        navVisible: nav.getBoundingClientRect().width > 0,
        navScrollable: nav.scrollWidth > nav.clientWidth,
        pauseHeight: pause.getBoundingClientRect().height,
        refreshHeight: refresh.getBoundingClientRect().height,
        cards: cards.map(el => ({
          width: el.getBoundingClientRect().width,
          right: el.getBoundingClientRect().right,
        })),
        heading: document.querySelector("h1").innerText,
      };
    });
    assert.equal(errors.length, 0, "JavaScript rendering errors: " + errors.join("; "));
    assert.deepEqual(forbidden, [], "External/private network request occurred");
    assert.ok(shape.rootWidth <= shape.viewWidth + 1,
      "Global page overflow at " + viewport.width + "px: " + JSON.stringify(shape));
    assert.ok(shape.mainWidth > 0 && shape.navVisible && shape.cards.length === 4);
    assert.ok(shape.cards.every(c => c.width > 0 && c.right <= shape.viewWidth + 1));
    assert.match(shape.heading, /Operations/);
    assert.ok(shape.pauseHeight >= 44 && shape.refreshHeight >= 44,
      "Refresh controls are too small for touch");

    // Actual DOM keyboard behavior, rather than only CSS source pattern checks.
    await page.keyboard.press("Tab");
    const first = await page.evaluate(() => document.activeElement?.className);
    assert.ok(typeof first === "string" && first.includes("skip-link"),
      "First keyboard stop must be the skip-to-main link");
    await page.keyboard.press("Enter");
    assert.equal(await page.evaluate(() => document.activeElement?.id), "overview",
      "Skip-link activation should focus the main landmark");

    const navItem = page.locator('nav a[href="#reviewer"]');
    await navItem.focus();
    await page.keyboard.press("Enter");
    assert.equal(new URL(page.url()).hash, "#reviewer");
    if (viewport.width <= 390) {
      assert.equal(shape.navScrollable, true,
        "320/390px mobile navigation should be horizontally scrollable");
      const visible = await navItem.evaluate(el => {
        const box = el.getBoundingClientRect();
        const parent = el.parentElement.getBoundingClientRect();
        return box.left >= parent.left - 1 && box.right <= parent.right + 1;
      });
      assert.equal(visible, true, "Focused end-of-nav item must scroll into visible area");
    }

    const toggle = page.locator("#auto-refresh-toggle");
    await toggle.focus();
    await page.keyboard.press("Enter");
    assert.equal(await toggle.getAttribute("aria-pressed"), "true");
    assert.equal((await toggle.innerText()).trim(), "Resume auto");
    const refresh = page.locator("#refresh");
    await refresh.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector("#data-state")?.textContent === "Gateway & upstream responding");
    assert.equal(await refresh.isDisabled(), false);
    console.log("PASS synthetic Chromium " + viewport.width +
      "px: viewport, scrollable nav, keyboard skip/links/pause/refresh, no external requests");
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of sizes) await renderAt(browser, viewport);
    console.log("Synthetic browser QA PASS; real Mac Safari/manual P4 approval NOT VERIFIED.");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error("Synthetic browser QA FAIL:", error.message);
  process.exitCode = 1;
});
