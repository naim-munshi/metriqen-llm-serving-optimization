"use strict";

// No third-party dependencies, analytics, cookies or persistent token storage.
const api = "/api/v1/local";
const $ = id => document.getElementById(id);
let operatorToken = "";
let busy = false;

function notify(message, isError = false) {
  $("notice").textContent = String(message);
  $("notice").className = "notice" + (isError ? " error" : "");
}

function setBusy(value) {
  busy = value;
  $("refresh").disabled = value || !operatorToken;
  $("connect-button").disabled = value;
  $("disconnect").disabled = value || !operatorToken;
  document.querySelectorAll(".model-action").forEach(node => { node.disabled = value || node.dataset.locked === "true"; });
}

async function call(path, method = "GET", payload = null) {
  if (!operatorToken) throw new Error("Connect with your local operator token first.");
  const controller = new AbortController();
  const wait = method === "POST" && path.endsWith("/load") ? 130000 : 10000;
  const timer = setTimeout(() => controller.abort(), wait);
  try {
    const response = await fetch(api + path, {
      method,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      headers: {
        "Accept": "application/json",
        "Authorization": "Bearer " + operatorToken,
        ...(payload === null ? {} : {"Content-Type": "application/json"})
      },
      body: payload === null ? undefined : JSON.stringify(payload),
      signal: controller.signal
    });
    const body = await response.json();
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail.slice(0, 150) : "request rejected";
      throw new Error(response.status + ": " + detail);
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function smallText(value, css = "") {
  const p = document.createElement("span");
  p.className = css;
  p.textContent = value;
  return p;
}

function empty(id, message) {
  const list = $(id);
  list.replaceChildren();
  const item = document.createElement("p");
  item.className = "empty";
  item.textContent = message;
  list.append(item);
}

function action(text, handler, variant = "secondary") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = text;
  btn.className = "model-action " + variant;
  btn.addEventListener("click", () => perform(handler));
  return btn;
}

async function perform(fn) {
  if (busy || !operatorToken) return;
  setBusy(true);
  try {
    await fn();
  } catch (error) {
    notify(error instanceof Error ? error.message : "Operation failed", true);
  } finally {
    setBusy(false);
  }
}

function modelCard(model, actions, subtitle) {
  const card = document.createElement("article");
  card.className = "model-card";
  const top = document.createElement("div");
  top.className = "model-top";
  const info = document.createElement("div");
  info.className = "model-info";
  const title = document.createElement("strong");
  title.textContent = model.tag;
  info.append(title, smallText(subtitle, "muted"));
  top.append(info);
  const digest = document.createElement("code");
  digest.textContent = "SHA-256 " + model.digest.slice(0, 16) + "…";
  const controls = document.createElement("div");
  controls.className = "model-actions";
  for (const button of actions) controls.append(button);
  card.append(top, digest, controls);
  return card;
}

function render(available, registered) {
  const registry = new Set(registered.map(m => m.tag));
  $("available-count").textContent = available.length.toString();
  $("registry-count").textContent = registered.length.toString();
  if (!available.length) empty("available-list", "No supported model:tag entries found in local Ollama.");
  else {
    const list = $("available-list");
    list.replaceChildren();
    for (const model of available) {
      const button = action(registry.has(model.tag) ? "Registered" : "Register", async () => {
        await call("/models", "POST", {tag: model.tag});
        notify("Registered " + model.tag + ". Review and approve its digest.");
        await refresh();
      }, registry.has(model.tag) ? "secondary" : "primary");
      if (registry.has(model.tag)) { button.dataset.locked = "true"; button.disabled = true; }
      list.append(modelCard(model, [button], "Installed locally"));
    }
  }

  if (!registered.length) empty("registry-list", "No registered model revisions.");
  else {
    const list = $("registry-list");
    list.replaceChildren();
    for (const model of registered) {
      const controls = [];
      if (!model.approved) controls.push(action("Approve", async () => {
        await call("/models/" + encodeURIComponent(model.tag) + "/approve", "POST");
        notify("Approved exact revision of " + model.tag + ".");
        await refresh();
      }, "primary"));
      else {
        controls.push(action("Load", async () => {
          notify("Loading model into local Ollama memory…");
          await call("/models/" + encodeURIComponent(model.tag) + "/load", "POST");
          notify("Model loaded and digest verified: " + model.tag);
        }, "primary"));
        controls.push(action("Unload", async () => {
          const result = await call("/models/" + encodeURIComponent(model.tag) + "/unload", "POST");
          notify(result.already_unloaded ? "Model was already unloaded." : "Model unloaded: " + model.tag);
        }));
      }
      controls.push(action("Status", async () => {
        const s = await call("/models/" + encodeURIComponent(model.tag) + "/status");
        notify(model.tag + ": " + (s.loaded ? "loaded in Ollama memory" : "not loaded") +
          (s.digest_matches_loaded ? "" : " (digest mismatch)"));
      }));
      const card = modelCard(model, controls,
        model.approved ? "Approved revision" : "Pending operator approval");
      if (model.approved) {
        const playground = document.createElement("div");
        playground.className = "playground";
        const label = document.createElement("label");
        label.textContent = "Single-turn inference test · local Go gateway";
        const input = document.createElement("textarea");
        input.rows = 2;
        input.maxLength = 4096;
        input.placeholder = "Enter a short local prompt…";
        input.setAttribute("aria-label", "Prompt for " + model.tag);
        const output = document.createElement("pre");
        output.className = "inference-output";
        output.hidden = true;
        const runButton = action("Run inference", async () => {
          const prompt = input.value.trim();
          if (!prompt) throw new Error("Enter a nonempty prompt first.");
          output.hidden = true;
          notify("Sending an approved single-turn inference request to the local gateway…");
          const result = await call("/models/" + encodeURIComponent(model.tag) + "/chat",
                                    "POST", {prompt: prompt, max_tokens: 128});
          if (typeof result.content !== "string") throw new Error("Invalid local inference response.");
          output.textContent = result.content;
          output.hidden = false;
          notify("Received local inference response from " + model.tag + ".");
        }, "primary");
        playground.append(label, input, runButton, output);
        card.append(playground);
      }
      list.append(card);
    }
  }
}

async function refresh() {
  const [available, registered] = await Promise.all([
    call("/available-models"), call("/models")
  ]);
  if (!Array.isArray(available.models) || !Array.isArray(registered.models)) {
    throw new Error("Unexpected registry API response");
  }
  render(available.models, registered.models);
}

$("connect-form").addEventListener("submit", event => {
  event.preventDefault();
  if (busy) return;
  const supplied = $("token").value;
  if (!supplied) return;
  operatorToken = supplied;  // ephemeral in-memory only
  $("token").value = "";
  perform(async () => {
    try {
      await refresh();
      notify("Connected to local operator API. Registry synchronized.");
    } catch (error) {
      operatorToken = "";
      empty("available-list", "Connect using a valid local operator token.");
      empty("registry-list", "Registered models appear here.");
      $("available-count").textContent = "—";
      $("registry-count").textContent = "—";
      throw error;
    }
  });
});
$("refresh").addEventListener("click", () => perform(async () => {
  await refresh();
  notify("Local model list refreshed.");
}));
$("disconnect").addEventListener("click", () => {
  if (busy) return;
  operatorToken = "";
  $("refresh").disabled = true;
  $("disconnect").disabled = true;
  $("available-count").textContent = "—";
  $("registry-count").textContent = "—";
  empty("available-list", "Connect to inspect installed models.");
  empty("registry-list", "Registered models appear here.");
  notify("Disconnected. The operator token was not stored.");
});
