"""Offline regression tests for resource-safe local HTTP error handling."""
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.nlp_eval import online


class _ErrorOpener:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout):
        raise self.error


class HTTPErrorCleanupTests(unittest.TestCase):
    def test_http_error_response_body_is_closed_without_reading_or_disclosing_it(self):
        endpoint = "http://127.0.0.1:8081/v1/chat/completions"
        for status in (302, 429, 503):
            with self.subTest(status=status):
                # An HTTPError owns an open response stream; unlike a normal
                # successful response, it bypasses the opener.open context.
                body = BytesIO(b"PRIVATE UPSTREAM ERROR DETAILS")
                error = HTTPError(endpoint, status, "secret reason", {}, body)
                with patch.object(online, "build_opener", return_value=_ErrorOpener(error)):
                    with self.assertRaises(ValueError) as caught:
                        online.generate_one(
                            endpoint, "fixture", [{"role": "user", "content": "test"}],
                            max_tokens=32, timeout=5,
                        )
                self.assertEqual(str(caught.exception), f"upstream HTTP {status}")
                self.assertTrue(body.closed, "the rejected HTTP response was leaked")


if __name__ == "__main__":
    unittest.main()
