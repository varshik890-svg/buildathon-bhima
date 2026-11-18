# Bhima Harness: architecture contract (hackathon build)

Every module below is written fresh during the Buildathon (18 Nov 2026, 15:00–19:00 IST), inside this repo. Builders: follow `AGENTS.md`.

## What it does

`bhima` is a harness around two coding CLIs, **Claude Code** (`claude -p`) and **Codex** (`codex exec`). For each request it:
1. plans the work
2. **routes** each task to a CLI, a model and an effort level (GLiDE decision model, with a rule fallback)
3. builds a **context pack**
4. runs the worker
5. **checks** the result (tests plus an assumptions list), retrying with feedback
6. asks the **human to approve** (CLI or Telegram)
7. **remembers**: Hermes-style memory and skills, rebuilt here
8. **reports** rounds, tokens and time

## Layout

```
bhima/
  __main__.py        # python -m bhima ...
  cli.py             # ask | approve <id> | deny <id> | report | bot | baseline
  config.py          # loads .env; paths; constants
  store.py           # SQLite at .bhima/state.db
  models.py          # dataclasses below (the shared contract)
  orchestrator.py    # the flow; state machine
  planner.py         # request -> Plan (claude -p, low effort, JSON schema)
  router.py          # Task -> Route (GLiDE first, rules fallback)
  glide_client.py    # POST {GLIDE_URL}/v1/systemone
  context.py         # Task -> ContextPack (repo map + ranked files + memory + skill index)
  memory.py          # memory/MEMORY.md, memory/USER.md with caps; add/patch/remove; proposals
  skills.py          # skills/<name>/SKILL.md; index (name+description), load full on demand
  scrape.py          # fetch URL -> extract tables/lists -> sort -> JSON/CSV (stdlib)
  gitops.py          # workspace repo, task branch, commit, merge-by-SHA (never push)
  checks.py          # run tests; parse "## Assumptions:"
  workers/base.py    # Worker protocol
  workers/claude.py  # claude -p --output-format json --model M --effort E --allowedTools ...
  workers/codex.py   # codex exec --json -m M -c model_reasoning_effort=E --sandbox workspace-write -C dir
  telegram_bot.py    # python-telegram-bot v22; owner chat only; /ask /status /report; approve/deny buttons
  report.py          # tables
demo/template/       # small sample project with tests (copied to the workspace at run time)
demo/tasks.json      # 5 seeded demo tasks
memory/              # MEMORY.md, USER.md (committed starting files)
skills/              # SKILL.md folders
tests/               # pytest for the harness
.bhima/              # runtime state, workspaces, logs (gitignored)
```

## Shared contract (`bhima/models.py`)

```python
@dataclass
class Task:        id: str; request_id: str; title: str; detail: str; kind: str  # "code"|"scrape"|"question"
                   files_hint: list[str]; acceptance: list[str]
@dataclass
class Plan:        id: str; request_id: str; tasks: list[Task]; test_command: str; summary: str
@dataclass
class Route:       cli: str  # "claude"|"codex"
                   model: str; effort: str  # "low"|"medium"|"high"
                   needs_plan: bool; confidence: float; source: str  # "glide"|"rules"
                   reason: str
@dataclass
class ContextPack: task_id: str; text: str; files: list[str]; tokens_est: int
@dataclass
class RunResult:   task_id: str; round: int; cli: str; ok: bool; report: str; assumptions: list[str]
                   usage: dict; duration_s: float; raw_path: str; error: str | None
@dataclass
class CheckResult: task_id: str; tests_passed: bool; output: str; assumptions: list[str]
```

**Function signatures:**
- `planner.make_plan(request: str, project_dir: Path) -> Plan`
- `router.route(task: Task, history: list[RunResult]) -> Route`
- `context.build_pack(task: Task, project_dir: Path, budget_tokens: int) -> ContextPack`
- `Worker.run(task, pack, route, workdir: Path, round: int) -> RunResult`
- `checks.run_checks(task, workdir, test_command) -> CheckResult`
- `memory.propose(run: RunResult) -> list[str]`, `memory.apply(entries)`, `memory.snapshot() -> str`
- `skills.index() -> str`, `skills.load(name) -> str`
- `scrape.scrape(url, sort_by: str | None) -> list[dict]`
- `orchestrator.submit(request: str) -> str` (request id), `orchestrator.approve(target_id)`, `orchestrator.deny(target_id)`, `orchestrator.status() -> str`
- The orchestrator notifies through `orchestrator.on_event(callback)`, which the Telegram bot subscribes to.

## Rules inside the harness

- Workers only touch the **workspace** under `.bhima/workspaces/<project>`, which is its own git repo created from `demo/template`. They never touch this repo.
- Merges only happen for the approved commit SHA. The harness **never pushes**.
- Retries: up to 2 times with the failure fed back. Then the other CLI once. Then ask the human.
- Never use `--dangerously-skip-permissions` or bypass modes.
- Secrets only come from `.env`, which is gitignored. `.env.example` is committed.
