"""Offline four-phase P2 bundle review from four operator-produced phase dirs.

Strictly validates all A1/B1/A2/B2 preflights and raw timed evidence before
creating private aggregate reports. No model calls or system mutations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from tools.benchmark import abab_preflight_gate, abab_analysis, abab_review, phase_run

PHASES = ("A1", "B1", "A2", "B2")


class ReviewError(ValueError):
    """Emit fixed codes only; never include private report contents."""


def _phase_paths(directories: dict[str, Path]) -> tuple[dict[str, Path], dict[str, Path]]:
    if set(directories) != set(PHASES):
        raise ReviewError("four_distinct_phases_required")
    root = phase_run.PRIVATE.resolve()
    locations = [p.resolve() for p in directories.values()]
    if len(set(locations)) != 4:
        raise ReviewError("phase_directories_must_be_distinct")
    manifests = {}
    preflights = {}
    for phase in PHASES:
        directory = directories[phase]
        resolved = directory.resolve()
        if (directory.is_symlink() or not directory.is_dir() or resolved == root
                or not resolved.is_relative_to(root)):
            raise ReviewError("phase_directory_missing_unsafe_or_outside_private_root")
        preflight = directory / "preflight.json"
        manifest = directory / "sweep/evidence-manifest.json"
        if (not preflight.is_file() or preflight.is_symlink() or
                not manifest.is_file() or manifest.is_symlink()):
            raise ReviewError("phase_preflight_or_manifest_missing")
        preflights[phase] = preflight
        manifests[phase] = manifest
    return manifests, preflights


def review(directories: dict[str, Path]) -> tuple[dict, dict, str]:
    manifests, preflights = _phase_paths(directories)
    # Required: validates raw per-run reports and reference-quality evidence
    # plus non-overlapping timed chronology and preflight <=15m before first run.
    binding = abab_preflight_gate.validate(manifests, preflights)
    analysis = abab_analysis.analyze(*(manifests[name] for name in PHASES))
    markdown = abab_review.render(analysis)
    if (binding.get("classification") != "operator_sourced_preflight_consistency_only"
            or set(binding.get("phases", {})) != set(PHASES)
            or not isinstance(markdown, str) or not markdown.strip()):
        raise ReviewError("four_phase_analysis_incomplete")
    return binding, analysis, markdown


def _write_private(path: Path, contents: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(contents)


def finalize(directories: dict[str, Path], output: Path) -> None:
    root = phase_run.PRIVATE.resolve()
    resolved = output.resolve()
    if (resolved == root or not resolved.is_relative_to(root)
            or output.is_symlink() or output.exists()):
        raise ReviewError("private_output_directory_must_be_new")
    if any(resolved == path.resolve() for path in directories.values()):
        raise ReviewError("output_must_be_separate_from_input_phase")
    # Do not create any output before all four evidence bundles pass.
    binding, analysis, markdown = review(directories)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    _write_private(output / "abab-preflight-binding.json",
                   json.dumps(binding, sort_keys=True, indent=2) + "\n")
    _write_private(output / "abab-analysis.json",
                   json.dumps(analysis, sort_keys=True, indent=2) + "\n")
    _write_private(output / "abab-review.md", markdown)
    print("P2 four-phase private evidence consistency: PASS")
    print("Saved three owner-only reports under:", output)
    print("NOT ATTESTED: actual runtime slots, genuine Mac/GPU execution, or a causal speedup.")
    print("P2 portfolio acceptance requires human slot/power/reviewer checks.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review A1/B1/A2/B2 private phase directories offline")
    for phase in PHASES:
        parser.add_argument("--" + phase.lower() + "-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    directories = {phase: getattr(args, phase.lower() + "_dir") for phase in PHASES}
    try:
        finalize(directories, args.output_dir)
        return 0
    except (ReviewError, abab_preflight_gate.PreflightEvidenceError,
            OSError, ValueError, KeyError, TypeError):
        print("P2 four-phase review blocked: incomplete, invalid, or inconsistent private phase evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
