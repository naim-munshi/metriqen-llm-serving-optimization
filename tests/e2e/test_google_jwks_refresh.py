"""Offline Google public-key refresh tests: no real OAuth or network access."""
import base64
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.identity import google_jwks as mod


def b64(value: int) -> str:
    return base64.urlsafe_b64encode(
        value.to_bytes((value.bit_length()+7)//8, "big")
    ).decode("ascii").rstrip("=")


def fixture(**changes) -> bytes:
    key = {
        "kid": "google-test-1",
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "e": b64(65537),
        "n": b64((1 << 2047) + (1 << 1024) + 123456789),
        "x5c": ["synthetic-public-cert-is-discarded"],
    }
    key.update(changes)
    return json.dumps({"keys": [key]}).encode()


class _Headers:
    def __init__(self, mimetype="application/json"):
        self.mimetype = mimetype

    def get_content_type(self):
        return self.mimetype


class _FakeResponse:
    def __init__(self, content, url=mod.JWKS_URL, mimetype="application/json"):
        self.body, self.url, self.headers = content, url, _Headers(mimetype)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def geturl(self):
        return self.url

    def read(self, cap):
        assert cap == mod.MAX_BYTES + 1
        return self.body[:cap]


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        return self.response


class GoogleJWKSSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "reports" / "local" / "google-public-jwks.json"
        self.patch_path = patch.object(mod, "PUBLIC_KEY_PATH", self.output)
        self.patch_path.start()
        self.addCleanup(self.patch_path.stop)

    def test_key_set_sanitizes_optional_public_metadata(self):
        clean = json.loads(mod.normalize_public_jwks(fixture()))
        self.assertEqual(len(clean["keys"]), 1)
        self.assertEqual(set(clean["keys"][0]), {"kid", "kty", "alg", "use", "n", "e"})
        self.assertNotIn("x5c", str(clean))
        self.assertEqual(clean["keys"][0]["alg"], "RS256")

    def test_oversized_invalid_and_duplicate_json_rejected(self):
        for text in (b"", b"bad", b"[]", b'{"keys":[]}',
                     b'{"keys":[],"keys":[]}', b'{"keys":[{"kid":"a","kid":"b"}]}',
                     b"x" * (mod.MAX_BYTES+1)):
            with self.subTest(text=text[:24]), self.assertRaises(ValueError):
                mod.normalize_public_jwks(text)

    def test_invalid_private_and_malformed_public_keys_rejected(self):
        cases = (
            {"d": "secret"},
            {"alg": "HS256"},
            {"use": "enc"},
            {"key_ops": ["sign"]},
            {"e": b64(3)},
            {"e": "not-base64="},
            {"n": b64(11)},
            {"n": 123},
            {"kid": ""},
            {"kid": "bad\nkey"},
            {"kty": "EC"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                mod.normalize_public_jwks(fixture(**overrides))

    def test_reject_duplicate_signing_key_id(self):
        obj = json.loads(fixture())
        obj["keys"].append(dict(obj["keys"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate key identifier"):
            mod.normalize_public_jwks(json.dumps(obj).encode())

    def test_fixed_google_url_and_bounded_read_with_no_credentials(self):
        response = _FakeResponse(fixture())
        opener = _FakeOpener(response)
        payload = mod.fetch_public_jwks(opener=opener)
        self.assertEqual(payload, mod.normalize_public_jwks(fixture()))
        self.assertEqual(opener.calls, [(mod.JWKS_URL, "GET", 10)])
        self.assertTrue(response.closed)
        self.assertNotIn("access_token", str(opener.calls))
        self.assertNotIn("client_secret", str(opener.calls))

    def test_redirect_other_origin_and_wrong_content_type_fail_closed(self):
        for response in (
            _FakeResponse(fixture(), url="https://attacker.invalid/keys"),
            _FakeResponse(fixture(), mimetype="text/html"),
            _FakeResponse(b"x" * (mod.MAX_BYTES + 1)),
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                mod.fetch_public_jwks(opener=_FakeOpener(response))
            self.assertTrue(response.closed)

    def test_private_new_file_and_explicit_atomic_rotation(self):
        path = mod.write_public_jwks(fixture())
        self.assertEqual(path, self.output)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        initial = path.read_bytes()
        with self.assertRaises(FileExistsError):
            mod.write_public_jwks(fixture(kid="new-kid"))
        self.assertEqual(path.read_bytes(), initial)
        mod.write_public_jwks(fixture(kid="new-kid"), replace=True)
        self.assertEqual(json.loads(path.read_text())["keys"][0]["kid"], "new-kid")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(path.parent.glob(".google-jwks-*")), [])

    def test_invalid_remote_payload_never_modifies_trusted_snapshot(self):
        path = mod.write_public_jwks(fixture())
        initial = path.read_bytes()
        with self.assertRaises(ValueError):
            mod.write_public_jwks(fixture(d="private"), replace=True)
        self.assertEqual(path.read_bytes(), initial)

    def test_symlink_and_world_readable_files_refused(self):
        path = mod.write_public_jwks(fixture())
        os.chmod(path, 0o644)
        with self.assertRaises(ValueError):
            mod.write_public_jwks(fixture(), replace=True)
        os.chmod(path, 0o600)
        path.unlink()
        path.symlink_to(Path(self.temp.name) / "external-target")
        with self.assertRaises(ValueError):
            mod.write_public_jwks(fixture(), replace=True)

    def test_unsafe_parent_directory_refused(self):
        path = mod.write_public_jwks(fixture())
        os.chmod(path.parent, 0o755)
        with self.assertRaises(ValueError):
            mod.write_public_jwks(fixture(), replace=True)

    def test_cli_check_offline_and_manual_replace(self):
        out = StringIO()
        with patch.object(mod, "fetch_public_jwks", return_value=fixture()), redirect_stdout(out):
            self.assertEqual(mod.main(["--refresh"]), 0)
            self.assertEqual(mod.main(["--check"]), 0)
        self.assertIn("validated: 1 keys", out.getvalue())
        self.assertNotIn("123456789", out.getvalue())
        error = StringIO()
        with patch.object(mod, "fetch_public_jwks", return_value=fixture(kid="rotated")), redirect_stderr(error):
            self.assertEqual(mod.main(["--refresh"]), 1)
            self.assertEqual(mod.main(["--refresh", "--replace"]), 0)
        self.assertIn("FileExistsError", error.getvalue())
        self.assertEqual(json.loads(self.output.read_text())["keys"][0]["kid"], "rotated")

    def test_cli_never_uses_unknown_endpoint_or_network_when_checking(self):
        mod.write_public_jwks(fixture())
        with patch.object(mod, "fetch_public_jwks", side_effect=AssertionError("network called")):
            with redirect_stdout(StringIO()):
                self.assertEqual(mod.main(["--check"]), 0)
        with patch.object(mod, "fetch_public_jwks", return_value=b"unsafe") as fetch:
            with redirect_stderr(StringIO()):
                self.assertEqual(mod.main(["--refresh", "--replace"]), 1)
            fetch.assert_called_once_with()
        self.assertEqual(json.loads(self.output.read_text())["keys"][0]["kid"], "google-test-1")


if __name__ == "__main__":
    unittest.main()
