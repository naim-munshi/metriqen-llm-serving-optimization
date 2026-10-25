"""Synthetic, loopback-free tests of the isolated Mac acceptance alias setup."""
from __future__ import annotations

from copy import deepcopy
import unittest

from tools.demo import isolated_deployment_setup as setup
from tools.demo import local_deployment_acceptance as lifecycle

TOKEN = "synthetic-operator-credential-32-bytes"
BASELINE, CANDIDATE = "llama3.2:1b", "llama3.2:latest"
D1, D2 = "a" * 64, "b" * 64


class FakeOperatorAPI:
    def __init__(self):
        self.aliases = [
            {
                "name": "assistant", "active_tag": BASELINE, "active_digest": D1,
                "previous_tag": CANDIDATE, "previous_digest": D2,
                "enabled": True, "ready": False,
            }
        ]
        self.registry = [
            {"tag": BASELINE, "digest": D1, "approved": True},
            {"tag": CANDIDATE, "digest": D2, "approved": True},
        ]
        self.installed = [
            {"tag": BASELINE, "digest": D1},
            {"tag": CANDIDATE, "digest": D2},
        ]
        self.loaded = {BASELINE: False, CANDIDATE: False}
        self.calls = []
        self.conflict_on_create = False

    def __call__(self, method, path, *, token, payload=None):
        assert token == TOKEN
        lifecycle.endpoint(method, path)
        self.calls.append((method, path, deepcopy(payload)))
        if method == "GET":
            if path == lifecycle.API + "/deployments":
                return 200, {"deployments": [
                    {**row, "ready": row["enabled"] and self.loaded.get(row["active_tag"], False)}
                    for row in self.aliases
                ]}
            if path == lifecycle.API + "/models":
                return 200, {"models": deepcopy(self.registry)}
            if path == lifecycle.API + "/model-statuses":
                return 200, {"models": [
                    {
                        "tag": model["tag"], "approved": model["approved"],
                        "loaded": self.loaded.get(model["tag"], False),
                        "digest_matches_loaded": True,
                    } for model in self.registry
                ]}
            if path == lifecycle.API + "/available-models":
                return 200, {"models": deepcopy(self.installed)}
            raise AssertionError("unexpected GET")
        if path == lifecycle.API + "/models/" + BASELINE + "/load":
            self.loaded[BASELINE] = True
            self.loaded[CANDIDATE] = False
            return 200, {"tag": BASELINE, "digest": D1, "loaded": True}
        if path == lifecycle.API + "/deployments":
            if self.conflict_on_create or any(x["name"] == payload["name"] for x in self.aliases):
                return 409, {"detail": "synthetic concurrent create"}
            created = {
                "name": payload["name"], "active_tag": payload["tag"],
                "active_digest": D1, "previous_tag": None,
                "previous_digest": None, "enabled": True,
            }
            self.aliases.append(created)
            return 201, deepcopy(created)
        raise AssertionError("unexpected POST")


