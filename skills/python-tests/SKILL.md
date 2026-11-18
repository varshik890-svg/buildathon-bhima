---
name: python-tests
description: How to run pytest, read a failure and fix it with the smallest change
tags: [python, tests, pytest]
---

# Running and fixing pytest

1. Run `python -m pytest -q` from the project root. Use `-x` to stop at the first failure and `-k name` to rerun one test.
2. Read the failure bottom-up: the last `E` lines show the assertion; the line above names the file and line.
3. Decide whether the code or the test is wrong. The test is the spec unless the task says otherwise; never delete or weaken a test to make it pass.
4. Fix the smallest thing. Rerun the single test, then the whole suite.
5. Import errors usually mean a missing `__init__.py`, a wrong working directory, or a renamed function.
6. Report what you ran and the final pass/fail counts, then list assumptions under `## Assumptions:`.
