# ReplayStudio deterministic ReplayPacks

ReplayStudio turns a typed artifact dependency graph into a portable static directory and optional
ZIP archive. It preserves evidence labels and claim boundaries; it does not upgrade fixtures into
historical facts, internal checks into external validation, or model output into realized
performance.

## Input contracts

Every `ReplayArtifact` declares one of the seven engine names, status, evidence-class counts,
source record IDs and hashes, upstream artifact IDs, payload, limitations, and a SHA-256 over the
complete canonical content. Sourced `observed`, `reported`, or `extracted` counts require source
record IDs and hashes. Historical-replay eligibility is separate and cannot be true without a
source hash.

Every `ReplayClaim` identifies its supporting artifacts, truth label, boundary, and limitations.
Compilation fails when a declared label has no supporting artifact count of that class. A complete
pack can require exactly the full seven-engine set. Missing dependencies, duplicate IDs, cycles,
self-dependencies, unsorted inner identifiers, naive timestamps, and non-finite elapsed times fail
closed.

Input artifact and claim order is not meaningful. The compiler canonically sorts both, derives a
stable topological order, aggregates evidence and source identities, computes an input manifest
hash, and generates a cross-artifact trace ID. Pack identity covers the canonical specification and
all derived fields.

## Portable output

A ReplayPack contains exactly:

- `index.html`: accessible human-readable static report;
- `report.json`: complete machine-readable compiled graph;
- `manifest.json`: self-hashed receipt, input/output identities, file sizes and hashes;
- `checksums.sha256`: relative-path portable checksums;
- `README.md`: truth boundaries and verification route;
- `assets/styles.css`: responsive presentation with visible focus states and reduced-motion rules.

The HTML has no JavaScript or external requests. It uses a restrictive content-security policy,
semantic landmarks, a skip link, scoped table headers, a caption, visible evidence badges, escaped
untrusted text, and an explicit statement that the report is not live trading or realized
performance.

## Mutation and archive safety

Builds use a temporary sibling directory, flush file contents, and atomically rename the completed
pack. Existing destinations are accepted only when the verified receipt and every byte are
identical. Different content is never silently overwritten. Verification rejects missing or extra
files, extra directories, symlinks, unsafe relative paths, hash mismatches, inconsistent identities,
noncanonical reports, and files that differ from a fresh deterministic render. This last check also
rejects edited HTML whose attacker-controlled hashes and receipt were all recomputed.

ZIP export fixes entry order, timestamp, permissions, compression settings, relative paths, and
archive comment. A pre-existing archive is accepted only when byte-identical.

## Commands

```bash
finreplay build-replaypack spec.json output/replay --archive output/replay.zip
finreplay verify-replaypack output/replay
```

The build command validates the typed JSON input, writes the pack, verifies it, and optionally
archives it. The verify command recomputes structure, hashes, semantic invariants, and rendering.

## Internal golden evidence

`scripts/build_replaystudio_golden.py` builds
`verification/replaypacks/replaystudio-golden/`. The fixture contains all seven engine names and
all five evidence labels. `scripts/verify_replaystudio_golden.py` verifies the committed directory,
rebuilds it in a fresh temporary location, compares every byte, and compares two deterministic ZIP
archives.

`verification/evidence/replaystudio-browser-check.json` records a local desktop and 390 × 844
browser pass over that exact pack, including semantic landmarks, navigation, disclosure controls,
console errors, and containment of the wide claims table. The first mobile pass exposed unbroken
artifact hashes that widened the document; the renderer now permits code tokens to wrap, and the
retest records equal 390-pixel document client and scroll widths. The receipt is self-hashed and
`scripts/verify_replaystudio_browser_receipt.py` also resolves it back to the golden pack. It remains
maintainer-recorded internal evidence, not an independent accessibility audit.

The golden artifact is intentionally fixture-validated. Its engine payloads are compact internal
fixtures rather than executions of the six analytical engines, and its source identifiers are not
official records. It proves the ReplayStudio packaging method only. The separate A8 gate requires
an actual deterministic end-to-end engine flow.
