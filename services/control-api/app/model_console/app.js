"use strict";

// No third-party dependencies, analytics, cookies or persistent token storage.
const api = "/api/v1/local";
const $ = id => document.getElementById(id);
let operatorToken = "";
let busy = false;

const METRIC_FIELDS = Object.freeze({
  "metric-attempts": v => v.inference.admitted_attempts_total,
  "metric-complete": v => v.inference.nonstream_complete_total,
  "metric-active": v => v.inference.active_attempts,
  "metric-queue": v => v.admission.waiting_requests,
  "metric-rejected": v => v.admission.rejected_full_total,
  "metric-timeouts": v => v.admission.queue_timeout_total,
  "metric-failures": v => v.inference.pre_response_failure_total,
  "metric-duration": v => v.inference.mean_post_admission_duration_ms === null
    ? "—" : v.inference.mean_post_admission_duration_ms + " ms",
  "metric-upstream": v => v.upstream.failure_events_total
});

function clearMonitoring(message) {
  Object.keys(METRIC_FIELDS).forEach(id => { $(id).textContent = "—"; });
  $("monitor-state").textContent = message;
  $("monitor-time").textContent = "";
}

// Monitoring is independent of model discovery: a stopped gateway must not
// prevent model management or discard the operator's in-memory credentials.
async function refreshMonitoring() {
  if (!operatorToken) {
    clearMonitoring("Connect to view gateway metrics.");
    return;
  }
  const sessionToken = operatorToken;
  try {
    const values = await call("/monitoring");
    if (sessionToken !== operatorToken) return;
    if (values.schema_version !== 1 || values.process_local_counters !== true ||
        values.source !== "local_gateway_8081") {
      throw new Error("Unknown monitoring protocol");
    }
    for (const [id, getter] of Object.entries(METRIC_FIELDS)) {
      const reading = getter(values);
      if (typeof reading !== "number" && reading !== "—" && typeof reading !== "string") {
        throw new Error("Missing gateway metric");
      }
      $(id).textContent = String(reading);
    }
    $("monitor-state").textContent = "● Gateway metrics available";
    $("monitor-time").textContent = "Observed " + values.observed_at_utc;
  } catch (_) {
    if (sessionToken !== operatorToken) return;
    clearMonitoring("Gateway metrics unavailable — check localhost:8081 and refresh.");
  }
}

const promptDrafts = new Map(); // transient only; no browser storage

function notify(message, isError = false) {
  $("notice").textContent = String(message);
  $("notice").className = "notice" + (isError ? " error" : "");
}

function setBusy(value) {
  busy = value;
  $("refresh").disabled = value || !operatorToken;
  $("refresh-monitoring").disabled = value || !operatorToken;
  $("deployment-create").disabled = value || !operatorToken ||
    $("deployment-model").options.length === 0;
  $("deployment-name").disabled = value;
  $("deployment-model").disabled = value;
  $("connect-button").disabled = value;
  $("disconnect").disabled = value || !operatorToken;
  document.querySelectorAll(".model-action").forEach(node => { node.disabled = value || node.dataset.locked === "true"; });
}

