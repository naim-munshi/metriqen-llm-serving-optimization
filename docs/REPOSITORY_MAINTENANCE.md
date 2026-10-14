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

These GitHub About/profile settings are not editable through the available
file/PR workflow, and adding text to README does **not** update them.

## Branch cleanup: review before deletion

On October 14, 2026, the repository had 36 branches: `main`, 34 topic
branches associated with 34 **merged** pull requests, and a separate
legacy branch `MdNaim`. The legacy branch was 0 commits ahead and 37
commits behind main *at that inspection time*.

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
