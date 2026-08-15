# Completed independent-review records

There are currently no completed independent-review records in this directory. This README is
workflow documentation, not external evidence.

A reviewer should first open the repository's **Independent review report** issue form. After the
maintainer records a disposition or resolving commit and the same reviewer independently rechecks
it, the reviewer may add one JSON record conforming to
`verification/review/independent-review.schema.json`.

Before submitting a pull request, run:

```bash
.venv/bin/python scripts/validate_independent_review_records.py \
  verification/review/records/<review-id>.json
```

Schema validity, Git ancestry, hashes, an Issue, or a pull request do not authenticate the reviewer
or establish general certification. Identity details belong in a public record only with the
reviewer's consent.
