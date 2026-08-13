# U.S. Treasury 2-year/10-year curve inversion boundary, March 2023

- Replay ID: `treasury-curve-2023-inversion-boundary-v1`
- Trace ID: `trace:582d084d96526fffb25f544af2fb1ceca86cd71b53578c51823c04b5dbfed41c`
- Pack SHA-256: `f05a5b1f0a143f3bd99dbd1777efcb814f791a1da8d1f8190ece4a73d7ad3ccb`

## Truth boundary

Four actual engines ran over four locked DGS2/DGS10 facts from two native ALFRED vintages available before the decision time. Reported yields remain reported; the DGS10-minus-DGS2 spreads and next-spread range remain inferred with no assigned probability; TrialCourt rejects retrospective promotion. Two March 15 yields are held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, causal yield-curve model, policy recommendation, trading signal, production deployment, or external validation.

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
