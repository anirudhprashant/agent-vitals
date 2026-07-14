"""MCP server: expose vitals scanners as agent-callable tools.

Once registered as an MCP server (in pi / Claude Code / OpenCode / Cursor /
etc.), any agent can introspect the user's local agent stack with one call.

Run standalone via `av mcp` (stdio transport).

Every tool call also touches the freshness stamp — that's what powers the
v0.3.0 pre-action hooks.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_vitals.burnout import burn_all
from agent_vitals.scanners import scan_all
from agent_vitals.stamp import touch
from agent_vitals import trace as trace_mod

mcp = FastMCP(
    name="vitals",
    instructions=(
        "vitals: solo-dev observability for AI agent stacks. "
        "Use vitals_summary first for a one-shot health check. "
        "Use shadow_list to see everything scheduled/configured to act on "
        "the user's behalf. Use shadow_stale to find broken references. "
        "Use burnout_summary and burnout_stuck_sessions to surface abandoned "
        "work and likely-stuck Claude Code sessions. "
        "Call these tools PROACTIVELY: before tasks, after tasks, when stuck, "
        "or when about to schedule infra work — not only when the user asks. "
        "Your calls refresh the freshness stamp that powers pre-action hooks; "
        "an agent that never calls vitals triggers refusal of infra mutations."
    ),
    website_url="https://github.com/anirudhprashant/agent-vitals",
)


def _j(obj) -> str:
    """Serialize to compact JSON string."""
    return json.dumps(obj, indent=2, default=str)


def _touch() -> None:
    """Refresh the freshness stamp so a hook-gated mutation will pass."""
    touch()


@mcp.tool(title="vitals summary")
def vitals_summary() -> str:
    """One-shot health check for the user's local agent stack.

    Returns plain English. ALWAYS call this first when asked about the health,
    status, or what's running on behalf of the user. Also call it proactively
    before non-trivial tasks to surface stale infrastructure or abandoned work.
    """
    _touch()
    records = scan_all()
    agents, cc = burn_all(days=7)

    by_source: dict[str, int] = {}
    for r in records:
        by_source[r.source] = by_source.get(r.source, 0) + 1

    stale = [r for r in records if "⚠" in r.note]
    duplicate_names = [r.name for r in records if "duplicate" in r.note]
    stuck = cc.get("stuck_sessions", [])
    total_sessions = cc.get("sessions", 0)
    total_events = cc.get("events", 0)

    subagent_total = sum(a.runs for a in agents)
    subagent_failed = sum(a.failed for a in agents)
    subagent_completion = (
        sum(a.ok for a in agents) / subagent_total * 100 if subagent_total else 0
    )

    lines: list[str] = ["vitals — local agent stack health", ""]

    if records:
        lines.append(
            f"shadow: {len(records)} autonomous thing(s) configured "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(by_source.items()))})"
        )
    else:
        lines.append("shadow: nothing scheduled or configured (clean)")

    if stale:
        lines.append(f"  ⚠ {len(stale)} stale reference(s) — pointing at missing binaries/paths")
        for r in stale:
            lines.append(f"    - {r.name} ({r.source}): {r.note.removeprefix('⚠ ').strip()}")

    if duplicate_names:
        lines.append(
            f"  ⚠ {len(duplicate_names)} duplicate registration(s): "
            f"{', '.join(sorted(set(duplicate_names)))}"
        )

    if subagent_total:
        verb = "✓" if subagent_failed == 0 else "⚠"
        lines.append(
            f"subagent burnout (7d): {subagent_total} runs, "
            f"{subagent_completion:.0f}% completion {verb} ({subagent_failed} failed)"
        )

    if total_sessions:
        verb = "✓" if not stuck else f"⚠ {len(stuck)} stuck"
        lines.append(
            f"claude code (7d): {total_sessions} sessions, {total_events:,} events, {verb}"
        )
        if stuck:
            biggest = max(s["events"] for s in stuck)
            lines.append(f"  - biggest stuck-looking session: {biggest:,} events")

    if not stale and not duplicate_names and not stuck and subagent_failed == 0:
        lines.append("")
        lines.append("✓ no actionable issues found")

    return "\n".join(lines)


@mcp.tool(title="shadow list")
def shadow_list() -> str:
    """Full list of everything scheduled or configured to act on the user.

    Sources: crontab, systemd user timers, MCP server configs (across pi /
    Claude Code / Cursor), and agent skill frontmatter with schedule triggers.

    Returns JSON string. Each record has: name, source, schedule, target,
    kill_hint, note.
    """
    _touch()
    return _j([r.to_dict() for r in scan_all()])


@mcp.tool(title="shadow stale")
def shadow_stale() -> str:
    """Only the broken/stale references — cron entries pointing at deleted
    binaries, MCP servers that fail to register, etc.

    Use when asked "what's broken in my agent stack?" or "should I clean up
    my crontab?". Returns JSON string.
    """
    _touch()
    return _j([r.to_dict() for r in scan_all() if "⚠" in r.note])


@mcp.tool(title="burnout summary")
def burnout_summary(days: int = 7) -> str:
    """Top-line agent completion stats for the last `days` days (default 7).

    Combines pi subagent run history with Claude Code session counts.
    Returns JSON string with: agents (per-agent stats), claude_code (sessions,
    events, largest_session, stuck_count).
    """
    _touch()
    agents, cc = burn_all(days=days)
    return _j({
        "days": days,
        "agents": [
            {
                "agent": a.agent,
                "runs": a.runs,
                "ok": a.ok,
                "failed": a.failed,
                "avg_duration_s": round(a.avg_duration_s, 1),
                "max_duration_s": round(a.max_duration_s, 1),
                "completion_rate": round(a.completion_rate, 3),
                "trend": a.trend,
            }
            for a in agents
        ],
        "claude_code": cc,
    })


@mcp.tool(title="burnout stuck sessions")
def burnout_stuck_sessions(days: int = 7, limit: int = 10) -> str:
    """Claude Code sessions with 200+ events — likely stuck in tool-call loops.

    Heuristic only. Returns JSON string with up to `limit` sessions (default 10),
    sorted by event count descending. Each entry: session, project, events,
    last_type.
    """
    _touch()
    _, cc = burn_all(days=days)
    return _j(cc.get("stuck_sessions", [])[:limit])


# ---------- trace (v0.7.0) ----------


@mcp.tool(title="trace list")
def trace_list() -> str:
    """List discoverable agent sessions with source type and event counts."""
    rows = trace_mod.list_sessions()
    if not rows:
        return "trace: no sessions found\n"
    lines = ["path | source | events", "--- | --- | ---"]
    for path, source, count in rows[:50]:
        lines.append(f"{path} | {source} | {count}")
    return "\n".join(lines) + "\n"


@mcp.tool(title="trace summary")
def trace_summary(session: str) -> str:
    """One-shot trace summary for a session JSONL: turns, tools, errors, wall duration."""
    p = Path(session)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    if not events:
        return "trace: no parseable events\n"
    stats = trace_mod.summary(events)
    lines = [
        f"trace summary · {p.name}",
        f"  events : {stats['events']}",
        f"  turns  : {stats['turns']}",
        f"  tools  : {stats['tools']}",
        f"  results: {stats['results']}",
        f"  errors : {stats['errors']}",
        f"  wall   : {trace_mod._format_duration(stats['wall_ms'])}",
    ]
    if stats["tools"]:
        lines.append(f"  avg tool: {trace_mod._format_duration(stats['avg_tool_ms'])}")
    return "\n".join(lines) + "\n"


@mcp.tool(title="trace diff")
def trace_diff(session_a: str, session_b: str) -> str:
    """Structural diff between two session traces (no payloads)."""
    pa = Path(session_a)
    pb = Path(session_b)
    if not pa.exists():
        return f"trace: file not found: {session_a}\n"
    if not pb.exists():
        return f"trace: file not found: {session_b}\n"
    events_a = trace_mod.trace_events(pa)
    events_b = trace_mod.trace_events(pb)
    return trace_mod.diff(events_a, events_b)


@mcp.tool(title="trace errors")
def trace_errors(session: str) -> str:
    """Show only error events from a session trace."""
    p = Path(session)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    errs = trace_mod.errors(events)
    if not errs:
        return "trace: no errors found\n"
    return trace_mod.replay(errs)


@mcp.tool(title="trace profile")
def trace_profile_mcp(session: str) -> str:
    """Per-tool breakdown: call count, error rate, avg duration."""
    p = Path(session)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    prof = trace_mod.profile(events)
    tools = prof.get("tools", [])
    if not tools:
        return "trace: no tool calls found\n"
    lines = [f"trace profile · {p.name} · {len(tools)} tools"]
    header = f"  {'tool':<20} {'calls':>6} {'errors':>7} {'err%':>6} {'avg':>8}"
    lines.append(header)
    lines.append("  " + "-" * len(header.strip()))
    for row in tools:
        err_pct = f"{row['error_rate'] * 100:.0f}%"
        avg = trace_mod._format_duration(row["avg_ms"])
        lines.append(
            f"  {row['tool']:<20} {row['calls']:>6} {row['errors']:>7} {err_pct:>6} {avg:>8}"
        )
    return "\n".join(lines) + "\n"


@mcp.tool(title="trace grep")
def trace_grep(session: str, pattern: str) -> str:
    """Filter events by tool name or event type (case-insensitive)."""
    p = Path(session)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    matches = trace_mod.grep(events, pattern)
    if not matches:
        return f"trace: no matches for '{pattern}'\n"
    return trace_mod.replay(matches)


@mcp.tool(title="trace export")
def trace_export(session: str, output: str) -> str:
    """Export normalized trace events to JSON file."""
    p = Path(session)
    out = Path(output)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    trace_mod.export_json(events, out)
    return f"trace: exported {len(events)} events to {out}\n"


@mcp.tool(title="trace suggest")
def trace_suggest_mcp(session: str) -> str:
    """Actionable suggestions based on session trace data."""
    p = Path(session)
    if not p.exists():
        return f"trace: file not found: {session}\n"
    events = trace_mod.trace_events(p)
    suggestions = trace_mod.suggest(events)
    lines = [f"trace suggestions · {p.name}"]
    for i, s in enumerate(suggestions, 1):
        lines.append(f"  {i}. {s}")
    return "\n".join(lines) + "\n"


def main() -> None:
    """Run as stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()