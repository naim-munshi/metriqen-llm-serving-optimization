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

## Current owner-only main protection task (October 29, 2026)

The October 29 GitHub API inspection returned **`protected=false`** for
`main`, and **zero active repository rulesets**. Passing CI **does not
enforce** PR reviews or prevent direct pushes. The connected GitHub tooling
can create/test/merge PRs but does not offer administrative mutation of
branch rulesets; no source commit or CI pass has enabled protections.
The original Metriqen code now has an [MIT License](../../LICENSE),
which closes only the owner-selected-license part of P6.

From GitHub **Settings → Rules → Rulesets** (or the available
**Settings → Branches** protections), create/enable a rule targeting the
default `main` branch. Review repository-plan enforcement and any
administrator bypass permissions before saving:

1. Require a pull request before merge; require an appropriate independent
   approval and dismiss stale reviews after new commits as supported.
2. Prevent force pushes and branch deletion. Require branches to be up to
   date before merge if that suits the repository workflow. **Do not
   require an outside approval if no independent reviewer is available
   without first arranging a collaborator; that setting can block a
   solo-maintainer repository.**
3. Require the check names from the **actual CI** workflow, not inferred
   action labels or a stale screenshot. **The current October 29 CI
   workflow has 13 job keys** to review before selecting any required checks: `public-repository-file-safety`, `control-api-tests`,
   `postgres-integration-tests`, `team-postgres-integration-tests`,
   `durable-deployments-postgres-integration-tests`,
   `redis-project-quota-integration-tests`, `local-load-balancer-config`,
   `gateway-tests`, `gateway-model-selection-microbench`,
   `local-benchmark-tests`, `local-inference-e2e`,
   `p4-synthetic-chromium-responsive`, and `nlp-evaluation-tests`.
4. Check whether the repository currently offers **Dependabot alerts,
   secret scanning, push protection, code scanning**, and private
   vulnerability reporting in its **Security** settings. The presence
   of `.github/dependabot.yml` and `SECURITY.md` does not establish
   that every alert/scan feature is enabled or accessible.
5. Verify enforcement by viewing the branch's actual protected/ruleset
   state as an admin. A README or issue comment cannot enable protections.
   Do **not** put keys or token values into an issue as evidence.

The 13 GitHub CI jobs are **source-level checks only**; real Mac operator
P1–P5, branch protection, demo media and owner-reviewed P6 signoff remain
open. The owner-requested MIT license is recorded separately in
[licensing and third-party rights](licensing-and-third-party.md). If
GitHub's account or plan cannot enforce a particular setting, document
the limitation rather than reporting it as enabled.

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
