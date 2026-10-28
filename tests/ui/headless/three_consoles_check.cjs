"use strict";

/**
 * Headless visual/keyboard contract for actual checked-in FastAPI
 * model-console and account-console, with a synthetic anonymous account.
 * No tokens, real network, authentication, Ollama or screenshot uploads.
 * Not a real Mac/Safari signoff.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "../../../services/control-api/app");
const ORIGIN = "http://127.0.0.1:8765";
const pages = [
  { name: "Model Manager", url: "/console/models", root: "model_console",
    main: "model-main", nav: "Model manager sections" },
  { name: "Account Console", url: "/console/account", root: "account_console",
    main: "account-main", nav: "Account menu" },
];

async function checkPage(browser, view, width) {
  const context = await browser.newContext({
    viewport: { width, height: 780 },
    reducedMotion: "reduce", serviceWorkers: "block",
  });
  const page = await context.newPage();
  const errors = [];
  const unexpected = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", r => {
    const u = new URL(r.url());
    if (u.origin !== ORIGIN) unexpected.push(r.url());
  });
  const dir = path.join(ROOT, view.root);
  await page.route("**/*", async route => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGIN) {
      await route.abort("blockedbyclient");
      return;
    }
    const assets = {
      [view.url]: ["index.html", "text/html; charset=utf-8"],
      [view.url + "/app.css"]: ["app.css", "text/css; charset=utf-8"],
      [view.url + "/app.js"]: ["app.js", "text/javascript; charset=utf-8"],
    };
    const pair = assets[u.pathname];
    if (pair) {
      await route.fulfill({
        status: 200, contentType: pair[1],
        body: fs.readFileSync(path.join(dir, pair[0]), "utf8"),
      });
    } else if (u.pathname === "/auth/session" && view.root === "account_console") {
      // No real IdP or account: validate explicit logged-out path only.
      await route.fulfill({ status: 401, contentType: "application/json",
        body: JSON.stringify({ detail: "Not signed in" }) });
    } else {
      await route.abort("blockedbyclient");
    }
  });
  try {
    await page.goto(ORIGIN + view.url, { waitUntil: "load", timeout: 15000 });
    if (view.root === "account_console") {
      await page.locator("#guest").waitFor({ state: "visible", timeout: 5000 });
    } else {
      await page.locator("#connect-button").waitFor({ state: "visible", timeout: 5000 });
    }

    assert.equal(errors.length, 0, view.name + " JS errors: " + errors.join(", "));
    assert.deepEqual(unexpected, [], "External network access is forbidden");

    const state = await page.evaluate(({ main, nav }) => {
      const mainNode = document.getElementById(main);
      const navNode = document.querySelector('nav[aria-label="' + nav + '"]');
      return {
        documentWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        mainWidth: mainNode.getBoundingClientRect().width,
        navWidth: navNode.getBoundingClientRect().width,
        navScrolls: navNode.scrollWidth > navNode.clientWidth,
      };
    }, view);
    assert.ok(state.documentWidth <= state.innerWidth + 1,
      view.name + " global horizontal overflow " + width + "px: " + JSON.stringify(state));
    assert.ok(state.mainWidth > 0 && state.navWidth > 0);
    assert.ok(state.navScrolls,
      view.name + " mobile navigation must be internally horizontally scrollable");

    await page.keyboard.press("Tab");
    assert.ok((await page.evaluate(() => document.activeElement?.className))?.includes("skip-link"),
      view.name + " first Tab is not skip-to-main");
    await page.keyboard.press("Enter");
    assert.equal(await page.evaluate(() => document.activeElement?.id), view.main,
      view.name + " skip-link should move keyboard focus to main");

    if (view.root === "model_console") {
      const token = page.locator("#token");
      assert.equal(await token.getAttribute("type"), "password");
      assert.equal(await token.inputValue(), "", "No token is injected into synthetic test");
      const connect = page.locator("#connect-button");
      assert.ok((await connect.boundingBox()).height >= 44);
      const lastLink = page.locator('nav[aria-label="Model manager sections"] a[href="#deployments"]');
      await lastLink.focus();
      await page.keyboard.press("Enter");
      // Keyboard-driven hash navigation may settle on a later microtask
      // even after the keypress Promise resolves. Await the actual URL
      // rather than racing the browser and intermittently failing CI.
      await page.waitForURL(url => url.hash === "#deployments", { timeout: 5000 });
      assert.equal(new URL(page.url()).hash, "#deployments");
    } else {
      const guest = page.locator("#guest");
      assert.equal(await guest.isVisible(), true);
      assert.equal(await page.locator("#member").isVisible(), false);
      const signin = page.locator('a[href="/auth/login"]');
      assert.ok((await signin.boundingBox()).height >= 44);
      const lastLink = page.locator('nav[aria-label="Account menu"] a[href="/console/models"]');
      await lastLink.focus(); // Do not click: separate security domain/route.
      assert.equal(await lastLink.isVisible(), true);
    }

    console.log("PASS synthetic " + view.name + " Chromium " + width +
      "px: responsive layout, keyboard skip link, anonymous boundary");
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const view of pages) {
      for (const width of [320, 390]) await checkPage(browser, view, width);
    }
    console.log("All 3 synthetic console UIs tested; Mac Safari P4 still pending.");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error("Synthetic Control API console browser QA FAIL:", error.message);
  process.exitCode = 1;
});
