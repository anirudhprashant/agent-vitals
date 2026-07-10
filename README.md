# agent-vitals

> **Make your AI agent less stupid.**
> Local-first observability for AI agent stacks. Built so your agent calls it proactively — not just when you ask.

```
$ av vitals_summary       ← what your agent sees (via MCP)
agent-vitals — local agent stack health

shadow: 8 autonomous thing(s) configured (mcp: 8)
  ⚠ 2 duplicate registration(s): agent-vitals, mempalace
subagent burnout (7d): 5 runs, 100% completion ✓
claude code (7d): 170 sessions, 45,626 events, ⚠ 36 stuck
  - biggest stuck-looking session: 7,118 events
```

Your agent (pi / Claude Code / OpenCode / Cursor / Codex) gets a set of MCP tools:
**`vitals_summary` · `shadow_list` · `shadow_stale` · `burnout_summary` · `burnout_stuck_sessions`**

It uses them automatically: before tasks, before scheduling infra, when stuck, after long tasks, when claiming something works.

## Install + wire up (30 seconds)

```bash
uv tool install git+https://github.com/anirudhprashant/agent-vitals
av init     # detects pi / Claude Code / OpenCode / Cursor / Codex CLI on your box,
           # registers agent-vitals as an MCP server in each, and drops a SKILL.md
           # that primes the agent to call vitals proactively
```

That's it. Restart your agent host. From now on it knows:

| Trigger | What it does |
|---|---|
| Starting any non-trivial task | Calls `vitals_summary` — surfaces stale infra or stuck sessions first |
| About to schedule cron/timer work | Calls `shadow_stale` — verifies target binary exists |
| After a long task | Calls `burnout_summary` — compares to your baseline |
| Suspects it's in a loop | Calls `vitals_summary` + `burnout_stuck_sessions` |
| About to claim "your crontab is fine" | Calls `shadow_stale` first |
| User asks "what's broken?" | Triage: `vitals_summary` → `shadow_stale` + `burnout_stuck_sessions` |

The full trigger table lives in the priming skill that `av init` installs — visible at `~/.claude/skills/agent-vitals/SKILL.md` after install.

## What `av init` does

Detects every agent host on your box and wires them up:

```
$ av init
                  detected 3 agent host(s)
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ host        ┃ config                           ┃ status   ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ pi          │ /home/anirudh/.pi/agent/mcp.json │ detected │
│ Claude Code │ /home/anirudh/.claude/.mcp.json  │ detected │
│ Codex CLI   │ /home/anirudh/.codex/config.toml │ detected │
└─────────────┴──────────────────────────────────┴────────━━┘

installing:

┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ host        ┃ mcp config         ┃ skill/rule        ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ pi          │ added              │ installed         ┃
│ Claude Code │ added              │ already installed ┃
│ Codex CLI   │ added              │ installed         ┃
└─────────────┴────────────────────┴──────────────────━┛

✓ done. Restart your agent host so it picks up the new MCP server.
```

Supported hosts: **pi** (`~/.pi/agent/mcp.json`), **Claude Code** (`~/.claude/.mcp.json` + skills), **Cursor** (`~/.cursor/mcp.json` + rules), **OpenCode** (`~/.config/opencode/`), **Codex CLI** (`~/.codex/config.toml`). Idempotent — re-run safely.

## CLI reference (for humans, to verify it's working)

```bash
av                # one-shot health summary
av doctor         # health summary + actionable recommendations
av shadow         # what's scheduled/configured on your box
av shadow --watch # live refresh every 2s
av burnout        # task completion metrics, last 7 days
av burnout --days 30
av detect         # list detected agent hosts
av init           # wire agent-vitals into all detected hosts
av mcp            # start the MCP server (stdio)
av --help
```

## MCP tool reference

All tools are local-only, read-only, and safe to call repeatedly.

### `vitals_summary() → str`

Plain-English health check. **Always call this first** when asked about the user's infrastructure. Also call it proactively before non-trivial tasks.

Returns a multi-line string like:
```
shadow: 8 autonomous thing(s) configured (mcp: 8)
  ⚠ 2 duplicate registration(s): agent-vitals, mempalace
subagent burnout (7d): 5 runs, 100% completion ✓
claude code (7d): 170 sessions, 45,626 events, ⚠ 36 stuck
  - biggest stuck-looking session: 7,118 events
```

### `shadow_list() → str` (JSON)

Full list of everything scheduled or configured. Each record:
```json
[{"name": "mempalace-backup", "source": "systemd", "schedule": "in 9.6h",
  "target": "mempalace-backup.timer → mempalace-backup.service",
  "kill_hint": "systemctl --user disable --now mempalace-backup.timer", "note": ""}, ...]
```

### `shadow_stale() → str` (JSON)

Only the broken references — cron entries pointing at deleted binaries, MCP servers that fail to register, etc.

### `burnout_summary(days: int = 7) → str` (JSON)

Per-agent completion stats + Claude Code session counts:
```json
{"days": 7, "agents": [...], "claude_code": {"sessions": 170, "events": 45626, ...}}
```

### `burnout_stuck_sessions(days: int = 7, limit: int = 10) → str` (JSON)

Claude Code sessions with 200+ events — likely stuck in tool-call loops. Heuristic only.

## What it scans

| Source | Path | What it finds |
|---|---|---|
| Crontab | `crontab -l` | Scheduled jobs; flags targets that no longer exist |
| systemd user timers | `systemctl --user list-timers` | Timed services; computes next-fire from `next - now` (systemd v255 quirk-resistant) |
| MCP server configs | `~/.pi/agent/mcp.json`, `~/.claude/.mcp.json`, `~/.cursor/mcp.json`, `~/.config/opencode/mcp.json` | All MCP servers; dedupes across configs |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.*]` entries (TOML-aware) |
| Agent skill frontmatter | `~/.claude/skills/*/SKILL.md` | Skills with `schedule:`, `cron:`, or `interval:` triggers |
| pi subagent history | `~/.pi/agent/run-history.jsonl` | Per-agent completion + trend |
| Claude Code sessions | `~/.claude/projects/*/*.jsonl` | Session counts, event totals, stuck-session detection |

## Anti-patterns (that agent-vitals exists to prevent)

❌ Agent claims "your crontab is fine" without checking `shadow_stale`
❌ Agent schedules new cron work without verifying the target binary exists
❌ Agent starts a 4-hour task while 6 other sessions are stuck on the same box
❌ Agent pretends a task completed without checking `burnout_summary`
❌ Agent recommends installing an MCP server without checking `shadow_list` for duplicates

## Stack

Python 3.11+, [uv](https://github.com/astral-sh/uv)-managed. Three runtime deps: typer, rich, pyyaml. Plus `mcp` for the server side. ~800 LOC total.

MIT licensed.

## Roadmap

- [ ] `shadow live` — currently running agent processes (ps-tree view)
- [ ] Cross-session "agent déjà vu" detector — surface tasks you already attempted N weeks ago
- [ ] Burnout trend over time (sparklines per agent)
- [ ] Webhook to GitHub: post summary as a comment on PRs that touch infra configs
- [ ] Per-project scoping — read `.agent-vitals.toml` from the project root

## Contributing

Issues and PRs welcome. Two things to know:

1. Each scanner in `src/agent_vitals/scanners.py` is independent and fails gracefully. Add new sources by writing one `scan_*()` function and adding it to `scan_all()`.
2. The MCP server in `src/agent_vitals/mcp_server.py` is the actual product surface. Tool docstrings become the tool descriptions agents read — write them as instructions to the agent, not API docs.

## License

MIT. See [LICENSE](LICENSE).