# SVB 2023 point-in-time boundary replay

- Replay ID: `svb-2023-seven-engine-v1`
- Trace ID: `trace:7c6888e9ecebd390acb2f7fecbcde816d7e50906632483215244acf0f17462bf`
- Pack SHA-256: `c62c22dcbd15e29592a10811117a565d2bf9bee34877a4fbcbf24994383efd35`

## Truth boundary

Seven actual engine implementations ran over a locked seven-record SEC fact set. Filer-reported balance-sheet values remain reported; ratios, network propagation, and the TrialCourt disposition are model-derived; execution and capital-allocation inputs are explicitly simulated research boundaries. This is a retrospective historical boundary replay, not a live 2023 system, causal failure attribution, trading performance, investment advice, client work, production deployment, or external validation. Runtime is excluded from deterministic pack identity and is recorded separately by the replay verifier.

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
