# agent-vitals

> Solo-dev observability for AI agent stacks.
> **What's running on your behalf. How well it's running.**

Two commands. Zero config. No daemon, no cloud, no accounts. Reads your local config and logs and shows you a table.

```
$ agent-vitals shadow
──────────────────── shadow — what's running on your behalf ────────────────────

cron (5)
  backup-openclaw.sh       0 15 * * *    /home/anirudh/.openclaw/workspace/scri…
⚠ target missing: /home/anirudh/.openclaw/workspace/scripts/backup-openclaw.sh
  npm                      17 9 * * *    /usr/bin/npm install -g @kilocode/cli@…
  openclaw                 0 10 2 3 *    openclaw system event --text "Reminder…
⚠ target missing: openclaw
  run-nightly-improvement… 0 2 * * *     /home/anirudh/.openclaw/scripts/run-ni…
⚠ target missing: /home/anirudh/.openclaw/scripts/run-nightly-improvement.sh
  sync-memory.sh           0 3 * * *     /home/anirudh/.openclaw/scripts/sync-m…
⚠ target missing: /home/anirudh/.openclaw/scripts/sync-memory.sh

systemd (5)
  launchpadlib-cache-clean in 21.7h    launchpadlib-cache-clean.timer → launchp…
  mempalace-backup         in 10.1h    mempalace-backup.timer → mempalace-backu…
  mempalace-maintenance    in 11.5h    mempalace-maintenance.timer → mempalace-…
  pi-dark-theme-patch      in 1.5d     pi-dark-theme-patch.timer → pi-dark-them…
  pi-update                in 1.5d     pi-update.timer → pi-update.service

mcp (7)
  brave-search     always-on (htt… https://api.search.brave.com/mcp  from /home/anirudh/.pi/agent/mcp.json
  claude-mem       always-on (laz… node /home/anirudh/.claude/plugins/cache/the…  from /home/anirudh/.pi/agent/mcp.json
  context7         always-on (htt… https://mcp.context7.com/mcp  from /home/anirudh/.pi/agent/mcp.json
  filesystem       always-on (laz… npx -y @modelcontextprotocol/server-filesyst…  from /home/anirudh/.pi/agent/mcp.json
  firecrawl        always-on (laz… npx -y firecrawl-mcp  from /home/anirudh/.pi/agent/mcp.json
  github           always-on (laz… npx -y @modelcontextprotocol/server-github  from /home/anirudh/.pi/agent/mcp.json
  mempalace        always-on       /home/anirudh/.local/bin/mempalace-mcp --tra…  from /home/anirudh/.pi/agent/mcp.json | duplicate in /home/anirudh/.claude/.mcp.json

cron: 5 · mcp: 7 · systemd: 5

╭──────────────── stale references — these may no longer work ─────────────────╮
│   · backup-openclaw.sh  — ⚠ target missing: /home/anirudh/.openclaw/...sh     │
│   · openclaw  — ⚠ target missing: openclaw                                   │
│   · run-nightly-improvement.sh  — ⚠ target missing: /home/anirudh/...sh       │
│   · sync-memory.sh  — ⚠ target missing: /home/anirudh/.openclaw/...sh         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

```
$ agent-vitals burnout
─────────────────────────── burnout — last 7 day(s) ────────────────────────────

                          pi subagent run history
┏━━━━━━━━━━━━┳━━━━━━┳━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ agent      ┃ runs ┃ ok ┃ failed ┃ avg dur ┃ max dur ┃ completion ┃ trend ┃
┡━━━━━━━━━━━━╇━━━━━━╇━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│ scout      │    2 │  2 │      0 │  220.0s │  328.7s │       100% │ —     │
│ advisor    │    1 │  1 │      0 │   21.2s │   21.2s │       100% │ —     │
│ researcher │    1 │  1 │      0 │   62.3s │   62.3s │       100% │ —     │
│ reviewer   │    1 │  1 │      0 │  108.9s │  108.9s │       100% │ —     │
└────────────┴──────┴────┴────────┴─────────┴─────────┴───────────�━━━━━━━━━━━┛

