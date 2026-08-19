# FinReplay OS public evidence site

This directory contains the read-only public evidence surface for FinReplay OS.
It summarizes the currently reproducible repository evidence without presenting
internal validation as external adoption, domain review, or production use.

## What the site exposes

- the seven implemented engines;
- the 30 official-source adapters and 30 real-data scenarios;
- the sealed 1,014,736,394-row SEC processing receipt;
- the current internal quality-gate results and their limitations;
- direct artifact hashes and an independent-review protocol;
- 30 stable scenario detail routes with structured claims and boundaries;
- a generated 10-path capability directory with direct, transferable, and boundary-only labels;
- 30 deterministic ReplayPack downloads plus a self-hashed manifest; and
- a 7.49 MB review-source snapshot bound to commit `044661bf0d4d` and SHA-256
  `380a33d52890a09a6f686e3fa5d522d0e2b2e5c9022c7421091e61e26657e6f0`.

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

The rendered-page tests assert all 30 cards and detail routes, the capability
directory and catalog identity, documentation, claim and hash identity, all 30
ZIP digests, and the fixed independent-review snapshot. The site is intentionally
read-only, uses system fonts, and requires no credentials, database, object
storage, or remote font fetch during the build.

## Hosting boundary

`.openai/hosting.json` declares no D1 or R2 bindings. A public URL is not evidence
of independent reproduction, external review, real-user impact, or production
deployment; those states must be recorded separately when they actually occur.
