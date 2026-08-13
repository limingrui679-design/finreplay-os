# U.S. Treasury General Account cash-balance boundary, June 2023

- Replay ID: `treasury-tga-2023-cash-boundary-v1`
- Trace ID: `trace:077515977a23370bda2bc75cd4f7b9905f7f46e7b202b5807a67b03b1e7322bb`
- Pack SHA-256: `a74daab723834be217a925b19bc5306094bc9d7eb3791085a6a067fc504ed18e`

## Truth boundary

Four actual engines ran over two locked Treasury DTS Table I facts available before the decision time. Reported balances remain reported; the next-balance range remains inferred with no assigned probability; TrialCourt rejects retrospective promotion. The June 2 balance is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, debt-limit causal model, fiscal-solvency measure, trading signal, production deployment, or external validation.

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
