# MarketTwin method boundary

MarketTwin stores evidence-graded, versioned nodes and directed edges with separate economic
validity and knowledge-availability clocks. A historical-safe snapshot excludes `latest_only`
objects before choosing the newest eligible version; if a newer current-only version exists, the
query falls back to the most recent eligible historical version rather than deleting the identity.

Every exposure has a lower and upper dollar bound. Reported or observed exposures must be exact;
inferred relationships may be intervals. The propagation engine applies the same initial shock to
both bounds, transmits loss monotonically along directed edges, caps each node at total loss, and
reports whether both bounds converged. This is a transparent stress envelope, not a causal forecast.

`verification/evidence/svb-markettwin.json` contains two deliberately separate snapshots:

- a 2023-03-08 historical-safe SEC-only graph built from XBRL facts accepted on 2023-02-24;
- a current three-source SEC/FDIC/Treasury graph that proves public-data graph construction but is
  not represented as a 2023 point-in-time FDIC or Treasury vintage.

The zero-exposure FDIC parent-context and Treasury market-context links are labelled inferred and
do not assert an official ownership relation, security composition, or financial exposure. The
bounded shock result is simulated research output, not realized loss, investment performance, or
evidence of deployment.
