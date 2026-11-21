# When is another release worth waiting for?

An analyst flagging quarters with negative US real output growth can communicate on the first quarterly vintage or wait for the next one. FinReplay prices that delay, learns when to defer from previously matured observations and compares three policies on exactly the same quarters.

## Reproduce

```sh
finreplay release-timing examples/release_timing/quarterly_output.csv --delay-cost 0.10
finreplay release-timing examples/release_timing/quarterly_output.csv --delay-cost 0.25 --show-decisions
```

The [Philadelphia Fed real-output vintage archive](https://www.philadelphiafed.org/surveys-and-data/real-time-data-research/routput) retains the level of real GDP/GNP available at each vintage. Each measurement uses the archive’s quarterly snapshot. For a quarter q, calculate annualised growth as 100 × ((level(q) / level(q−1))⁴ − 1), using both levels from one column of the archive. Compute this growth independently in quarterly vintages q+1, q+2 and q+5. Using within-vintage ratios avoids splicing levels across base-year changes. A negative rate triggers a contraction flag. The q+5 vintage supplies the common evaluation reference.

The adaptive policy issues the first flag when its absolute growth rate reaches a learned margin. Below the margin it waits one quarter and issues the second flag. It chooses among margins 0, 0.25, 0.5, 1 and 2 annualised percentage points, plus always waiting. Training minimises sign disagreements + delay cost × number of deferred decisions. Ties choose the smallest margin. Each training quarter must have a q+5 reference strictly before the current q+1 decision quarter. Fitting begins with at least 40 complete training paths. The policy never sees the pending second or reference release when deciding whether to wait.

## Public result

The 1965Q4–2026Q4 archive yields 245 target quarters with a first-vintage column. Six paths lack at least one required level pair (1995Q4 and 2025Q3–2026Q3). Another 44 complete quarters precede the first eligible decision. All three strategies are compared on the remaining 195 quarters, 1976Q3–2025Q2.

| Policy at a delay cost of 0.10 | Disagreements with q+5 | Decisions deferred one quarter | Illustrative loss |
|---|---:|---:|---:|
| Issue immediately | 6 | 0 | 6.00 |
| Always wait | 4 | 195 | 23.50 |
| Learn when to wait | 5 | 7 | 5.70 |

Relative to immediate issuance, the selective policy corrected two flags and introduced one disagreement. At a cost of 0.25 per delay, retraining yields five disagreements and six delays (loss 6.50), while immediate issuance stays at 6.00. One reference disagreement costs one unit. These weights express reviewer preferences. The reference is a later vintage, and the modest net improvement is descriptive of this paired archive evaluation. The contraction flag describes the sign of quarterly real-output growth.

## Frozen input

The CSV retains the six source levels per target quarter required to reconstruct the evaluation. Obtain the [November 2026 quarterly workbook](https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx?sc_lang=en&hash=34FA1C6BF0007996E1885C8C32E3BEF9) and compare the extracted panel with the frozen copy.

```sh
python scripts/build_philadelphia_output_panel.py ROUTPUTQvQd.xlsx /tmp/quarterly_output.csv
cmp /tmp/quarterly_output.csv examples/release_timing/quarterly_output.csv
```

Workbook SHA-256: `6a7971b31b7336551705465de8a0855826f3652abe112406f52b2c2ed0994c0c`. The extractor checks this identity before reading the workbook. The committed CSV supports offline reruns when the publisher rolls its live file forward.
