"""MCP server: expose agent-vitals scanners as agent-callable tools.

Once registered as an MCP server (in pi / Claude Code / OpenCode / Cursor /
etc.), any agent can introspect the user's local agent stack with one call.

Run standalone via `av mcp` (stdio transport).
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from agent_vitals.burnout import burn_all
from agent_vitals.scanners import scan_all

mcp = FastMCP(
    name="agent-vitals",
    instructions=(
        "agent-vitals: solo-dev observability for AI agent stacks. "
        "Use vitals_summary first for a one-shot health check. "
        "Use shadow_list to see everything scheduled/configured to act on "
        "the user's behalf. Use shadow_stale to find broken references. "
        "Use burnout_summary and burnout_stuck_sessions to surface abandoned "
        "work and likely-stuck Claude Code sessions. "
        "Call these tools PROACTIVELY: before tasks, after tasks, when stuck, "
        "or when about to schedule infra work — not only when the user asks."
    ),
    website_url="https://github.com/anirudhprashant/agent-vitals",
)


def _j(obj) -> str:
    """Serialize to compact JSON string."""
    return json.dumps(obj, indent=2, default=str)


@mcp.tool(title="vitals summary")
def vitals_summary() -> str:
    """One-shot health check for the user's local agent stack.

    Returns plain English. ALWAYS call this first when asked about the health,
    status, or what's running on behalf of the user. Also call it proactively
    before non-trivial tasks to surface stale infrastructure or abandoned work.
    """
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

    lines: list[str] = ["agent-vitals — local agent stack health", ""]

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
    return _j([r.to_dict() for r in scan_all()])


@mcp.tool(title="shadow stale")
def shadow_stale() -> str:
    """Only the broken/stale references — cron entries pointing at deleted
    binaries, MCP servers that fail to register, etc.

    Use when asked "what's broken in my agent stack?" or "should I clean up
    my crontab?". Returns JSON string.
    """
    return _j([r.to_dict() for r in scan_all() if "⚠" in r.note])


@mcp.tool(title="burnout summary")
def burnout_summary(days: int = 7) -> str:
    """Top-line agent completion stats for the last `days` days (default 7).

    Combines pi subagent run history with Claude Code session counts.
    Returns JSON string with: agents (per-agent stats), claude_code (sessions,
    events, largest_session, stuck_count).
    """
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
    _, cc = burn_all(days=days)
    return _j(cc.get("stuck_sessions", [])[:limit])


def main() -> None:
    """Run as stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()