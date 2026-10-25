"""Approved local inference must never misattribute an upstream reply's model.

These are offline synthetic Gateway contracts. Identity matching is
consistency validation, NOT Ollama digest or cryptographic attestation.
"""
from __future__ import annotations

from email.message import Message
from io import BytesIO
from types import SimpleNamespace
import json

from fastapi import HTTPException
import pytest

from app.local_inference import LocalGatewayChat

TAG = "llama3.2:1b"
OTHER = "llama3.2:latest"
PRIVATE = "SYNTHETIC_PRIVATE_REPLY_NEVER_LEAK"


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"


@pytest.mark.parametrize("reported_model", [
    None, "", OTHER, True, 123, ["llama3.2:1b"], {"tag": TAG},
])
def test_model_manager_rejects_missing_or_wrong_reported_model(reported_model):
    chat = LocalGatewayChat()
    payload = {
        "choices": [{"message": {"role": "assistant", "content": PRIVATE},
                     "finish_reason": "stop"}]
    }
    if reported_model is not None:
        payload["model"] = reported_model
    response = Response(payload)
    def fake_open(request, timeout):
        assert request.full_url == "http://127.0.0.1:8081/v1/chat/completions"
        assert timeout == 125
        sent = json.loads(request.data)
        assert sent["model"] == TAG
        assert sent["max_tokens"] == 24
        return response
    chat.opener = SimpleNamespace(open=fake_open)
    with pytest.raises(HTTPException) as err:
        chat.complete(TAG, "short private prompt", 24)
    assert err.value.status_code == 503
    assert err.value.detail == "local inference gateway model identity unverified"
    assert PRIVATE not in str(err.value)
    assert response.closed


def test_matching_model_identity_allows_existing_bounded_completion():
    chat = LocalGatewayChat()
    response = Response({
        "model": TAG,
        "choices": [{"message": {"role": "assistant", "content": "fixture OK"},
                     "finish_reason": "stop"}],
    })
    chat.opener = SimpleNamespace(open=lambda request, timeout: response)
    result = chat.complete(TAG, "probe", 16)
    assert result == {"tag": TAG, "content": "fixture OK", "finish_reason": "stop"}
    assert response.closed
