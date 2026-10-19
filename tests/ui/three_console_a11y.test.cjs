"use strict";
// Static DOM/CSS safety contracts only; NOT a real mobile/keyboard acceptance run.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const BASE = path.join(__dirname, "../../services/control-api/app");
function assets(name) {
  const directory = path.join(BASE, name);
  return {
    html: fs.readFileSync(path.join(directory, "index.html"), "utf8"),
    css: fs.readFileSync(path.join(directory, "app.css"), "utf8"),
  };
}

test("model manager first keyboard stop skips directly to the labelled main landmark", () => {
  const { html, css } = assets("model_console");
  assert.match(html, /<body>\s*<a class="skip-link" href="#model-main">Skip to model manager content<\/a>/);
  assert.match(html, /<main class="content" id="model-main" tabindex="-1">/);
  assert.match(css, /\.skip-link:focus\{transform:translateY\(0\)\}/);
  assert.match(css, /select:focus-visible,input:focus-visible\{outline:2px solid/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)\{html\{scroll-behavior:auto\}/);
  assert.match(html, /<nav aria-label="Model manager sections">/);
  for (const target of ["inventory", "registry", "deployments"]) {
    assert.match(html, new RegExp('href="#' + target + '"'));
    assert.match(html, new RegExp('id="' + target + '"'));
  }
});

test("optional account first keyboard stop skips to its main content", () => {
  const { html, css } = assets("account_console");
  assert.match(html, /<body>\s*<a class="skip-link" href="#account-main">Skip to account content<\/a>/);
  assert.match(html, /<main id="account-main" tabindex="-1">/);
  assert.match(css, /\.skip-link:focus\{transform:translateY\(0\)\}/);
  assert.match(html, /<nav aria-label="Account menu">/);
  assert.match(html, /href="http:\/\/127\.0\.0\.1:8081\/console" target="_blank" rel="noopener noreferrer"/);
});

test("account mobile nav retains all links and permits horizontal keyboard/touch scrolling", () => {
  const { css } = assets("account_console");
  assert.match(css, /@media\(max-width:750px\)/);
  assert.match(css, /\.rail nav\{display:flex;gap:8px;overflow-x:auto;/);
  assert.match(css, /\.rail nav a\{flex:0 0 auto;white-space:nowrap;min-height:44px;/);
  assert.doesNotMatch(css, /\.rail nav\{display:none/);
});

test("all three consoles keep keyboard focus explicit and not hidden for tablet/mobile", () => {
  const gateway = path.join(__dirname,
    "../../services/inference-gateway/internal/httpapi/console");
  const html = fs.readFileSync(path.join(gateway, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(gateway, "app.css"), "utf8");
  assert.match(html, /class="skip-link" href="#overview"/);
  assert.match(css, /\.navigation\{display:flex;[^}]*overflow-x:auto/);
  for (const type of ["model_console", "account_console"]) {
    const { html: page, css: styles } = assets(type);
    assert.match(page, /class="skip-link"/);
    assert.match(styles, /\.skip-link\{[^}]*z-index:100;/);
  }
});
