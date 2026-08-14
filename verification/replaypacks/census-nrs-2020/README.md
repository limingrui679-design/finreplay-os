# Census/HUD NRS boundary before the March 2020 new-home-sales decline

- Replay ID: `census-nrs-2020-new-home-sales-level-boundary-v1`
- Trace ID: `trace:3954cc711c3352c64a9af890452323f9318a8a5dcfe936263b5811392aa77f6e`
- Pack SHA-256: `87ea7da019f6ad6255f0375650e9906efd7695108d13618d9630ce0685483d4d`

## Truth boundary

Four actual engines ran over official archived Census/HUD NRS PDFs. Range construction uses only the 800,000-unit revised January and 765,000-unit initial February SAAR values co-published in the March 24 decision snapshot; the 764,000 January initial release is revision lineage only. Reported facts remain reported, while the 730,000-to-765,000 continuation envelope remains inferred with no probability. The April 23 March event and revisions stay only in a disjoint event lock. This is not an official forecast, sampling-confidence interval, calibrated interval, causal or housing-market model, COVID effect, transaction count, closing, trading signal, deployment, external validation, or user-impact claim.

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
