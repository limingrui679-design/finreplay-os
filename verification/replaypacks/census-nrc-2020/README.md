# Census/HUD NRC boundary before the March 2020 housing-starts decline

- Replay ID: `census-nrc-2020-housing-starts-boundary-v1`
- Trace ID: `trace:f9e49daaed06fe042c5612ef39fcf02463e17a914cda3ec8f9633821c63aea39`
- Pack SHA-256: `c2cae95fd76a7c16943ba7c6e6b27c72b9ed9cd1047b41dcf7abceb497cee6aa`

## Truth boundary

Four actual engines ran over two locked Census/HUD NRC preliminary headline housing-starts levels available before the decision time. Reported release snapshots remain reported; the latest-persistence-or-repeat-known-headline-increase range remains inferred with no assigned probability. The two-headline difference is not the official month-over-month change, which uses a revised prior month. Official 90-percent sampling intervals are not used in the range. TrialCourt rejects retrospective promotion. The April 16 March value and February revision are held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, housing-market causal model, trading signal, production deployment, or external validation.

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
