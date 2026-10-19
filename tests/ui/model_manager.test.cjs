"use strict";
// Executable local Model Manager browser request timeout & credential contract.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const code = fs.readFileSync(path.join(__dirname,
  "../../services/control-api/app/model_console/app.js"),"utf8");

function fixture() {
  const waits=[], requests=[];
  const fakeDOM = {
    hidden:false,
    getElementById() {
      return {
        addEventListener() {},
        replaceChildren() {},
        options: [],
        disabled: false, textContent:"", value:"", className:"",
      };
    },
  };
  const ctx = vm.createContext({
    document:fakeDOM,
    AbortController,
    setTimeout(fn,delay) { waits.push(delay); return 1; },
    clearTimeout() {},
    setInterval(){},
    fetch: async (url, options)=>{
      requests.push({url,options});
      return {ok:true,status:200,json:async()=>({})};
    },
  });
  vm.runInContext(code,ctx,{timeout:1500});
  vm.runInContext('operatorToken="synthetic-runtime-test-only-token";',ctx);
  return {waits,requests,ctx, call:(uri,method,payload)=>vm.runInContext(
    "call("+JSON.stringify(uri)+","+JSON.stringify(method)+","+
      JSON.stringify(payload)+")",ctx)};
}

test("model and deployment chat use real local inference timeout, not short status timeout",
  async()=>{
    const ui=fixture();
    await ui.call("/models/llama3.2%3A1b/chat","POST",{prompt:"test"});
    await ui.call("/deployments/assistant/chat","POST",{prompt:"test"});
    await ui.call("/models/llama3.2%3A1b/load","POST",{});
    await ui.call("/deployments/assistant/stop","POST",{});
    await ui.call("/deployments","GET",null);
    assert.deepEqual(ui.waits,[130000,130000,130000,10000,10000]);
    assert.deepEqual(ui.requests.map(x=>x.url),[
      "/api/v1/local/models/llama3.2%3A1b/chat",
      "/api/v1/local/deployments/assistant/chat",
      "/api/v1/local/models/llama3.2%3A1b/load",
      "/api/v1/local/deployments/assistant/stop",
      "/api/v1/local/deployments",
    ]);
    for (const item of ui.requests) {
      assert.equal(item.options.credentials,"omit");
      assert.equal(item.options.redirect,"error");
      assert.equal(item.options.cache,"no-store");
      assert.equal(item.options.headers.Authorization,
        "Bearer synthetic-runtime-test-only-token");
    }
  });

test("missing operator token refuses all network requests", async()=>{
  const ui=fixture();
  vm.runInContext('operatorToken="";',ui.ctx);
  await assert.rejects(ui.call("/deployments/assistant/chat","POST",
    {prompt:"test"}),/Connect with your local operator token first/);
  assert.equal(ui.requests.length,0);
  assert.equal(ui.waits.length,0);
});
