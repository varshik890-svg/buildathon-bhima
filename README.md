# 🚀 Buildathon: Make Wednesdays Great Again

**Masters' Union · For AI Developers · Winner gets Rs. 25,000**

> **Every team must build in a fork of this repository.** Projects not built in a fork of this repo won't be judged.

| | |
|---|---|
| **Date** | Wednesday, 18 November 2026 |
| **Hacking** | **3:00 PM – 7:00 PM IST** (code freeze at 7:00 PM sharp) |
| **Venue** | CDS Audi, 8th floor |
| **Prize** | Rs. 25,000 to the winning team |
| **Register your fork** | 👉 **[https://forms.gle/ZJUYKwTYjsMsVB2b9](https://forms.gle/ZJUYKwTYjsMsVB2b9)** (**deadline 3:30 PM IST**) |
| **Final submission** | Your fork as it stands at **7:00 PM IST**. There's nothing else to submit. |

> 🤖 **Using an AI coding agent?** Point it at [`AGENTS.md`](AGENTS.md). It has the full event brief, tracks, judging and rules in a form agents can read directly. Claude Code loads it automatically through `CLAUDE.md`, and Codex reads `AGENTS.md` natively.

---

## 🎯 Pick one build track

| # | Track | Brief |
|---|---|---|
| **01** | **Build for the Physical World** | Make a real-world task easier, faster or more reliable. Show it in a realistic scenario. |
| **02** | **Build for AI Agents** | Find something an agent struggles with. Build the tool that fixes it, then demo the agent using it. |
| **03** | **Build in Your Area of Expertise** | Pick a field you know firsthand. Ship a working product that improves how people handle it today. |

## ⚖️ How you'll be judged (each criterion is ⅓ of your score)

| Criterion | The question judges ask |
|---|---|
| **Working product** | Does it do what you say it does? Show the core workflow in your demo. |
| **Problem insight** | Who faces this problem, and why does it happen? |
| **Depth of execution** | How well did you solve the hardest part of the build? |

---

## Why fork this repo?

Everyone starts from the same commit (the **genesis commit**) at the same time. Organizers can then check that:
- all code was written **during** the Buildathon and not brought in from an earlier project,
- teams commit steadily instead of pasting in one huge AI-generated dump at the end,
- every repo stays public and visible while you build.

The organizers track every fork automatically: commits, timing and activity. Read [RULES.md](RULES.md) before you write any code.

---

## ✅ Setup (about 5 minutes, do it at 3:00 PM)

**In short:** fork → register your fork in the form **by 3:30 PM** → build → push before 7:00 PM.

### 1. Fork this repo
Click **Fork** (top right). On the fork screen:
- **Owner:** one team member's personal account. Only **one fork per team**.
- **Repository name:** `buildathon-<team-name>`, for example `buildathon-byte-bandits`
- Click **Create fork**.

> ⚠️ Don't download a ZIP or copy the files into a new repo. **It has to be a real GitHub fork**, or the tracker can't see you.

### 2. Keep it public
Forks of a public repo are public. **Don't make it private, archive it, or delete it** until judging is over.

### 3. Add your teammates
In your fork: **Settings → Collaborators → Add people**. Add each teammate's GitHub account so their commits count toward the team.

### 4. Enable Actions
Open the **Actions** tab of your fork and click **"I understand my workflows, go ahead and enable them"**. This runs the automatic check on every push.

### 5. Make your registration commit (your first commit)
Fill in [`team.json`](team.json). `track` must be one of `physical-world`, `ai-agents` or `area-of-expertise`. Then:

```bash
git clone https://github.com/<your-username>/buildathon-<team-name>.git
cd buildathon-<team-name>
# edit team.json
git add team.json
git commit -m "register: <team name>"
git push
```

The **Hackathon check** in the Actions tab should go green ✅. If it's red, open the run to see what's wrong in `team.json`.

### 6. Register your fork (deadline: 3:30 PM IST)
👉 **[https://forms.gle/ZJUYKwTYjsMsVB2b9](https://forms.gle/ZJUYKwTYjsMsVB2b9)**

It's a 4-question Google Form: team name, fork link, track, team members. It connects your repo to the organizers' live tracker.

- Fill it in **once per team**, right after forking, and **no later than 3:30 PM IST**. **Teams that don't register can't be tracked.**
- Paste your **fork's** URL (`https://github.com/<you>/buildathon-<team-name>`), not this starter repo's.
- Need to change something? Use the **"Edit your response"** link on the confirmation page. Don't fill it in twice.
- **Final submission is automatic.** Whatever is pushed to your fork at 7:00 PM IST is what gets judged.

---

## 🛠 While you build (3:00 – 7:00 PM)

- Put your code anywhere in the repo (`app/`, `src/`, `frontend/`, `backend/`…). There's no required layout.
- **Commit early, commit often**, at least every 30 minutes of real work, with messages that say what changed.
- **Push regularly.** Commits you never push don't exist to the tracker.
- AI coding tools (Claude Code, Codex, Cursor, Copilot…) are **allowed and encouraged**. Keep `AGENTS.md` and `CLAUDE.md` in the repo; they keep your agent within the rules.
- Before 7:00 PM, replace the "Your project" section below with your project's README.

## 🧊 Code freeze: 7:00 PM IST

At 7:00 PM the tracker takes a snapshot of every fork. **Commits pushed after code freeze are ignored.** Judges look at your last commit before 7:00 PM.

## ❌ Things that get you flagged

- Rewriting history (`git push --force`, rebasing away the genesis commit, squashing everything into one commit)
- Commits dated before 3:00 PM
- One or two giant commits containing most of the project
- A repo that isn't a fork of this one, or that's private or deleted

See [RULES.md](RULES.md) for the full list.

---

## Your project

# Bhima Harness: first-try-right for coding agents

- **Team:** bhima (varshik890-svg)
- **Track:** Build for AI Agents (`ai-agents`)

> **Evaluators:** the [walkthrough below](#for-evaluators-walkthrough-and-testing) shows how to check everything in about 10 minutes: the test suite, a live run, the benchmark, and a code tour.

### What is Bhima?

**Bhima is a local-first AI setup for one developer's laptop.** Small local models do the cheap, constant work: planning, chatting and deciding. Subscription coding agents (Claude Code and Codex) do the heavy coding. A harness around all of them makes sure every result is **planned, routed, checked and approved** before it counts.

The idea in one line: **local models plan, GLiDE routes, tests prove.**

- **The Talker** is a local LLM that chats, plans and explains, with no subscription cost.
- **GLiDE** is a local decision model that picks which agent, which model and how much effort each task gets.
- **Claude Code and Codex** are the workers, used through the subscriptions you already have (no API keys).
- **The Bhima harness** wraps them all with context ranking, hidden-test checks, human approval, memory and skills.

**This repo is the hackathon build:** the harness core, built in 4 hours. It runs today with a small Talker (Qwen3-4B) and a small GLiDE (0.4B). The [roadmap](#future-goals-the-bhima-roadmap) below covers the full project.

### Problem: who has it and why it happens

Developers who hand coding tasks to agents like **Claude Code** and **Codex** often get a first answer that is wrong, or only *looks* right. This isn't the model being lazy. There are three structural reasons:
1. **The context is wrong.** The agent gets too much, too little, or the wrong files, so it fixes the wrong place.
2. **Nothing checks the work before it says "done".** Tests aren't run against the real acceptance criteria, and the agent's hidden assumptions are never shown.
3. **The human only sees the result at the end.** There's no plan checkpoint, and no record of which model, effort level or route actually works for which kind of task.

Every miss costs another round, more subscription usage and more trust.

### What we built

`bhima` is a harness that sits **around** Claude Code (`claude -p`) and Codex (`codex exec`):

| Step | What Bhima does |
|---|---|
| Plan | Turns the request into small tasks. The human approves the plan (CLI or Telegram button). The planner has no tools. |
| **Local Talker** | A small **local model** (Qwen3-4B on llama.cpp, `127.0.0.1:8791`, on the laptop's GPU) plans first and answers plain Telegram chat. That costs no subscription usage. If it's down or returns an invalid plan, Claude Haiku plans instead, then an offline planner. Every plan shows `planned by: talker-local / claude-haiku / offline`. |
| **Route** | Picks the **CLI, model and effort level** per task: the GLiDE decision model first (typed `choice`/`noul`/`score` questions with a confidence score), then a rule table. It escalates after failures and switches CLI after repeated failures. |
| **Context** | Builds a **context pack**: a repo map, **BM25F-ranked files** with a "why these files" list, memory and the skill index, all within a token budget. |
| Run | Runs the worker on a task branch in a separate workspace repo, with a fixed allowed-tools list and a cleaned environment. |
| **Check** | Runs the visible tests **and the hidden acceptance tests** on a clean checkout of the candidate commit. The agent must end with an `## Assumptions:` list. A failure is fed back for a retry. |
| Approve | The human approves the merge of the **exact commit SHA** that was checked. Bhima never pushes. |
| **Remember** | Hermes-style persistent memory (`memory/MEMORY.md`, `memory/USER.md`, size caps, proposals the human approves) and skills (`skills/*/SKILL.md`, progressive loading), rebuilt from the idea. |
| Report | Rounds, tokens, time and pass rate per task and per route, plus a **baseline**: plain `claude -p` against Bhima on the same seeded tasks. |
| Telegram | `@Bhima_varshik_bot`, owner only: `/ask`, `/status`, `/report`, `/memory`, `/skills`, `/scrape`, and Approve and Deny buttons. |

There's also a **scrape-and-sort tool** (`python -m bhima.scrape <url> --sort <column>`) that pulls tables and lists out of web pages, sorted with number awareness.

### The hardest part

**Getting the right context to the agent and proving the result, cheaply.** Three pieces:
- **Context ranking:** BM25F over paths, symbol definitions and contents, with bonuses for files the task mentions, test-to-module pairing and a token budget. On the seeded tasks, it found 10 of the 11 needed files without any hints.
- **The check loop:** hidden acceptance tests run on a clean checkout of the candidate commit, so an agent can't "pass" by editing the tests or leaving stray files behind. Failures go back into both the retry and the router's history.
- **Routing by outcome:** the router learns from real outcomes (failed checks count as failures), so effort and model escalate only when needed.

### How to run it

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_OWNER_ID
.\.venv\Scripts\python -m pytest -q                 # harness tests
.\.venv\Scripts\python -m bhima demo t1             # one seeded task end to end
.\.venv\Scripts\python -m bhima approve <id>        # approve the plan, then the merge
.\.venv\Scripts\python -m bhima report              # rounds / tokens / time per task
.\.venv\Scripts\python -m bhima baseline            # plain claude -p vs bhima
.\.venv\Scripts\python -m bhima bot                 # Telegram
```

You need Claude Code and Codex logged in. GLiDE is optional: `scripts/run_glide.sh` serves GLiDE-0.4B locally, and without it the rule router is used.

### Interfaces

- **`python -m bhima tui`** is a chat-style terminal app (Textual). Plain text chats with the local Talker, and `/ask <task>` runs the workflow. A live sidebar shows the plan ("planned by"), the route with GLiDE probability bars, the context files, the checks and the totals. ctrl+a and ctrl+d approve or deny.
- **`python -m bhima dash`** is a lightweight live dashboard, using only the standard library.
- **Telegram** (`python -m bhima bot`) lets you control it from the phone: `/ask`, Approve and Deny buttons, `/report`.

### Demo

1. `python -m bhima tui`, then type `hi, what can you do?`. The local Talker answers in under a second.
2. `/ask Fix cent discrepancies in discounted invoice totals` (seeded task **t6**: the symptom is in `invoice.py`, the root cause is in the GST rounding module).
3. The plan appears with `planned by: talker-local`. Press ctrl+a.
4. The sidebar shows the **route** (GLiDE's probabilities, then the chosen CLI, model and effort), the **context pack** ("why these files"), and the round.
5. The checks run the visible tests **and the hidden acceptance tests on a clean checkout of the candidate SHA**, and the agent's **Assumptions** list is shown.
6. The merge approval shows the diff, the SHA and the assumptions. Press ctrl+a to merge (it never pushes).
7. `/report` shows rounds, tokens and time.

### Results (measured live, `demo/results/baseline.md`)

Plain one-shot `claude -p` against Bhima on 7 seeded tasks, scored by hidden acceptance tests:

Method (the same idea as [HarnessBench](https://github.com/reacher-z/HarnessBench)): **fix the base model, vary the harness.** The final run (17:48 IST):

| | Plain `claude -p` | Bhima |
|---|---|---|
| Hidden acceptance tests passed | 7/7 | 7/7 |
| Tokens, total | 1.88M | **1.63M (−13.6%)** |
| Tasks where Bhima used fewer tokens | — | **5 of 7** |
| Misleading-location task (t6) | 370k | **222k (−40%)** |
| Wall time | 147 s | 164 s (+12%, from planning and checks) |

**What this shows, honestly:** Claude Sonnet already solves tasks of this size in one shot, so Bhima's value here **isn't a higher pass rate**. It's:
- **proof instead of trust:** hidden tests on the exact SHA, plus human approval
- **visibility:** the route, the context and the assumptions for every task
- **cheaper planning and chat:** a local model on the laptop's GPU, at zero subscription cost
- **fewer tokens overall,** most of all where ranked context matters (−40% on t6)

**Next benchmarks:** Python exercises from the public [Aider polyglot benchmark](https://github.com/Aider-AI/polyglot-benchmark), and a Bhima adapter for [Harbor / Terminal-Bench 2.0](https://github.com/harbor-framework/harbor), which already includes Claude Code and Codex as agents.

## For evaluators: walkthrough and testing

### 1. The test suite: 124 tests, no network, about 1 minute

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q        # expected: 124 passed
```

Every test uses **fakes** for Claude Code, Codex, the Talker, GLiDE and Telegram, so the whole suite runs offline with no accounts.

| Test file | Tests | What it proves |
|---|---|---|
| `tests/test_core.py` | 46 | The full workflow and its state machine: plan → approve → route → pack → worker → checks → retry (twice, then the other CLI) → merge approval → merge **by exact SHA**. Hidden acceptance tests run on a clean worktree. Failed checks count as failures in routing. Honest statuses and merge-conflict retry. A trusted test command (never the planner's). A scrubbed child environment. The Codex-through-WSL command builder: path translation, quoting, model and effort allow-list, prompt on stdin. |
| `tests/test_intel.py` | 23 | **Context ranking** (BM25F: file mentions, definition matches, test pairing, budget). The router's GLiDE-first logic and rule fallback. Memory caps (Hermes-style). The skills index. Scrape parsing and numeric sorting. Planner fallback order (Talker → Haiku → offline). |
| `tests/test_telegram.py` | 33 | **Owner-only access** (chat ID *and* user ID). Approve and Deny buttons. Message splitting. Event relay. Talker chat versus `/ask`. |
| `tests/test_talker.py` | 9 | The local Talker client: health check, JSON plans, returns None on failure, timeouts. |
| `tests/test_tui.py` | 8 | The Textual TUI, driven by Textual's test pilot: chat, `/ask`, approval keys, an empty store. |
| `tests/test_dashboard.py` | 5 | Dashboard frames: GLiDE percentages, "planned by", checks, totals, an empty database. |

### 2. A live run, about 2 minutes (needs Claude Code logged in)

```powershell
.\.venv\Scripts\python -m bhima reset
.\.venv\Scripts\python -m bhima demo t6 --yes      # plan is auto-approved; watch route, round, checks
.\.venv\Scripts\python -m bhima approve <id>       # the merge id it prints
.\.venv\Scripts\python -m bhima report
```

Or the interactive version: `python -m bhima tui`, then `/ask …` and ctrl+a. Optional local models:
- Talker: any llama.cpp server on `TALKER_URL`, default `127.0.0.1:8791`
- GLiDE: `scripts/run_glide.sh` in WSL

Without them, Bhima falls back to Claude Haiku planning and rule routing.

### 3. The benchmark: fix the model, vary the harness

```powershell
.\.venv\Scripts\python -m bhima baseline                 # all 7 tasks: plain claude -p vs Bhima
.\.venv\Scripts\python -m bhima baseline --tasks t6      # one task, about 1 minute
```

- Both sides use the **same model**; only the harness differs.
- Both are scored by **hidden acceptance tests** (`demo/hidden_tests/`) that neither agent sees.
- For t6 and t7, the hidden checks were shown to **fail on the unfixed code and on a plausible partial fix**, and to pass only on the correct fix.
- All three runs from today, with their raw tables, are in [`demo/results/baseline.md`](demo/results/baseline.md).

### 4. A code tour: where the hardest parts are

| What | File | Look for |
|---|---|---|
| Context ranking | `bhima/context.py` | BM25F scoring, mention and definition bonuses, test pairing, the "why these files" list, the token budget |
| Proof instead of trust | `bhima/checks.py`, `bhima/gitops.py` | Hidden tests on a clean worktree of the candidate SHA; merge of the approved SHA only, never pushing |
| Routing | `bhima/router.py`, `bhima/glide_client.py` | The GLiDE `choice`/`noul`/`score` questions, the confidence threshold, the rule fallback, escalation after failures |
| The workflow | `bhima/orchestrator.py` | The state machine, retries, approvals, events |
| Workers | `bhima/workers/claude.py`, `bhima/workers/codex.py` | Fixed tools, the `--effort` fallback, Codex through WSL, timeouts that kill the whole process tree |
| Memory and skills | `bhima/memory.py`, `bhima/skills.py` | Capped `MEMORY.md`/`USER.md`, proposals you approve, progressive skill loading |
| Interfaces | `bhima/tui.py`, `bhima/dashboard.py`, `bhima/telegram_bot.py` | Live sidebar, dashboard panels, owner-only bot |

### 5. Build history

- **Everything was written during the event**, in small commits starting with the `register: bhima` commit at 15:43 IST. There was no force-push or rebase, and the genesis commit is intact.
- **Parallel AI agents** (Claude Code and Codex, each owning separate files) built it from the contract in `ARCHITECTURE.md`. A human reviewed each step.
- **Two independent reviews** (an EGM review and a Codex review) drove the security and correctness fixes. They're visible as `fix(...)` commits.

### Honest limits (today)

- Workers run on the host with fixed tools and a clean environment, but **without OS-level filesystem isolation**. The next step is to run them inside a bubblewrap jail (WSL2).
- Git metadata in the workspace can be reached by the worker. The plan is a separate trusted checkout.
- Native-Windows Codex blocks commands in non-interactive mode, so the Codex worker is meant to run from WSL.

## Future goals: the Bhima roadmap

The long-term aim: **a personal AI team on your own laptop that you can trust, because every result is proven, not just claimed.**

1. **Three connected harnesses, one per model.** The Talker, Claude Code and Codex each get their own Bhima harness, and the three share a plan, memory and results. GLiDE sits inside the harness and decides who does what.
2. **Bigger local models on the GPU.** A larger Talker (Qwen3.6-35B, quantized) for better plans and chat, and GLiDE-9B for sharper routing, both on the laptop's RTX 5080.
3. **Real isolation.** Workers run inside an OS-level jail (bubblewrap on WSL2) with secrets and git metadata out of reach, and merges happen from a separate trusted checkout.
4. **Memory and skills that grow.** Every approved task can add a lesson or a skill, Hermes-style but rebuilt in our own code, so the team gets better at *your* codebase over time.
5. **A company-style dashboard.** A UI that shows what every agent is doing, what it costs in tokens and time, and where the setup could be optimised.
6. **Public benchmarks.** Python exercises from the Aider polyglot benchmark, and a Bhima adapter for Harbor / Terminal-Bench 2.0, comparing Bhima against plain Claude Code and Codex.
7. **Control from anywhere.** Telegram for plans, approvals and reports from your phone, with you always making the final call.

The guiding rules stay the same: **local first, subscriptions only, a human approves, and tests decide what "done" means.**
