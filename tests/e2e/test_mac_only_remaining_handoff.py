"""Repository-only checks for the current Mac/owner finish line.

No macOS, Ollama, running Go gateway, browser, external URL, secrets or
private operator reports are read. A green CI cannot approve P1-P6.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MacOnlyHandoffContracts(unittest.TestCase):
    def test_mac_only_guide_present_and_uses_existing_read_only_runbooks(self):
        page = (ROOT / "docs/operations/mac-only-remaining-acceptance.md").read_text(
            encoding="utf-8"
        )
        for phrase in ("P1", "P2", "P3", "P4", "P5", "P6",
                       "390px", "320px", "VoiceOver", "Shift+Tab",
                       "A1", "B1", "A2", "B2",
                       "p5_stream_cancel_recovery", "client_canceled_total",
                       "reports/local/", "MIT", "NO-GO",
                       "branch protection", "v1.0.0", "13 current CI checks"):
            # Word-case differences are intentional, not semantics.
            if phrase == "client_canceled_total":
                # Metric is described in the linked detailed runbook.
                detail = (ROOT / "docs/operations/p5-stream-cancel-recovery.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(phrase, detail)
            else:
                self.assertIn(phrase.casefold(), page.casefold(), phrase)
        self.assertIn("Do not publish private screenshots", page)
        self.assertIn("one still-missing real client-cancellation run", page)

    def test_every_current_entrypoint_links_remaining_runbook(self):
        paths = (
            "README.md",
            "docs/PORTFOLIO_V1_ACCEPTANCE.md",
            "docs/operations/portfolio-final-mac-handoff.md",
            "docs/operations/v1-final-closeout.md",
        )
        for path in paths:
            with self.subTest(path=path):
                contents = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("mac-only-remaining-acceptance.md", contents)

    def test_p5_error_body_unit_test_really_exercises_private_redaction(self):
        go = (ROOT / "services/inference-gateway/internal/httpapi/upstream_error_redaction_test.go").read_text(
            encoding="utf-8"
        )
        for fragment in (
            "TestGatewayDoesNotExposeNon2xxUpstreamErrorBodies",
            "TestGatewayUnreachableLocalUpstreamReturnsSanitized502",
            "PRIVATE_UPSTREAM_TOKEN_AND_PROMPT",
            "http.StatusBadGateway",
            "http.StatusTooManyRequests",
            "http.StatusServiceUnavailable",
        ):
            self.assertIn(fragment, go)

    def test_readiness_still_fails_closed_and_license_is_not_release(self):
        from tools.demo.portfolio_readiness import collect_source_status
        status = collect_source_status()
        self.assertTrue(status["owner_licensing_decision_file_present"])
        self.assertFalse(status["release_approved"])
        self.assertFalse(status["portfolio_v1_released"])
        self.assertEqual([x["id"] for x in status["gates"]],
                         ["P1", "P2", "P3", "P4", "P5", "P6"])
        self.assertEqual(
            {x["status"] for x in status["gates"]},
            {"manual_acceptance_required"},
        )


if __name__ == "__main__":
    unittest.main()
