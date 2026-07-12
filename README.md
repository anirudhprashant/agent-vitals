<div align="center">

# <span style="color:#00d992">⚡ vitals</span>

### <span style="color:#f2f2f2">Give your AI agent a memory of its own infrastructure.</span>

<p>
<a href="https://github.com/anirudhprashant/agent-vitals/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL v3-00d992?style=flat-square" alt="License"></a>
&nbsp;<a href="https://github.com/anirudhprashant/agent-vitals/releases"><img src="https://img.shields.io/badge/version-v0.6.0-3d3a39?style=flat-square" alt="Version"></a>
&nbsp;<a href="https://github.com/anirudhprashant/agent-vitals"><img src="https://img.shields.io/badge/python-3.11%2B-2fd6a1?style=flat-square" alt="Python"></a>
&nbsp;<a href="https://github.com/anirudhprashant/agent-vitals"><img src="https://img.shields.io/badge/LOC-%7E2800-8b949e?style=flat-square" alt="LOC"></a>
&nbsp;<a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-stdio-00d992?style=flat-square" alt="MCP"></a>
</p>

<p>
  <code style="color:#00d992">5 MCP tools · 14 CLI commands</code>
  &nbsp;·&nbsp;
  <code style="color:#b8b3b0">wires pi · Claude Code · Cursor · OpenCode · Codex CLI</code>
  &nbsp;·&nbsp;
  <code style="color:#b8b3b0">~4900 LOC · 220 tests · GPL v3 · no daemon · no cloud</code>
</p>

<p>
  <a href="https://github.com/anirudhprashant/" style="color:#00d992">→ landing page (github.com/anirudhprashant)</a>
</p>

</div>

---

## <span style="color:#00d992">▍</span> What your agent sees

When your agent (pi, Claude Code, OpenCode, Cursor, Codex) calls `vitals_summary`, it gets:

```text
  ▄▀ vitals — local agent stack health

  shadow: 13 autonomous thing(s) configured (mcp: 8, systemd: 5)
  subagent burnout (7d): 5 runs, 100% completion ✓
  claude code (7d): 170 sessions, 45,626 events, ⚠ 36 stuck
    - biggest stuck-looking session: 7,118 events
```

That's not a CLI you read. That's a tool your agent calls from inside its own loop — before it does anything risky.

---

## <span style="color:#00d992">▍</span> Before / after

<table>
<tr>
<th width="50%" align="left"><span style="color:#fb565b">▍ Without vitals</span></th>
<th width="50%" align="left"><span style="color:#00d992">▍ With vitals</span></th>
</tr>
<tr>
<td valign="top">

```text
User: schedule a daily backup at 2am

Agent: yep, done.

  0 2 * * * /home/me/backup.sh
        >> /tmp/backup.log 2>&1
```

… and `/home/me/backup.sh` doesn't exist. Crontab fails silently forever.

</td>
<td valign="top">

```text
User: schedule a daily backup at 2am

Agent: one moment — verifying first.

  → shadow_stale       (no other cron broken)
  → check backup.sh    (exists? writable?)
  → write crontab entry
  → shadow_stale       (confirm no breakage)

  ✓ crontab updated.
```

</td>
</tr>
<tr>
<td valign="top">

```text
User: is my agent setup healthy?

Agent: looks fine to me!
```

(blind vibes)

</td>
<td valign="top">

```text
User: is my agent setup healthy?

Agent: → vitals_summary

  shadow: 13 autonomous thing(s) configured
  claude code (7d): ⚠ 36 stuck sessions
    - biggest: 7,118 events

  ⚠ 36 sessions look stuck. want me to triage?
```

</td>
</tr>
</table>

The diff is **vibes vs. data**.

---

## <span style="color:#00d992">▍</span> Install

```bash
uv tool install agent-vitals
av install  # detects every agent host on your box and wires them up
            # restart your agent host so it picks up the new MCP server
```

That's the whole setup. <span style="color:#00d992">30 seconds.</span>

---

## <span style="color:#00d992">▍</span> Pre-action hooks (v0.3.0)

Priming asks the agent to call vitals. **Hooks refuse the operation** when it hasn't.

```bash
av hooks install     # one-time setup (~3 seconds)
av hooks status      # check freshness
av hooks disable     # temporarily turn off (rename to *.disabled)
av hooks uninstall   # full removal
```

After `av hooks install`, the one-liner above is appended to your `~/.bashrc` / `~/.zshrc`. Open a new terminal and `crontab -e` or `systemctl --user enable foo` will be **refused at the OS level** unless an agent (or `av doctor`) refreshed the vitals stamp in the last 60 seconds.

```text
  ⚡ vitals hook: refused `crontab -e`

  reason:   vitals stamp is 5m12s old — exceeds 60s window.
  stamp:    5m12s ago

  refresh:  call any vitals tool or run `av doctor`
  bypass:   VITALS_BYPASS=1 crontab -e
```

