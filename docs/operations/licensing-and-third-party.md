# Metriqen licensing and third-party boundaries

**Decision recorded October 29, 2026:** The repository owner requested a
permissive license and the original eligible Metriqen source and
documentation are published under the [MIT License](../../LICENSE),
**Copyright © 2026 Md Naim**.

MIT is a permissive **copyright license for licensed materials**, allowing
commercial use, modification, distribution, private use and sublicensing,
subject to retaining its copyright and license notices. The text includes
an "AS IS" warranty and liability disclaimer.

## What the repository LICENSE covers

- Original software and accompanying documentation in this repository to
  the extent that their legitimate copyright holder has licensed them.
- Contributions only where their author has the authority to contribute
  them under MIT-compatible terms. Copyright of independent contributors
  is not silently transferred by a pull request.
- There are no automatic fees, model-hosting terms, user-data rights,
  ownership transfers or production deployment guarantees.

## Independent rights — not relicensed by this file

- **Ollama and underlying language models** (such as Llama, DeepSeek or
  Mistral): obtaining/invoking/benchmarking model tags does not license
  their weights, tokenizer files, model outputs or trademarks under
  Metriqen's MIT license. Check the **exact model's own published terms**
  before redistributing or commercially deploying it.
- **Third-party Go/Python packages, container images and browser tools**:
  retain their own licenses, notices, versions, and terms. This
  repository's MIT does not replace them. Lockfiles, package metadata,
  container images and distributed binaries should be reviewed before
  making any bundled binary or third-party asset release.
- **External datasets, prompts, screenshots, sample transcripts and
  reports**: apply source-owner permissions and privacy rules
  independently. Do not publish private `reports/local/` content, tokens,
  browser account screenshots, model outputs, unique host details or
  private acceptance evidence simply because source code is MIT.
- **Branding**: this code copyright license does not claim a trademark
  grant to use the Metriqen product name, logo or identity.
- **Patents**: the standard MIT text is not the same as an explicit
  Apache-2.0 contributor patent grant. Assess patent needs separately
  for a future commercial product.

## Checks before publishing a code or binary release

1. Confirm the current `LICENSE` copyright notice and record the
   version/SHA to which the license applies.
2. Check the authorship and permissions of any contributions or
   pre-existing third-party files. Remove or license them independently
   if their rights cannot be confirmed.
3. Verify separately redistributed dependency license notices and model
   terms. Do **not** bundle model weights or sample data by default.
4. Keep real Mac/screenshot/benchmark evidence private until the
   repository owner explicitly reviews and approves redaction/publication.
5. Follow the six independent [portfolio acceptance gates](../PORTFOLIO_V1_ACCEPTANCE.md).
   A public MIT license resolves **only the owner-license decision portion
   of P6**. It does not establish real model/browsers, security, performance,
   branch protection, or release approval; no `v1.0.0` tag is created.

**Future licensing changes:** releasing current eligible code under MIT
grants permissions for copies already distributed under that license;
changing terms for future versions does not automatically revoke prior MIT
grants. This document is operational guidance, not individualized legal
advice.
