# Baseline results (2026-11-18, recorded live during the Buildathon)

Plain one-shot `claude -p` vs Bhima, same model family, each scored by the hidden acceptance tests on a clean checkout.

## Run 1: tasks t1-t5 (16:52 IST)

```text
task  title                         plain ok  rnd  tokens  secs  bhima ok  rnd  tokens  secs
----  ----------------------------  --------  ---  ------  ----  --------  ---  ------  ----
t1    Fix bulk discount boundary    PASS      1    199652  15    PASS      1    162836  15
t2    Add CSV export                PASS      1    202318  19    PASS      1    220154  19
t3    Refactor rounding into curre  PASS      1    262295  20    PASS      1    403503  31
t4    Add input validation to Line  PASS      1    423184  35    PASS      1    285779  26
t5    Load supplier price list and  PASS      1    207463  23    PASS      1    163472  21
Hidden acceptance tests passed: plain claude -p 5/5, bhima 5/5
```

## Run 2: harder tasks t6-t7 (17:18 IST)

```text
task  title                         plain ok  rnd  tokens  secs  bhima ok  rnd  tokens  secs
----  ----------------------------  --------  ---  ------  ----  --------  ---  ------  ----
t6    Fix cent discrepancies in di  PASS      1    363081  25    PASS      1    282621  37
t7    Handle pasted and unknown di  PASS      1    144172  9     PASS      1    162531  16
Hidden acceptance tests passed: plain claude -p 2/2, bhima 2/2
```

## Summary

- Accuracy: plain 7/7, Bhima 7/7 (hidden tests).
- Tokens: plain ~1.80M, Bhima ~1.68M (~7% fewer overall; per task between -22% and +54%).
- Bhima's added value on these tasks is proof (hidden tests on the exact SHA, human approval), visibility (route, context, assumptions) and cheaper planning via the local Talker, not a higher pass rate. Claude Sonnet already solves tasks of this size.
## Run 3: final, all 7 tasks (17:48 IST), the headline result

Method (the same idea as HarnessBench): **fix the base model, vary the harness**. Both sides use Claude Code with the same model. The only difference is the harness. Every result is scored by hidden acceptance tests on a clean checkout.

```text
task  title                         plain ok  rnd  tokens  secs  bhima ok  rnd  tokens  secs
----  ----------------------------  --------  ---  ------  ----  --------  ---  ------  ----
t1    Fix bulk discount boundary    PASS      1    199679  12    PASS      1    161738  19
t2    Add CSV export                PASS      1    312849  21    PASS      1    220877  20
t3    Refactor rounding into curre  PASS      1    213161  21    PASS      1    334571  27
t4    Add input validation to Line  PASS      1    377460  29    PASS      1    286810  30
t5    Load supplier price list and  PASS      1    265531  28    PASS      1    294321  25
t6    Fix cent discrepancies in di  PASS      1    369996  27    PASS      1    222087  29
t7    Handle pasted and unknown di  PASS      1    144160  9     PASS      1    105600  14
Hidden acceptance tests passed: plain claude -p 7/7, bhima 7/7
```

| Category | Task | Token change (Bhima vs plain) |
|---|---|---|
| Bug fix | t1 | -19% |
| Feature | t2 | -29% |
| Refactor | t3 | +57% |
| Validation | t4 | -24% |
| Scrape and sort | t5 | +11% |
| Misleading location (root cause in another file) | t6 | **-40%** |
| Edge cases only the hidden tests check | t7 | -27% |

**Totals:** plain 7/7, 1,882,836 tokens, 147 s; Bhima 7/7, 1,626,004 tokens (**-13.6%**), 164 s (+12%). Bhima used fewer tokens on 5 of 7 tasks.

**Reading:** same accuracy, now with proof, and fewer tokens where ranked context matters most. The extra time is planning and the checks.