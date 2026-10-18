"use strict";

// Intentionally no framework, CDN, third-party analytics, local storage,
// model prompts, or unsafe HTML injection. All values are fixed-key numeric
// aggregates returned by the existing same-origin loopback gateway.
const paths = Object.freeze({
  gateway: "/health/ready",
  upstream: "/health/upstream",
  admission: "/metrics/admission",
  inference: "/metrics/inference",
  failures: "/metrics/upstream",
  usage: "/metrics/usage"
});
const $ = id => document.getElementById(id);
const text = (id, value) => {
  const node = $(id);
  if (node) node.textContent = String(value);
};
const number = v => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0)
  ? v.toLocaleString("en-US") : "—";
const metric = (id, obj, key) => text(id, number(obj?.[key]));
const fraction = (part, total) => (Number.isFinite(part) && Number.isFinite(total) && total > 0)
  ? Math.max(0, Math.min(100, part / total * 100)) : 0;
function bar(id, part, total) {
  const node = $(id);
  if (node) {
    node.style.width = fraction(part, total) + "%";
    node.setAttribute("aria-label", number(part) + " of " + number(total));
  }
}
function badge(id, value, healthy) {
  const node = $(id);
  if (!node) return;
  node.textContent = value;
  node.className = "pill " + (healthy === true ? "positive" : healthy === false ? "negative" : "neutral");
}
function indicator(value, kind) {
  text("data-state", value);
  const dot = $("data-dot");
  if (dot) dot.className = "live-indicator " + kind;
}
function health(id, badgeId, result, healthyName) {
  if (!result.ok) {
    text(id, "Unavailable");
    badge(badgeId, "Probe failed", false);
    return;
  }
  const status = result.value?.status;
  if (status === healthyName) {
    text(id, healthyName === "ready" ? "Operational" : "Reachable");
    badge(badgeId, "Healthy", true);
  } else if (status === "disabled") {
    text(id, "Disabled");
    badge(badgeId, "Not configured", null);
  } else {
    text(id, "Unavailable");
    badge(badgeId, "Unhealthy", false);
  }
}
async function snapshot(url, parentSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  parentSignal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(url, {
      cache: "no-store", credentials: "omit", redirect: "error",
      headers: { Accept: "application/json" }, signal: controller.signal
    });
    const body = await response.json();
    // Upstream-health 503 is an expected, meaningful JSON diagnostic.
    return { ok: response.ok || (url === paths.upstream && response.status === 503),
             value: body };
  } catch (_) {
    return { ok: false };
  } finally {
    clearTimeout(timer);
    parentSignal.removeEventListener("abort", abort);
  }
}
function showAdmission(data) {
  metric("rejected", data, "rejected_full_total");
  metric("active", data, "active_requests");
  metric("waiting", data, "waiting_requests");
  metric("max-active", data, "max_active_requests");
  metric("max-waiting", data, "max_waiting_requests");
  metric("immediate", data, "admitted_immediate_total");
  metric("queued", data, "admitted_queued_total");
  metric("queue-timeouts", data, "queue_timeout_total");
  bar("active-bar", data?.active_requests, data?.max_active_requests);
  bar("waiting-bar", data?.waiting_requests, data?.max_waiting_requests);
}
function showInference(data) {
  metric("attempts", data, "attempts_total");
  metric("nonstream", data, "nonstream_complete_total");
  metric("stream-done", data, "stream_done_total");
  metric("stream-missing", data, "stream_missing_done_total");
  metric("pre-fail", data, "pre_response_failure_total");
  metric("active-attempts", data, "active_attempts");
  metric("interrupted", data, "post_response_interrupted_total");
  metric("cancelled", data, "client_canceled_total");
  metric("duration-observations", data, "duration_observations_total");
  const observations = data?.duration_observations_total;
  const totalUs = data?.duration_total_us;
  const valid = Number.isSafeInteger(observations) && observations > 0
    && Number.isSafeInteger(totalUs) && totalUs >= 0;
  text("duration-mean", valid ? (totalUs / observations / 1000).toFixed(1) : "—");
}
function showFailures(data) {
  const keys = {
    refused: "connection_refused_total", transport: "transport_errors_total",
    timeouts: "timeouts_total", "http-rejected": "http_rejected_total",
    "http-other": "http_other_status_total", protocol: "protocol_errors_total",
    "too-large": "response_too_large_total", "stream-read": "stream_read_errors_total",
    "stream-limit": "stream_size_limit_total"
  };
  for (const [id, key] of Object.entries(keys)) metric(id, data, key);
}
// Untrusted provider reports: only fixed, nonnegative safe integer aggregates
// are rendered. Do not infer actual generated-token usage or per-project billing.
function showUsage(data) {
  const keys = [
    ["usage-prompt", "provider_prompt_tokens_reported_total"],
    ["usage-output", "provider_output_tokens_reported_total"],
    ["usage-total", "provider_all_tokens_reported_total"],
    ["usage-nonstream", "nonstream_provider_plausible_total"],
    ["usage-stream", "stream_provider_plausible_total"],
    ["usage-unmeasured", "stream_not_measured_total"]
  ];
  const partitions = [
    "nonstream_provider_missing_total", "nonstream_provider_malformed_total",
    "nonstream_provider_inconsistent_total", "nonstream_provider_above_cap_total",
    "nonstream_no_completion_total", "stream_provider_missing_total",
    "stream_provider_malformed_total", "stream_provider_inconsistent_total",
    "stream_provider_above_cap_total", "stream_incomplete_total"
  ];
  const safe = value => Number.isSafeInteger(value) && value >= 0;
  const valid = data?.schema_version === 2 && [...keys.map(([, key]) => key),
    ...partitions, "classified_terminal_attempts_total"].every(key => safe(data[key]));
  // Validate *all* partition counters and safe integer arithmetic before
  // presenting provider claims. Missing fields and >2^53 totals are UNKNOWN,
  // never zero. A malicious/incompatible upstream must fail closed in UI.
  const sum = values => values.every(safe) && Number.isSafeInteger(
    values.reduce((acc, value) => acc + value, 0))
    ? values.reduce((acc, value) => acc + value, 0) : null;
  const expectedUnmeasured = valid ? sum([
    data.stream_provider_missing_total, data.stream_provider_malformed_total,
    data.stream_provider_inconsistent_total, data.stream_provider_above_cap_total,
    data.stream_incomplete_total]) : null;
  const expectedClassified = valid ? sum([
    data.nonstream_provider_plausible_total,
    data.nonstream_provider_missing_total, data.nonstream_provider_malformed_total,
    data.nonstream_provider_inconsistent_total,
    data.nonstream_provider_above_cap_total, data.nonstream_no_completion_total,
    data.stream_provider_plausible_total, data.stream_not_measured_total]) : null;
  const totalReported = valid ? sum([
    data.provider_prompt_tokens_reported_total,
    data.provider_output_tokens_reported_total]) : null;
  const consistent = valid && expectedUnmeasured !== null
    && expectedClassified !== null && totalReported !== null
    && expectedUnmeasured === data.stream_not_measured_total
    && expectedClassified === data.classified_terminal_attempts_total
    && totalReported === data.provider_all_tokens_reported_total;
  for (const [id, key] of keys) metric(id, consistent ? data : null, key);
  text("usage-state", consistent
    ? "Provider-reported aggregates · local process · not verified billing"
    : "Provider usage unavailable or unsupported schema");
}
let inFlight = null;
async function refresh() {
  if (inFlight) return;
  const controller = new AbortController();
  inFlight = controller;
  $("refresh").disabled = true;
  try {
    const [gateway, upstream, admission, inference, failures, usage] =
      await Promise.all(Object.values(paths).map(p => snapshot(p, controller.signal)));
    health("gateway-label", "gateway-badge", gateway, "ready");
    health("upstream-label", "upstream-badge", upstream, "reachable");
    showAdmission(admission.ok ? admission.value : null);
    showInference(inference.ok ? inference.value : null);
    showFailures(failures.ok ? failures.value : null);
    showUsage(usage.ok ? usage.value : null);
    const available = [gateway, admission, inference, failures].every(item => item.ok);
    if (!available) {
      indicator("Some diagnostics unavailable", "bad");
    } else if (upstream.value?.status === "reachable" && upstream.ok) {
      indicator("Gateway & upstream responding", "good");
    } else {
      indicator("Gateway responding · upstream unavailable", "pending");
    }
    text("last-sample", new Date().toLocaleTimeString());
  } catch (_) {
    indicator("Metrics temporarily unavailable", "bad");
  } finally {
    inFlight = null;
    $("refresh").disabled = false;
  }
}
$("refresh").addEventListener("click", refresh);
void refresh();
// Only poll while visible; no background processes, external telemetry,
// automatic Ollama restarts or inference requests.
setInterval(() => {
  if (document.visibilityState === "visible") void refresh();
}, 5000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void refresh();
});
