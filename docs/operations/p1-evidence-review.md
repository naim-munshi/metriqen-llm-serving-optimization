# P1 CLI evidence review — private, fail-closed

**Purpose:** cross-check the operator's real Ollama/Gateway smoke report with
the operator's later complete real deployment Stop/Start/Promote/Rollback
report. This is a deterministic **offline JSON consistency** review. It
does not run any inference, change deployment state, read model outputs,
read access tokens, contact a service or attest actual hardware. The
results are **not sufficient to close P1 alone**: actual operator/browser
screenshots and manual review remain required.

## Preconditions

Finish the read-only and explicitly executed [P1 sequential deployment
acceptance](local-deployment-acceptance.md). You should now have TWO
*distinct* owner-only private JSON files in the Git-ignored
`reports/local/` directory:

- `real-ollama-smoke-<timestamp>.json` — reports all six actual
  Ollama native, Go JSON, Go SSE, digest and gateway counter checks.
- `deployment-sequential-<timestamp>.json` — reports complete
  Stop/Start/Promote/Rollback + actual inference, **not** the earlier
  Stop/Start-only report.

These reports must describe the same exact pinned original model tag and
64-character SHA-256 model digest. Check both by eye before accepting
them. The source reports must be regular files, mode 0600, no symlinks
and at most 64 KiB each.

## Validate offline

Use actual paths from your Mac; never type `<timestamp>` literally:

~~~sh
cd ~/metriqen-llm-serving-optimization
python3 -m tools.demo.p1_evidence_review \
  --smoke reports/local/real-ollama-smoke-20261019-083400.json \
  --lifecycle reports/local/deployment-sequential-YYYYMMDD-HHMMSS.json \
  --output "reports/local/p1-cli-review-$(date +%Y%m%d-%H%M%S).json"
~~~

The reviewer rejects missing SSE/digest checks, missing model promotion
or rollback, missing sequential reload assertions when selected, wrong
model tag/digest, malformed/duplicate JSON, world-readable artifacts and
reused report paths. Output is written exclusively (never overwritten)
as mode 0600 under `reports/local/` and contains only pre-selected
status fields, model names, the first 12 digest characters, and checked
assertion names. **No original private report contents are copied.**

A successful result prints:

~~~text
P1 private CLI evidence consistency: PASS
Real browser evidence / independent operator review: PENDING
P1 portfolio acceptance: NOT YET COMPLETE
~~~

This status verifies **internal consistency of two operator-sourced
files only**. Anyone can create matching JSON; it does not establish
authenticity, GPU/Metal execution, browser UX, raw video/screenshot
approval or benchmark speedups. The operator must review screenshots
and personally approve a **sanitized public evidence excerpt** before
a GitHub issue link or README claims P1 complete.

Do not upload either private file without reviewing it for personal
data. Do not check P2/P3 based on this report; the 4-phase
A/B/A/B performance experiment is a completely separate gate.
