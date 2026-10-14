## Summary

What does this change do? Identify the implementation boundary and link any relevant issue/ADR.

## Motivation

What problem is being addressed? Why this change instead of a simpler alternative?

## Verification

- [ ] Applicable Python/Go unit tests and integration tests pass (list exact commands).
- [ ] CI status has been checked.
- [ ] New/changed behavior has regression coverage or a documented reason it cannot be tested.
- [ ] Documentation, examples and environment-variable descriptions are updated as needed.

## Safety and reproducibility

- [ ] No secrets, raw private prompts, completions or reports under `reports/local/` are committed.
- [ ] Loopback-only and authentication boundaries are unchanged or explicitly justified.
- [ ] Any model/version/hardware/quality/performance statement is backed by real recorded evidence.
- [ ] Synthetic fixture results are **not** presented as real GPU measurements.
- [ ] Any new queue, model or decoding setting is identified as a changed benchmark variable.

## Known limitations / follow-up

What remains unimplemented or requires operator validation? Do not claim production readiness solely because tests pass.
