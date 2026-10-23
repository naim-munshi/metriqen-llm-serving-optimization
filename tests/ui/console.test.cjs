"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../../services/inference-gateway/internal/httpapi/console/app.js"), "utf8");
function usage(overrides={}) {
  return {
    schema_version: 2, classified_terminal_attempts_total: 4,
    nonstream_provider_plausible_total: 2, nonstream_provider_missing_total: 1,
    nonstream_provider_malformed_total: 0, nonstream_provider_inconsistent_total: 0,
    nonstream_provider_above_cap_total: 0, nonstream_no_completion_total: 0,
    stream_not_measured_total: 0, stream_provider_plausible_total: 1,
    stream_provider_missing_total: 0, stream_provider_malformed_total: 0,
    stream_provider_inconsistent_total: 0, stream_provider_above_cap_total: 0,
    stream_incomplete_total: 0, provider_prompt_tokens_reported_total: 10,
    provider_output_tokens_reported_total: 5, provider_all_tokens_reported_total: 15,
    ...overrides
  };
}
function fixture() {
  const nodes = new Map();
  const events = new Map();
  const calls = [];
  const data = {
    "/health/ready": {status: "ready"},
    "/health/upstream": {status: "reachable"},
    "/metrics/admission": {active_requests: 0, max_active_requests: 4},
    "/metrics/inference": {attempts_total: 0},
    "/metrics/upstream": {},
    "/metrics/usage": usage()
  };
  const element = id => {
    if (!nodes.has(id)) nodes.set(id,{
      textContent: "", className: "", disabled: false, style: {}, attributes: {},
      setAttribute(name,value){this.attributes[name]=value;},
      addEventListener(name,fn){events.set(id+":"+name,fn);}
    });
    return nodes.get(id);
  };
  const document = {
    visibilityState: "visible", getElementById: element,
    addEventListener(name,fn){events.set("document:"+name,fn);}
  };
  const fetch = async (url, options) => {
    calls.push({url,options});
    assert.ok(url.startsWith("/health/") || url.startsWith("/metrics/"));
    assert.equal(options.credentials,"omit");
    if (!(url in data)) throw new Error("simulated unavailable");
    const status = url === "/health/upstream" && data[url].status !== "reachable" ? 503 : 200;
    return {ok:status===200,status,json:async()=>data[url]};
  };
  const context=vm.createContext({
    document,fetch,AbortController,Date,
    setTimeout(){return 1;},clearTimeout(){},
    setInterval(fn,interval){assert.equal(interval,5000);events.set("poll",fn);}
  });
  vm.runInContext(script,context,{timeout:2000});
  return {element,events,calls,data,context,
    render:x=>vm.runInContext("showUsage("+JSON.stringify(x)+")",context),
    refresh:()=>vm.runInContext("refresh()",context),
    flush:()=>new Promise(done=>setImmediate(done))
  };
}
test("valid provider telemetry and same-origin requests",async()=>{
  const app=fixture();await app.flush();app.render(usage());
  assert.equal(app.element("usage-total").textContent,"15");
  assert.equal(app.element("usage-prompt").textContent,"10");
  assert.match(app.element("usage-state").textContent,/not verified billing/);
  assert.equal(app.calls.length,6);
  assert.equal(app.calls.some(x=>x.url.includes("chat/completions")),false);
});
test("inconsistent usage observations fail closed instead of showing stale values",async()=>{
  const app=fixture();await app.flush();
  for(const item of [
    usage({schema_version:1}),
    usage({classified_terminal_attempts_total:3}),
    usage({stream_not_measured_total:2}),
    usage({provider_all_tokens_reported_total:14}),
    usage({provider_prompt_tokens_reported_total:Number.MAX_SAFE_INTEGER}),
    usage({stream_incomplete_total:null})
  ]) {
    app.render(usage());
    app.render(item);
    assert.equal(app.element("usage-total").textContent,"—");
  }
});