async function call(path, method = "GET", payload = null) {
  if (!operatorToken) throw new Error("Connect with your local operator token first.");
  const controller = new AbortController();
  // Model loading and chat can legitimately take longer than a health/status
  // GET. Go's local relay waits up to 125s for inference; keep a bounded
  // browser margin rather than aborting a valid 10s+ generation.
  const longRunning = method === "POST" &&
    (path.endsWith("/load") || path.endsWith("/chat"));
  const wait = longRunning ? 130000 : 10000;
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

function render(available, registered, statuses) {
  const readiness = new Map(statuses.map(m => [m.tag, m]));
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
          await refresh();
          notify("Model loaded and digest verified: " + model.tag + ". You can now run inference.");
        }, "primary"));
        controls.push(action("Unload", async () => {
          const result = await call("/models/" + encodeURIComponent(model.tag) + "/unload", "POST");
          await refresh();
          notify(result.already_unloaded ? "Model was already unloaded." : "Model unloaded: " + model.tag);
        }));
      }
      controls.push(action("Status", async () => {
        const s = await call("/models/" + encodeURIComponent(model.tag) + "/status");
        await refresh();
        notify(model.tag + ": " + (s.loaded ? "loaded in Ollama memory" : "not loaded") +
          (s.digest_matches_loaded ? "" : " (digest mismatch)"));
      }));
      const card = modelCard(model, controls,
        model.approved ? "Approved revision" : "Pending operator approval");
      if (model.approved) {
        const status = readiness.get(model.tag);
        const loaded = Boolean(status && status.loaded && status.digest_matches_loaded);
        const stateLine = document.createElement("p");
        stateLine.className = "load-state " + (loaded ? "ready" : "waiting");
        stateLine.textContent = loaded
          ? "● Loaded and ready for inference"
          : status && !status.digest_matches_loaded
            ? "● Loaded model revision mismatch — inference blocked"
            : "● Not loaded — click Load before running inference";
        card.append(stateLine);
        const playground = document.createElement("div");
        playground.className = "playground";
        const label = document.createElement("label");
        label.textContent = "Single-turn inference test · local Go gateway";
        const input = document.createElement("textarea");
        input.rows = 2;
        input.maxLength = 4096;
        input.placeholder = "Enter a short local prompt…";
        input.setAttribute("aria-label", "Prompt for " + model.tag);
        input.value = promptDrafts.get(model.tag) || "";
        input.addEventListener("input", () => { promptDrafts.set(model.tag, input.value); });
        const output = document.createElement("pre");
        output.className = "inference-output";
        output.hidden = true;
        const runButton = action("Run inference", async () => {
          const prompt = input.value.trim();
          if (!prompt) throw new Error("Enter a nonempty prompt first.");
          // Status may expire since the last UI refresh; never rely on stale readiness.
          const current = await call("/models/" + encodeURIComponent(model.tag) + "/status");
          if (!current.loaded) {
            await refresh();
            notify(model.tag + " is not loaded. Click Load, wait for confirmation, then run inference.", true);
            return;
          }
          output.hidden = true;
          notify("Sending an approved single-turn inference request to the local gateway…");
          let result;
          try {
            result = await call("/models/" + encodeURIComponent(model.tag) + "/chat",
                                "POST", {prompt: prompt, max_tokens: 128});
          } catch (error) {
            if (error instanceof Error && error.message.includes("approved model revision is not loaded")) {
              await refresh();
              throw new Error(model.tag + " was unloaded before inference. Click Load and try again.");
            }
            throw error;
          }
          if (typeof result.content !== "string") throw new Error("Invalid local inference response.");
          output.textContent = result.content;
          output.hidden = false;
          notify("Received local inference response from " + model.tag + ".");
          await refreshMonitoring();
        }, "primary");
        if (!loaded) {
          runButton.dataset.locked = "true";
          runButton.disabled = true;
          runButton.textContent = "Load model first";
        }
        playground.append(label, input, runButton, output);
        card.append(playground);
      }
      list.append(card);
    }
  }
}


function renderDeployments(rows, registered, statuses) {
  $("deployment-count").textContent = String(rows.length);
  const ready = new Set(statuses.filter(s => s.approved && s.loaded &&
    s.digest_matches_loaded).map(s => s.tag));
  const candidates = registered.filter(m => m.approved && ready.has(m.tag));
  const createSelect = $("deployment-model");
  const prior = createSelect.value;
  createSelect.replaceChildren();
  for (const model of candidates) {
    const option = document.createElement("option");
    option.value = model.tag;
    option.textContent = model.tag + " · " + model.digest.slice(0, 12);
    createSelect.append(option);
  }
  if (candidates.some(model => model.tag === prior)) createSelect.value = prior;
  $("deployment-create").disabled = busy || !operatorToken || candidates.length === 0;

  const list = $("deployment-list");
  list.replaceChildren();
  if (!rows.length) {
    empty("deployment-list", "No local deployment names yet. Load an approved model, then create one.");
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "model-card deployment-card";
    const heading = document.createElement("strong");
    heading.textContent = row.name + "  →  " + row.active_tag;
    const state = document.createElement("p");
    state.className = "load-state " + (row.ready ? "ready" : "waiting");
    state.textContent = row.ready ? "● Routing enabled · active model loaded"
      : row.enabled ? "● Model not ready — load or verify the active revision"
        : "● Deployment paused — no inference through this alias";
    const digest = document.createElement("code");
    digest.textContent = "Active SHA-256 " + row.active_digest.slice(0, 16) + "…";
    const former = document.createElement("p");
    former.className = "muted";
    former.textContent = row.previous_tag ?
      "Rollback revision: " + row.previous_tag : "No previous revision";
    const controls = document.createElement("div");
    controls.className = "model-actions";
    const prefix = "/deployments/" + encodeURIComponent(row.name);
    controls.append(action(row.enabled ? "Stop routing" : "Start routing", async () => {
      await call(prefix + (row.enabled ? "/stop" : "/start"), "POST");
      await refresh();
      notify(row.name + (row.enabled ? " routing stopped." : " routing enabled."));
    }, row.enabled ? "secondary" : "primary"));
    const rollback = action("Rollback", async () => {
      await call(prefix + "/rollback", "POST");
      await refresh();
      notify("Rolled back " + row.name + " to its previous approved, loaded revision.");
    });
    rollback.disabled = !row.previous_tag;
    if (!row.previous_tag) rollback.dataset.locked = "true";
    controls.append(rollback);
    const promotePanel = document.createElement("div");
    promotePanel.className = "promotion-control";
    const label = document.createElement("label");
    label.textContent = "Promote another approved, loaded model";
    const chooser = document.createElement("select");
    chooser.setAttribute("aria-label", "Candidate revision for " + row.name);
    for (const candidate of candidates.filter(m => m.tag !== row.active_tag)) {
      const option = document.createElement("option");
      option.value = candidate.tag;
      option.textContent = candidate.tag;
      chooser.append(option);
    }
    const promote = action("Promote", async () => {
      await call(prefix + "/promote", "POST", {tag: chooser.value});
      await refresh();
      notify("Deployment " + row.name + " now routes to " + chooser.value + ".");
    });
    if (!chooser.options.length) {
      promote.disabled = true;
      promote.dataset.locked = "true";
    }
    promotePanel.append(label, chooser, promote);

    const playground = document.createElement("div");
    playground.className = "playground";
    const promptLabel = document.createElement("label");
    promptLabel.textContent = "Inference via stable deployment name";
    const input = document.createElement("textarea");
    input.maxLength = 4096;
    input.rows = 2;
    input.placeholder = "Prompt sent to the current active model…";
    input.setAttribute("aria-label", "Deployment prompt " + row.name);
    const draftKey = "deployment:" + row.name;
    input.value = promptDrafts.get(draftKey) || "";
    input.addEventListener("input", () => { promptDrafts.set(draftKey, input.value); });
    const output = document.createElement("pre");
    output.className = "inference-output";
    output.hidden = true;
    const run = action("Run deployment inference", async () => {
      const prompt = input.value.trim();
      if (!prompt) throw new Error("Enter a prompt.");
      if (!row.ready) throw new Error("Load the approved active model and enable routing first.");
      const result = await call(prefix + "/chat", "POST", {prompt, max_tokens: 128});
      if (typeof result.content !== "string") throw new Error("Invalid inference response.");
      output.textContent = result.content;
      output.hidden = false;
      notify("Deployment " + row.name + " responded using " + result.tag + ".");
      await refreshMonitoring();
    }, "primary");
    if (!row.ready) {
      run.disabled = true;
      run.dataset.locked = "true";
    }
    playground.append(promptLabel, input, run, output);
    card.append(heading, state, digest, former, controls, promotePanel, playground);
    list.append(card);
  }
}