**What's gated:** `crontab -e/-r/-i/<file>/-` and `systemctl --user {enable,disable,start,stop,restart,reload,mask,unmask,daemon-reload,...}`.

**What's NOT gated:** `crontab -l` (reads), `systemctl status / list-* / is-active / show / cat` (reads), and power management (`reboot`, `poweroff`, `suspend`) — a stale stamp must never block a reboot.

**Bypass for emergencies:** `VITALS_BYPASS=1 crontab -e` skips the check.

## <span style="color:#00d992">▍</span> What `av install` does

```text
  $ av install
                    detected 3 agent host(s)
  ┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
  ┃ host        ┃ config                           ┃ status   ┃
  ┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
  │ pi          │ ~/.pi/agent/mcp.json │ detected │
  │ Claude Code │ ~/.claude/.mcp.json  │ detected │
  │ Codex CLI   │ ~/.codex/config.toml │ detected │
  └─────────────┴──────────────────────────────────┴────────━━┘

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
| <span style="color:#00d992">**pi**</span> | `~/.pi/agent/mcp.json` | `~/.claude/skills/vitals/SKILL.md` |
| <span style="color:#00d992">**Claude Code**</span> | `~/.claude/.mcp.json` | `~/.claude/skills/vitals/SKILL.md` |
| <span style="color:#00d992">**Cursor**</span> | `~/.cursor/mcp.json` | `~/.cursor/rules/vitals.md` |
| <span style="color:#00d992">**OpenCode**</span> | `~/.config/opencode/mcp.json` | `~/.config/opencode/AGENTS.md` |
| <span style="color:#00d992">**Codex CLI**</span> | `~/.codex/config.toml` | `~/.codex/AGENTS.md` |

> [!NOTE]
> Idempotent. Re-run `av install` any time — existing entries are skipped, never duplicated. TOML configs (Codex CLI) get TOML sections; JSON configs get JSON entries.

---

## <span style="color:#00d992">▍</span> The five tools

| Tool | Returns | When the agent should reach for it |
|---|---|---|
| `vitals_summary()` | plain English | **always first** — health check, before tasks, when stuck |
| `shadow_list()` | JSON array | before infra changes — see everything running on the user's behalf |
| `shadow_stale()` | JSON array | before claiming "your crontab is fine" or scheduling new cron work |
| `burnout_summary(days=7)` | JSON object | after long tasks, to compare to baseline |
| `burnout_stuck_sessions(days=7, limit=10)` | JSON array | when suspecting a loop, to see if other sessions are stuck too |
| `av loops` | text report | after a session is "taking forever" or cost spikes — detects exact + soft doom loops |
| `av unused` | text report | after installing a new MCP server, or weekly — reports unused servers + per-tool usage |
| `av cost` | text report | monthly budget review — uses observed model pricing + Effective Tokens + model downgrade suggestions |
| `av tokens` | text report | identify token-heavy tools — which tools burn the most tokens per call |
| `av tokens --suggest` | text report | get concrete optimization suggestions based on usage patterns |
| `av ssh` | text report | detect SSH polling loops — repeated SSH commands to same host |
| `av overlap` | text report | detect overlapping MCP tools across servers — possible redundancy |
| `av compact` | text report | compact large session files to reduce context bloat |
| `av compact --dry-run` | text report | preview compaction savings without modifying files |
| `av coach` | text report | generate optimized system prompts + playbooks from real session data |

All tools are **local-only, read-only, safe to call repeatedly**. None of them modify state.

---

## <span style="color:#00d992">▍</span> The trigger table

The table `av install` installs into your priming skill — so the agent knows when to reach for each tool **without you asking**:

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
> **Honesty note.** Priming isn't enforcement. The SKILL.md puts these triggers in front of the agent's face, but the agent still has to *remember* to follow them. In practice this catches ~30–40% of cases — better than nothing, not a magic bullet.
> 
> **v0.3.0 changes this.** `av hooks install` deploys PATH-level wrappers around `crontab` and `systemctl --user` that *refuse* any mutation when the vitals stamp is older than 60 seconds. Read operations (`crontab -l`, `systemctl status`, etc.) are never gated. See [Pre-action hooks](#pre-action-hooks-v030) below.

---

## <span style="color:#fb565b">▍</span> Anti-patterns this exists to prevent

> [!IMPORTANT]
> These are the failure modes that made us build vitals. If you see an agent doing any of these, it's a sign the priming didn't reach them — or they need **v0.3.0 hooks**.

- ❌ **"Your crontab is fine"** — without calling `shadow_stale` first
- ❌ **Scheduling cron / systemd work** — without verifying the target binary exists
- ❌ **Starting a 4-hour task** — while 6 other sessions are stuck on the same box
- ❌ **Pretending a task completed** — without checking `burnout_summary`
- ❌ **Recommending an MCP install** — without `shadow_list` to check for duplicates
- ❌ **Debugging slowness** — without first checking `vitals_summary`

---

## <span style="color:#00d992">▍</span> What it scans

| Source | Path | What it finds |
|---|---|---|
| Crontab | `crontab -l` | flags targets that no longer exist |
| systemd user timers | `systemctl --user list-timers` | systemd-v255 quirk-resistant (computes `next - now` itself) |
| MCP configs | `~/.pi/agent/mcp.json`, `~/.claude/.mcp.json`, `~/.cursor/mcp.json`, `~/.config/opencode/mcp.json` | one entry per host registration |
| Codex CLI | `~/.codex/config.toml` | TOML-aware, appends `[mcp_servers.vitals]` |
| Skill frontmatter | `~/.claude/skills/*/SKILL.md` | surfaces skills with `schedule:` / `cron:` / `interval:` triggers |
| pi subagent history | `~/.pi/agent/run-history.jsonl` | per-agent completion + trend |
| Claude Code sessions | `~/.claude/projects/*/*.jsonl` | session counts + stuck-loop heuristic |

---

## <span style="color:#00d992">▍</span> CLI (humans only — for verification)

```bash
av                # one-shot health summary
av doctor         # summary + actionable recommendations
av shadow         # what's configured on your box
av shadow --watch # live refresh every 2s
av burnout        # completion metrics, last 7 days
av burnout --days 30
av detect         # list detected agent hosts
av install        # wire vitals into every detected host
av mcp            # start the MCP server (stdio)

