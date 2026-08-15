# FinReplay OS independent review protocol

This protocol defines the only external evidence that can complete acceptance gate G. It is
designed to make a real criticism more valuable than a courtesy endorsement.

## What counts

A completed review must be performed by a person who did not author the reviewed FinReplay code
or prepare the maintainer's recorded outputs. The public record must establish the reviewer's
relevant qualification, the exact source revision, the commands they personally ran, and one
substantive issue they identified. The issue must then be confirmed or rebutted with evidence,
resolved when confirmed, and independently rechecked on the resolving revision.

All of the following are required:

1. A named source commit and a SHA-256-verified source archive or an equivalent fresh clone.
2. A fresh environment created by the reviewer, with OS, architecture, runtime, and lockfile hash.
3. Exact commands, exit codes, and hashes of retained stdout and stderr—not pasted maintainer logs.
4. At least one real reproducibility, method, boundary, documentation, or usability issue.
5. A resolving commit or an evidence-backed explanation that the reported issue is not a defect.
6. A second reviewer-run check against that resolution, including its command-output hashes.
7. A completed record conforming to
   `verification/review/independent-review.schema.json`, with any public identity details included
   only with the reviewer's consent.

Stars, traffic, automated CI, maintainer-written reviews, unchanged reruns on the maintainer's
machine, and general statements such as “looks good” do not satisfy this protocol.

## Bounded review targets

The reviewer should choose one target and may expand the scope. Passing a smaller target never
proves a larger one.

| Target ID | Suggested command | What it can establish | What it cannot establish |
|---|---|---|---|
| `timevault-revision-boundary` | `.venv/bin/pytest -q tests/integration/test_engine_method_evidence.py` | Committed ALFRED revision-boundary behavior and internal method fixtures | General economic correctness, source authenticity, or all seven engines |
| `scenario-catalog-30` | `.venv/bin/python scripts/verify_scenario_catalog.py` | Integrity of 30 committed scenario locks, ReplayPacks, and failure/evaluation labels | Forecast skill, clients, live trading, or complete raw-source re-download |
| `site-release-readiness` | `.venv/bin/python scripts/verify_public_site_readiness.py` | Binding of the committed site readiness receipt to its source commit | Public availability, accessibility certification, or external review by itself |
| `billion-row-small-chain` | `.venv/bin/pytest -q tests/integration/test_sec_edgar_final_scale_evidence.py` | Internal consistency of committed manifests, receipts, queries, and hashes | Independent reprocessing of every SEC byte |
| `billion-row-full-reproduction` | Follow `docs/scale/sec-edgar-log-lake.md` from official SEC archives | Independent download, processing, deep verification, and query evidence at the stated scale | Deployment, user impact, investment performance, or semantic uniqueness of access-log rows |

The full billion-row route currently requires approximately 15.5 GB of official ZIP downloads
and 12.3 GB of generated Parquet data. The smaller evidence-chain test is intentionally not
represented as a substitute for that rerun.

## Fresh-environment baseline

From the root of the extracted review archive or clone:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/verify_internal_quality_receipt.py
```

The reviewer must retain the archive SHA-256, `git rev-parse HEAD` when Git metadata is available,
runtime versions, the dependency-lock hash, and the complete command outputs outside the source
tree. Output hashes in the public record allow later comparison without forcing publication of
machine paths, usernames, or unrelated environment data.

## Issue and resolution rules

A qualifying issue must be specific enough to reproduce or inspect. Examples include a future
revision leaking across a decision boundary, a claimed count that does not reconcile, an official
source rule that is misclassified, a verifier accepting a mutated artifact, a documented command
that fails in a supported environment, or a public label that overstates the evidence.

The maintainer may disagree, but must record the evidence for that disposition. When code or
documentation changes, the resolving commit must be immutable and the reviewer must repeat the
relevant check against it. The final record must preserve the original issue rather than replacing
it with only the successful result.

## Record and publication boundary

Completed records belong under `verification/review/records/` only after the reviewer has approved
the public identity and conflict disclosure. Raw logs may remain outside Git if they contain local
paths or personal data; their SHA-256 values and narrowly redacted excerpts can be recorded instead.

Schema validation proves only that the required fields are present and well formed. It does not
authenticate the reviewer, prove their qualifications, prove that they personally ran the commands,
or turn one bounded review into general external certification. Those facts require separately
checkable public attribution and evidence.
