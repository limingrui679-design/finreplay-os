# PacWest 2023 pre-disclosure funding boundary replay

- Replay ID: `pacwest-2023-seven-engine-v1`
- Trace ID: `trace:ea712e982b91244c741174069afa890549ef4182a5c8182965eefb5fec21aab3`
- Pack SHA-256: `2d0fcab57a64d709317fd4fe22f296d2a756e4638053a275d057a7af5f7a9599`

## Truth boundary

Seven actual engine implementations ran over seven locked SEC filing facts for PacWest Bancorp. Filer-reported values remain reported; ratios, network propagation, and TrialCourt disposition are model-derived; execution and allocation inputs are explicitly simulated. The separate post-decision official event lock is not a ReplayPack input. This is a retrospective boundary replay, not causal failure attribution, a historical trading signal, investment advice, client work, production deployment, or external validation.

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
