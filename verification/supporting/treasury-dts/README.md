# Treasury Daily Treasury Statement supporting evidence

This directory proves live retrieval and strict parsing of the three archived Treasury report PDFs
used by the TGA cash-boundary scenario. It is separate from the capped formal adapter inventory:

- `treasury.dts.published_report` is a scenario-specific supporting source, not a thirty-first
  counted adapter;
- the verified report dates are May 31, June 1, and June 2, 2023;
- the reported TGA closing balances are respectively 48,512, 22,892, and 23,368 million dollars;
- each parser run also verifies opening balance plus deposits minus withdrawals equals closing
  balance;
- Treasury's stated following-business-day 4:00 p.m. deadline becomes the conservative knowledge
  time in `America/New_York`; it is not asserted to be the exact publication instant;
- full PDFs are download-only and remain in ignored content-addressed storage; committed receipts
  retain hashes, URLs, sizes, retrieval times, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_treasury_dts_reports.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/treasury-dts/live \
  --raw-store data/raw/supporting/treasury-dts \
  --output verification/supporting/treasury-dts/latest-summary.json
```

This evidence establishes internal source retrieval, PDF identity checks, Table I extraction,
arithmetic reconciliation, hashing, and local ingestion. It does not establish a forecast,
causality from debt-limit negotiations, external validation, deployment, or investment results.
