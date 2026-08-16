# Adapter authoring guide

An adapter is a source contract, not merely an HTTP request. New adapters should fail closed when
the upstream identity, timing, schema, type, size, or license assumptions do not hold.

## Required contract

1. Declare a stable adapter ID and the authoritative publisher.
2. Allowlist HTTPS origins, paths, redirects, media types, and bounded response sizes.
3. Preserve raw response bytes in ignored content-addressed storage when redistribution is not
   permitted; commit only safe receipts and hashes.
4. Normalize every fact with separate economic and knowledge/availability times.
5. Assign temporal coverage explicitly: `latest_only`, `immutable_event`, or the supported
   vintage-aware class. Do not infer historical eligibility from a date field alone.
6. Attach an evidence class and source-specific license/attribution boundary.
7. Reject missing, duplicated, malformed, or semantically inconsistent records.
8. Test valid responses and failures with local fixtures before an opt-in live run.

## Formal live catalog eligibility

A connector enters the capped formal catalog only after the repository's live-verification path
retrieves and validates its official source and emits a current receipt. Scenario-specific archive
connectors can remain useful without being counted as formal live adapters.

After a counted receipt changes, regenerate the installable matrix and verify it is current:

```bash
python scripts/verify_live_receipts.py
python scripts/build_user_catalogs.py --write
python scripts/build_user_catalogs.py --check
```

## Historical replay eligibility

Historical eligibility requires evidence for when the exact value became knowable. Examples
include an immutable event with a defensible release timestamp or a dated archived release whose
content is bound to that publication. A current API response carrying an old observation date is
not sufficient.

## Review checklist

- Is the publisher official and the requested product unambiguous?
- Are redirects and content types constrained?
- Is pagination bounded and duplicate-safe?
- Are units, revisions, missing values, and annual/special rows handled explicitly?
- Can a revised value overwrite a prior vintage? It must not.
- Does the receipt disclose the source and temporal limitations?
- Do tests demonstrate at least one honest failure path?
