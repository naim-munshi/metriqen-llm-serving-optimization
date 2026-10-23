"""Read-only, source-only portfolio readiness report.

This command deliberately cannot approve P1–P6, attest real Mac measurements,
mark production features shipped, publish a release, or read private reports.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
GATES = (
    ("P1", "Human-reviewed real inference, complete lifecycle and browser proof",
     "Run and independently inspect pinned real Mac model, SSE, rollback and screenshots."),
    ("P2", "Controlled repeated real-model experiment",
     "Review real A/B/A/B slots, AC power, thermals, drift and matched quality."),
    ("P3", "Metriqen-owned serving-path optimization and tradeoff evidence",
     "Validate an attributable controlled change; do not claim FIFO goodput speedup."),
    ("P4", "Real accessible browser demo",
     "Approve real desktop/mobile screenshots, keyboard walkthrough and demo video."),
    ("P5", "Operator reliability and security acceptance",
     "Inspect configured Redis/PostgreSQL/auth opt-ins and real gateway failure recovery."),
    ("P6", "Release decision and publication",
     "Approve licensing decision, review original evidence, protect main and publish v1.0.0."),
)
PUBLIC_DOCS = (
    "README.md", "CHANGELOG.md", "docs/PORTFOLIO_CURRENT_STATUS.md",
    "docs/PORTFOLIO_V1_ACCEPTANCE.md", "docs/PORTFOLIO_CASE_STUDY.md",
    "docs/operations/portfolio-final-mac-handoff.md",
)
LICENSE_DECISIONS = (
    "LICENSE", "LICENSE.md", "COPYING", "docs/release/owner-license-decision.md",
)


def collect_source_status(root: Path = ROOT) -> dict:
    """No network, git remote, private paths, prompts, tokens or subprocess."""
    docs_ready = all((root / name).is_file() for name in PUBLIC_DOCS)
    licensing_written = any((root / name).is_file() for name in LICENSE_DECISIONS)
    gates = [
        {
            "id": key,
            "status": "manual_acceptance_required",
            "acceptance": label,
            "next_evidence": action,
        }
        for key, label, action in GATES
    ]
    return {
        "schema": "metriqen.portfolio-readiness.v1",
        "scope": "source-only-offline",
        "portfolio_v1_released": False,
        "release_approved": False,
        "operator_mac_execution_verified": False,
        "gpu_autoscaling_deployed": False,
        "public_https_deployed": False,
        "real_google_oauth_verified": False,
        "docs_present": docs_ready,
        "owner_licensing_decision_file_present": licensing_written,
        "gates": gates,
        "conclusion": "BLOCKED: human-reviewed P1–P6 evidence and a release decision required",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only portfolio evidence gap report; never approves releases"
    )
    parser.add_argument("--json", action="store_true", help="emit sanitized JSON")
    args = parser.parse_args(argv)
    state = collect_source_status()
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
    else:
        print("Metriqen portfolio v1: BLOCKED (source-only audit, not an acceptance run)")
        print(f"Public documentation present: {state['docs_present']}")
        print("Owner licensing decision recorded: "
              f"{state['owner_licensing_decision_file_present']}")
        for gate in state["gates"]:
            print(f"{gate['id']} MANUAL REVIEW REQUIRED: {gate['next_evidence']}")
        print("No Mac, browser, Google OAuth, cloud GPU or release was verified.")
    # Not an execution failure: this is a read-only status reporter. Blocking
    # status is carried explicitly in the machine-readable result.
    return 0


if __name__ == "__main__":
    sys.exit(main())
