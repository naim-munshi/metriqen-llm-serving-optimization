# Threat model v1 — Metriqen control and inference services

Status: initial design baseline; not a penetration test or security certification.

## Assets and trust boundaries

| Asset / boundary | Entry point | Threat | Required mitigation / verification |
| --- | --- | --- | --- |
| Browser → control API | Authenticated UI and REST API | Session theft, CSRF, account takeover | OIDC PKCE, secure cookies, CSRF controls, MFA, session invalidation tests |
| Control API → tenant data | Organization/project object IDs | IDOR / horizontal privilege escalation | Server-resolved membership, per-object authorization, cross-tenant negative tests |
| App → identity provider | OIDC tokens and JWKS | Forged tokens, replay, wrong audience/issuer | Validate signature, issuer, audience, expiry, nonce/state; key rotation tests |
| Clients → inference gateway | Project API key and request body | Key disclosure, request flood, runaway token use | Hashed scoped keys, rotation, quotas, max tokens, concurrency limits |
| Services → model runtime | Request and model endpoint configuration | SSRF, arbitrary external endpoint, data exfiltration | Approved endpoint allowlist, egress restrictions, service identities |
| Model artifacts → GPU workload | Model files, tokenizer and runtime parameters | Malicious artifacts, unexpected code execution | Pin model revisions, verify licenses/hashes, isolate GPU containers, disallow unreviewed remote code |
| Jobs → infrastructure | Benchmark worker and deployment controller | Privilege escalation or GPU denial of service | Least-privilege service account, quotas, approval gates, network policies |
| Data stores & telemetry | Benchmarks, API keys, logs | Sensitive data leakage | Encryption at rest/transit, secrets vault, redaction, retention policies, access audits |
| CI/CD → deployment | GitHub Actions, images | Supply-chain compromise | Least-privilege workflows, lock dependencies/actions, scanning, signed provenance |

## Abuse cases for acceptance tests

1. User in organization A requests organization B's benchmark by guessed ID → deny.
2. Viewer attempts to create a deployment or API key → deny.
3. Member with no project grant attempts benchmark execution → deny.
4. Revoked membership or expired API key attempts access → deny.
5. Unapproved model revision or arbitrary endpoint URL is submitted → reject.
6. Over-limit streaming request arrives → throttle without exposing another tenant's metadata.
7. Failed admin login or sensitive role change occurs → audited without recording secrets.

## Residual risks and limits

The foundation only provides process health endpoints. This document describes **planned controls**, not deployed features. Runtime endpoints remain unimplemented. Before any public deployment, perform a detailed STRIDE review, real integration tests, load tests, secret scanning, dependency/container scanning, and infrastructure permission review.
