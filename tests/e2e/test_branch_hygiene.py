"""Offline guards for manual GitHub branch cleanup. No API requests."""
import unittest

from scripts.branch_hygiene import Candidate, CONFIRMATION, classify, confirm_mode


REPO = "naim-munshi/metriqen-llm-serving-optimization"


def remote(name, sha, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def merged_pr(name, sha, number=25, repo=REPO, merged_at="2026-10-21T01:00:00Z"):
    return {"number": number, "merged_at": merged_at,
            "head": {"ref": name, "sha": sha,
                     "repo": {"full_name": repo}}}


class BranchHygieneContracts(unittest.TestCase):
    def test_only_exact_merged_same_repo_head_is_candidate(self):
        branches = [
            remote("main", "A"),
            remote("feat/completed", "B"),
            remote("MdNaim", "C"),
            remote("fix/unique", "D"),
        ]
        candidates, manual = classify(
            REPO, "main", branches,
            [merged_pr("feat/completed", "B"),
             merged_pr("fix/unique", "different", number=26)],
        )
        self.assertEqual(candidates, [Candidate("feat/completed", "B", 25)])
        self.assertEqual({x[0] for x in manual},
                         {"main", "MdNaim", "fix/unique"})

    def test_unmerged_pr_and_cross_repository_pr_never_eligible(self):
        branches = [remote("feat/closed", "A"), remote("feat/fork", "B")]
        prs = [
            merged_pr("feat/closed", "A", merged_at=None),
            merged_pr("feat/fork", "B", repo="other/repository"),
        ]
        matches, manual = classify(REPO, "main", branches, prs)
        self.assertEqual(matches, [])
        self.assertEqual(len(manual), 2)

    def test_protected_release_and_hotfix_branches_are_preserved(self):
        branches = [
            remote("feat/protected", "P", protected=True),
            remote("release/v1.0", "R"),
            remote("hotfix/security", "H"),
        ]
        prs = [merged_pr(b["name"], b["commit"]["sha"]) for b in branches]
        matches, manual = classify(REPO, "main", branches, prs)
        self.assertFalse(matches)
        self.assertEqual(len(manual), 3)

    def test_changed_head_ref_is_not_deleted(self):
        matches, manual = classify(
            REPO, "main", [remote("feat/changed", "new")],
            [merged_pr("feat/changed", "old")],
        )
        self.assertEqual(matches, [])
        self.assertEqual(manual[0][0], "feat/changed")

    def test_malformed_response_aborts_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            classify(REPO, "main", [{"name": "feat/odd", "commit": {}}], [])

    def test_prune_requires_exact_confirmation_phrase(self):
        confirm_mode("audit", "")
        for value in ("", "delete", CONFIRMATION.lower()):
            with self.assertRaises(ValueError):
                confirm_mode("prune", value)
        confirm_mode("prune", CONFIRMATION)


if __name__ == "__main__":
    unittest.main()
