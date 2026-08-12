# TrialCourt method boundary

TrialCourt is an append-only, hash-chained research ledger. Registration, every attempt—including
negative attempts—and each adjudication are immutable entries. Attempt numbers must be contiguous;
a missing number is rejected because it could conceal a failed search path.

The current attack suite applies six gates:

1. preregistration precedes the holdout and covers the disclosed attempt family;
2. point-in-time availability, purge, holdout, and embargo boundaries all pass;
3. the candidate survives Holm family-wise multiple-testing adjustment;
4. the metric direction is stable across at least two named regimes;
5. gross return remains positive after preregistered non-zero execution and borrow costs;
6. requested capital remains below the preregistered volume-participation limit.

Any failed attack yields `reject`; a warning yields `revise`; only an all-pass result is
`eligible`. `eligible` means eligible for the next research gate. It does not mean approved for
trading, production, client use, or real capital allocation.

The implementation currently has fixture-backed method tests, including a known Holm step-down
example and an explicit retained negative attempt. A repository-internal result is not an external
method validation. A cited published-method fixture and an independent review remain required by
the acceptance matrix.
