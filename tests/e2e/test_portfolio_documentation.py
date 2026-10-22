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
    "docs/operations/portfolio-final-mac-handoff.md",
    "docs/operations/macos-python-tls.md",
    "docs/operations/repository-branch-hygiene.md",
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
            "not a new Metriqen scheduling algorithm", "synthetic",
            "Portfolio v1.0 not released", "Apple M2",
        ):
            self.assertIn(phrase.lower(), body.lower())
        self.assertNotIn("production-ready SaaS", body)
        self.assertIn("real Mac tests in CI", body)

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



    def test_local_demo_launcher_is_syntactically_valid_and_stays_private(self):
        """Offline contract only: no Mac, Ollama, model or browser is run in CI."""
        import subprocess

        path = ROOT / "scripts/start-local-demo.sh"
        contents = path.read_text(encoding="utf-8")
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True,
                                text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for phrase in (
            "set -euo pipefail", "umask 077",
            '[[ "$(uname -s)" == "Darwin" ]]',
            'METRIQEN_UPSTREAM_URL="$OLLAMA_URL"',
            "METRIQEN_QUEUE_DEPTH=0",
            "METRIQEN_LOCAL_MODEL_TOKEN=",
            "METRIQEN_LOCAL_MODEL_DB=",
            "--host 127.0.0.1 --port 8000",
            "operator token", "model preload", "127.0.0.1:8081",
            "127.0.0.1:8000", "no model download",
        ):
            self.assertIn(phrase.lower(), contents.lower())
        # A diagnostic may recommend a manual pull, but the launcher must not
        # execute an automatic model download on the operator's behalf.
        self.assertNotIn("\nollama pull ", contents)
        self.assertNotIn("--trusted-host", contents)
        self.assertNotIn("0.0.0.0:8000", contents)
        self.assertNotIn("0.0.0.0:8081", contents)
        self.assertNotIn("curl -k ", contents)

    def test_portfolio_makefile_uses_python3_on_macos(self):
        """Check CLI portability; dependencies still require explicit installation."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("PYTHON ?= python3", makefile)
        self.assertIn("cd services/control-api && $(PYTHON) -m pytest -q", makefile)
        self.assertIn("$(PYTHON) -m unittest discover", makefile)
        self.assertNotIn("&& python -m pytest", makefile)
        self.assertNotIn("\\n\\tpython -m unittest", makefile)

    def test_fifo_observed_tradeoff_is_linked_and_not_misrepresented(self):
        """Source-only documentation checks; cannot authenticate Mac execution."""
        path = "docs/benchmarks/experiments/2026-10-21-fifo-admission-overload.md"
        report = (ROOT / path).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        acceptance = (ROOT / "docs/PORTFOLIO_V1_ACCEPTANCE.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for phrase in ("144/432", "252/432", "288", "180", "n_slots",
                       "p95", "TTFT", "human P3 acceptance pending"):
            self.assertIn(phrase.lower(), report.lower())
        self.assertIn(path, readme)
        self.assertIn("2026-10-21-fifo-admission-overload.md", acceptance)
        self.assertIn("2026-10-21-fifo-admission-overload.md", changelog)
        self.assertIn("did not improve successful-request goodput", report)
        self.assertIn("causal_speedup_verified: false", report)
        self.assertNotIn(r"\n- Added strict offline", changelog)

    def test_operator_handoff_preserves_all_six_release_gates(self):
        guide = (ROOT / "docs/operations/portfolio-final-mac-handoff.md").read_text(
            encoding="utf-8")
        for item in ("P1", "P2", "P3", "P4", "P5", "P6",
                     "real-ollama-smoke.json", "OLLAMA_NUM_PARALLEL",
                     "optimization_signal", "rollback", "screenshots",
                     "LICENSE", "v1.0.0", "reports/local/",
                     "Mac is not tested by CI"):
            self.assertIn(item.lower(), guide.lower())
        self.assertIn("make portfolio-ci", guide)
        self.assertIn("operator-only", guide.lower())

    def test_login_release_docs_do_not_contradict_implemented_optional_oidc(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        login = (ROOT / "services/control-api/app/browser_auth.py").read_text(
            encoding="utf-8")
        self.assertIn('router.get("/auth/login")', login)
        self.assertIn('router.post("/auth/logout")', login)
        self.assertIn("optional localhost-only browser oidc", security.lower())
        self.assertIn("not been connected and accepted", security.lower())
        self.assertIn("no local password signup", roadmap.lower())
        self.assertIn("not itself", readme.lower())
        self.assertNotIn("There is still no actual browser OIDC login/session/logout", security)
        self.assertNotIn("No user signup, login/logout or session issuance yet", roadmap)

    def test_current_auth_team_and_gateway_docs_do_not_repeat_obsolete_missing_features(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        briefing = (ROOT / "docs/PORTFOLIO.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        team = (ROOT / "docs/operations/team-invitations.md").read_text(encoding="utf-8")
        self.assertIn("team invitation", readme.lower())
        self.assertIn("guarded project inference authorization", briefing)
        self.assertIn("opt-in", briefing.lower())
        self.assertIn("verified-subject invitations", roadmap.lower())
        self.assertIn("POST /api/v1/organizations/{org_id}/invitations", team)
        self.assertNotIn("Not wired to Go serving auth", briefing)
        self.assertNotIn("no Go gateway enforcement", roadmap)
        self.assertNotIn("Missing: org/project update/delete, invitations", roadmap)
        self.assertNotIn("Redis-backed BFF sessions, auth-entrance protection", roadmap)
        self.assertIn("default developer gateway remains unguarded", roadmap)
        self.assertIn("production", briefing.lower())

    def test_historical_status_and_starting_commit_are_not_current_release_claims(self):
        status = (ROOT / "docs/PROJECT_STATUS.md").read_text(encoding="utf-8")
        contract = (ROOT / "docs/PORTFOLIO_V1_ACCEPTANCE.md").read_text(
            encoding="utf-8")
        self.assertNotIn(r"|\n|", status)
        self.assertIn("Historical snapshot", status)
        self.assertIn("not the current `main` HEAD", contract)
        self.assertIn("all P1–P6", contract)

if __name__ == "__main__":
    unittest.main()
