---
name: agent-vitals
description: |
  Introspect the user's local AI agent infrastructure in real time. Use proactively:
  before starting tasks, before scheduling cron/timer work, when stuck, after long
  tasks, or when claiming something works. Exposes tools: vitals_summary,
  shadow_list, shadow_stale, burnout_summary, burnout_stuck_sessions.
---

# agent-vitals — make yourself less stupid

The MCP server `agent-vitals` lets you introspect the user's local AI agent
stack — cron jobs, systemd timers, MCP server configs, Claude Code sessions,
subagent run history, drift across hosts, session size, token spend — without
asking the user.

**Call these tools proactively. Not just when the user asks.** Your calls
refresh the freshness stamp that powers pre-action hooks; an agent that never
calls vitals triggers refusal of infra mutations.

## When to call (proactive triggers)

| Trigger | Tool |
|---|---|
| Starting any non-trivial task | `vitals_summary` |
| About to schedule cron / timer / systemd work | `shadow_stale` |
| After a long task completes | `burnout_summary` |
| Suspect you're in a loop | `vitals_summary` + `burnout_stuck_sessions` |
| User asks "is X working?" | `shadow_list` or `vitals_summary` |
| About to claim "all cron is fine" | `shadow_stale` (verify first) |
| About to recommend an MCP install | `shadow_list` (check duplicates) |
| User asks "what's broken?" | `vitals_summary` → `shadow_stale` + `burnout_stuck_sessions` |
| User asks "are my configs consistent across tools?" | call your MCP host to invoke drift detection |
| User asks "how much have I spent on tokens?" | call your MCP host to invoke cost tracking (now with ET metric) |
| User asks "are there old session files eating disk?" | call your MCP host to invoke sessions listing |
| User asks "back up my agent state" | call your MCP host to invoke snapshot creation |
| Session is "taking forever" or costing way more than expected | call your MCP host to invoke loops detection (v0.6.0) — detects exact + soft loops |
| Just installed a new MCP server, want to see if it's actually used | call your MCP host to invoke unused-tools check (v0.6.0) — reports unused servers + per-tool usage |
| Token bill looks high and you want to know which tools are responsible | call your MCP host to invoke token-heavy tool identification (v0.6.0) |
| Session is "taking forever" or cost spikes | call your MCP host to invoke loops detection (v0.6.0) — detects exact + soft loops |
| Running a small model and want opus-level performance | call your MCP host to invoke coaching (v0.6.0) — generates optimized system prompts from session data |
| Session has heavy SSH usage with repeated commands to same host | call your MCP host to invoke SSH loop detection (v0.6.0) — detects polling patterns |
| Installed multiple MCP servers and wondering if they overlap | call your MCP host to invoke overlap detection (v0.6.0) — finds similar/duplicate tool names |
| Session files are large or numerous | call your MCP host to invoke compaction suggestions (v0.6.0) — flags files >10MB or >5000 events |
| Want to make current model smarter without changing it | call your MCP host to invoke coaching (v0.6.0) — generates optimized prompts from session data |
| Session has many identical Bash calls (e.g. same ps command 20+ times) | `av loops` excludes polling; only exact duplicate commands with no variation are flagged |
| Session has many near-identical Bash calls (same pattern, varying paths/flags) | `av loops` soft-loop detection catches repeated command structures |
| Session edits the same file many times | `av loops` compares edit content, not just count; progressive edits are not flagged |
| Large session files eating disk | `av sessions --suggest` flags sessions > 10MB or > 5000 events for compaction |

## Anti-patterns this exists to prevent

❌ **"Your crontab is fine"** — without calling `shadow_stale` first
❌ **Scheduling cron / systemd work** — without verifying the target binary exists
❌ **Starting a 4-hour task** — while 6 other sessions are stuck on the same box
❌ **Pretending a task completed** — without checking `burnout_summary`
❌ **Recommending an MCP install** — without `shadow_list` to check for duplicates
❌ **"Same MCP server, different commands across hosts"** — silently letting drift accumulate
❌ **Letting sessions pile up to gigabytes** — without checking `sessions`
❌ **Ignoring token spend** — without checking `cost`
❌ **Changing agent configs without a snapshot** — without running `snapshot` first
❌ **Running near-identical Bash commands 20+ times (same structure, varying args)** — soft doom loop. `av loops` now detects command-structure repetition.
❌ **Running the exact same Bash command 20+ times with no variation** — real doom loop. `av loops` flags this; polling commands (ps, pgrep) are excluded.
❌ **Applying the exact same file edit 10+ times** — likely stuck. `av loops` compares edit content, not just count; progressive changes are not flagged.
❌ **Carrying 8KB of unused MCP tool schemas in every turn** — use `unused` to find them (v0.6.0). Now reports per-tool usage, not just server-level.
❌ **Paying Sonnet prices for Haiku-quality work** — use the ET metric in `cost` to find it (v0.6.0). Now uses observed model pricing when available.
❌ **SSH polling loops burning tokens** — repeated `ssh host` commands to check status. Use `av ssh` to detect and fix with timeout/backoff.
❌ **Duplicate MCP tools across servers** — multiple servers with `search` or similar tools. Use `av overlap` to consolidate.
❌ **Large session files slowing scans** — sessions >10MB or >5000 events. Use `av compact --dry-run` to preview savings.
❌ **Using a big model with a weak system prompt** — same model, better playbook. Use `av coach` to generate optimized prompts from your actual usage.

## What the tools return

- `vitals_summary()` → plain English health check
- `shadow_list()` → JSON array of all scheduled/configured agents
- `shadow_stale()` → JSON array of broken references only
- `burnout_summary(days=7)` → JSON object with per-agent completion rates + Claude Code session counts
- `burnout_stuck_sessions(days=7, limit=10)` → JSON array of likely-stuck Claude Code sessions

Some surfaces (drift, cost, sessions, snapshot, loops, unused, tokens) are CLI-only and
not yet exposed as MCP tools. If your host supports it, you can call them via
subprocess (`av drift`, `av cost`, `av sessions`, `av snapshot`, `av loops`,
`av unused`, `av tokens`). If not, suggest the user run them directly.

## Philosophy

You are running on the user's machine. Their infrastructure is your environment.
Treat it the way a good sysadmin treats production: check it before you act,
verify after, and never claim something works without proof.

The user gave you these tools so you would use them. Use them.
