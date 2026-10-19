"""Synthetic-only P1 deployment operator flow tests; no Mac/LLM execution."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import local_deployment_acceptance as demo

NAME = "assistant"
A, B = "llama3.2:1b", "llama3.2:latest"
DA, DB = "a" * 64, "b" * 64
TOKEN = "testing-only-operator-token-32-characters"


class FakeLocalAPI:
    def __init__(self):
        self.alias = {
            "name": NAME, "active_tag": A, "active_digest": DA,
            "previous_tag": None, "previous_digest": None,
            "enabled": True,
        }
        self.models = [
            {"tag": A, "digest": DA, "approved": True},
            {"tag": B, "digest": DB, "approved": True},
        ]
        self.loaded = {A: True, B: True}
        self.calls = []
        self.fail_candidate_chat = False
        self.fail_rollback = False
        self.force_external_change = False

    def __call__(self, method, path, *, token, payload=None):
        if token != TOKEN:
            raise AssertionError("unexpected private test token")
        demo.endpoint(method, path)  # every request is allowlisted
        self.calls.append((method, path, payload))
        if method == "GET":
            if path.endswith("/models"):
                return 200, {"models": list(self.models)}
            if path.endswith("/model-statuses"):
                return 200, {"models": [
                    {"tag": model["tag"], "approved": model["approved"],
                     "loaded": self.loaded[model["tag"]],
                     "digest_matches_loaded": True}
                    for model in self.models]}
            if path.endswith("/deployments"):
                a = dict(self.alias)
                a["ready"] = self.loaded[a["active_tag"]] and a["enabled"]
                return 200, {"deployments": [a]}
            raise AssertionError("unexpected test GET")

        action = path.rsplit("/", 1)[-1]
        if action == "stop":
            self.alias["enabled"] = False
        elif action == "start":
            if not self.loaded[self.alias["active_tag"]]:
                return 409, {"detail": "not loaded"}
            self.alias["enabled"] = True
        elif action == "chat":
            if not self.alias["enabled"]:
                return 409, {"detail": "local deployment is stopped"}
            if not self.loaded[self.alias["active_tag"]]:
                return 409, {"detail": "model not loaded"}
            if self.fail_candidate_chat and self.alias["active_tag"] == B:
                return 503, {"detail": "private failure"}
            if self.force_external_change:
                self.alias["active_tag"] = "unexpected:1b"
            return 200, {
                "deployment": NAME, "tag": self.alias["active_tag"],
                "content": "FIXTURE REPLY ONLY", "finish_reason": "stop",
            }
        elif action == "promote":
            target = payload["tag"]
            current = self.alias["active_tag"]
            d = self.alias["active_digest"]
            self.alias.update(
                active_tag=target,
                active_digest=next(x["digest"] for x in self.models if x["tag"] == target),
                previous_tag=current, previous_digest=d,
            )
        elif action == "rollback":
            if self.fail_rollback:
                return 409, {"detail": "target evicted"}
            prev, digest = self.alias["previous_tag"], self.alias["previous_digest"]
            self.alias.update(
                previous_tag=self.alias["active_tag"],
                previous_digest=self.alias["active_digest"],
                active_tag=prev, active_digest=digest,
            )
        else:
            raise AssertionError("unexpected test action")
        return 200, dict(self.alias)


class OperatorLifecycleTests(unittest.TestCase):
    def test_read_only_default_never_sends_post(self):
        fake = FakeLocalAPI()
        result = demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                              execute=False, fetch=fake)
        self.assertTrue(result["passed"])
        self.assertFalse(result["executed_state_changes"])
        self.assertFalse(result["portfolio_p1_complete"])
        self.assertEqual(fake.alias["active_tag"], A)
        self.assertTrue(fake.alias["enabled"])
        self.assertTrue(all(method == "GET" for method, _, _ in fake.calls))

    def test_full_lifecycle_stop_start_promote_rollback_and_chat(self):
        fake = FakeLocalAPI()
        result = demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                              execute=True, fetch=fake)
        self.assertEqual(result["kind"], "operator_local_deployment_lifecycle_observation")
        self.assertTrue(result["actual_model_execution_http_observed"])
        self.assertFalse(result["performance_benchmark"])
        self.assertFalse(result["portfolio_p1_complete"])
        self.assertEqual(result["candidate_digest"], DB)
        self.assertIn("stopped_alias_rejects_chat_409", result["checks_passed"])
        self.assertIn("promoted_alias_routes_candidate_inference", result["checks_passed"])
        self.assertIn("rollback_restores_original_inference", result["checks_passed"])
        self.assertEqual(fake.alias["active_tag"], A)
        self.assertTrue(fake.alias["enabled"])
        actions = [p.rsplit("/", 1)[-1] for verb, p, _ in fake.calls if verb == "POST"]
        self.assertEqual(actions, [
            "stop", "chat", "start", "chat", "promote",
            "chat", "rollback", "chat",
        ])
        self.assertNotIn("FIXTURE REPLY ONLY", json.dumps(result))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_stop_start_without_candidate_does_not_promote(self):
        fake = FakeLocalAPI()
        report = demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=None,
                              execute=True, fetch=fake)
        self.assertEqual(fake.alias["active_tag"], A)
        self.assertTrue(fake.alias["enabled"])
        self.assertFalse(any(p.endswith("/promote") for method, p, _ in fake.calls))
        self.assertIn("started_alias_routes_real_inference", report["checks_passed"])
        self.assertNotIn("rollback_restores_original_inference", report["checks_passed"])

    def test_promoted_candidate_failure_attempts_restoration(self):
        fake = FakeLocalAPI()
        fake.fail_candidate_chat = True
        with self.assertRaisesRegex(demo.AcceptanceFailure, "unexpected_local_api_status_503"):
            demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                         execute=True, fetch=fake)
        self.assertEqual(fake.alias["active_tag"], A)
        self.assertTrue(fake.alias["enabled"])
        self.assertTrue(any(path.endswith("/rollback") for _, path, _ in fake.calls))

    def test_rollback_failure_never_makes_false_success(self):
        fake = FakeLocalAPI()
        fake.fail_rollback = True
        with self.assertRaisesRegex(demo.AcceptanceFailure, "unexpected_local_api_status_409"):
            demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                         execute=True, fetch=fake)
        self.assertEqual(fake.alias["active_tag"], B)
        self.assertFalse(any(path.endswith("/start") and (
            path.endswith("rollback")) for _, path, _ in fake.calls))

    def test_preflight_rejects_unloaded_candidate_before_any_write(self):
        fake = FakeLocalAPI()
        fake.loaded[B] = False
        with self.assertRaisesRegex(demo.AcceptanceFailure, "approved_model_not_loaded"):
            demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                         execute=True, fetch=fake)
        self.assertTrue(all(verb == "GET" for verb, _, _ in fake.calls))

    def test_preflight_rejects_prior_rollback_target_before_any_write(self):
        fake = FakeLocalAPI()
        fake.alias["previous_tag"], fake.alias["previous_digest"] = B, DB
        with self.assertRaisesRegex(demo.AcceptanceFailure, "existing_rollback_history_requires_manual_review"):
            demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=B,
                         execute=True, fetch=fake)
        self.assertTrue(all(verb == "GET" for verb, _, _ in fake.calls))

    def test_stop_error_does_not_leave_alias_paused(self):
        fake = FakeLocalAPI()
        fake.force_external_change = True
        with self.assertRaisesRegex(demo.AcceptanceFailure, "deployment_inference_invalid"):
            demo.collect(token=TOKEN, name=NAME, baseline=A, candidate=None,
                         execute=True, fetch=fake)
        self.assertTrue(fake.alias["enabled"])

    def test_reject_bad_deployment_and_candidate_tag_without_network(self):
        for name, model, candidate in [
            ("../escape", A, B), ("Bad", A, B),
            (NAME, A, A), (NAME, "bad", B),
            (NAME, A, "https://evil.example.com"),
        ]:
            fake = FakeLocalAPI()
            with self.subTest(name=name, model=model, candidate=candidate):
                with self.assertRaisesRegex(demo.AcceptanceFailure, "invalid_deployment_or_model_identifier"):
                    demo.collect(token=TOKEN, name=name, baseline=model,
                                 candidate=candidate, execute=True, fetch=fake)
                self.assertEqual(fake.calls, [])

    def test_endpoint_allowlist_no_arbitrary_requests(self):
        valid = demo.endpoint("POST", "/api/v1/local/deployments/assistant/chat")
        self.assertEqual(valid, demo.ORIGIN + "/api/v1/local/deployments/assistant/chat")
        for method, path in [
            ("GET", "http://evil.test/secret"), ("POST", "/api/v1/local/models"),
            ("POST", "/api/v1/local/deployments/a/delete"),
            ("POST", "/api/v1/local/deployments/%2e%2e/chat"),
            ("GET", "/api/v1/local/deployments/a/chat"),
            ("POST", "/api/v1/local/deployments/assistant/chat/other"),
        ]:
            with self.subTest(path=path):
                with self.assertRaisesRegex(demo.AcceptanceFailure, "endpoint_not_allowlisted"):
                    demo.endpoint(method, path)

    def test_token_file_private_permissions_and_never_emits_secret(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.object(demo, "ROOT", Path(work)):
                p = Path(work) / "reports/local/.model-operator-token"
                p.parent.mkdir(parents=True)
                p.write_text(TOKEN)
                os.chmod(p, 0o600)
                self.assertEqual(demo.operator_token(p), TOKEN)
                os.chmod(p, 0o644)
                with self.assertRaisesRegex(demo.AcceptanceFailure, "permissions_too_open"):
                    demo.operator_token(p)
                os.chmod(p, 0o600)
                link = p.parent / "link"
                link.symlink_to(p)
                with self.assertRaisesRegex(demo.AcceptanceFailure, "must_be_private"):
                    demo.operator_token(link)

    def test_reports_private_exclusive_0600_without_model_output(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.object(demo, "ROOT", Path(work)):
                # private_report is delegated to a shared evidence writer.
                from tools.demo import real_ollama
                with patch.object(real_ollama, "ROOT", Path(work)):
                    fake = FakeLocalAPI()
                    result = demo.collect(token=TOKEN, name=NAME, baseline=A,
                                          candidate=B, execute=True, fetch=fake)
                    out = Path(work) / "reports/local/deployment.json"
                    demo.private_report(out, result)
                    report = json.loads(out.read_text())
                    self.assertTrue(report["passed"])
                    self.assertEqual(out.stat().st_mode & 0o777, 0o600)
                    self.assertNotIn(TOKEN, out.read_text())
                    self.assertNotIn("FIXTURE REPLY ONLY", out.read_text())
                    with self.assertRaisesRegex(real_ollama.AcceptanceFailure, "create_failed"):
                        demo.private_report(out, result)

    def test_cli_does_not_claim_pass_on_invalid_token_file(self):
        with patch.object(demo, "operator_token", side_effect=demo.AcceptanceFailure("missing_private_token")):
            with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
                code = demo.main(["--execute", "--candidate", B])
            self.assertEqual(code, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("missing_private_token", err.getvalue())


if __name__ == "__main__":
    unittest.main()
