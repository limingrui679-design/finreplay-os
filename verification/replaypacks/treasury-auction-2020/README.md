# Treasury 91-day bill auction-rate boundary before the March 2020 zero result

- Replay ID: `treasury-auction-2020-zero-rate-boundary-v1`
- Trace ID: `trace:402e025e428e17a945b90b2e2279bf242c2534eec4dee6ab96103fe3bdba0b09`
- Pack SHA-256: `84adfcfbada5dc7676e46a2aca7545b73f609f4c80eb8a99d97aed470ef35c60`

## Truth boundary

Four actual engines ran over two locked Treasury 91-day bill auction facts available before the decision time. Reported high rates remain reported; the persistence-or-repeat-known-decline range remains inferred with no assigned probability; TrialCourt rejects retrospective promotion. The March 23 zero-rate result is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, auction-demand or policy causal model, trading signal, production deployment, or external validation.

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
