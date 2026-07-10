# agent-vitals

> **Give your AI agent a memory of its own infrastructure.**

```text
  ┌───────────────────────────────────────────────────────────────┐
  │                                                               │
  │   ▄▀ agent-vitals                                            │
  │                                                               │
  │   ⚡ 5 MCP tools an agent calls proactively                   │
  │   🔌 wires pi · Claude Code · Cursor · OpenCode · Codex CLI   │
  │   📜 installs a SKILL.md that primes the agent to use them    │
  │   🪶 ~900 LOC Python · MIT · no daemon · no cloud             │
  │                                                               │
  └───────────────────────────────────────────────────────────────┘
```

---

## What your agent sees

```text
  $ vitals_summary

  agent-vitals — local agent stack health

  shadow: 13 autonomous thing(s) configured (mcp: 8, systemd: 5)
  subagent burnout (7d): 5 runs, 100% completion ✓
  claude code (7d): 170 sessions, 45,626 events, ⚠ 36 stuck
    - biggest stuck-looking session: 7,118 events
```

That's not a CLI you read. That's a tool your agent calls — from inside its own loop — before it does anything risky.

---

## Before / after

<table>
<tr><th>Without agent-vitals</th><th>With agent-vitals</th></tr>
<tr>
<td>

```text
User: schedule a daily backup at 2am

Agent: yep, done.

  0 2 * * * /home/me/backup.sh >> /tmp/backup.log 2>&1
```

…and `/home/me/backup.sh` doesn't exist.
Crontab fails silently forever.

</td>
<td>

```text
User: schedule a daily backup at 2am

Agent: one moment — let me verify first.

  → shadow_stale   (no other cron jobs already broken)
  → checks /home/me/backup.sh   (exists? writable?)
  → writes crontab entry
  → shadow_stale   (confirm new entry didn't break anything)
  → reports back

  ✓ crontab updated. ran shadow_stale before and after.
```

</td>
</tr>
<tr>
<td>

```text
User: is my agent setup healthy?

Agent: looks fine to me!
```

(blind vibes)

</td>
<td>

```text
User: is my agent setup healthy?

Agent: → vitals_summary

  shadow: 13 autonomous thing(s) configured
  subagent burnout (7d): 100% completion ✓
  claude code (7d): 170 sessions, ⚠ 36 stuck
    - biggest stuck-looking session: 7,118 events

  ⚠ 36 sessions look stuck. want me to triage them?
```

</td>
</tr>
</table>

The diff is "vibes vs. data."

---

## Install

```bash
uv tool install git+https://github.com/anirudhprashant/agent-vitals
av init     # detects every agent host on your box and wires them up
            # restart your agent host so it picks up the new MCP server
```

That's the whole setup. 30 seconds.

---

## What `av init` does

```text
  $ av init
                    detected 3 agent host(s)
  ┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
  ┃ host        ┃ config                           ┃ status   ┃
  ┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
  │ pi          │ /home/anirudh/.pi/agent/mcp.json │ detected │
  │ Claude Code │ /home/anirudh/.claude/.mcp.json  │ detected │
  │ Codex CLI   │ /home/anirudh/.codex/config.toml │ detected │
  └─────────────┴──────────────────────────────────┴──────────┘

  installing:

  ┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
  ┃ host        ┃ mcp config         ┃ skill/rule        ┃
  ┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
  │ pi          │ added              │ installed         ┃
  │ Claude Code │ added              │ already installed ┃
  │ Codex CLI   │ added              │ installed         ┃
  └─────────────┴────────────────────┴────────────────━━━┛

  ✓ done. Restart your agent host.
```

| Host | MCP config | Priming |
|---|---|---|
| **pi** | `~/.pi/agent/mcp.json` | `~/.claude/skills/agent-vitals/SKILL.md` |
| **Claude Code** | `~/.claude/.mcp.json` | `~/.claude/skills/agent-vitals/SKILL.md` |
| **Cursor** | `~/.cursor/mcp.json` | `~/.cursor/rules/agent-vitals.md` |
| **OpenCode** | `~/.config/opencode/mcp.json` | `~/.config/opencode/AGENTS.md` |
| **Codex CLI** | `~/.codex/config.toml` | `~/.codex/AGENTS.md` |

> [!NOTE]
> Idempotent. Re-run `av init` any time — existing entries are skipped, never duplicated. TOML configs (Codex CLI) get TOML sections; JSON configs get JSON entries.

---

## The five tools

