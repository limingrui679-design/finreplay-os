# U.S. Census MARTS boundary before the March 2020 retail-sales decline

- Replay ID: `census-marts-2020-retail-sales-boundary-v1`
- Trace ID: `trace:003837c4d189436a7549faef47d21b3a8a4b9a8635106ef249a1b0fc67ccd00f`
- Pack SHA-256: `356f456c96a448e7a7c9f9e27f2436b317e1b94b6674ce331f150d8adea9f9bd`

## Truth boundary

Four actual engines ran over two locked U.S. Census MARTS monthly-change facts available before the decision time. Reported release-snapshot values remain reported; the repeat-known-decrease-or-persistence range remains inferred with no assigned probability; TrialCourt rejects retrospective promotion. The April 15 March value and its revision of February are held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, retailer or consumer causal model, trading signal, production deployment, or external validation.

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
