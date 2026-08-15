# Changelog

All notable changes to FinReplay OS are documented here. The project remains
pre-alpha; entries describe repository evidence, not production readiness or
investment performance.

## [Unreleased]

### Added

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

- README scenario totals and all 30 evidence summaries now match the verified
  scenario catalog.
- PyArrow now requires the patched 23.0.1 release line.
- Strict typing now covers the complete `src` and `tests` trees without errors.

### Security

- CI audits declared Python dependencies and scans repository history for
  committed secrets.
- CI upgrades pip to a patched release before installation and audits the complete resolved
  development environment, including the installer and review-schema tooling.
