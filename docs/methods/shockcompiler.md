# ShockCompiler truth and expansion rules

ShockCompiler treats the scenario mode as an enforceable contract:

- observed reconstruction accepts only exact, sourced observed/reported/extracted values;
- bounded reconstruction accepts sourced non-simulated intervals and expands only their endpoints;
- counterfactual accepts one explicit value labelled `simulated`;
- adversarial mode accepts bounded simulated grids with at least two values per dimension.

Every compiled shock retains its target, variable, unit, operation, evidence class, source record
IDs, source content hashes, derivation, and limitations. Cartesian expansion is deterministic and
is refused before materialization if it exceeds a configured trial cap. `add` and `multiply`
operations require an explicit baseline; missing baselines fail rather than defaulting to zero or
one.

`verification/evidence/svb-shock-programs.json` compiles filer-reported SVB securities-loss ratios,
a sourced inferred loss-realization interval, one explicit deposit-run counterfactual, and a finite
funding-duration adversarial grid. Counterfactual and adversarial vectors contain no fake source
references and are not described as observed outcomes, forecasts, or returns.
