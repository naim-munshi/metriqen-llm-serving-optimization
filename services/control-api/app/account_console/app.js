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
