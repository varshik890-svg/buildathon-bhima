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