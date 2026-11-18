---
name: safe-edits
description: Make small, reviewable diffs and state every assumption you made
tags: [editing, review, assumptions]
---

# Safe edits

- Touch only the files the task needs. Prefer editing an existing function over adding a new module.
- Keep each change small enough that a human can review the diff in a minute.
- Do not reformat untouched code, rename public functions, or change dependencies unless asked.
- Never add secrets, never disable tests, never use bypass or skip-permission flags.
- If the task is ambiguous, pick the most conservative reading and say so.
- End your report with:

```
## Assumptions:
- <each guess you made, one per line>
```
