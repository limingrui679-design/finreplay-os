# ReplayStudio seven-engine deterministic fixture

- Replay ID: `replaystudio-seven-engine-golden`
- Trace ID: `trace:1d61844b4593ebe6baf913bb780b94cdc51825d4de764864c7cdf80531c80f0d`
- Pack SHA-256: `e363424d7c92d7c14792430c28597b4beb7f399edfbb0078b4d8ab4bfde2a828`

## Truth boundary

This golden pack proves deterministic packaging, traceability, static rendering, and tamper detection over synthetic fixture artifacts only. It does not prove an historical event reconstruction, source authenticity, external review, production deployment, user impact, or realized financial performance.

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
