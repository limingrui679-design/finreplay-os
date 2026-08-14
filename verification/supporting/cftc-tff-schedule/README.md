# CFTC TFF scheduled-release supporting evidence

This directory proves current official retrieval and five-artifact validation for the three U.S.
2-year Treasury-note Futures Only rows used by the CFTC open-interest boundary scenario. It is a
scenario-specific connector outside the capped formal 30-adapter inventory:

- `cftc.cot.tff_scheduled_ust2y` requests exactly three rows for contract code `042601` from the
  official TFF Socrata view, then cross-checks all selected positions, weekly changes, trader
  counts, contract units, mode, contract code, and report dates against the 2026 annual compressed
  Futures Only file;
- the official current 2026 schedule lists July 17, 24, and 31 and states that COT reports are
  released at 3:30 p.m. Eastern using the previous Tuesday's data; each timestamp is validated as
  EDT under `America/New_York`;
- the schedule calls itself tentative and CFTC publishes no row-level actual-publication log, so
  the records say **official scheduled availability**, not independently confirmed actual
  publication to the second; their availability confidence is `0.98`, not `1.0`;
- the selected July 14, 21, and 28 rows report total Futures Only open interest of `4,465,199`,
  `4,335,075`, and `4,406,588` contracts; the latter two reported weekly changes reconcile exactly
  to `-130,124` and `+71,513` contracts;
- the COT policy page must retain the historical-data immutability statement, Form 40
  classification basis, lack of position-reason knowledge, and reclassification/entry/exit
  caveats;
- the complete four-page TFF explanatory PDF must retain its four category definitions, spreading
  definition, trader-count overlap, and warning that staff classifies traders rather than every
  trading activity;
- the `$200,000 FACE VALUE` label remains source text only. FinReplay does not multiply it into
  notional exposure and does not infer direction, intent, P&L, volume, executions, probability,
  causality, forecast skill, adoption, or user impact;
- raw response hashes, including changing HTML wrappers and the growing annual ZIP, remain in live
  receipts and ignored content-addressed storage. Stable financial records bind the API artifact
  and normalized selected-row/document semantics, so harmless wrapper drift cannot mutate history.

Rebuild twice and verify the newest live receipt with:

```bash
python scripts/validate_cftc_tff_schedule.py
python scripts/validate_cftc_tff_schedule.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/cftc-tff-schedule/live \
  --raw-store data/raw/supporting/cftc-tff-schedule \
  --output verification/supporting/cftc-tff-schedule/latest-summary.json
```

The committed latest summary records `inserted_records=0` and `idempotent_records=3` on the second
live run. This establishes official current retrieval, cross-format semantic agreement, scheduled
time anchoring, content addressing, and local idempotence. It does not upgrade a tentative schedule
into an actual-publication log or establish market impact, external validation, deployment, or
real-user outcomes.