| Tool | Returns | When agent should reach for it |
|---|---|---|
| `vitals_summary()` | plain English | **always first** — health check, before tasks, when stuck |
| `shadow_list()` | JSON array | before infra changes — see everything running on the user's behalf |
| `shadow_stale()` | JSON array | before claiming "your crontab is fine" or scheduling new cron work |
| `burnout_summary(days=7)` | JSON object | after long tasks, to compare to baseline |
| `burnout_stuck_sessions(days=7, limit=10)` | JSON array | when suspecting a loop, to see if other sessions are stuck too |

All tools are **local-only, read-only, safe to call repeatedly**. None of them modify state.

---

## The trigger table

This is the table `av init` installs into your priming skill — so the agent knows when to reach for each tool **without you asking**:

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

> [!WARNING]
> **Honesty note.** Priming isn't enforcement. The SKILL.md puts these triggers in front of the agent's face, but the agent still has to *remember* to follow them. In practice this catches ~30–40% of cases — better than nothing, not a magic bullet. Real enforcement comes in v0.3 via pre-action hooks (e.g. block any cron modification until `shadow_stale` has been called in the last 60 seconds).

---

## Anti-patterns this exists to prevent

> [!IMPORTANT]
> These are the failure modes that made us build agent-vitals. If you see an agent doing any of these, it's a sign the priming didn't reach them — or they need v0.3 hooks.

- ❌ **"Your crontab is fine"** — without calling `shadow_stale` first
- ❌ **Scheduling cron / systemd work** — without verifying the target binary exists
- ❌ **Starting a 4-hour task** — while 6 other sessions are stuck on the same box
- ❌ **Pretending a task completed** — without checking `burnout_summary`
- ❌ **Recommending an MCP install** — without `shadow_list` to check for duplicates
- ❌ **Debugging slowness** — without first checking `vitals_summary`

---

## What it scans

| Source | Path | Notes |
|---|---|---|
| Crontab | `crontab -l` | flags targets that no longer exist |
| systemd user timers | `systemctl --user list-timers` | systemd-v255 quirk-resistant (computes `next - now` itself) |
| MCP configs | `~/.pi/agent/mcp.json`, `~/.claude/.mcp.json`, `~/.cursor/mcp.json`, `~/.config/opencode/mcp.json` | one entry per host registration |
| Codex CLI | `~/.codex/config.toml` | TOML-aware, appends `[mcp_servers.agent-vitals]` |
| Skill frontmatter | `~/.claude/skills/*/SKILL.md` | surfaces skills with `schedule:` / `cron:` / `interval:` triggers |
| pi subagent history | `~/.pi/agent/run-history.jsonl` | per-agent completion + trend |
| Claude Code sessions | `~/.claude/projects/*/*.jsonl` | session counts + stuck-loop heuristic |

---

## CLI (humans only — for verification)

```bash
av                # one-shot health summary
av doctor         # summary + actionable recommendations
av shadow         # what's configured on your box
av shadow --watch # live refresh every 2s
av burnout        # completion metrics, last 7 days
av burnout --days 30
av detect         # list detected agent hosts
av init           # wire agent-vitals into every detected host
av mcp            # start the MCP server (stdio)
av --help
```

---

## Stack

```
Python 3.11+   ──  type hints, tomllib, asyncio
uv             ──  one-tool install / build / publish
typer          ──  CLI
rich           ──  terminal rendering
pyyaml         ──  SKILL.md frontmatter parsing
mcp            ──  MCP server (FastMCP, stdio transport)
```

~900 LOC of Python + the priming `SKILL.md`. MIT licensed.

---

## Roadmap

- [x] **v0.1.0** — `shadow` + `burnout` CLI commands
- [x] **v0.2.0** — MCP server + `av init` for 5 host types
- [x] **v0.2.1** — fix false-positive duplicate detection across hosts
- [ ] **v0.3.0** — **pre-action hooks** that *enforce* vitals calls before infra mutations (not just priming)
- [ ] **v0.4.0** — `shadow live` (running agent processes, ps-tree view)
- [ ] **v0.5.0** — cross-session "agent déjà vu" detector (you researched this codebase 3 weeks ago)
- [ ] later — burnout trend over time (sparklines per agent)

---

## Contributing

Issues and PRs welcome. Two things to know:

1. **Scanners** in `src/agent_vitals/scanners.py` are independent and fail gracefully. Add a new source by writing one `scan_*()` function and adding it to `scan_all()`.
2. **MCP tool docstrings are the product.** The docstring on `vitals_summary` is the instruction the agent reads. Write it as a directive to the agent ("always call this first when…"), not API docs.

When you open a PR, paste the output of `av shadow` on your box so we can see what surfaces in your environment.

---

## License

MIT. See [`LICENSE`](LICENSE).

<br/>

<sub>built by [anirudh prashant](https://github.com/anirudhprashant) · agent-vitals v0.2.1 · 2026</sub>