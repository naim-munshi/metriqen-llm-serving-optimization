# Security policy

**Current status: local-only engineering foundation. Not production-ready.**

## Report a vulnerability

Do not post credentials, exploit details, or private data in public issues. Use GitHub's private vulnerability reporting or security advisory flow if it is enabled by the maintainer.

## Current threat exposure

- Only unauthenticated liveness/readiness routes exist. They are deliberately simple and return no credentials.
- Docker Compose publishes service ports to the host loopback interface only.
- Do not expose these services publicly or route production traffic to them.
- The readiness routes currently check process availability only; they do not validate external dependencies.
- Authentication, authorization, tenant isolation, TLS termination, rate limits, inference access and audit logging are NOT implemented.

## Required before production use

Threat modeling; OIDC/MFA; server-side authorization; scoped, rotatable API keys; secret management; model-artifact verification; tenant-isolation tests; inference quotas and timeouts; logs without raw prompts; vulnerability scans; backup/restore and incident-response tests.

Never commit tokens, credentials, private prompts, or proprietary benchmark datasets.