class IsolatedAliasTests(unittest.TestCase):
    def test_plan_inspects_only_and_does_not_pretend_created(self):
        fake = FakeOperatorAPI()
        before = deepcopy(fake.aliases)
        report = setup.collect(
            token=TOKEN, name="acceptance-test", baseline=BASELINE,
            candidate=CANDIDATE, fetch=fake,
        )
        self.assertEqual(report["mode"], "plan")
        self.assertFalse(report["isolated_alias_exists"])
        self.assertFalse(report["baseline_loaded"])
        self.assertFalse(report["created"])
        self.assertFalse(report["executed_state_changes"])
        self.assertFalse(report["portfolio_p1_complete"])
        self.assertEqual(fake.aliases, before)
        self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))
        self.assertNotIn(TOKEN, str(report))

    def test_explicit_create_with_explicit_baseline_load_preserves_assistant(self):
        fake = FakeOperatorAPI()
        original_assistant = deepcopy(fake.aliases[0])
        report = setup.collect(
            token=TOKEN, name="acceptance-test", baseline=BASELINE,
            candidate=CANDIDATE, execute=True, load_baseline=True, fetch=fake,
        )
        self.assertEqual(report["mode"], "execute")
        self.assertTrue(report["created"])
        self.assertTrue(report["isolated_alias_exists"])
        self.assertTrue(fake.loaded[BASELINE])
        self.assertFalse(fake.loaded[CANDIDATE])
        self.assertEqual(fake.aliases[0], original_assistant)
        self.assertEqual(fake.aliases[1]["name"], "acceptance-test")
        self.assertIsNone(fake.aliases[1]["previous_tag"])
        self.assertEqual(
            [path for method, path, _ in fake.calls if method == "POST"],
            [lifecycle.API + "/models/" + BASELINE + "/load",
             lifecycle.API + "/deployments"],
        )
        self.assertFalse(report["candidate_lifecycle_executed"])
        self.assertNotIn(D1, str(report))
        self.assertNotIn(TOKEN, str(report))

    def test_existing_clean_alias_reused_without_any_post(self):
        fake = FakeOperatorAPI()
        fake.loaded[BASELINE] = True
        fake.aliases.append({
            "name": "acceptance-test", "active_tag": BASELINE, "active_digest": D1,
            "previous_tag": None, "previous_digest": None, "enabled": True,
        })
        before = deepcopy(fake.aliases)
        report = setup.collect(
            token=TOKEN, name="acceptance-test", baseline=BASELINE,
            candidate=CANDIDATE, execute=True, fetch=fake,
        )
        self.assertFalse(report["created"])
        self.assertFalse(report["executed_state_changes"])
        self.assertEqual(fake.aliases, before)
        self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))

    def test_history_conflict_cannot_modify_test_alias(self):
        fake = FakeOperatorAPI()
        fake.loaded[BASELINE] = True
        fake.aliases.append({
            "name": "acceptance-test", "active_tag": BASELINE, "active_digest": D1,
            "previous_tag": CANDIDATE, "previous_digest": D2, "enabled": True,
        })
        before = deepcopy(fake.aliases)
        with self.assertRaisesRegex(setup.Failure, "isolated_alias_has_existing_rollback_history"):
            setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                          candidate=CANDIDATE, execute=True, load_baseline=True, fetch=fake)
        self.assertEqual(fake.aliases, before)
        self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))

    def test_existing_alias_wrong_revision_or_disabled_never_changed(self):
        for change, code in (
            ({"active_digest": D2}, "isolated_alias_revision_conflict"),
            ({"enabled": False}, "isolated_alias_disabled_requires_manual_review"),
        ):
            with self.subTest(change=change):
                fake = FakeOperatorAPI()
                fake.aliases.append({
                    "name": "acceptance-test", "active_tag": BASELINE,
                    "active_digest": D1, "previous_tag": None,
                    "previous_digest": None, "enabled": True, **change,
                })
                with self.assertRaisesRegex(setup.Failure, code):
                    setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                                  candidate=CANDIDATE, execute=True,
                                  load_baseline=True, fetch=fake)
                self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))

    def test_unloaded_baseline_requires_separate_preload_opt_in(self):
        fake = FakeOperatorAPI()
        with self.assertRaisesRegex(
            setup.Failure, "baseline_not_loaded_use_explicit_load_baseline"
        ):
            setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                          candidate=CANDIDATE, execute=True, fetch=fake)
        self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))
        with self.assertRaisesRegex(setup.Failure, "load_baseline_requires_execute"):
            setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                          candidate=CANDIDATE, load_baseline=True, fetch=fake)

    def test_candidate_unapproved_or_installed_digest_change_fails_without_writes(self):
        for failure in ("unapproved", "wrong_installed_digest", "missing"):
            with self.subTest(failure=failure):
                fake = FakeOperatorAPI()
                if failure == "unapproved":
                    fake.registry[1]["approved"] = False
                    reason = "candidate_registered_model_not_approved"
                elif failure == "wrong_installed_digest":
                    fake.installed[1]["digest"] = "c" * 64
                    reason = "candidate_installed_digest_mismatch"
                else:
                    fake.installed.pop()
                    reason = "candidate_installed_model_missing_or_ambiguous"
                with self.assertRaisesRegex(setup.Failure, reason):
                    setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                                  candidate=CANDIDATE, execute=True,
                                  load_baseline=True, fetch=fake)
                self.assertTrue(all(m == "GET" for m, _, _ in fake.calls))

    def test_invalid_alias_name_or_models_do_not_make_any_network_calls(self):
        for name, base, other in (
            ("assistant", BASELINE, CANDIDATE),
            ("acceptance-", BASELINE, CANDIDATE),
            ("acceptance-../../evil", BASELINE, CANDIDATE),
            ("ACCEPTANCE-test", BASELINE, CANDIDATE),
            ("acceptance-test", BASELINE, BASELINE),
            ("acceptance-test", "bad", CANDIDATE),
        ):
            with self.subTest(name=name, base=base, other=other):
                fake = FakeOperatorAPI()
                with self.assertRaisesRegex(setup.Failure, "invalid_isolated_alias_or_model"):
                    setup.collect(token=TOKEN, name=name, baseline=base,
                                  candidate=other, execute=True,
                                  load_baseline=True, fetch=fake)
                self.assertEqual(fake.calls, [])

    def test_duplicate_name_race_is_refused_without_adoption(self):
        fake = FakeOperatorAPI()
        fake.loaded[BASELINE] = True
        fake.conflict_on_create = True
        with self.assertRaisesRegex(setup.Failure, "local_api_http_409|isolated_alias_create_status_unexpected"):
            # The real transport rejects HTTP 409 before returning to collect;
            # this fake returns status 409 and exercises the explicit response check.
            setup.collect(token=TOKEN, name="acceptance-test", baseline=BASELINE,
                          candidate=CANDIDATE, execute=True, fetch=fake)
        self.assertEqual(len(fake.aliases), 1)


if __name__ == "__main__":
    unittest.main()
