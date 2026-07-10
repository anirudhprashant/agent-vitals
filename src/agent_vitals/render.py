"""Rich renderers for shadow + burnout output.

We render shadow as one Table per source rather than one mega-table —
each source (cron, systemd, mcp, skill) has different target-length
profiles and benefits from its own column widths.
"""

from __future__ import annotations

from collections import defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent_vitals.burnout import AgentStats
from agent_vitals.scanners import ShadowRecord


def _truncate(s: str, n: int) -> str:
    """Truncate s to n chars, ending with … if cut."""
    return s if len(s) <= n else s[: n - 1] + "…"


def _group(records: list[ShadowRecord]) -> dict[str, list[ShadowRecord]]:
    by: dict[str, list[ShadowRecord]] = defaultdict(list)
    for r in records:
        by[r.source].append(r)
    return by


def _render_source_block(source: str, records: list[ShadowRecord], width: int) -> str:
    """Render one source as plain fixed-width lines. Returns text to console.print.

    Layout (target widths fit an 80-col terminal):
      cron:    name(24)  schedule(13)  command(rest)
      systemd: name(24)  next(11)      target(rest)
      mcp:     name(16)  schedule(15)  command(rest) [note: from <path>]
      skill:   name(24)  schedule(13)  path(rest)
    """
    lines: list[str] = [f"[bold magenta]{source}[/bold magenta] [dim]({len(records)})[/dim]"]
    if source == "cron":
        name_w, sched_w = 24, 13
    elif source == "systemd":
        name_w, sched_w = 24, 11
    elif source == "mcp":
        name_w, sched_w = 16, 15
    elif source == "skill":
        name_w, sched_w = 24, 13
    else:
        name_w, sched_w = 20, 13
    body_w = max(20, width - name_w - sched_w - 4)  # 4 for separators + indent
    for r in records:
        name = _truncate(r.name, name_w).ljust(name_w)
        sched = _truncate(r.schedule, sched_w).ljust(sched_w)
        target = _truncate(r.target, body_w)
        if r.note:
            # Render target + note on same line; note in yellow
            lines.append(f"  [bold]{name}[/bold] {sched} {target}  [yellow]{r.note}[/yellow]")
        else:
            lines.append(f"  [bold]{name}[/bold] {sched} {target}")
    return "\n".join(lines)


def render_shadow(records: list[ShadowRecord], console: Console, as_json: bool = False) -> None:
    if as_json:
        console.print_json(data=[r.to_dict() for r in records])
        return
    width = console.width or 100
    console.rule("[bold cyan]shadow — what's running on your behalf[/bold cyan]")
    console.print()
    if not records:
        console.print(Panel("[dim]No shadow agents detected.[/dim]", title="shadow"))
        return
    by_source = _group(records)
    blocks: list[str] = []
    for src in ["cron", "systemd", "mcp", "skill"]:
        if src in by_source:
            blocks.append(_render_source_block(src, by_source[src], width))
    console.print("\n\n".join(blocks))
    console.print()
    summary = " · ".join(f"{src}: {len(recs)}" for src, recs in sorted(by_source.items()))
    console.print(f"[dim]{summary}[/dim]")
    stale = [r for r in records if "⚠" in r.note]
    if stale:
        console.print()
        console.print(
            Panel(
                "\n".join(
                    f"  · [yellow]{r.name}[/yellow] [{r.source}] — {r.note}"
                    for r in stale
                ),
                title="[yellow]stale references — these may no longer work[/yellow]",
                border_style="yellow",
            )
        )


def render_burnout(agents: list[AgentStats], cc: dict, days: int, console: Console) -> None:
    console.rule(f"[bold cyan]burnout — last {days} day(s)[/bold cyan]")
    console.print()
    if agents:
        table = Table(title="pi subagent run history", header_style="bold cyan")
        table.add_column("agent", style="bold")
        table.add_column("runs", justify="right")
        table.add_column("ok", justify="right", style="green")
        table.add_column("failed", justify="right", style="red")
        table.add_column("avg dur", justify="right")
        table.add_column("max dur", justify="right")
        table.add_column("completion", justify="right")
        table.add_column("trend")
        for a in agents:
            avg = f"{a.avg_duration_s:.1f}s" if a.avg_duration_s else "—"
            mx = f"{a.max_duration_s:.1f}s" if a.max_duration_s else "—"
            comp = f"{a.completion_rate * 100:.0f}%"
            table.add_row(
                a.agent,
                str(a.runs),
                str(a.ok),
                str(a.failed),
                avg,
                mx,
                comp,
                a.trend,
            )
        console.print(table)
        console.print()
    else:
        console.print(Panel("[dim]No pi subagent history found at ~/.pi/agent/run-history.jsonl.[/dim]"))

    console.rule()
    console.print()
    cc_table = Table(title="Claude Code sessions (same window)", header_style="bold cyan")
    cc_table.add_column("metric", style="bold")
    cc_table.add_column("value", justify="right")
    cc_table.add_row("sessions", str(cc.get("sessions", 0)))
    cc_table.add_row("total events", str(cc.get("events", 0)))
    cc_table.add_row("largest session", f"{cc.get('largest_session', 0)} events")
    console.print(cc_table)
    console.print()

    stuck = cc.get("stuck_sessions", [])
    if stuck:
        stuck_table = Table(
            title=f"⚠ {len(stuck)} session(s) with 200+ events (likely stuck loops)",
            header_style="bold yellow",
        )
        stuck_table.add_column("project", style="bold")
        stuck_table.add_column("session", style="dim")
        stuck_table.add_column("events", justify="right")
        stuck_table.add_column("last type")
        for s in stuck[:10]:
            stuck_table.add_row(
                s["project"],
                s["session"][:8],
                str(s["events"]),
                str(s.get("last_type") or "—"),
            )
        console.print(stuck_table)
        if len(stuck) > 10:
            console.print(f"[dim]… and {len(stuck) - 10} more[/dim]")