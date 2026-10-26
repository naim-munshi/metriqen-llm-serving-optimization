"use strict";
// Browser-like DOM contract: local deployment inference always reports
// missing prompt, running state, success or failure beside its own card.
// This uses synthetic HTTP responses; real Safari acceptance is separate.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname,
  "../../services/control-api/app/model_console/app.js"), "utf8");

function fixture() {
  const elements = new Map();
  const requests = [];
  let chatMode = "success";

  class Node {
    constructor(tag = "div") {
      this.tag = tag;
      this.children = [];
      this.listeners = {};
      this.textContent = "";
      this.value = "";
      this.hidden = false;
      this.disabled = false;
      this.dataset = {};
      this.className = "";
    }
    get options() { return this.children.filter(child => child.tag === "option"); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    setAttribute(name, value) { this[name] = value; }
    focus() { this.focused = true; }
  }
  const document = {
    hidden: false,
    createElement: tag => new Node(tag),
    getElementById: id => {
      if (!elements.has(id)) elements.set(id, new Node());
      return elements.get(id);
    },
    querySelectorAll: query => query === ".model-action"
      ? [...elements.values()].flatMap(root => {
        const found = [];
        const walk = node => {
          if (node.className?.split(" ").includes("model-action")) found.push(node);
          for (const child of node.children) walk(child);
        };
        walk(root);
        return found;
      })
      : [],
  };
  const ctx = vm.createContext({
    document,
    AbortController,
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url.endsWith("/chat")) {
        if (chatMode === "error") {
          return {ok:false, status:503, json:async()=>({
            detail:"local inference gateway model identity unverified"
          })};
        }
        return {ok:true, status:200, json:async()=>({
          deployment:"acceptance-test",tag:"llama3.2:1b",content:"READY."
        })};
      }
      return {ok:false, status:503, json:async()=>({detail:"not available"})};
    },
  });
  vm.runInContext(script, ctx, {timeout:1500});
  vm.runInContext('operatorToken="synthetic-local-test-token";', ctx);
  const makeRows = () => vm.runInContext(
    'renderDeployments(['+
    '{name:"acceptance-test",active_tag:"llama3.2:1b",active_digest:"'+"a".repeat(64)+'",previous_tag:"llama3.2:latest",previous_digest:"'+"b".repeat(64)+'",enabled:true,ready:true},'+
    '{name:"assistant",active_tag:"llama3.2:1b",active_digest:"'+"a".repeat(64)+'",previous_tag:"llama3.2:latest",previous_digest:"'+"b".repeat(64)+'",enabled:true,ready:true}'+
    '],[{tag:"llama3.2:1b",approved:true,digest:"'+"a".repeat(64)+'"}],'+
    '[{tag:"llama3.2:1b",approved:true,loaded:true,digest_matches_loaded:true}])',
    ctx
  );
  makeRows();
  const cards = elements.get("deployment-list").children;
  function control(index) {
    const playground = cards[index].children.at(-1);
    const [label, input, button, feedback, output] = playground.children;
    return {input,button,feedback,output};
  }
  return {requests,control, setMode(mode) { chatMode = mode; }};
}

test("wrong-card or blank prompt shows inline error and sends no chat", async () => {
  const ui = fixture();
  const left = ui.control(0), right = ui.control(1);
  right.input.value = "Reply READY";
  await left.button.listeners.click();
  assert.equal(ui.requests.length, 0);
  assert.equal(left.feedback.hidden, false);
  assert.match(left.feedback.textContent, /Enter a prompt in this deployment card/);
  assert.equal(left.feedback.className, "inference-feedback error");
  assert.equal(left.input.focused, true);
  assert.equal(left.output.hidden, true);
  assert.equal(right.feedback.hidden, true);
});

test("deployment inference shows same-card response after bounded chat", async () => {
  const ui = fixture();
  const left = ui.control(0);
  left.input.value = "Reply READY";
  await left.button.listeners.click();
  assert.equal(left.output.hidden, false);
  assert.equal(left.output.textContent, "READY.");
  assert.equal(left.feedback.hidden, false);
  assert.match(left.feedback.textContent, /Response received from acceptance-test/);
  const chats = ui.requests.filter(r => r.url.endsWith("/chat"));
  assert.equal(chats.length, 1);
  assert.equal(chats[0].url, "/api/v1/local/deployments/acceptance-test/chat");
  assert.equal(chats[0].options.method, "POST");
  assert.deepEqual(JSON.parse(chats[0].options.body),
    {prompt:"Reply READY", max_tokens:128});
});

test("HTTP failure is visible beside deployment action without showing stale output", async () => {
  const ui = fixture();
  const left = ui.control(0);
  left.input.value = "Reply READY";
  await left.button.listeners.click();
  assert.equal(left.output.hidden, false);
  ui.setMode("error");
  await left.button.listeners.click();
  assert.equal(left.output.hidden, true);
  assert.equal(left.feedback.className, "inference-feedback error");
  assert.match(left.feedback.textContent, /503: local inference gateway model identity unverified/);
  assert.equal(left.feedback.hidden, false);
});
