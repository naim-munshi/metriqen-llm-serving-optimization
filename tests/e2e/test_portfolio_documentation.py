"""Static, offline reviewer documentation integrity checks.

CI validates *links and honesty*, not the operator's runtime or visuals.
No network, model process, GPU, or secrets are consulted.
"""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
KEY_DOCUMENTS = (
    "README.md",
    "CHANGELOG.md",
    "docs/PORTFOLIO_CASE_STUDY.md",
    "docs/PORTFOLIO_V1_ACCEPTANCE.md",
    "docs/PROJECT_STATUS.md",
    "docs/operations/portfolio-local-reviewer-journey.md",
    "docs/operations/real-ollama-portfolio-acceptance.md",
    "docs/operations/local-console.md",
)

INLINE_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)")


class PortfolioDocumentationContracts(unittest.TestCase):
    def test_local_markdown_link_destinations_exist_and_cannot_escape_repository(self):
        for rel in KEY_DOCUMENTS:
            file = ROOT / rel
            self.assertTrue(file.is_file(), f"missing linked portfolio document: {rel}")
            contents = file.read_text(encoding="utf-8")
            for url in INLINE_LINK.findall(contents):
                if url.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                # Markdown anchors need no network. Only check the local path,
                # not arbitrary heading-derived anchors or remote websites.
                local = unquote(url.split("#", 1)[0])
                if not local:
                    continue
                resolved = (file.parent / local).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT),
                                f"portfolio Markdown link escapes repository: {rel}")
                self.assertTrue(resolved.exists(),
                                f"broken portfolio Markdown link {url!r} in {rel}")

    def test_case_study_distinguishes_ollama_experiment_from_gateway_cpu_microbench(self):
        body = (ROOT / "docs/PORTFOLIO_CASE_STUDY.md").read_text(encoding="utf-8")
        for phrase in (
            "not causal", "OLLAMA_NUM_PARALLEL", "34.235%",
            "27.025%", "25,350 ns/op", "16,258 ns/op",
            "not a real", "synthetic", "not",
            "Portfolio v1.0 not released", "Apple M2",
        ):
            self.assertIn(phrase.lower(), body.lower())
        self.assertNotIn("production-ready SaaS", body)
        self.assertIn("actual Mac tests in CI", body)

    def test_unreleased_changelog_has_no_synthetic_v1_release_or_license_claim(self):
        changes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## Unreleased", changes)
        self.assertIn("no tagged Portfolio v1.0 release", changes)
        self.assertIn("license", changes)
        self.assertNotIn("## [1.0.0]", changes)
        self.assertIn("2026-10", changes)
        for id in (69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 82):
            self.assertIn(f"/pull/{id}", changes)

    def test_readme_and_historical_status_point_to_authoritative_release_criteria(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        status = (ROOT / "docs/PROJECT_STATUS.md").read_text(encoding="utf-8")
        self.assertIn("docs/PORTFOLIO_CASE_STUDY.md", readme)
        self.assertIn("CHANGELOG.md", readme)
        self.assertIn("docs/PORTFOLIO_V1_ACCEPTANCE.md", readme)
        self.assertIn("not released yet", readme)
        self.assertIn("Historical snapshot", status)
        self.assertIn("PORTFOLIO_V1_ACCEPTANCE.md", status)
        self.assertIn("opt-in", status)


if __name__ == "__main__":
    unittest.main()