async function refresh() {
  const [available, registered, readiness, deployments] = await Promise.all([
    call("/available-models"), call("/models"), call("/model-statuses"),
    call("/deployments")
  ]);
  if (!Array.isArray(available.models) || !Array.isArray(registered.models) ||
      !Array.isArray(readiness.models) || !Array.isArray(deployments.deployments)) {
    throw new Error("Unexpected registry API response");
  }
  render(available.models, registered.models, readiness.models);
  renderDeployments(deployments.deployments, registered.models, readiness.models);
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
      await refreshMonitoring();
      notify("Connected to local operator API. Registry synchronized.");
    } catch (error) {
      operatorToken = "";
      clearMonitoring("Connect to view gateway metrics.");
      $("deployment-count").textContent = "—";
      empty("deployment-list", "Connect to view local deployment routes.");
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
  await refreshMonitoring();
  notify("Local model list refreshed.");
}));
$("refresh-monitoring").addEventListener("click", () => perform(async () => {
  await refreshMonitoring();
}));
$("deployment-form").addEventListener("submit", event => {
  event.preventDefault();
  perform(async () => {
    const name = $("deployment-name").value.trim();
    const tag = $("deployment-model").value;
    if (!tag) throw new Error("Load an approved model before creating a deployment.");
    await call("/deployments", "POST", {name, tag});
    $("deployment-name").value = "";
    await refresh();
    notify("Created stable local deployment " + name + " → " + tag + ".");
  });
});
// Periodic read-only refresh stops automatically while disconnected/hidden/busy.
setInterval(() => {
  if (operatorToken && !busy && !document.hidden) {
    void refreshMonitoring();
  }
}, 15000);
$("disconnect").addEventListener("click", () => {
  if (busy) return;
  operatorToken = "";
  promptDrafts.clear();
  $("refresh").disabled = true;
  $("refresh-monitoring").disabled = true;
  $("disconnect").disabled = true;
  clearMonitoring("Disconnected. Connect to view gateway metrics.");
  $("deployment-count").textContent = "—";
  $("deployment-model").replaceChildren();
  $("deployment-create").disabled = true;
  empty("deployment-list", "Connect to view local deployment routes.");
  $("available-count").textContent = "—";
  $("registry-count").textContent = "—";
  empty("available-list", "Connect to inspect installed models.");
  empty("registry-list", "Registered models appear here.");
  notify("Disconnected. The operator token was not stored.");
});
