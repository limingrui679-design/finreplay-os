# EIA commercial crude stock boundary before the April 2020 inventory build

- Replay ID: `eia-wpsr-2020-crude-stock-boundary-v1`
- Trace ID: `trace:02607eb05647ea0f55341031c4c55035a27f86fadfd8e20ffada0a15dd1a8780`
- Pack SHA-256: `baf7d20f12097dbc58745d1e2eeb19fb0229ff062c987425addddc2d96c1f7c4`

## Truth boundary

Four actual engines ran over two locked WPSR commercial-crude-stock facts available before the decision time. Reported stocks remain reported; the next-stock range remains inferred with no assigned probability; TrialCourt rejects retrospective promotion. The April 17 stock is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, oil-market causal model, trading signal, production deployment, or external validation.

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
