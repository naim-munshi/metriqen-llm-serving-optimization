"""Synthetic regressions: local HTTP rejects release response handles without reading bodies."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi import HTTPException
import pytest

from app.local_models import OllamaRuntime
from app.local_monitoring import GatewayMonitoring


class ForbiddenReadBody(BytesIO):
    def read(self, *_args, **_kwargs):
        raise AssertionError("local upstream error body must never be read")


@pytest.mark.parametrize("status", [401, 404, 429, 500, 503])
@pytest.mark.parametrize("path,method", [
    ("/api/tags", "GET"),
    ("/api/ps", "GET"),
    ("/api/generate", "POST"),
])
def test_ollama_http_error_response_is_closed_and_sanitized(status, path, method):
    runtime = OllamaRuntime()
    private = b"DO_NOT_LEAK_PRIVATE_OLLAMA_ERROR"
    body = ForbiddenReadBody(private)
    error = HTTPError(runtime.ORIGIN + path, status, "fixture only", {}, body)

    def reject(request, timeout):
        assert request.full_url == runtime.ORIGIN + path
        assert request.get_method() == method
        assert timeout == (120 if method == "POST" else 4)
        raise error

    runtime.opener = SimpleNamespace(open=reject)
    with pytest.raises(HTTPException) as caught:
        if path == "/api/tags":
            runtime.installed()
        elif path == "/api/ps":
            runtime.loaded()
        else:
            runtime.preload("llama3.2:1b")
    assert caught.value.status_code == 503
    assert caught.value.detail == "local Ollama model API unavailable or invalid"
    assert body.closed
    assert private.decode() not in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_gateway_metrics_http_error_response_is_closed_and_sanitized(status):
    monitor = GatewayMonitoring()
    private = b"DO_NOT_LEAK_PRIVATE_GATEWAY_METRICS_BODY"
    body = ForbiddenReadBody(private)
    path = "/metrics/inference"
    error = HTTPError(monitor.ORIGIN + path, status, "fixture only", {}, body)

    def reject(request, timeout):
        assert request.full_url == monitor.ORIGIN + path
        assert request.get_method() == "GET"
        assert timeout == 2
        raise error

    monitor.opener = SimpleNamespace(open=reject)
    with pytest.raises(HTTPException) as caught:
        monitor._fetch(path, 3, ("attempts_total",))
    assert caught.value.status_code == 503
    assert caught.value.detail == "local inference monitoring unavailable"
    assert body.closed
    assert private.decode() not in str(caught.value)
