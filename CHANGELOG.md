# Changelog

All notable changes to FinReplay OS are documented here. The project remains
pre-alpha; entries describe repository evidence, not production readiness or
investment performance.

## [Unreleased]

### Added

- Counted Census/HUD new-home-sales, EIA WNGSR working-gas, and BLS PPI
  final-demand boundary replays, bringing the eight-gate scenario catalog to
  26 of 30 planned scenarios.
- Apache-2.0 license text, contribution and security policies, continuous
  integration, CodeQL, dependency review, secret scanning, and Dependabot.

### Changed

- README scenario totals and all 26 evidence summaries now match the verified
  scenario catalog.
- PyArrow now requires the patched 23.0.1 release line.
- Strict typing now covers the complete `src` and `tests` trees without errors.

### Security

- CI audits declared Python dependencies and scans repository history for
  committed secrets.