────────────────────────────────────────────────────────────────────────────────

   Claude Code sessions (same window)
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ metric          ┃       value ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ sessions        │         170 │
│ total events    │       45626 │
│ largest session │ 7118 events │
└─────────────────┴────────━━━━━┛

 ⚠ 36 session(s) with 200+ events (likely stuck loops)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━┓
┃ project                                  ┃ session ┃ events ┃ last type   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━┩
│ -home-anirudh                            │ 65184081│  7118  │ last-prompt │
│ -home-anirudh                            │ 7ffc1918│  6611  │ system      │
│ -home-anirudh                            │ 894cf5e4│  4143  │ system      │
│ ...                                      │ ...     │ ...    │ ...         │
└──────────────────────────────────────────┴─────────┴───────┴─────────────┘
```

## Why

You run a stack of AI agents: cron jobs, MCP servers, scheduled skills, subagents.
Two questions you can't easily answer today:

1. **What is my stack actually doing on my behalf, right now and over time?**
2. **Of the work it's doing, how much is getting finished vs abandoned?**

`agent-vitals` answers both with one CLI. It runs in a few hundred milliseconds, leaves your system untouched, and tells you things like "this crontab entry references a binary that no longer exists" or "this Claude Code session has 7,000 events — it might be stuck."

## Install

```bash
uv tool install git+https://github.com/anirudhprashant/agent-vitals
```

Or from a local checkout:

```bash
git clone https://github.com/anirudhprashant/agent-vitals
cd agent-vitals
uv tool install .
```

Then `agent-vitals --help` from anywhere.

## Usage

```bash
agent-vitals shadow                    # what's scheduled/configured
agent-vitals shadow --json             # machine-readable
agent-vitals burnout                   # completion metrics, last 7 days
agent-vitals burnout --days 30         # custom window
```

## What it scans

### `shadow` surfaces

- **Crontab** — `crontab -l`, detects stale references (target binary missing)
- **systemd user timers** — `systemctl --user list-timers`, deduplicates `left`-field quirks across systemd versions
- **MCP server configs** — `~/.pi/agent/mcp.json`, `~/.claude/.mcp.json`, `~/.cursor/mcp.json`, `~/.config/claude/mcp.json`. Detects duplicates across configs
- **Agent skills with schedules** — `~/.claude/skills/*/SKILL.md` frontmatter with `schedule:`, `cron:`, or `interval:` keys

### `burnout` parses

- **pi-coding-agent run history** — `~/.pi/agent/run-history.jsonl`
- **Claude Code session logs** — `~/.claude/projects/*/*.jsonl`, surfaces sessions with 200+ events as likely stuck loops

## Philosophy

- **One file, one command.** No config. No init. No accounts.
- **Local-only.** Your agent logs stay on your box.
- **Honest.** If a cron references a deleted framework, we say so.
- **Small.** <500 lines of Python, three runtime deps (typer, rich, pyyaml).
- **Friendly to the user, not the tool.** We truncate long targets with `…` rather than wrapping. We de-duplicate MCP servers across configs but tag duplicates instead of hiding them.

## Roadmap

- `shadow live` — currently running agent processes (ps-tree view)
- Cross-session "agent déjà vu" detector (you researched this codebase 3 weeks ago)
- `burnout --agent <name>` — deep dive on one agent's history
- Export `agent-vitals` reports as portable JSON / Markdown for archival
- TUI mode (`agent-vitals tui`) — live tail of shadow + burnout

## Contributing

Issues and PRs welcome. Two things to know:

1. Each scanner in `src/agent_vitals/scanners.py` is independent and fails gracefully. Add new sources by writing one `scan_*()` function and adding it to `scan_all()`.
2. Real-world demo output beats synthetic data. When you open a PR, paste the output of `agent-vitals shadow` on your machine into the issue so we can see what surfaces.

## License

MIT. See `LICENSE`.