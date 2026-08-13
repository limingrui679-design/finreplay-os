# DOL archived weekly initial-claims supporting evidence

This directory proves live retrieval and strict validation of three archived U.S. Department of
Labor Unemployment Insurance Weekly Claims PDFs selected for a March 2020 initial-claims boundary.
It is separate from the capped formal adapter inventory:

- `dol.eta.archived_weekly_initial_claims` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the March 12 release reports `211,000` seasonally adjusted initial claims for the week ending
  March 7, down `4,000` from its revised prior level of `215,000`;
- the March 19 release reports `281,000` for the week ending March 14, up `70,000` from the
  unrevised `211,000`, and explicitly applies annual seasonal-factor revisions;
- the March 26 release reports `3,283,000` for the week ending March 21, up `3,001,000` from a
  prior level revised from `281,000` to `282,000`;
- each PDF must contain exactly nine pages, the official release identity, one 8:30 a.m. Eastern
  embargo timestamp, one initial-claims headline, matching USDL release number, and technical
  notes describing the advance and following-week revision process;
- the headline and any prior-week revision bridge must reconcile exactly;
- the exact archived bytes become eligible at the later of the stated embargo end or the PDF's
  official `Last-Modified` time, producing UTC boundaries of `2020-03-12T12:30:10Z`,
  `2020-03-19T12:30:00Z`, and `2020-03-26T12:46:21Z`;
- a later revised prior-week value never overwrites the earlier advance release snapshot;
- full PDFs remain in ignored content-addressed storage; the committed receipt retains hashes,
  URLs, sizes, retrieval times, warnings, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_dol_ui_claims.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/dol-ui-claims/live \
  --raw-store data/raw/supporting/dol-ui-claims \
  --output verification/supporting/dol-ui-claims/latest-summary.json
```

This evidence establishes internal source retrieval, PDF identity checks, exact release-snapshot
arithmetic, revision preservation, conservative timing, hashing, and local ingestion. The
separately verified `dol-ui-2020-initial-claims-boundary` proof uses it as supporting evidence for
the thirteenth counted scenario. Neither artifact establishes a forecast, calibrated range,
pandemic or labor-market causality, external validation, deployment, or investment results.
