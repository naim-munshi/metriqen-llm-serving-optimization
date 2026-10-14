# GitHub repository hygiene

This page describes maintainer-only GitHub settings and branch housekeeping.
**No history rewrite is needed or recommended.** Avoid deleting active work.

## GitHub repository metadata

Open the [repository homepage](https://github.com/naim-munshi/metriqen-llm-serving-optimization), select **About → Edit repository details**, and use:

- **Description:** Local LLM serving gateway in Go with FIFO admission scheduling, reproducible inference benchmarks, and quality evaluation.
- **Website:** leave empty until a real deployed demo exists.
- **Topics:** `llm-serving`, `inference`, `golang`, `ollama`, `benchmarking`, `llmops`, `performance-testing`.
- **Visibility:** public (existing).
- **Default branch:** `main` (existing).

The description and seven repository topics above were confirmed live on
October 14, 2026. Their current state is a GitHub repository setting, **not**
part of the Git history. Recheck via the GitHub About panel before assuming
these settings are unchanged.

## Branch cleanup: review before deletion

At the October 14, 2026 audit, `main` was the default branch and
35 topic branches were checked against the exact head SHAs of their 35
**merged** pull requests. A separate legacy branch `MdNaim` had
**zero unique commits ahead of `main`**. Branch counts and ahead/behind
distances change after each merge, so **never act from these historical
numbers alone**.

Before deleting anything, **recheck live GitHub state**. This review is
not a permanent authorization to delete a branch if new commits appear.

1. Open [branches](https://github.com/naim-munshi/metriqen-llm-serving-optimization/branches) and [merged PRs](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pulls?q=is%3Apr+is%3Amerged). Confirm topic branch names and that the matching PR is **Merged**.
2. Check each branch's current HEAD against the corresponding PR's head commit; a post-merge branch update should be preserved and investigated, not deleted automatically. **Squash merging makes raw Git ancestry insufficient** to prove a branch's work is already on main.
3. Use the GitHub **Delete branch** control on only those confirmed merged and unchanged branches. Retain `main`; retain any active or uncertain branch.
4. Separately review `MdNaim` in the GitHub compare UI: verify it has zero unique commits compared with `main` before deleting; check that no local worktree or deployment uses it.
5. Verify branch count again and enable **Settings → General → Pull Requests → Automatically delete head branches** if supported. Enable branch protection/rulesets for `main` when the maintainer's plan and repository settings permit it. Require CI status checks and restrict force-pushes.

Branch deletion removes a branch pointer, **not** the merged commit history or PR
discussion. Do not delete branches merely because they are old, and do not
force-push or rebase main to make the graph look simpler.

## Release and license policies

The repository has no public release or declared OSS license yet. Do not
publish a `v1.0` tag or a fake hosted demo just for presentation. A
`v0.x` release should follow a tested, reproducible feature boundary and
include a changelog, limitations and a link to its CI run.

An open-source license must be chosen deliberately by the maintainer.
Until then the repository remains public for viewing without a general
reuse/redistribution grant.

## GitHub profile presentation

For a portfolio, pin Metriqen alongside other maintained AI engineering
projects and link it from the [profile README](https://github.com/naim-munshi/naim-munshi).
Only showcase verified features and public, provenance-labeled results.

## Continuous integration and dependency maintenance

The [CI workflow](../.github/workflows/ci.yml) checks pull requests and
`main` pushes, avoiding duplicate push-triggered jobs for open feature PRs.
Every job has a bounded timeout, and external Actions are pinned to full
commit digests. Only `contents: read` is granted to CI workflows. This
constrains routine test execution, but does not replace branch protection.

[Dependabot](../.github/dependabot.yml) checks GitHub Actions, the Python
control API and Go module metadata weekly. Every generated PR must pass
the same checks before merging. No auto-merge is configured. Python
dependency versions still use ranges, not a complete cryptographically
locked reproducible environment; that remains a separate reproducibility
task before production deployment.