test("upstream and telemetry outages recover on a later refresh",async()=>{
  const app=fixture();await app.flush();
  app.data["/health/upstream"]={status:"unreachable"};
  delete app.data["/metrics/usage"];
  await app.refresh();
  assert.equal(app.element("upstream-label").textContent,"Unavailable");
  assert.equal(app.element("usage-total").textContent,"—");
  assert.equal(app.element("data-state").textContent,
    "Gateway responding · upstream unavailable");
  app.data["/health/upstream"]={status:"reachable"};
  app.data["/metrics/usage"]=usage();
  await app.refresh();
  assert.equal(app.element("upstream-label").textContent,"Reachable");
  assert.equal(app.element("usage-total").textContent,"15");
  assert.equal(app.element("data-state").textContent,"Gateway & upstream responding");
});
test("hidden dashboard never polls or consumes inference slots",async()=>{
  const app=fixture();await app.flush();
  const start=app.calls.length;
  app.context.document.visibilityState="hidden";
  app.events.get("poll")();
  app.events.get("document:visibilitychange")();
  await app.flush();
  assert.equal(app.calls.length,start);
  app.context.document.visibilityState="visible";
  app.events.get("poll")();
  await app.flush();
  assert.equal(app.calls.length,start+6);
  assert.equal(app.element("refresh").disabled,false);
  assert.equal(app.calls.some(x=>x.url.includes("chat")),false);
});
test("missing report means unknown, while a measured missing stream remains explicit",async()=>{
  const app=fixture();await app.flush();
  app.render(usage({classified_terminal_attempts_total:6,
    stream_not_measured_total:2,stream_provider_missing_total:1,
    stream_incomplete_total:1}));
  assert.equal(app.element("usage-unmeasured").textContent,"2");
  app.render(null);
  assert.equal(app.element("usage-unmeasured").textContent,"—");
  assert.equal(app.element("usage-total").textContent,"—");
});

test("manual pause blocks interval and visibility refresh but keeps explicit refresh available",async()=>{
  const app=fixture();await app.flush();
  const toggle=app.events.get("auto-refresh-toggle:click");
  assert.equal(typeof toggle,"function");
  const original=app.calls.length;
  toggle();
  assert.equal(app.element("auto-refresh-toggle").attributes["aria-pressed"],"true");
  assert.equal(app.element("auto-refresh-toggle").textContent,"Resume auto");
  assert.equal(app.element("auto-refresh-toggle").attributes["aria-label"],"Resume automatic metrics refresh");
  app.events.get("poll")();
  app.events.get("document:visibilitychange")();
  await app.flush();
  assert.equal(app.calls.length,original,"paused dashboard still polled");
  app.events.get("refresh:click")();
  await app.flush();
  assert.equal(app.calls.length,original+6,"explicit read-only Refresh must work while paused");
  toggle();
  assert.equal(app.element("auto-refresh-toggle").attributes["aria-pressed"],"false");
  assert.equal(app.element("auto-refresh-toggle").textContent,"Pause auto");
  app.events.get("poll")();
  await app.flush();
  assert.equal(app.calls.length,original+12,"resumed dashboard did not poll");
  assert.equal(app.calls.some(x=>x.url.includes("chat/completions")),false);
});

test("identical automatic health observations do not reannounce the same live status",async()=>{
  const app=fixture();await app.flush();
  const region=app.element("data-state");
  let writes=0;
  let old=region.textContent;
  Object.defineProperty(region,"textContent",{
    configurable:true,
    get(){return old;},
    set(value){writes++;old=value;}
  });
  await app.refresh();
  assert.equal(writes,0,"identical periodic health summary caused repeat live announcement");
  app.data["/health/upstream"]={status:"unreachable"};
  await app.refresh();
  assert.equal(writes,1,"changed health should announce exactly once");
  await app.refresh();
  assert.equal(writes,1,"unchanged degraded health should not repeat announcement");
  app.data["/health/upstream"]={status:"reachable"};
  await app.refresh();
  assert.equal(writes,2,"recovery must be announced");
});
