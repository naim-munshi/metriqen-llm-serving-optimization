"use strict";
// The browser does not persist profile data or authentication secrets.
// The browser holds only a CSRF value from its own same-origin session API.
const el = id => document.getElementById(id);
let csrf = "";
let hasProfile = false;
let busy = false;

function status(message, failure = false) {
  el("status").textContent = message;
  el("status").className = "notice" + (failure ? " error" : "");
}

function setBusy(value) {
  busy = value;
  el("save").disabled = value;
  el("logout").disabled = value;
  el("create-organization").disabled = value;
  el("refresh-workspaces").disabled = value;
  el("refresh-invitations").disabled = value;
  document.querySelectorAll(".workspace-action").forEach(b => { b.disabled = value; });
}

async function jsonRequest(path, method = "GET", payload = null, csrfRequired = false) {
  const headers = {"Accept": "application/json"};
  if (payload !== null) headers["Content-Type"] = "application/json";
  if (csrfRequired) headers["X-Metriqen-CSRF"] = csrf;
  const response = await fetch(path, {
    method,
    headers,
    body: payload === null ? undefined : JSON.stringify(payload),
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  });
  let body = null;
  if (response.status !== 204) {
    body = await response.json();
  }
  if (!response.ok) {
    const detail = body && typeof body.detail === "string"
      ? body.detail.slice(0, 180) : "Request rejected";
    const error = new Error(response.status + ": " + detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function boot() {
  try {
    const current = await jsonRequest("/auth/session");
    if (!current.authenticated) {
      el("guest").hidden = false;
      el("member").hidden = true;
      status("Not signed in. Use your configured identity provider to continue.");
      return;
    }
    if (typeof current.csrf !== "string" || current.csrf.length < 32) {
      throw new Error("Invalid browser session; sign in again.");
    }
    csrf = current.csrf;
    el("subject").textContent = current.subject;
    el("issuer").textContent = current.issuer;
    el("guest").hidden = true;
    el("member").hidden = false;
    status("Signed in with a verified identity.");
    await refreshInvitations().catch(error => {
      el("pending-invitations").replaceChildren(makeNode("p", "description",
        error instanceof Error ? error.message : "Invitations unavailable"));
    });
    await refreshWorkspaces().catch(error => {
      el("workspace-note").textContent =
        error instanceof Error ? error.message : "Workspace API unavailable";
    });
    try {
      const profile = await jsonRequest("/auth/profile");
      hasProfile = true;
      el("display-name").value = profile.display_name;
      el("profile-note").textContent = "Profile retrieved from PostgreSQL.";
      el("save").textContent = "Update profile";
    } catch (error) {
      if (error.status === 404) {
        hasProfile = false;
        el("profile-note").textContent = "No profile saved yet. Create one below.";
        el("save").textContent = "Create profile";
      } else {
        el("save").disabled = true;
        throw error;
      }
    }
  } catch (error) {
    status(error instanceof Error ? error.message : "Session unavailable", true);
  }
}


function makeNode(tag, className = "", content = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = content;
  return node;
}

async function refreshWorkspaces() {
  const result = await jsonRequest("/auth/organizations");
  if (!Array.isArray(result.organizations)) throw new Error("Invalid workspace API response");
  const list = el("workspace-list");
  list.replaceChildren();
  if (!result.organizations.length) {
    list.append(makeNode("p", "description", "No organizations yet. Create your first workspace."));
  }
  for (const org of result.organizations) {
    const card = makeNode("section", "workspace-card");
    const heading = makeNode("div", "workspace-heading");
    const meta = makeNode("div");
    meta.append(makeNode("strong", "", org.name),
                makeNode("p", "description", "Role: " + org.role));
    const view = makeNode("button", "button secondary workspace-action", "View projects");
    view.type = "button";
    const detail = makeNode("div", "workspace-projects");
    heading.append(meta, view);
    if (org.role === "owner" || org.role === "admin") {
      const manage = makeNode("button", "button secondary workspace-action", "Manage team");
      manage.type = "button";
      const teamDetails = makeNode("div", "workspace-projects");
      manage.addEventListener("click", async () => {
        if (busy) return;
        setBusy(true);
        try {
          await showTeam(org, teamDetails);
        } catch (error) {
          status(error instanceof Error ? error.message : "Team access denied", true);
        } finally {
          setBusy(false);
        }
      });
      heading.append(manage);
      card.append(teamDetails);
    }
    card.append(heading, detail);
    view.addEventListener("click", async () => {
      if (busy) return;
      setBusy(true);
      try {
        const data = await jsonRequest("/auth/organizations/" +
          encodeURIComponent(org.id) + "/projects");
        if (!Array.isArray(data.projects)) throw new Error("Invalid project list response");
        detail.replaceChildren();
        const items = makeNode("ul", "project-items");
        for (const project of data.projects) {
          items.append(makeNode("li", "", project.name));
        }
        detail.append(items);
        const form = makeNode("form", "project-form");
        const label = makeNode("label", "", "Create project in " + org.name);
        const input = document.createElement("input");
        input.setAttribute("aria-label", "Project name in " + org.name);
        input.maxLength = 200;
        input.required = true;
        input.placeholder = "Project name";
        const create = makeNode("button", "button primary workspace-action", "Create project");
        create.type = "submit";
        form.append(label, input, create);
        form.addEventListener("submit", async event => {
          event.preventDefault();
          if (busy) return;
          const name = input.value.trim();
          if (!name) return;
          setBusy(true);
          try {
            await jsonRequest("/auth/organizations/" +
              encodeURIComponent(org.id) + "/projects", "POST", {name}, true);
            input.value = "";
            status("Project created in " + org.name + ".");
            const fresh = await jsonRequest("/auth/organizations/" +
              encodeURIComponent(org.id) + "/projects");
            items.replaceChildren();
            for (const project of fresh.projects) {
              items.append(makeNode("li", "", project.name));
            }
          } catch (error) {
            status(error instanceof Error ? error.message : "Project creation failed", true);
          } finally {
            setBusy(false);
          }
        });
        detail.append(form);
      } catch (error) {
        status(error instanceof Error ? error.message : "Project list unavailable", true);
      } finally {
        setBusy(false);
      }
    });
    list.append(card);
  }
  el("workspace-note").textContent = result.organizations.length +
    " organization(s) available to your verified identity.";
}


async function refreshInvitations() {
  const result = await jsonRequest("/auth/invitations");
  if (!Array.isArray(result.invitations)) throw new Error("Invalid invitations response");
  const list = el("pending-invitations");
  list.replaceChildren();
  if (!result.invitations.length) {
    list.append(makeNode("p", "description", "No pending invitations for this identity."));
    return;
  }
  for (const invite of result.invitations) {
    const row = makeNode("div", "workspace-card");
    row.append(makeNode("strong", "", invite.organization_name),
               makeNode("p", "description", "Role: " + invite.role +
                 " · Expires: " + invite.expires_at));
    const accept = makeNode("button", "button primary workspace-action", "Accept invitation");
    accept.type = "button";
    accept.addEventListener("click", async () => {
      if (busy) return;
      setBusy(true);
      try {
        await jsonRequest("/auth/invitations/" + encodeURIComponent(invite.id) +
          "/accept", "POST", null, true);
        status("Invitation accepted. Your membership is now stored in PostgreSQL.");
        await refreshInvitations();
        await refreshWorkspaces();
      } catch (error) {
        status(error instanceof Error ? error.message : "Acceptance denied", true);
      } finally {
        setBusy(false);
      }
    });
    row.append(accept);
    list.append(row);
  }
}

async function showTeam(org, container) {
  const base = "/auth/organizations/" + encodeURIComponent(org.id);
  const data = await jsonRequest(base + "/members");
  if (!Array.isArray(data.members)) throw new Error("Invalid team response");
  container.replaceChildren(makeNode("h3", "", "Members of " + org.name));
  const list = makeNode("ul", "project-items");
  for (const member of data.members) {
    const item = makeNode("li", "team-member");
    item.append(makeNode("span", "", member.subject + " (" + member.role + ") "));
    if (member.subject !== el("subject").textContent && member.role !== "owner" &&
        (org.role === "owner" || member.role !== "admin")) {
      const remove = makeNode("button", "button secondary workspace-action", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + member.subject);
      remove.addEventListener("click", async () => {
        if (busy) return;
        setBusy(true);
        try {
          await jsonRequest(base + "/members/revoke", "POST",
            {target_subject: member.subject}, true);
          status("Membership revoked and recorded in audit.");
          await showTeam(org, container);
        } catch (error) {
          status(error instanceof Error ? error.message : "Revocation denied", true);
        } finally { setBusy(false); }
      });
      item.append(remove);
    }
    list.append(item);
  }
  container.append(list);

  const form = makeNode("form", "project-form");
  const label = makeNode("label", "", "Invite by exact OIDC subject (not email)");
  const target = document.createElement("input");
  target.required = true;
  target.maxLength = 255;
  target.placeholder = "Verified subject identifier";
  target.setAttribute("aria-label", "Invitee OIDC subject");
  const role = document.createElement("select");
  role.setAttribute("aria-label", "Invitation role");
  const allowedRoles = org.role === "owner"
    ? ["viewer", "developer", "ml_engineer", "deployment_operator", "auditor", "admin"]
    : ["viewer", "developer", "ml_engineer", "deployment_operator", "auditor"];
  for (const value of allowedRoles) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value.replaceAll("_", " ");
    role.append(option);
  }
  const send = makeNode("button", "button primary workspace-action", "Create invitation");
  send.type = "submit";
  form.append(label, target, role, send);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await jsonRequest(base + "/invitations", "POST",
        {target_subject: target.value, role: role.value}, true);
      target.value = "";
      status("Invitation recorded. Recipient can accept from their own My Account.");
    } catch (error) {
      status(error instanceof Error ? error.message : "Invitation denied", true);
    } finally { setBusy(false); }
  });
  container.append(form);
  const audit = await jsonRequest(base + "/audit");
  if (!Array.isArray(audit.events)) throw new Error("Invalid audit response");
  container.append(makeNode("h3", "", "Recent team audit events"));
  const events = makeNode("ul", "project-items");
  for (const event of audit.events.slice(0, 10)) {
    events.append(makeNode("li", "", event.action + " · " +
      event.actor_subject + " → " + event.target_subject + " (" + event.role + ")"));
  }
  if (!audit.events.length) events.append(makeNode("li", "", "No team events yet."));
  container.append(events);
}

