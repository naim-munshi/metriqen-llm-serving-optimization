"""Offline safety regressions for localhost-only read-only portfolio preflight."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import local_readonly_review as review


def synthetic_response(url: str):
    paths = {name: (endpoint, kind) for name, endpoint, kind, _ in review.PROBES}
    by_url = {endpoint: (name, kind) for name, (endpoint, kind) in paths.items()}
    if url not in by_url:
        raise AssertionError("a fake test tried an unexpected URL")
    name, kind = by_url[url]
    if kind == "must_401":
        return 401, "", b"PRIVATE_401_ERROR_BODY_MUST_NOT_APPEAR"
    if kind == "html":
        return 200, "text/html", b"<!doctype html><html><title>Metriqen</title></html>"
    if kind == "ollama_json":
        return 200, "application/json", b'{"version":"synthetic-test-version"}'
    if kind == "gateway_json":
        return 200, "application/json", b'{"service":"metriqen-inference-gateway","status":"ok"}'
    if kind == "control_json":
        return 200, "application/json", b'{"service":"metriqen-control-api","status":"ok"}'
    if kind == "upstream_json":
        return 200, "application/json", b'{"status":"reachable","probe":"ollama_api_version"}'
    raise AssertionError(name)


class LocalReviewSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "reports" / "local"
        self.base.mkdir(parents=True, mode=0o700)
        self.base.chmod(0o700)
        self.patch_root = patch.object(review, "REPORT_ROOT", self.base)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def test_default_cli_has_no_io_and_requires_explicit_execute(self):
        with patch.object(review, "collect", side_effect=AssertionError("network should not run")):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(review.main([]), 0)
        self.assertIn("PLAN ONLY", output.getvalue())
        self.assertEqual(list(self.base.iterdir()), [])

    def test_all_eight_probes_are_fixed_loopback_and_get_only(self):
        self.assertEqual(len(review.PROBES), 8)
        for name, url, _, required in review.PROBES:
            self.assertTrue(name.isidentifier())
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            self.assertNotIn("@", url)
            self.assertNotIn("?", url)
            self.assertNotIn("#", url)
        self.assertEqual(
            {name for name, url, _, required in review.PROBES if not required},
            {"account_console_page"},
        )
        for bad in ("https://example.com/", "http://localhost:8081/console",
                    "http://127.0.0.1:8081/v1/chat/completions",
                    "http://127.0.0.1:8000/auth/login",
                    "http://127.0.0.1:11434/api/generate"):
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                review.fetch_local(bad)

    def test_healthy_synthetic_report_never_signs_off_acceptance(self):
        report = review.collect(fetch=synthetic_response)
        self.assertTrue(report["required_checks_passed"])
        self.assertEqual(len(report["checks"]), 8)
        self.assertTrue(all(e["status"] == "pass" for e in report["checks"]))
        self.assertFalse(report["oauth_signin_verified"])
        self.assertFalse(report["real_model_inference_verified"])
        self.assertFalse(report["browser_visual_acceptance_verified"])
        self.assertFalse(report["portfolio_p4_or_p5_approved"])
        self.assertFalse(report["mac_network_listener_binding_verified"])
        text = json.dumps(report)
        self.assertNotIn("PRIVATE_401_ERROR_BODY_MUST_NOT_APPEAR", text)
        self.assertNotIn("synthetic-test-version", text)
        self.assertNotIn("cookie", text.lower())

    def test_missing_mandatory_service_fails_and_optional_page_can_be_unavailable(self):
        control_url = next(url for name, url, _, _ in review.PROBES if name == "control_live")
        account_url = next(url for name, url, _, _ in review.PROBES if name == "account_console_page")
        def probe(url):
            if url == control_url:
                raise ValueError("unreachable")
            if url == account_url:
                return 404, "", b"secret debug body"
            return synthetic_response(url)
        report = review.collect(fetch=probe)
        self.assertFalse(report["required_checks_passed"])
        statuses = {a["check"]: a["status"] for a in report["checks"]}
        self.assertEqual(statuses["control_live"], "fail")
        self.assertEqual(statuses["account_console_page"], "optional_unavailable")
        self.assertNotIn("secret debug body", json.dumps(report))

    def test_wrong_origin_bypass_or_unsafely_open_registry_is_failure(self):
        target = next(url for name, url, _, _ in review.PROBES
                      if name == "model_manager_unauthenticated")
        for status in (200, 302, 403, 404, 500):
            with self.subTest(status=status):
                def probe(url):
                    if url == target:
                        return status, "application/json", b'{"models":[]}'
                    return synthetic_response(url)
                report = review.collect(fetch=probe)
                self.assertFalse(report["required_checks_passed"])
                item = next(e for e in report["checks"] if e["check"] == "model_manager_unauthenticated")
                self.assertEqual(item["status"], "fail")
                self.assertEqual(item["http_status"], status)

    def test_invalid_status_mime_and_hostile_json_are_never_accepted(self):
        for returned in (
            (True, "application/json", b'{"status":"ok"}'),
            (200, "text/plain", b'{"service":"metriqen-control-api","status":"ok"}'),
            (200, "application/json", b'{"service":"metriqen-control-api","status":"ok"}'),
            (200, "text/html", b'<script>Metriqen</script>'),
            (200, "application/json", b"invalid-json"),
            (200, "application/json", b'{"status":"reachable","probe":"not_ollama"}'),
        ):
            with self.subTest(returned=returned[:2]):
                def probe(url):
                    if url == "http://127.0.0.1:8081/health/upstream":
                        return returned
                    return synthetic_response(url)
                doc = review.collect(fetch=probe)
                self.assertFalse(doc["required_checks_passed"])

    def test_private_report_permissions_and_refuse_existing_symlinks(self):
        path = self.base / "review.json"
        report = review.collect(fetch=synthetic_response)
        saved = review.save_private(report, path)
        self.assertEqual(saved, path)
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertTrue(json.loads(saved.read_text())["required_checks_passed"])
        with self.assertRaises(ValueError):
            review.save_private(report, path)
        saved.unlink()
        saved.symlink_to(self.base / "another-file")
        with self.assertRaises(ValueError):
            review.save_private(report, path)

    def test_unsafe_report_path_and_parent_permissions_refused(self):
        doc = review.collect(fetch=synthetic_response)
        for target in (self.base / ".." / "escape.json",
                       Path(self.temp.name) / "elsewhere.json",
                       self.base / "no-extension",
                       self.base / "subfolder" / "nested.json"):
            with self.subTest(path=target), self.assertRaises(ValueError):
                review.save_private(doc, target)
        os.chmod(self.base, 0o755)
        with self.assertRaises(ValueError):
            review.save_private(doc, self.base / "should-not-exist.json")

    def test_execute_outputs_only_sanitized_probe_state_and_nonzero_for_failure(self):
        for success in (True, False):
            doc = review.collect(fetch=synthetic_response)
            if not success:
                doc["required_checks_passed"] = False
            with patch.object(review, "collect", return_value=doc):
                out = StringIO()
                with redirect_stdout(out):
                    rc = review.main(["--execute"])
                self.assertEqual(rc, 0 if success else 2)
                self.assertIn("P1/P4/P5 acceptance NOT approved", out.getvalue())
                self.assertNotIn("PRIVATE_401", out.getvalue())
                self.assertNotIn("synthetic-test-version", out.getvalue())


if __name__ == "__main__":
    unittest.main()
