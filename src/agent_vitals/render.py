"""Rich renderers for shadow + burnout output.

We render shadow as a plain text block per source (readable at 80 cols).
burnout uses tables (it's already narrow). Both have a `__summary__`
helper that produces a one-shot health header so callers can show the
high-level picture before the detail.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from agent_vitals.burnout import AgentStats, burn_all
from agent_vitals.scanners import ShadowRecord, scan_all


# ---------- summary header (shared) ----------


def health_summary(records: list[ShadowRecord], cc: dict, agents: list[AgentStats]) -> str:
    """Plain-English health check, suitable for printing first."""
    by_source: dict[str, int] = {}
    for r in records:
        by_source[r.source] = by_source.get(r.source, 0) + 1

    stale = [r for r in records if "⚠" in r.note]
    duplicate_names = sorted({r.name for r in records if "duplicate" in r.note})
    stuck = cc.get("stuck_sessions", [])
    total_sessions = cc.get("sessions", 0)
    total_events = cc.get("events", 0)

    subagent_total = sum(a.runs for a in agents)
    subagent_failed = sum(a.failed for a in agents)
    subagent_completion = (
        sum(a.ok for a in agents) / subagent_total * 100 if subagent_total else 0
    )

    lines: list[str] = []
    if records:
        bits = ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items()))
        lines.append(f"[bold]shadow[/bold]  {len(records)} autonomous thing(s)  [dim]({bits})[/dim]")
    else:
        lines.append("[bold]shadow[/bold]  nothing scheduled or configured  [green]✓[/green]")

    if stale:
        lines.append(f"  [yellow]⚠[/yellow] [yellow]{len(stale)} stale reference(s) — pointing at missing binaries/paths[/yellow]")
        for r in stale[:5]:
            clean = r.note.removeprefix("⚠ ").strip()
            lines.append(f"    - [yellow]{r.name}[/yellow] [{r.source}]: {clean}")
        if len(stale) > 5:
            lines.append(f"    [dim]… and {len(stale) - 5} more[/dim]")

    if duplicate_names:
        lines.append(
            f"  [yellow]⚠[/yellow] [yellow]{len(duplicate_names)} duplicate MCP registration(s): "
            f"{', '.join(duplicate_names)}[/yellow]"
        )

    if subagent_total:
        verb = "[green]✓[/green]" if subagent_failed == 0 else "[yellow]⚠[/yellow]"
        lines.append(
            f"[bold]subagents[/bold]  {subagent_total} runs in 7d  "
            f"{subagent_completion:.0f}% completion {verb}  "
            f"[dim]({subagent_failed} failed)[/dim]"
        )

    if total_sessions:
        verb = "[green]✓[/green]" if not stuck else f"[yellow]⚠ {len(stuck)} stuck[/yellow]"
        lines.append(
            f"[bold]claude code[/bold]  {total_sessions} sessions in 7d  "
            f"{total_events:,} events  {verb}"
        )
        if stuck:
            biggest = max(s["events"] for s in stuck)
            lines.append(f"  [dim]biggest stuck-looking session: {biggest:,} events[/dim]")

    if not stale and not duplicate_names and not stuck and subagent_failed == 0 and records:
        lines.append("")
        lines.append("[green]✓ no actionable issues found[/green]")

    return "\n".join(lines)


# ---------- shadow ----------


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _group(records: list[ShadowRecord]) -> dict[str, list[ShadowRecord]]:
    by: dict[str, list[ShadowRecord]] = defaultdict(list)
    for r in records:
        by[r.source].append(r)
    return by


def _render_source_block(source: str, records: list[ShadowRecord], width: int) -> str:
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
    body_w = max(20, width - name_w - sched_w - 4)
    for r in records:
        name = _truncate(r.name, name_w).ljust(name_w)
        sched = _truncate(r.schedule, sched_w).ljust(sched_w)
        target = _truncate(r.target, body_w)
        if r.note:
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
        console.print(Panel("[green]✓ nothing scheduled or configured[/green]", title="shadow"))
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


# ---------- burnout ----------


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
            table.add_row(a.agent, str(a.runs), str(a.ok), str(a.failed), avg, mx, comp, a.trend)
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
                s["project"], s["session"][:8], str(s["events"]), str(s.get("last_type") or "—"),
            )
        console.print(stuck_table)
        if len(stuck) > 10:
            console.print(f"[dim]… and {len(stuck) - 10} more[/dim]")


# ---------- doctor ----------


def render_doctor(records: list[ShadowRecord], cc: dict, agents: list[AgentStats], console: Console) -> None:
    """Health check with actionable recommendations."""
    console.rule("[bold cyan]agent-vitals doctor[/bold cyan]")
    console.print()
    console.print(f"[dim]run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
    console.print()
    console.print(health_summary(records, cc, agents))
    console.print()

    actions: list[str] = []
    stale = [r for r in records if "⚠" in r.note]
    duplicates = sorted({r.name for r in records if "duplicate" in r.note})
    stuck = cc.get("stuck_sessions", [])
    failed_agents = [a for a in agents if a.failed > 0]

    if stale:
        actions.append(
            f"• [yellow]clean stale references[/yellow]: "
            f"`crontab -e` to remove {len(stale)} line(s) pointing at deleted paths"
        )
    if duplicates:
        actions.append(
            f"• [yellow]dedupe MCP servers[/yellow]: "
            f"'{', '.join(duplicates)}' registered in multiple configs"
        )
    if stuck:
        biggest = max(s["events"] for s in stuck)
        actions.append(
            f"• [yellow]review stuck sessions[/yellow]: "
            f"{len(stuck)} Claude Code session(s) with 200+ events "
            f"(biggest: {biggest:,}). Consider adding a turn or token budget."
        )
    if failed_agents:
        names = ", ".join(a.agent for a in failed_agents)
        actions.append(
            f"• [yellow]investigate failed agents[/yellow]: {names}"
        )

    if actions:
        console.print(Panel("\n".join(actions), title="[bold]recommended actions[/bold]", border_style="cyan"))
    else:
        console.print(Panel("[green]✓ all clean — nothing to do[/green]", border_style="green"))


# ---------- watch ----------


def watch_shadow(console: Console, interval: float = 2.0) -> None:
    """Live-refresh the shadow output until Ctrl+C."""
    console.print(f"[dim]live mode — refreshing every {interval:.0f}s (ctrl+c to exit)[/dim]")
    console.print()
    try:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            while True:
                records = scan_all()
                cc = burn_all(days=7)[1]
                agents = burn_all(days=7)[0]
                panel = Panel(
                    health_summary(records, cc, agents) + "\n\n" + _shadow_compact(records, console.width or 100),
                    title=f"[bold cyan]shadow[/bold cyan] [dim]— {datetime.now().strftime('%H:%M:%S')}[/dim]",
                    border_style="cyan",
                )
                live.update(panel)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped[/dim]")


def watch_burnout(console: Console, days: int = 7, interval: float = 30.0) -> None:
    """Live-refresh the burnout output until Ctrl+C."""
    console.print(f"[dim]live mode — refreshing every {interval:.0f}s (ctrl+c to exit)[/dim]")
    console.print()
    try:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            while True:
                agents, cc = burn_all(days=days)
                table = Table(title=f"burnout — {datetime.now().strftime('%H:%M:%S')}", header_style="bold cyan")
                table.add_column("source", style="bold")
                table.add_column("metric")
                table.add_column("value", justify="right")
                for a in agents:
                    table.add_row("subagent", a.agent, f"{a.runs} runs ({a.completion_rate*100:.0f}% ok)")
                table.add_row("claude code", "sessions", str(cc.get("sessions", 0)))
                table.add_row("claude code", "events", f"{cc.get('events', 0):,}")
                table.add_row("claude code", "stuck-looking", str(len(cc.get("stuck_sessions", []))))
                live.update(table)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped[/dim]")


def _shadow_compact(records: list[ShadowRecord], width: int) -> str:
    """Compact shadow view for the watch panel."""
    if not records:
        return "[green]✓ nothing scheduled or configured[/green]"
    by_source = _group(records)
    blocks: list[str] = []
    for src in ["cron", "systemd", "mcp", "skill"]:
        if src in by_source:
            blocks.append(_render_source_block(src, by_source[src], width))
    return "\n\n".join(blocks)