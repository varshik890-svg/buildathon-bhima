Workers edit only the workspace under .bhima/workspaces; never this repo.
§
Tests run with: python -m pytest -q
§
Assumed: Half-up rounding is the intended convention (the task's `round(amount*100)/100` hints at it). Python's `round()` uses banker's rounding on floats
