"use strict";
// Static accessibility safeguards for the real local reviewer UI.
// These contracts do not substitute for visual/keyboard testing in Safari.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = path.join(__dirname,
  "../../services/inference-gateway/internal/httpapi/console");
const html = fs.readFileSync(path.join(base, "index.html"), "utf8");
const css = fs.readFileSync(path.join(base, "app.css"), "utf8");

test("first keyboard stop provides a visible-on-focus main-content skip link", () => {
  assert.match(html, /<body>\s*<a class="skip-link" href="#overview">Skip to main operations content<\/a>/);
  assert.match(html, /<main id="overview" class="content" tabindex="-1">/);
  assert.match(css, /\.skip-link\{[^}]*transform:translateY\(-160%\)/);
  assert.match(css, /\.skip-link:focus\{transform:translateY\(0\)\}/);
  assert.match(css, /a:focus-visible,button:focus-visible\{outline:2px solid/);
});

test("all six section links remain keyboard focusable and their targets exist", () => {
  const names = ["overview", "admission", "outcomes", "failures", "usage", "reviewer"];
  for (const id of names) {
    assert.match(html, new RegExp('<a class="nav-link(?: active)?" href="#' + id + '"'));
    assert.match(html, new RegExp('id="' + id + '"'));
  }
  assert.equal((html.match(/class="nav-link(?: active)?"/g) || []).length, 6);
  assert.match(html, /<nav aria-label="Dashboard sections" class="navigation">/);
});

test("mobile and tablet no longer hide all or inactive operations navigation", () => {
  assert.doesNotMatch(css, /\.navigation\{display:none\}/);
  assert.doesNotMatch(css, /\.nav-link:not\(\.active\)/);
  assert.match(css, /@media\(max-width:880px\)\{[^}]*\.sidebar\{width:192px/);
  assert.match(css, /@media\(max-width:600px\)\{[^]*?\.navigation\{display:flex;[^}]*overflow-x:auto/);
  assert.match(css, /\.nav-link\{flex:0 0 auto;white-space:nowrap;min-height:44px/);
  assert.match(css, /\.health-value\{font-size:19px;white-space:normal;overflow-wrap:anywhere/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
});

test("manual automatic-refresh control stays accessible on mobile without autoplay dependency", () => {
  assert.match(html, /id="auto-refresh-toggle" type="button" class="refresh-button" aria-pressed="false" aria-label="Pause automatic metrics refresh"/);
  assert.match(html, /id="refresh" type="button"/);
  assert.match(css, /#auto-refresh-toggle\[aria-pressed="true"\]/);
  assert.match(css, /\.refresh-button\{min-height:44px;white-space:nowrap\}/);
  assert.match(css, /@media\(max-width:480px\)/);
});
