# Local pinned-JWKS identity smoke test (development only)

The FastAPI control API includes GET /api/v1/identity. It is only a
read-only check that a JWT was signed by a trusted public signing key,
belongs to the configured issuer/audience/client, and represents a
dedicated API access-token type. It does not log in a user or grant
organization/project rights.

## Default behavior: no configuration

With no METRIQEN_OIDC_* settings, health routes remain accessible and
GET /api/v1/identity returns HTTP 503. The Go inference bridge is not
protected by these settings.

## Configure a trusted issuer snapshot

An operator obtains the JSON public JWKS only from the legitimate IdP
through an independently trusted provisioning step and stores it locally
outside the repository. Do not commit private RSA keys or access tokens.

From the services/control-api working directory, with development
dependencies installed, configure:

~~~sh
export METRIQEN_OIDC_ISSUER='https://idp.example.org/realms/metriqen'
export METRIQEN_OIDC_AUDIENCE='metriqen-control-api'
export METRIQEN_OIDC_AUTHORIZED_PARTY='metriqen-local-client'
export METRIQEN_OIDC_JWKS_PATH='/private/path/to/trusted-public-jwks.json'
uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

These values are placeholders, not working credentials. Replace them
with actual verified IdP configuration and a trusted public JWKS.
The file must contain 1-32 uniquely identified RSA public signing keys,
be valid JSON and not exceed 64 KiB. Incorrect or incomplete settings
stop startup rather than accepting unverified tokens.

The first HTTP profile requires typ=at+jwt, RS256, aud, azp, iss,
sub, iat, nbf, and exp. Generic signed typ=JWT / ID-token profiles
are rejected. Token-supplied jku, jwk or x5u key locations are never
trusted. Operator-controlled key rotation requires a new approved snapshot
and a process restart; there is no network key fetch.

## Exercise a real signed access token

Obtain a dedicated access JWT from the configured trusted IdP only if
the provider supports this strict token profile. Store it privately:

~~~sh
curl -i -H "Authorization: Bearer $LOCAL_ACCESS_JWT" \
  http://127.0.0.1:8000/api/v1/identity
~~~

Expected success: HTTP 200 with subject, issuer and audience from
verified claims. Missing/expired/forged tokens return HTTP 401 and
WWW-Authenticate: Bearer. Unconfigured installation returns HTTP 503.

Never paste signed tokens, headers, IdP secrets or private key fields
into public issues or benchmark reports.

## Offline regression tests

~~~sh
cd services/control-api
python -m pytest -q tests/test_identity_http.py
~~~

This does not implement RBAC-backed APIs, user provisioning, OIDC login/MFA,
tenant persistence, access-key rotation, protected gateway inference, or
public deployment. See ADR-0007.
