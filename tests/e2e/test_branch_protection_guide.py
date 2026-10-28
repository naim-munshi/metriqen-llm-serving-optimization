"""Source-only GitHub branch-rule operator guide contract (never mutates rules).

CI can validate that documentation lists the same required check job keys
as the workflow, but cannot establish that GitHub actually enforces them.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BranchRuleGuideContracts(unittest.TestCase):
    def test_current_required_ci_job_keys_match_workflow_without_stale_12_job_list(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        guide = (ROOT / "docs/operations/repository-branch-hygiene.md").read_text(
            encoding="utf-8")
        keys = re.findall(r"^  ([a-z][a-z0-9-]+):\n    runs-on:", workflow, re.M)
        self.assertEqual(len(keys), 13, "Unexpected CI job set: update ruleset guide")
        for key in keys:
            self.assertIn("`" + key + "`", guide, key)
        self.assertIn("protected=false", guide)
        self.assertIn("zero active repository rulesets", guide)
        self.assertIn("administrative mutation", guide)
        self.assertIn("[MIT License](../../LICENSE)", guide)
        self.assertIn("solo-maintainer", guide)

    def test_ci_job_coverage_does_not_prove_actual_branch_protection(self):
        guide = (ROOT / "docs/operations/repository-branch-hygiene.md").read_text(
            encoding="utf-8")
        for fragment in ("does not", "source-level", "Mac operator", "owner"):
            self.assertIn(fragment, guide)
        from tools.demo.portfolio_readiness import collect_source_status
        status = collect_source_status()
        self.assertFalse(status["release_approved"])
        self.assertFalse(status["portfolio_v1_released"])


if __name__ == "__main__":
    unittest.main()
