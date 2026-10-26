"""Release documentation must not turn synthetic CI into v1 acceptance."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

from tools.demo.portfolio_readiness import collect_source_status

ROOT = Path(__file__).resolve().parents[2]


class FinalCloseoutContractTests(unittest.TestCase):
    def test_closeout_page_links_real_local_source_and_preserves_gates(self):
        page = (ROOT / "docs/operations/v1-final-closeout.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("**Decision: NO-GO", page)
        self.assertIn("Mac", page)
        self.assertIn("P3", page)
        self.assertIn("branch-protected", page)
        self.assertIn("protected=false", page)
        self.assertIn("owner", page)
        self.assertIn("reports/local/", page)
        self.assertIn("61d62ce577826904b4d83991793ee6a4873cd91d", page)
        self.assertEqual(
            re.findall(r"^- \[ \] (P[1-6]) — ", page, flags=re.MULTILINE),
            ["P1", "P2", "P3", "P4", "P5", "P6"],
        )
        self.assertFalse(
            re.search(r"^- \[x\] P[1-6] — ", page, flags=re.MULTILINE)
        )
        for path in (
            "docs/PORTFOLIO_V1_ACCEPTANCE.md",
            "docs/PORTFOLIO_CURRENT_STATUS.md",
            "docs/operations/portfolio-final-mac-handoff.md",
            "docs/operations/local-deployment-acceptance.md",
            "docs/operations/real-ollama-portfolio-acceptance.md",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_readme_and_status_link_to_final_decision(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs/PORTFOLIO_CURRENT_STATUS.md").read_text(
            encoding="utf-8"
        )
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("docs/operations/v1-final-closeout.md", readme)
        self.assertIn("operations/v1-final-closeout.md", matrix)
        self.assertIn("docs/operations/v1-final-closeout.md", changelog)
        self.assertIn("P1–P6", readme)
        self.assertIn("NO-GO", matrix)
        self.assertIn("12/12", readme)

    def test_offline_source_readiness_still_explicitly_blocks_release(self):
        status = collect_source_status(ROOT)
        self.assertFalse(status["portfolio_v1_released"])
        self.assertFalse(status["release_approved"])
        self.assertFalse(status["operator_mac_execution_verified"])
        self.assertEqual(
            [gate["id"] for gate in status["gates"]],
            ["P1", "P2", "P3", "P4", "P5", "P6"],
        )
        self.assertTrue(all(
            gate["status"] == "manual_acceptance_required"
            for gate in status["gates"]
        ))


if __name__ == "__main__":
    unittest.main()
