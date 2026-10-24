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

function fakeBrowser(role="owner") {
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
    name:"Owner workspace",role};
  const responses = {
    "/auth/session": {authenticated:false},
    "/auth/logout": null,
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
    const payload = responses[url];
    const status = payload && payload._status || (url==="/auth/logout" ? 204 : 200);
    return {ok:status<400,status,json:async()=>payload};
  };
  const ctx = vm.createContext({document,fetch});
  vm.runInContext(source,ctx,{timeout:1500});
  vm.runInContext('csrf="synthetic-account-test-csrf";',ctx);
  return {byId,requests,ctx,responses,org};
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


test("non-creator roles can view projects but never see Create project controls",async()=>{
  for (const role of ["viewer","developer","ml_engineer",
                      "deployment_operator","auditor"]) {
    const browser=fakeBrowser(role);
    await vm.runInContext("refreshWorkspaces()",browser.ctx);
    const card=browser.byId.get("workspace-list").children[0];
    const heading=card.children[0];
    assert.equal(heading.children.some(node=>node.textContent==="Manage team"),false);
    const view=heading.children.find(node=>node.textContent==="View projects");
    assert.ok(view);
    await view.handlers.click();
    assert.equal(hasVisibleText(card,"Create project in Owner workspace"),false,
      "Unauthorized UI control exposed to "+role);
    assert.equal(browser.requests.some(url=>url.includes("chat/completions")),false);
  }
});

test("owner and admin retain the project creation form",async()=>{
  for (const role of ["owner","admin"]) {
    const browser=fakeBrowser(role);
    await vm.runInContext("refreshWorkspaces()",browser.ctx);
    const card=browser.byId.get("workspace-list").children[0];
    const view=card.children[0].children.find(x=>x.textContent==="View projects");
    await view.handlers.click();
    assert.equal(hasVisibleText(card,"Create project in Owner workspace"),true,
      "Creator UI missing for "+role);
  }
});

test("a revoked session 401 immediately erases previous subject, drafts and workspace data",async()=>{
  const browser=fakeBrowser();
  const get=id=>browser.ctx.document.getElementById(id);
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext('csrf="synthetic-account-test-csrf";hasProfile=true;',browser.ctx);
  get("guest").hidden=true;
  get("member").hidden=false;
  get("subject").textContent="SENSITIVE_SUBJECT";
  get("issuer").textContent="SENSITIVE_ISSUER";
  get("display-name").value="SENSITIVE_NAME";
  get("organization-name").value="SENSITIVE_ORG";
  get("workspace-list").append(new Element());
  get("pending-invitations").append(new Element());
  browser.responses["/auth/organizations"]={_status:401,detail:"no active browser session"};

  await assert.rejects(
    vm.runInContext("refreshWorkspaces()",browser.ctx),
    /401: no active browser session/,
  );
  assert.equal(vm.runInContext("csrf",browser.ctx),"");
  assert.equal(vm.runInContext("hasProfile",browser.ctx),false);
  assert.equal(get("member").hidden,true);
  assert.equal(get("guest").hidden,false);
  assert.equal(get("subject").textContent,"—");
  assert.equal(get("issuer").textContent,"—");
  assert.equal(get("display-name").value,"");
  assert.equal(get("organization-name").value,"");
  assert.equal(get("workspace-list").children.length,0);
  assert.equal(get("pending-invitations").children.length,0);
});

test("mobile/keyboard dynamic project control has a real associated label and ARIA state",async()=>{
  const browser=fakeBrowser();
  await vm.runInContext("refreshWorkspaces()",browser.ctx);
  const card=browser.byId.get("workspace-list").children[0];
  const heading=card.children[0];
  const view=heading.children.find(node=>node.textContent==="View projects");
  const manage=heading.children.find(node=>node.textContent==="Manage team");
  const details=card.children[1],team=card.children[2];
  assert.equal(view.attributes["aria-expanded"],"false");
  assert.equal(view.attributes["aria-controls"],details.id);
  assert.equal(manage.attributes["aria-expanded"],"false");
  assert.equal(manage.attributes["aria-controls"],team.id);
  await view.handlers.click();
  assert.equal(view.attributes["aria-expanded"],"true");
  const form=details.children.find(node=>node.tagName==="form");
  assert.ok(form);
  const label=form.children.find(node=>node.tagName==="label");
  const input=form.children.find(node=>node.tagName==="input");
  assert.equal(label.htmlFor,input.id);
  assert.match(input.id,/^project-name-[a-f0-9-]+$/);
  await manage.handlers.click();
  assert.equal(manage.attributes["aria-expanded"],"true");
});

test("successful sign-out clears sensitive identity and workspace DOM, not just hides it",async()=>{
  const browser=fakeBrowser();
  const get=id=>browser.ctx.document.getElementById(id);
  await new Promise(resolve=>setImmediate(resolve)); // finish unauthenticated boot
  vm.runInContext('csrf="synthetic-account-test-csrf";hasProfile=true;',browser.ctx);
  get("guest").hidden=true;
  get("member").hidden=false;
  get("subject").textContent="PRIVATE_OLD_SUBJECT";
  get("issuer").textContent="PRIVATE_OLD_ISSUER";
  get("display-name").value="PRIVATE_OLD_NAME";
  get("organization-name").value="PRIVATE_ORG_DRAFT";
  get("profile-note").textContent="PRIVATE_PROFILE_METADATA";
  get("workspace-note").textContent="PRIVATE_WORKSPACE_METADATA";
  get("save").textContent="Update profile";
  const secretNode=new Element();
  secretNode.textContent="PRIVATE_ORG_CONTENT";
  get("workspace-list").append(secretNode);
  const invitation=new Element();
  invitation.textContent="PRIVATE_INVITATION_SUBJECT";
  get("pending-invitations").append(invitation);

  await get("logout").handlers.click();
  assert.equal(vm.runInContext('csrf',browser.ctx),"");
  assert.equal(vm.runInContext('hasProfile',browser.ctx),false);
  assert.equal(get("member").hidden,true);
  assert.equal(get("guest").hidden,false);
  assert.equal(get("subject").textContent,"—");
  assert.equal(get("issuer").textContent,"—");
  for(const id of ["display-name","organization-name"]) {
    assert.equal(get(id).value,"");
  }
  for(const id of ["profile-note","workspace-note"]) {
    assert.equal(get(id).textContent,"");
  }
  for(const id of ["workspace-list","pending-invitations"]) {
    assert.equal(get(id).children.length,0);
  }
  assert.equal(get("save").textContent,"Save profile");
  assert.match(get("status").textContent,/Signed out/);
  assert.equal(browser.requests.filter(url=>url==="/auth/logout").length,1);
  assert.equal(browser.requests.some(url=>url.includes("chat/completions")),false);
});
