# New York Fed SOFR level boundary before the September 2019 spike

- Replay ID: `nyfed-sofr-2019-spike-boundary-v1`
- Trace ID: `trace:7402c1952f5bbaf31f5f5d89a85a8864f64ee146bd142e25da1872f4292ee636`
- Pack SHA-256: `00475798a6bdfc0b713a272d340b0586d8b647f95c8a11435d809777037b106e`

## Truth boundary

Four actual engines ran over two locked final SOFR facts available before the decision time. Reported rates remain reported; the next-rate range remains inferred with no assigned probability; TrialCourt rejects retrospective promotion. The September 17 rate is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, repo-market causal model, trading signal, production deployment, or external validation.

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
