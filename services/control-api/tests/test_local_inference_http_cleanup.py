"""No private gateway error bodies or open urllib response handles on failure."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
import unittest

from fastapi import HTTPException

from app.local_inference import LocalGatewayChat


class ReadForbiddenBody(BytesIO):
    def read(self, *args, **kwargs):
        raise AssertionError("gateway error body must not be read")


class LocalGatewayErrorCleanupTests(unittest.TestCase):
    def test_http_error_response_closed_and_unread_on_all_rejections(self):
        for upstream_status, public_status, public_detail in (
            (429, 429, "local inference gateway overloaded"),
            (403, 503, "local inference gateway rejected the request"),
            (503, 503, "local inference gateway rejected the request"),
        ):
            with self.subTest(status=upstream_status):
                chat = LocalGatewayChat()
                private = "SYNTHETIC_PRIVATE_RESPONSE_MUST_NOT_LEAK"
                body = ReadForbiddenBody(private.encode())
                error = HTTPError(
                    "http://127.0.0.1:8081/v1/chat/completions",
                    upstream_status, "synthetic error", {}, body,
                )

                def fake_open(request, timeout):
                    self.assertEqual(
                        request.full_url,
                        "http://127.0.0.1:8081/v1/chat/completions",
                    )
                    self.assertEqual(timeout, 125)
                    raise error

                chat.opener = SimpleNamespace(open=fake_open)
                with self.assertRaises(HTTPException) as caught:
                    chat.complete("llama3.2:1b", "synthetic prompt", 16)
                self.assertEqual(caught.exception.status_code, public_status)
                self.assertEqual(caught.exception.detail, public_detail)
                self.assertTrue(body.closed, "HTTPError must release response")
                self.assertNotIn(private, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
