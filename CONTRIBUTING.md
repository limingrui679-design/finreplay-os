# Contributing

FinReplay OS welcomes focused, evidence-grounded fixes. Before proposing a
change, use the matching issue form to describe the affected contract, source,
scenario, or user surface. Small corrections can go directly to a pull request.

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
python -m pip install --upgrade 'pip>=26.1.2'
python -m pip install -e '.[dev]'
python scripts/build_user_catalogs.py --check
ruff check .
mypy src tests scripts
pytest --cov=finreplay --cov-report=term-missing
python scripts/verify_scenario_catalog.py
python scripts/validate_independent_review_records.py
pip-audit --local
```

Update tests, scenario receipts, documentation, and `CHANGELOG.md` together
when their claims change. A pull request should explain what evidence changed,
what did not change, and which failure modes were exercised.

Generated files such as `docs/catalog-matrix.md` and the installable catalogs
under `src/finreplay/resources/` must be regenerated with
`python scripts/build_user_catalogs.py --write`; do not edit their counted rows
by hand.

## Independent review

External reviewers should use the **Independent review report** Issue form and the protocol in
`docs/verification/independent-review-protocol.md`. An initial Issue or successful rerun alone
does not count. A completed record requires a substantive issue, maintainer disposition or fix,
and a same-reviewer recheck before it can be proposed under `verification/review/records/`.

Validate proposed records with:

```bash
python scripts/validate_independent_review_records.py path/to/review-record.json
```
