# Security policy

## Supported version

Security fixes are applied to the current `main` branch. FinReplay OS is
pre-alpha research software and must not be used as a production trading,
allocation, compliance, or institutional risk system.

## Reporting

Use the repository's private **Security → Report a vulnerability** flow. Do not
open a public issue containing exploit details, access tokens, personal data,
licensed source responses, or local evidence artifacts.

Include the affected file or command, a minimal reproduction, impact, and any
mitigation already tested. Please preserve source-license and evidence-boundary
requirements when sharing a fixture.

## Security and trust boundaries

- Official-source retrieval is allowlisted, bounded, and expected to fail
  closed when identity, timing, type, size, or schema checks do not hold.
- Full source responses may be download-only and remain in ignored local
  storage; committed receipts and hashes do not grant redistribution rights.
- ReplayPack hashes and internal rebuilds prove repository behavior, not source
  authenticity, methodological correctness, external review, forecast skill,
  investment performance, or real-world impact.
- Secrets, SEC contact details, credentials, and private evidence must never be
  committed. CI performs dependency, static-analysis, and secret checks.
