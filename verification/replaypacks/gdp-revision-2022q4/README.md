# U.S. GDP 2022 Q4 advance-to-second-estimate revision boundary

- Replay ID: `gdp-2022q4-revision-boundary-v1`
- Trace ID: `trace:0318d33e4e29fce0de5555c94e4cef24a4605bddaca362a57637319322ed194d`
- Pack SHA-256: `5f9f64b719003d2677c1fb5bef7e33be745c2644356a364d53faa8fbfed9a8a9`

## Truth boundary

Four actual engine implementations ran over four locked native-vintage ALFRED GDP facts available before the historical decision time. Reported estimates remain reported; revision deltas and the symmetric boundary are arithmetic inferences; TrialCourt rejects retrospective promotion. The later Q4 second estimate is held only in a disjoint post-decision event lock and is not a ReplayPack input. This is not a forecast, probability distribution, causal model, trading signal, policy recommendation, production deployment, or external validation.

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
