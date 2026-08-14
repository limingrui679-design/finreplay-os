# March 2020 FHFA House Price Index change boundary replay

This counted scenario places a historical decision boundary at 2020-04-22 13:00 UTC, the exact
9:00 a.m. EDT time in FHFA's preannounced 2020 House Price Index release calendar. Its two inputs
are the national purchase-only seasonally adjusted monthly changes for January and February 2020
from their first verified FHFA reports. The May 26 March change and that report's revision snapshot
are locked separately as post-decision evidence and are absent from every ReplayPack source record.

FHFA's purchase-only HPI is a weighted repeat-transactions index based on Enterprise mortgage
acquisitions. This case does not claim that FinReplay existed at the historical boundary, measure
every U.S. home, observe property records, transaction counts, appraisals, mortgages, borrowers,
investments, contemporaneous COVID effects, or predict a future release.

## Official releases and knowledge boundary

`scenarios/fhfa-hpi-2020/input-lock.json` contains exactly two reported national monthly changes:

- January 2020: `30` basis points (`0.3%`) from the March 25 report;
- February 2020: `70` basis points (`0.7%`) from the April 22 report.

The adapter validates FHFA's August 20, 2019 release calendar, which lists both dates and states
that 2020 HPI reports are released at 9 a.m. ET. It resolves each date through
`America/New_York`, producing 13:00 UTC for both selected releases. The January report footer says
`9AM EST`, while the official schedule and April report use ET wording. The lock preserves that
source discrepancy explicitly; it does not silently rewrite the January footer.

The current FHFA schedule page can change navigation and wrapper markup. Therefore, the input lock
binds a stable semantic digest of the validated publisher, publication date, time rule, and complete
2020 release table—not a claim that today's HTML response bytes are an immutable 2019 snapshot.
Each selected PDF must independently pass exact page count, geometry, page-rotation, metadata,
cover, press-release, national and regional table, revision-row, methodology, COVID-timing text,
and release-calendar checks before one record is emitted.

The three decision-input evidence digests are:

- official schedule semantic SHA-256:
  `02f589a1d47ef046e87be9391a74f1d6e65fe92cdd552b87ad4144722f67cfba`;
- January report PDF:
  `bc885fac528f66a02a3f0760b81dcace6fe1ef0f0f980aecb5e34c600d239a46`;
- February report PDF:
  `3624bf523c7afa70616e155deb506fe419b756511a0c14a22d1fb3f16b0da993`.

Current HTTP metadata and retrieval timestamps remain present-time evidence only and are never
backdated. Full schedule and PDF responses remain in ignored content-addressed storage; the
repository retains minimal facts, URLs, hashes, and release-snapshot provenance.

## Transparent range with no probability

ShockCompiler uses only the two first-report national changes:

- latest-known persistence baseline: `70` basis points;
- one known initial-release increase: `70 - 30 = 40` basis points;
- stress endpoints: persistence at `70`, or one repeat of the increase at `110` basis points;
- range width: `40` basis points;
- probability assigned: none.

This is a transparent stress construction from two values and one difference. It is not an FHFA
forecast, confidence interval, calibrated coverage band, stationary-regime estimate, housing or
credit model, pandemic-effect estimate, or causal model.

## Disjoint post-decision event and revisions

`scenarios/fhfa-hpi-2020/event-lock.json` contains the May 26 report's March 2020 national change
of `10` basis points (`0.1%`). Its scheduled 9:00 a.m. EDT release time is 2020-05-26 13:00 UTC,
strictly after the decision boundary. The event record ID is disjoint from both input IDs.

The same report snapshot retains January at `50` basis points and revises February from its
first-report `70` basis points to `80` basis points. Those later-snapshot values and their `0` and
`+10` basis-point revision deltas remain in the event lock and never overwrite the first-report
inputs. On the range fixed at the April 22 boundary, the reported March change is `60` basis points
below the `70`-basis-point lower endpoint. The verifier requires the miss to remain visible; it
neither widens the range nor relabels the outcome as success.

The official PDF currently served by FHFA has a May 26 creation timestamp and a June 15, 2020
modification timestamp. Its exact current hash is verified as official archived evidence, but the
scenario does not claim that those bytes were unchanged from the release instant through June 15.

## Four relevant engines

TimeVault reconstructs the two-release, two-record decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a
retrospective one-increase attempt; ReplayStudio exports a deterministic human- and machine-readable
pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no property network,
security, order, execution, portfolio, allocation, capital, return, or real-user evidence exists
for this question.

## Rebuild and counted proof

```bash
python scripts/build_house_price_change_boundary_replaypack.py \
  --input-lock scenarios/fhfa-hpi-2020/input-lock.json \
  --output verification/replaypacks/fhfa-hpi-2020 \
  --code-commit c2891ea05c93f3de2a10dbfef3578ee44f583bc2

python scripts/verify_house_price_change_boundary_replaypack.py \
  --input-lock scenarios/fhfa-hpi-2020/input-lock.json \
  --event-lock scenarios/fhfa-hpi-2020/event-lock.json \
  --pack verification/replaypacks/fhfa-hpi-2020 \
  --receipt verification/scenarios/rebuilds/fhfa-hpi-2020.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 24 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/fhfa-hpi-2020-house-price-change-boundary-v1.json` binds the
supporting inventory, evidence hashes, locks, scripts, pack, receipt, truth labels, persistence
baseline, schedule/footnote discrepancy, no-probability range, TrialCourt rejection, exact event
identity, revision isolation, PDF modification-metadata warning, and required `60`-basis-point
breach. This establishes internal reproducibility only—not forecast skill, calibrated coverage,
source or economic-method correctness, universal home prices, property outcomes, a contemporaneous
COVID effect, housing, credit, pandemic, or policy causality, external validation, deployment,
investment performance, or user impact.
