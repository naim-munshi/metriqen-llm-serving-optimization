# GitHub branch hygiene — owner-operated

This is repository maintenance, **not** a Portfolio P1–P6 acceptance
substitute. Prefer short-lived branches: one focused change, CI, reviewed PR,
merge, then delete the merged head. Git's merged history and PR discussion are
preserved even when that branch ref is deleted.

## Snapshot and classification (October 21, 2026)

An authenticated GitHub read-only comparison of **94 remote branches**
(including `main`) and 103 closed/merged-or-unmerged pull requests found:

- **91** non-main branch heads that exactly matched a **merged, same-repo PR**
  head SHA. They are strong candidates for cleanup *after an owner reviews the
  names and CI history*; no branch has been deleted by this audit.
- `MdNaim`: no matching merged PR, but Git compare showed **0 commits
  ahead** and 103 behind `main`. Preserve it explicitly as a historical
  integration branch until the owner chooses otherwise.
- `fix/verify-benchmark-derived-rates`: no matching merged PR; compared with
  `main` it had **1 unmerged commit ahead** and 25 commits behind.
  **Preserve this branch** and review the unique commit/diff before changing it.
- `main` was not protected, and repository auto-delete of merged PR heads
  was disabled. These settings require GitHub repository owner/admin access.

This is a dated snapshot. **Do not reuse the counts to justify future
destructive actions.** Classification must recheck GitHub's current state.

## Manual GitHub Actions workflow

Use [Manual branch hygiene](../../.github/workflows/manual-branch-hygiene.yml)
in **GitHub → Actions → Manual branch hygiene → Run workflow**, after its PR is
merged into `main`.

1. Choose **audit** (the default). It makes **no changes** and logs each
   candidate with matching merged PR number/head SHA and the excluded refs.
2. Review the audit log and confirm important historical and unique branches
   are excluded. `main`, `master`, `MdNaim`, `release/*`, `hotfix/*`,
   protected refs and refs without an exact merged same-repository PR SHA
   cannot be pruned by this workflow.
3. Only with explicit repository-owner authorization choose **prune** and enter
   the exact confirmation `DELETE_VERIFIED_MERGED_BRANCHES`. It queries
   current GitHub refs and rechecks each SHA/protection state before deleting
   a candidate. No dry-run can delete a ref, and no scheduled deletion exists.
4. Re-run **audit** after pruning. If a deletion fails, inspect the partial
   log: already-deleted refs stay deleted; the script stops instead of
   continuing past a failed deletion.

The workflow uses GitHub's short-lived Actions token. The **audit job has
read-only permissions**, while the deliberately invoked prune job requests
`contents: write` and `pull-requests: read`. GitHub settings or existing
branch protection may block deletion. GitHub's delete-ref REST endpoint lacks
an atomic compare-and-swap condition, so rechecking immediately before the
delete minimizes—but cannot entirely eliminate—a concurrent-update race.
Do not run cleanup during active branch development.

## Prevent this from recurring (repository settings)

An administrator can visit **Settings → General → Pull Requests → Automatically
delete head branches**. This affects future merged PRs; it does not safely
classify or clean up the 91 existing stale branches. In **Settings → Branches**
or **Rules → Rulesets**, protect `main` against deletion and force pushes,
and configure pull request reviews/status checks as allowed by the repository
plan and organization. Confirm enforcement for the actual GitHub account;
do not claim protection merely because this document mentions it.

**Avoid:** `git push --mirror`, mass deletion based solely on
`git branch --merged` (misses squash merges), guessing from branch names,
or deleting unique unmerged commits. None is necessary for portfolio release.