el("refresh-invitations").addEventListener("click", async () => {
  if (busy || !csrf) return;
  setBusy(true);
  try { await refreshInvitations(); }
  catch (error) {
    status(error instanceof Error ? error.message : "Invitations unavailable", true);
  } finally { setBusy(false); }
});

el("refresh-workspaces").addEventListener("click", async () => {
  if (busy || !csrf) return;
  setBusy(true);
  try {
    await refreshWorkspaces();
  } catch (error) {
    status(error instanceof Error ? error.message : "Cannot refresh workspaces", true);
  } finally {
    setBusy(false);
  }
});

el("organization-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !csrf) return;
  const name = el("organization-name").value.trim();
  if (!name) return;
  setBusy(true);
  try {
    await jsonRequest("/auth/organizations", "POST", {name}, true);
    el("organization-name").value = "";
    await refreshWorkspaces();
    status("Organization created. You are its owner.");
  } catch (error) {
    status(error instanceof Error ? error.message : "Organization creation failed", true);
  } finally {
    setBusy(false);
  }
});

el("profile-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !csrf) return;
  const name = el("display-name").value.trim();
  if (!name) { status("Display name is required.", true); return; }
  setBusy(true);
  try {
    const result = await jsonRequest("/auth/profile", hasProfile ? "PATCH" : "POST",
                                    {display_name: name}, true);
    hasProfile = true;
    el("display-name").value = result.display_name;
    el("save").textContent = "Update profile";
    el("profile-note").textContent = "Saved securely to PostgreSQL.";
    status("Profile saved.");
  } catch (error) {
    status(error instanceof Error ? error.message : "Profile update failed", true);
  } finally {
    setBusy(false);
  }
});

el("logout").addEventListener("click", async () => {
  if (busy || !csrf) return;
  setBusy(true);
  try {
    await jsonRequest("/auth/logout", "POST", null, true);
    csrf = "";
    el("display-name").value = "";
    el("guest").hidden = false;
    el("member").hidden = true;
    status("Signed out. The local session was revoked.");
  } catch (error) {
    status(error instanceof Error ? error.message : "Sign-out failed", true);
  } finally {
    setBusy(false);
  }
});
boot();
