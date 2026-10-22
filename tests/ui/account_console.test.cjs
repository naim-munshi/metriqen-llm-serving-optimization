"use strict";
// Executable mocked-DOM regression for the optional local account console.
// Not a substitute for real Safari/mobile/OIDC or live PostgreSQL acceptance.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname,
  "../../services/control-api/app/account_console/app.js"), "utf8");

class Element {
  constructor(tag="div") {
    this.tagName = tag;
    this.children = [];
    this.handlers = {};
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.attributes = {};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener(type,fn) { this.handlers[type] = fn; }
  setAttribute(name,value) { this.attributes[name] = value; }
}

function hasVisibleText(node, fragment) {
  return node.textContent.includes(fragment)
    || node.children.some(child => child instanceof Element
      && hasVisibleText(child, fragment));
}

function fakeBrowser() {
  const byId = new Map();
  const requests = [];
  const document = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id,new Element());
      return byId.get(id);
    },
    createElement(tag) { return new Element(tag); },
    querySelectorAll() { return []; },
  };
  const org = {id:"11111111-1111-4111-8111-111111111111",
    name:"Owner workspace",role:"owner"};
  const responses = {
    "/auth/session": {authenticated:false},
    "/auth/organizations": {organizations:[org]},
    ["/auth/organizations/"+org.id+"/members"]: {members:[]},
    ["/auth/organizations/"+org.id+"/projects"]: {projects:[]},
    ["/auth/organizations/"+org.id+"/invitations"]: {invitations:[]},
    ["/auth/organizations/"+org.id+"/audit"]: {events:[]},
  };
  const fetch = async (url,options) => {
    requests.push(url);
    assert.equal(options.credentials,"same-origin");
    assert.equal(options.redirect,"error");
    assert.equal(options.cache,"no-store");
    assert.ok(Object.hasOwn(responses,url), "Unexpected account request: "+url);
    return {ok:true,status:200,json:async()=>responses[url]};
  };
  const ctx = vm.createContext({document,fetch});
  vm.runInContext(source,ctx,{timeout:1500});
  vm.runInContext('csrf="synthetic-account-test-csrf";',ctx);
  return {byId,requests,ctx};
}

test("View projects cannot detach the visible owner Manage team panel",async()=>{
  const browser=fakeBrowser();
  await vm.runInContext("refreshWorkspaces()",browser.ctx);

  const list=browser.byId.get("workspace-list");
  assert.equal(list.children.length,1);
  const card=list.children[0];
  const heading=card.children[0];
  const view=heading.children.find(n=>n.textContent==="View projects");
  const manage=heading.children.find(n=>n.textContent==="Manage team");
  assert.ok(view && manage);

  await manage.handlers.click();
  assert.equal(hasVisibleText(card,"Members of Owner workspace"),true);

  await view.handlers.click();
  assert.equal(hasVisibleText(card,"Create project in Owner workspace"),true);
  assert.equal(hasVisibleText(card,"Members of Owner workspace"),true,
    "Viewing projects must preserve the previously loaded team panel");

  await manage.handlers.click();
  assert.equal(hasVisibleText(card,"Members of Owner workspace"),true);
  assert.ok(browser.requests.some(x=>x.endsWith("/projects")));
  assert.ok(browser.requests.some(x=>x.endsWith("/members")));
  assert.equal(browser.requests.some(x=>x.includes("chat/completions")),false);
});

