"""Strict Ollama revision identity parsing for registry/rollback safety."""
from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.local_models import OllamaRuntime

A = "llama3.2:1b"
B = "llama3.2:latest"
D1, D2 = "a" * 64, "b" * 64


@pytest.mark.parametrize("path", ["/api/tags", "/api/ps"])
@pytest.mark.parametrize("items", [
    [
        {"name": A, "model": A, "digest": D1},
        {"name": A, "model": A, "digest": D2},
    ],
    [
        {"name": A, "model": A, "digest": D1},
        {"name": A, "model": A, "digest": D1},
    ],
    [
        {"name": A, "model": B, "digest": D1},
    ],
])
def test_ambiguous_ollama_model_snapshot_rejected(path, items, monkeypatch):
    runtime = OllamaRuntime()
    monkeypatch.setattr(runtime, "_request", lambda supplied: {"models": items})
    with pytest.raises(HTTPException) as caught:
        runtime._models(path)
    assert caught.value.status_code == 503
    assert caught.value.detail == "local Ollama model identity is ambiguous"
    assert "digest" not in caught.value.detail


@pytest.mark.parametrize("path", ["/api/tags", "/api/ps"])
def test_valid_both_field_and_single_field_model_names(path, monkeypatch):
    runtime = OllamaRuntime()
    monkeypatch.setattr(runtime, "_request", lambda supplied: {"models": [
        {"name": A, "model": A, "digest": D1},
        {"model": B, "digest": D2},
    ]})
    assert runtime._models(path) == [
        {"tag": A, "digest": D1},
        {"tag": B, "digest": D2},
    ]


def test_invalid_name_field_does_not_hide_conflicting_valid_model_field(monkeypatch):
    runtime = OllamaRuntime()
    monkeypatch.setattr(runtime, "_request", lambda supplied: {"models": [
        {"name": "other:revision", "model": A, "digest": D1},
    ]})
    with pytest.raises(HTTPException) as caught:
        runtime._models("/api/ps")
    assert caught.value.status_code == 503
