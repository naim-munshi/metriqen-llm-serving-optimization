"""Functional read-only asset delivery checks for the local model manager UI.

These are static/CSP/route contract checks, not browser rendering or live Ollama QA.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_models import configure_local_models

TOKEN = "operator-local-console-test-token-32characters"


class NoOllama:
    def installed(self):
        return []
    def loaded(self):
        return []
    def preload(self, tag):
        raise AssertionError("not called")
    def unload(self, tag):
        raise AssertionError("not called")


def test_ui_not_registered_when_feature_disabled(monkeypatch):
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_TOKEN", raising=False)
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_DB", raising=False)
    app = FastAPI()
    configure_local_models(app)
    client = TestClient(app, client=("127.0.0.1", 51000))
    for path in ("/console/models", "/console/models/app.js", "/console/models/app.css"):
        assert client.get(path).status_code == 404


def test_console_assets_and_local_auth_contract(tmp_path):
    app = FastAPI()
    configure_local_models(app, token=TOKEN, path=tmp_path / "registry.sqlite3", runtime=NoOllama())
    client = TestClient(app, client=("127.0.0.1", 51000))
    for path, mime in (
        ("/console/models", "text/html"),
        ("/console/models/app.js", "application/javascript"),
        ("/console/models/app.css", "text/css"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(mime)
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
        assert response.text
    html = client.get("/console/models").text
    javascript = client.get("/console/models/app.js").text
    assert 'src="/console/models/app.js"' in html
    assert 'id="connect-form"' in html
    assert 'id="available-list"' in html
    assert 'id="registry-list"' in html
    assert "onclick=" not in html
    assert "innerHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert 'credentials: "omit"' in javascript
    assert 'Authorization": "Bearer " + operatorToken' in javascript
    assert client.get("/api/v1/local/models").status_code == 401


def test_query_strings_cannot_select_unintended_asset(tmp_path):
    app = FastAPI()
    configure_local_models(app, token=TOKEN, path=tmp_path / "registry.sqlite3", runtime=NoOllama())
    client = TestClient(app)
    standard = client.get("/console/models/app.js")
    malicious = client.get("/console/models/app.js?filename=../../../../.env&media_type=text/html")
    assert standard.status_code == malicious.status_code == 200
    assert malicious.content == standard.content
    assert malicious.headers["content-type"] == standard.headers["content-type"]


def test_console_assets_do_not_expose_operator_token(tmp_path):
    app = FastAPI()
    configure_local_models(app, token=TOKEN, path=tmp_path / "registry.sqlite3", runtime=NoOllama())
    client = TestClient(app)
    for path in ("/console/models", "/console/models/app.js", "/console/models/app.css"):
        assert TOKEN not in client.get(path).text
