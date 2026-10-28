"""MIT license is a real copyright notice, never a P1-P6 release approval.

Contract-only CI. It cannot certify rights in third-party assets or that
a developer Mac, browser, private models, or production security were tested.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from tools.demo.portfolio_readiness import collect_source_status

ROOT = Path(__file__).resolve().parents[2]

# Guard the full standardized license text and named copyright holder so
# accidental truncated or "all rights reserved" replacements do not pass.
MIT = """MIT License

Copyright (c) 2026 Md Naim

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class LicenseContractTests(unittest.TestCase):
    def test_license_matches_standard_mit_and_copyright(self):
        self.assertEqual((ROOT / "LICENSE").read_text(encoding="utf-8"), MIT)

    def test_license_status_is_consistent_without_overriding_third_party(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contrib = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        current = (ROOT / "docs/PORTFOLIO_CURRENT_STATUS.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs/operations/licensing-and-third-party.md").read_text(
            encoding="utf-8")
        handoff = (ROOT / "docs/operations/portfolio-final-mac-handoff.md").read_text(
            encoding="utf-8")
        self.assertIn("[MIT License](LICENSE)", readme)
        self.assertIn("[MIT License](LICENSE)", contrib)
        self.assertIn("[MIT License](../LICENSE)", current)
        self.assertIn("[MIT License](../../LICENSE)", guide)
        self.assertIn("[MIT License](../../LICENSE)", handoff)
        for phrase in ("model weights", "third-party", "reports/local/", "v1.0.0"):
            self.assertIn(phrase, guide)
        self.assertNotIn("no open-source license has been selected", readme.lower())
        self.assertNotIn("no open-source license has been selected", contrib.lower())
        self.assertNotIn("No open-source license has been selected", handoff)

    def test_license_does_not_authorize_portfolio_release(self):
        result = collect_source_status()
        self.assertTrue(result["owner_licensing_decision_file_present"])
        self.assertFalse(result["operator_mac_execution_verified"])
        self.assertFalse(result["release_approved"])
        self.assertFalse(result["portfolio_v1_released"])
        self.assertTrue(all(
            x["status"] == "manual_acceptance_required" for x in result["gates"]
        ))
        self.assertEqual([g["id"] for g in result["gates"]],
                         ["P1", "P2", "P3", "P4", "P5", "P6"])


if __name__ == "__main__":
    unittest.main()
