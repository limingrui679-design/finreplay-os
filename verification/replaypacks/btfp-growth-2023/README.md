# Federal Reserve BTFP early weekly growth boundary, March 2023

- Replay ID: `btfp-2023-early-growth-boundary-v1`
- Trace ID: `trace:48018b32d8fec0765cb14afed5aef57beec65b77ae5629b10e05e3ab08ec68c2`
- Pack SHA-256: `f595429d8a547a669f36b84ee6058933eb9c12ec5ee5a19632d8f510fe8b0141`

## Truth boundary

Four actual engines ran over four locked BTFP facts from two archived Federal Reserve H.4.1 releases that were conservatively knowable at the decision time. Reported balances remain reported; growth and the next-week envelope are arithmetic inferences with no assigned probability; TrialCourt rejects retrospective promotion. The March 30 release is held only in a disjoint post-decision event lock. This is not a forecast, systemic-stress attribution, causal model, policy recommendation, trading signal, production deployment, or external validation.

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
