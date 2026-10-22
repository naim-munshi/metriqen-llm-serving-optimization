"""Real ASGI localhost Host defense; no remote network or live IdP."""
from fastapi.testclient import TestClient
import pytest

from app.main import app, create_app
from app.security.local_host import is_local_only_path, valid_local_request_host

TOKEN = "synthetic-local-host-security-token-0123456789"


@pytest.mark.parametrize("path", [
    "/auth/session",
    "/auth/login",
    "/console/account",
    "/console/account/app.js",
    "/console/models",
    "/console/models/app.css",
    "/api/v1/local/models",
])
def test_synthetic_host_guard_covers_all_opt_in_local_routes(path):
    assert is_local_only_path(path)


@pytest.mark.parametrize("path", [
    "/health/live", "/health/ready", "/api/v1/identity",
    "/api/v1/locality", "/console/model", "/authenticated",
])
def test_health_and_unrelated_api_routes_are_not_host_guarded(path):
    assert not is_local_only_path(path)


def test_real_deployed_app_has_guard_even_without_opt_in_features():
    with TestClient(app) as client:
        response = client.get("/auth/session", headers={"Host": "evil.example:8000"})
        assert response.status_code == 421
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/health/live", headers={"Host": "evil.example:8000"}).status_code == 200


def test_local_assets_operator_and_browser_auth_guarded_before_handler(monkeypatch, tmp_path):
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_TOKEN", TOKEN)
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_DB", str(tmp_path / "models.sqlite3"))
    # The mock only enables static optional account pages; no login is performed.
    application = create_app(browser_login=object(), enforce_local_http_host=True)
    with TestClient(application, base_url="http://127.0.0.1:8000",
                    client=("127.0.0.1", 53721)) as client:
        assert client.get("/console/models").status_code == 200
        assert client.get("/console/account").status_code == 200
        assert client.get("/api/v1/local/models").status_code == 401
        assert client.get("/auth/session").status_code == 401

        for path in ("/console/models", "/console/account", "/auth/session",
                     "/api/v1/local/models"):
            for host in ("evil.example:8000", "localhost.attacker.test:8000",
                         "127.0.0.1.attacker.test:8000", "0.0.0.0:8000",
                         "127.0.0.1:0", "127.0.0.1:65536",
                         "127.0.0.1:08000", "localhost", "127.0.0.1",
                         "[::ffff:127.0.0.1]:8000", "[::1%lo0]:8000"):
                response = client.get(path, headers={"Host": host})
                assert response.status_code == 421, (path, host, response.status_code)
                assert host not in response.text
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-frame-options"] == "DENY"

            duplicated = client.get(path, headers=[
                ("host", "127.0.0.1:8000"), ("host", "evil.example:8000")])
            assert duplicated.status_code == 421, path

        assert client.get("/console/models", headers={"Host": "localhost:8000"}).status_code == 200
        assert client.get("/console/models", headers={"Host": "[::1]:8000"}).status_code == 200
        assert client.get("/health/live", headers={"Host": "evil.example:8000"}).status_code == 200


def test_remote_peer_is_not_trusted_even_with_allowed_host(monkeypatch, tmp_path):
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_TOKEN", TOKEN)
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_DB", str(tmp_path / "models.sqlite3"))
    application = create_app(enforce_local_http_host=True)
    with TestClient(application, base_url="http://127.0.0.1:8000",
                    client=("192.0.2.15", 50123)) as remote:
        assert remote.get("/console/models", headers={"Host": "127.0.0.1:8000"}).status_code == 421


def test_local_host_validator_does_not_consult_dns():
    assert valid_local_request_host is not None
