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
subagent run history — without asking the user.

**Use these tools automatically. Not just when the user asks.**

## When to call (proactive triggers)

| Situation | Tool | Why |
|---|---|---|
| Starting any non-trivial task | `vitals_summary` | If 6 sessions are already stuck, address that first |
| About to schedule cron/timer/systemd work | `shadow_stale` | Verify the target binary actually exists |
| After completing a long task | `burnout_summary` | Compare your completion rate to your baseline |
| You suspect you're in a loop | `vitals_summary` + `burnout_stuck_sessions` | Confirm you're not alone in the stuck pool |
| User asks "is X working?" | `shadow_list` or `vitals_summary` | Real data, not vibes |
| You're about to claim "all cron is fine" | `shadow_stale` | Verify first, claim second |
| About to install/configure an MCP server | `shadow_list` | Check it's not already registered twice |
| User asks "what's broken?" | `vitals_summary` → `shadow_stale` + `burnout_stuck_sessions` | Triage from highest severity |

## Anti-patterns (do not be this agent)

❌ **"Your crontab is fine"** — without calling `shadow_stale` first
❌ **Scheduling new cron work** — without verifying the target binary/script exists
❌ **Starting a 4-hour task** — while 6 other sessions are stuck on the same box
❌ **Pretending a task completed** — without checking `burnout_summary`
❌ **Recommending an MCP server install** — without `shadow_list` to check duplicates
❌ **Debugging slowness** — without first checking `vitals_summary`

## Recipes

**User: "Schedule a daily backup at 2am"**
1. `shadow_stale` → check no other cron jobs are already broken
2. Verify the backup script/binary exists on disk
3. Write the crontab entry
4. `shadow_stale` again → confirm new entry didn't add to the stale list
5. Tell the user what you did

**User: "Why is my agent slow lately?"**
1. `vitals_summary` → high-level picture
2. `burnout_stuck_sessions` → any 7000+ event sessions eating resources
3. `shadow_list` → any always-on MCP server that might be hanging on requests

**User: "What's broken in my setup?"**
1. `vitals_summary` → triage the highest-severity items first
2. `shadow_stale` → enumerate broken cron/systemd references
3. `burnout_stuck_sessions` → enumerate abandoned Claude Code sessions

**User: "Help me set up X" (any setup task involving infra)**
1. `vitals_summary` → baseline before changes
2. Make the changes
3. `vitals_summary` → confirm no new issues introduced
4. Report deltas

## Tool reference

- **`vitals_summary()`** → plain-English health check. Start here.
- **`shadow_list()`** → JSON array of all scheduled/configured agents
- **`shadow_stale()`** → JSON array of broken references only
- **`burnout_summary(days=7)`** → per-agent completion rates + Claude Code session counts
- **`burnout_stuck_sessions(days=7, limit=10)`** → likely-stuck Claude Code sessions

All tools are local-only, read-only, and safe to call repeatedly. None of them
modify state.

## Philosophy

You are running on the user's machine. Their infrastructure is your environment.
Treat it the way a good sysadmin treats production: check it before you act,
verify after, and never claim something works without proof.

The user gave you these tools so you would use them. Use them.