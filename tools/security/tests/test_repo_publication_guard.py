"""Regression tests: public tracked file guard, no real secrets or network."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tools.security import repo_publication_guard as guard


class PublicFilePathGuardTests(unittest.TestCase):
    def test_allow_normal_source_docs_and_public_env_template(self):
        safe = [
            "README.md", "SECURITY.md", ".env.example",
            ".github/workflows/ci.yml", "tools/security/repo_publication_guard.py",
            "docs/benchmarks/experiments/2026-10-13-ollama-parallelism.json",
            "services/control-api/app/security/jwks.py",
        ]
        self.assertEqual(guard.check_paths(safe), [])
        self.assertTrue(all(guard.rejected_reason(x) is None for x in safe))

    def test_reject_private_git_add_force_bypasses_ignore(self):
        unsafe = {
            ".env": "environment_file",
            ".env.local": "environment_file",
            ".ENV.production": "environment_file",
            "services/control-api/.env": "environment_file",
            "docs/.env.example": "environment_file",
            "reports/local/private-a1.json": "private_directory",
            "reports/private/tokens.json": "private_directory",
            "data/private/real-traces.jsonl": "private_directory",
            "artifacts/private/camera.png": "private_directory",
            "private-ui-samples/sample.png": "private_directory",
            ".venv/lib/site-packages/x.py": "private_directory",
            "node_modules/package/index.js": "private_directory",
            "keys/operator.pem": "potential_credential_or_state_file",
            "keys/operator.PFX": "potential_credential_or_state_file",
            "state/deployments.sqlite3": "potential_credential_or_state_file",
            "state/tenant.sqlite-wal": "potential_credential_or_state_file",
            "state/demo.db-journal": "potential_credential_or_state_file",
        }
        for path, reason in unsafe.items():
            with self.subTest(path=path):
                self.assertEqual(guard.rejected_reason(path), reason)

    def test_reject_ambiguous_malformed_paths(self):
        for path in ("", "../outside.env", "data/../.env.example",
                     "data\\private\\file.json", "name\x00tail"):
            with self.subTest(path=repr(path)):
                self.assertEqual(guard.rejected_reason(path), "invalid_path")

    def test_reads_only_nul_separated_git_index_paths(self):
        run = subprocess.CompletedProcess(
            args=["git"], returncode=0,
            stdout=b"README.md\0.env.example\0reports/local/secret.json\0",
        )
        with patch.object(guard.subprocess, "run", return_value=run) as call:
            self.assertEqual(
                guard.tracked_paths(Path("/synthetic/repo")),
                ["README.md", ".env.example", "reports/local/secret.json"],
            )
        args = call.call_args.args[0]
        self.assertEqual(args[:3], ["git", "-C", "/synthetic/repo"])
        self.assertEqual(args[3:], ["ls-files", "--cached", "-z", "--"])

    def test_git_inspection_failure_is_not_treated_as_clean(self):
        failed = subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout=b"",
        )
        with patch.object(guard.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "inspection unavailable"):
                guard.tracked_paths(Path("/synthetic/repo"))

    def test_main_blocks_private_files_and_never_reads_their_contents(self):
        with patch.object(guard, "tracked_paths", return_value=[
            ".env.example", "reports/local/secret.json",
        ]):
            with redirect_stderr(StringIO()) as error:
                self.assertEqual(guard.main(), 2)
        self.assertIn("private_directory", error.getvalue())
        self.assertNotIn("contents", error.getvalue())

    def test_main_reports_scope_limited_success_and_fails_closed(self):
        with patch.object(guard, "tracked_paths", return_value=["README.md"]):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(guard.main(), 0)
        self.assertIn("filenames only", output.getvalue())
        with patch.object(guard, "tracked_paths", side_effect=OSError("private secret")):
            with redirect_stderr(StringIO()) as error:
                self.assertEqual(guard.main(), 2)
        self.assertNotIn("private secret", error.getvalue())


if __name__ == "__main__":
    unittest.main()
