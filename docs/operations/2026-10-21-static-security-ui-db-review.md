# 2026-10-21 Metriqen static software review — scoped fixes

**Scope:** GitHub `main` source audit for the optional local FastAPI control
plane, PostgreSQL workspace data boundaries, the local model manager, browser
auth UI, and the loopback Go inference gateway. **No live Mac browser,
operator SQLite/PostgreSQL database, credentials, or running services were
accessed.** A passing hosted CI result is not a live/local acceptance result.

## Actual code review and changes

| Area | Observations and remaining boundary |
| --- | --- |
| Interface | Three local panels exist: Go read-only Operations, FastAPI Model Manager and opt-in OIDC My Account. Model/account JS generally uses DOM `textContent`, not HTML injection, and short-lived in-memory credentials/session-CSRF boundaries. Static CSP and mobile/keyboard source-level tests exist. **Actual responsive rendering, keyboard interaction, screenshots and login with real IdP remain operator-only P4 work.** |
| Identity and sessions | OIDC access JWTs use pinned JWKS; current PostgreSQL memberships/grants decide tenant authorization, not client claims. Opt-in local browser login uses PKCE, nonce/state, Redis-backed opaque sessions and CSRF+Origin on mutations; it is not public HTTPS auth. |
| Operator security | Local model operator endpoints require a loopback peer plus bearer token. **Fixed:** ambiguous duplicate `Authorization` headers now fail closed, rather than accepting whichever token Starlette first selected. No token logging or new auth mode. |
| HTTP/UI headers | FastAPI already enforced no-store and no-sniff, with CSP on the bundled account/model pages. **Added:** `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer` globally for consistent non-cacheable control-plane responses; the Go Operations static console already had these headers. |
| PostgreSQL workspace limits | **Fixed:** concurrent organization creation for one verified issuer/subject could exceed the 20-organization count cap; serialize the count/insert transaction via a PostgreSQL transaction-scoped advisory lock. Concurrent owner/admin project creation could exceed an organization's 100-project cap because the prior lock covered only the actor's membership (and only in shared-read mode); lock the shared parent organization row before reading authorization and counting projects. |
| PostgreSQL schema | Checked SQLAlchemy metadata, composite organization/project foreign keys, canonical issuer+subject identity, audited invitation tables, and migrations `0001`–`0006`. SQLAlchemy uses parameterized SQL instead of concatenating user text. No runtime migration or DB data read was performed. The PG CI integration tests apply migrations only to a tightly pinned ephemeral `metriqen_ci` database. |
| Serving gateway | Reviewed fixed allowlisted local upstream, no redirect/environment proxy forwarding of prompts, capped JSON and SSE, bounded admission/429, optional project/lease guards, and sanitized process-local counters. Existing Go race/vet/synthetic recovery CI remains required; not evidence of production authentication, public network hardening or LLM speedup. |

## Regression tests

- Existing SQLite unit and FastAPI synthetic tests must remain green.
- PostgreSQL 16 `test_postgres_integration.py` now launches parallel
  threads creating an organization for the **same verified identity**, and
  multiple owner/admin actors concurrently creating projects in the **same
  tenant**. Under a test-only one-item cap, exactly one succeeds and the
  other operations report a generic limit; assert the persisted row count
  remains one. PostgreSQL's row locks/advisory locks, not SQLite semantics,
  are the relevant release check.
- Duplicate bearer fields are checked in synthetic HTTP tests; global
  security headers are checked by liveness tests.

## Open findings (do not silently close)

1. **Live database verification**: PostgreSQL instance access, actual
   migration version/constraint parity, owner ACLs, backups/restoration,
   measured concurrent operator behavior and real Redis sessions must be
   tested by the authorized Mac/database operator; no live DSNs or user rows
   should be pasted into GitHub issues.
2. **P4 visual & interaction audit**: desktop and mobile widths, keyboard
   focus, empty/long values, API failure states, and one real
   approval → inference → metrics → rollback session with sanitized
   screenshot/video evidence.
3. **P5 security acceptance**: opt-in OIDC configuration, JWT revocation,
   cross-tenant negative tests and live Ollama recovery remain unverified
   on the operator's actual runtime.
4. **Release**: owner's license choice and P1–P6 human evidence signoff
   precede `v1.0.0`; branch protection and authorized cleanup are separate
   repository-admin tasks.

**Engineering verdict:** this review identifies and fixes narrow
reproducible authorization-input and database-concurrency risks. It is a
code-review artifact, **not** a certification, penetration test, or a
claim that the whole software is flawless.