# Efficiency suite (v0.6.0+)
av loops          # detect exact + soft doom loops in sessions
av loops -n 50     # show up to 50 findings
av unused         # find unused MCP servers + per-tool usage
av cost           # token spend with model-aware pricing + Effective Tokens
av tokens         # identify token-heavy tools
av tokens --suggest  # get optimization suggestions
av ssh            # detect SSH polling loops
av overlap        # detect overlapping MCP tools
av compact        # compact large session files
av compact --dry-run  # preview compaction without changes
av coach          # generate optimized system prompts from session data
```

---

## <span style="color:#00d992">▍</span> Stack

```
Python 3.11+    ──  type hints, tomllib, asyncio
uv              ──  one-tool install / build / publish
typer           ──  CLI
rich            ──  terminal rendering
pyyaml          ──  SKILL.md frontmatter parsing
mcp (FastMCP)   ──  MCP server, stdio transport
pytest          ──  220 tests across 5 modules
```

~4900 lines of Python + 220 tests + priming SKILL.md. GPL v3 licensed.

---

## <span style="color:#00d992">▍</span> Roadmap

- [x] **v0.1.0** — `shadow` + `burnout` CLI commands
- [x] **v0.2.0** — MCP server + `av install` for 5 host types
- [x] **v0.2.1** — fix false-positive duplicate detection across hosts
- [x] **v0.3.0** — <span style="color:#00d992">**pre-action hooks**</span> for crontab + systemctl (priming → enforcement, with bypass)
- [x] **v0.4.0** — <span style="color:#00d992">**the full suite**</span>: interactive `av install`, `av drift`, `av cost`, `av sessions`, `av snapshot`
- [x] **v0.6.0** — <span style="color:#00d992">**efficiency suite**</span>: `av loops` (doom-loop detection), `av unused` (registered-but-unused MCP tool detector), `av cost` with ET metric, `av tokens`, `av ssh`, `av overlap`, `av compact`, `av coach`. 220 tests total.
- [ ] **v0.7.0** — expose drift / cost / sessions / snapshot / loops / unused as MCP tools (currently CLI-only)
- [ ] **v0.7.0** — `shadow live` (running agent processes, ps-tree view)
- [ ] **v0.8.0** — cross-session "agent déjà vu" detector (you researched this codebase 3 weeks ago)

---

## <span style="color:#00d992">▍</span> Contributing

Issues and PRs welcome. Two things to know:

1. **Scanners** in `src/agent_vitals/scanners.py` are independent and fail gracefully. Add a new source by writing one `scan_*()` function and adding it to `scan_all()`.
2. **MCP tool docstrings are the product.** The docstring on `vitals_summary` is the instruction the agent reads. Write it as a directive to the agent ("always call this first when…"), not API docs.

When you open a PR, paste the output of `av shadow` on your box so we can see what surfaces in your environment.

---

## <span style="color:#00d992">▍</span> License

GPL v3. See [`LICENSE`](LICENSE).

<br/>

<sub>
<span style="color:#3d3a39">─────────────────────────────────────────────</span>
<br/>
<span style="color:#00d992">vitals v0.6.0</span> <span style="color:#8b949e">·</span> <span style="color:#b8b3b0">2026</span>
</sub>