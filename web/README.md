# FinReplay OS public evidence site

This directory contains the read-only public evidence surface for FinReplay OS.
It summarizes the currently reproducible repository evidence without presenting
internal validation as external adoption, domain review, or production use.

## What the site exposes

- the seven implemented engines;
- the 30 official-source adapters and 30 real-data scenarios;
- the sealed 1,014,736,394-row SEC processing receipt;
- the current internal quality-gate results and their limitations;
- direct artifact hashes and an independent-review protocol; and
- a downloadable machine-readable review manifest; and
- a 6.66 MB review-source ZIP bound to commit `62bf793d017b` and SHA-256
  `781df836758a84a37ee65cd76fcb1bfd185e32ebef36bda566cea5c1c566a418`.

## Local verification

Node.js `>=22.13.0` is required.

```bash
npm ci
npm run build
npm test
```

For local development:

```bash
npm run dev
```

The rendered-page tests assert that all 30 scenarios are present and that the
19 observed policy breaches are distinguished from the 11 in-range evaluation
cases. The site is intentionally read-only and uses no credentials, database,
or object storage.

## Hosting boundary

`.openai/hosting.json` declares no D1 or R2 bindings. A public URL is not evidence
of independent reproduction, external review, real-user impact, or production
deployment; those states must be recorded separately when they actually occur.
