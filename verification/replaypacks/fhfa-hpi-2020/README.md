# FHFA HPI boundary before the March 2020 national monthly change deceleration

- Replay ID: `fhfa-hpi-2020-house-price-change-boundary-v1`
- Trace ID: `trace:0546ce017baf309b17b31cb58eeb2d38b19413b3df33a9a4fc83b2f650e46d4d`
- Pack SHA-256: `338c19be4990422caa75c2e7ddc2ff34bc167e8b61c71e502916731aaa111e35`

## Truth boundary

Four actual engines ran over the exact January and February national purchase-only seasonally adjusted monthly HPI changes from their first verified FHFA reports, both knowable at the April 22 decision time. Reported values remain reported; the latest-change-persistence-or-repeat-known-increase envelope remains inferred with no probability. The May 26 March value and the report's January and February revision snapshot stay only in a disjoint event lock. The January report footer's '9AM EST' wording is retained alongside the controlling official schedule's 9 a.m. ET rule; it is not silently harmonized. This is not a forecast, calibrated interval, universal home-price measure, contemporaneous COVID effect, causal model, trading signal, deployment, external validation, or user-impact claim.

This is a static research ReplayPack. Historical replay is not live trading, simulated output is
not realized performance, public data is not a client engagement, and hashes/tests are not
external validation.

## Verify

From a FinReplay OS checkout:

```bash
finreplay verify-replaypack /path/to/this/directory
```

`report.json` is the machine-readable artifact graph. `index.html` is the accessible read-only
report. `checksums.sha256` uses portable relative paths only.
