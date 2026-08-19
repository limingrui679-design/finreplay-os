# Changelog

All notable changes to FinReplay OS are documented here. The project remains
pre-alpha; entries describe repository evidence, not production readiness or
investment performance.

## [Unreleased]

No unreleased changes.

## [0.2.0a1] - 2026-08-20

### Added

- A self-hashed, installable 10-path capability catalog with direct,
  transferable, and boundary-only scope labels, curated scenario links,
  evidence locators, negative claims, Python accessors, CLI discovery, and a
  generated Markdown map.
- A public capability directory plus capability-aware replay filtering and
  per-scenario capability lenses.
- A validated 30-case explorer with primary methods, decision questions, ten overlapping
  analytical dimensions, five cross-case pathways, generated documentation, CLI discovery, and
  an outcome-composition visualization.
- A system architecture overview covering layer authority, generated surfaces,
  extension contracts, and the fail-closed model.
- A one-command `make verify` contributor gate spanning generated artifacts, Python linting,
  strict typing, branch-aware coverage, evidence checks, dependency audits, and the public web
  build and tests.

### Changed

- The README, quickstart, examples, and public documentation now let readers
  begin with an analytical question rather than an undifferentiated scenario
  count.
- The public site now uses local system font stacks, removing the build-time
  dependency on a remote font service.
- GitHub verification now runs a separate locked Node 22 build, rendered-route tests, and npm
  audit; wheel smoke tests also exercise the packaged capability and scenario-explorer resources.
- The clean-commit site-readiness receipt now derives scenario, outcome, dimension, pathway, and
  capability counts from generated catalogs instead of obsolete page-source patterns.

## [0.1.0a1] - 2026-08-16

### Added

- An installable catalog for 30 formal live adapters and 30 byte-locked offline
  scenario runners.
- Unified `adapter`, `scenario`, `replaypack`, `evidence`, and three-minute
  offline `demo` CLI surfaces while retaining the original command aliases.
- Runnable Python examples, a point-in-time notebook, authoring guides, a
  generated eligibility matrix, community issue forms, a roadmap, and citation
  metadata.
- A wheel/sdist release workflow with fresh-environment artifact verification,
  deterministic checksums, GitHub Release support, and an explicitly gated
  trusted-publishing path for PyPI.
- Counted Census/HUD new-home-sales, EIA WNGSR working-gas, BLS PPI
  final-demand, CFTC TFF UST 2-year open-interest, and Federal Reserve H.4.1
  central-bank-liquidity-swap boundaries, plus the BLS all-import price-change
  and all-export price-change boundary replays, bringing the eight-gate scenario catalog to 30 of 30
  planned
  scenarios.
- Apache-2.0 license text, contribution and security policies, continuous
  integration, CodeQL, dependency review, secret scanning, and Dependabot.
- A public independent-review Issue form, schema-backed record validator, empty-catalog boundary,
  and CI validation route. These are review intake infrastructure, not external evidence.

### Changed

- The README now leads with a runnable offline demo, the evidence explorer
  image, verified surfaces, and direct documentation routes.
- Package metadata now declares version `0.1.0a1`, project URLs, typed-package
  support, and wheel resources for all bundled input locks.
- Dependabot updates are grouped monthly to avoid five simultaneous automated
  pull requests obscuring human review.
- README scenario totals and all 30 evidence summaries now match the verified
  scenario catalog.
- PyArrow now requires the patched 23.0.1 release line.
- Strict typing now covers the complete `src` and `tests` trees without errors.

### Security

- CI audits declared Python dependencies and scans repository history for
  committed secrets.
- CI upgrades pip to a patched release before installation and audits the complete resolved
  development environment, including the installer and review-schema tooling.
