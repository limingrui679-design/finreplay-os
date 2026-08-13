# FOMC federal-funds target-range boundary, spring 2023

- Replay ID: `fomc-2023-target-range-boundary-v1`
- Trace ID: `trace:4d1e9d9df61c5baffffb8bced078829ec4ba1064f07e7c65f68ba9a1368f7df7`
- Pack SHA-256: `4421dbfebff7e2e3e66124090d27cd6b9031bcb89c9b8726cf28d8fdf358f7e5`

## Truth boundary

Four actual engines ran over four locked target endpoints from two archived FOMC statements available before the decision time. Official target ranges remain reported; the next upper-bound range is an arithmetic heuristic with no assigned probability; TrialCourt rejects retrospective promotion. The May 3 upper target is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, causal monetary-policy model, policy recommendation, trading signal, production deployment, or external validation.

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
