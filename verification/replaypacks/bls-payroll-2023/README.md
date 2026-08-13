# BLS Employment Situation payroll headline boundary, early 2023

- Replay ID: `bls-2023-payroll-release-boundary-v1`
- Trace ID: `trace:93586d2362769bcfd48846586d4eefa3b7fbdf6a4b596e62297e775971802013`
- Pack SHA-256: `bfc14686983f676195b1175e5efecc6b8f4e8619d1ce937f3b1d871a972ee343`

## Truth boundary

Four actual engines ran over four locked headline facts from two archived BLS Employment Situation releases available before the decision time. Reported payroll changes and unemployment rates remain reported; the next-release payroll range is a two-point arithmetic heuristic with no assigned probability; TrialCourt rejects retrospective promotion. The March 10 release is held only in a disjoint post-decision event lock. This is not a forecast, calibrated interval, causal labor-market model, policy recommendation, trading signal, production deployment, or external validation.

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
