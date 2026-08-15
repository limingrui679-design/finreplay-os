# Contributing

FinReplay OS welcomes focused, evidence-grounded fixes. Before proposing a
change, open an issue describing the affected contract, source, or scenario.

## Required boundaries

1. Preserve economic time and knowledge time as separate fields.
2. Keep `observed`, `reported`, `extracted`, `inferred`, `bounded`, and
   `simulated` values distinct.
3. Never replace a historical release snapshot with a current revised value.
4. Do not count a scenario until all eight acceptance gates and the independent
   catalog verifier pass.
5. Do not represent public-data cases as clients, deterministic tests as
   external validation, or simulated outcomes as investment performance.
6. Keep raw-response redistribution within each source's license and terms.

## Verification

Use Python 3.11 or newer, then run:

```bash
python -m pip install -e '.[dev]'
ruff check .
mypy src tests
pytest --cov=finreplay --cov-report=term-missing
python scripts/verify_scenario_catalog.py
pip-audit .
```

Update tests, scenario receipts, documentation, and `CHANGELOG.md` together
when their claims change. A pull request should explain what evidence changed,
what did not change, and which failure modes were exercised.

## Independent review

External reviewers should use the **Independent review report** Issue form and the protocol in
`docs/verification/independent-review-protocol.md`. An initial Issue or successful rerun alone
does not count. A completed record requires a substantive issue, maintainer disposition or fix,
and a same-reviewer recheck before it can be proposed under `verification/review/records/`.

Validate proposed records with:

```bash
python scripts/validate_independent_review_records.py path/to/review-record.json
```
