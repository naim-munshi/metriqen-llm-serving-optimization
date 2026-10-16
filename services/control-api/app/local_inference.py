"""Bounded local-only, nonstream chat relay for approved registry models.

The separate Go gateway remains independently reachable on loopback; this
endpoint does not constitute a public inference authorization service.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=4096)
    max_tokens: int = Field(default=128, ge=1, le=256)


def selected_local_gateway_port(environ=None) -> int:
    """Only the direct local Go gateway or the fixed loopback HAProxy listener."""
    environment = os.environ if environ is None else environ
    raw = environment.get("METRIQEN_LOCAL_CHAT_GATEWAY_PORT", "8081")
    if raw not in ("8081", "8090"):
        raise ValueError("local chat gateway port must be 8081 or 8090")
    return int(raw)


class LocalGatewayChat:
    ORIGIN = "http://127.0.0.1:8081"

    def __init__(self, *, port: int = 8081):
        if type(port) is not int or port not in (8081, 8090):
            raise ValueError("only known loopback inference listener ports are allowed")
        self.ORIGIN = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def complete(self, tag: str, prompt: str, max_tokens: int) -> dict:
        data = json.dumps({
            "model": tag,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }, separators=(",", ":")).encode()
        request = Request(
            self.ORIGIN + "/v1/chat/completions", data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=125) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("invalid upstream content type")
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("gateway response too large")
                result = json.loads(raw)
        except HTTPError as exc:
            if exc.code == 429:
                raise HTTPException(429, "local inference gateway overloaded") from None
            raise HTTPException(503, "local inference gateway rejected the request") from None
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "local inference gateway unavailable or invalid") from None

        if not isinstance(result, dict):
            raise HTTPException(503, "local inference gateway returned invalid completion")
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise HTTPException(503, "local inference gateway returned invalid completion")
        first = choices[0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise HTTPException(503, "local inference gateway returned invalid completion")
        message = first["message"]
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, str):
            raise HTTPException(503, "local inference gateway returned invalid assistant response")
        # Return only explicitly selected fields, never arbitrary upstream data.
        return {"tag": tag, "content": content,
                "finish_reason": first.get("finish_reason") if
                first.get("finish_reason") in ("stop", "length") else None}
