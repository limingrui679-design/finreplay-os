# BLS CPI-U all-items release boundary, early 2023

- Replay ID: `bls-2023-cpi-release-boundary-v1`
- Trace ID: `trace:8634250349fc349378cf09f74ae129613d54fdd87257232ccd5547b62f1b1bb6`
- Pack SHA-256: `fac8e503c37804217d3db00634330cdb62ac02466f7a58a1b04e703d6ffc6d06`

## Truth boundary

Four actual engines ran over four locked headline facts from two archived BLS Consumer Price Index releases available before the decision time. Reported monthly and 12-month changes remain reported; the next-release monthly-change range is a two-point arithmetic heuristic with no assigned probability; TrialCourt rejects retrospective promotion. The March 14 release is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, causal inflation model, policy recommendation, trading signal, production deployment, or external validation.

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
