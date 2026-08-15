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

The implementation has fixture-backed method tests, including an explicit retained negative
attempt. [`verification/evidence/trialcourt-holm-method.json`](../../verification/evidence/trialcourt-holm-method.json)
binds the current implementation hash and independently expands a three-hypothesis step-down
calculation from Sture Holm's 1979 procedure, *A Simple Sequentially Rejective Multiple Test
Procedure*, *Scandinavian Journal of Statistics* 6(2), 65–70,
[DOI 10.2307/4615733](https://doi.org/10.2307/4615733). The independent calculation and
TrialCourt both map raw p-values `0.01`, `0.04`, and `0.03` to adjusted values `0.03`, `0.06`, and
`0.06` by hypothesis ID. The self-hashed receipt proves this bounded implementation comparison;
it is not an independent review or external method validation.
