"""Typer CLI entry point.

The real product is the MCP server that other agents call (`av mcp`).
The CLI exists to:
  - Verify what's wired up (`av doctor`, `av detect`, `av status`)
  - Drive the interactive installer (`av install`)
  - Surface drift / cost / session / snapshot tools for the user
  - Provide shell hooks (`av hooks install/status/...`)

Run `av --help` to see everything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from agent_vitals import __version__
from agent_vitals import coach as coach_mod
from agent_vitals.burnout import burn_all
from agent_vitals import hooks as hooks_mod
from agent_vitals import snapshot as snap_mod
from agent_vitals import trace as trace_mod
from agent_vitals.cost import render_cost_report, render_tokens_report, scan_all_sessions, scan_tool_tokens
from agent_vitals.efficiency import (
    find_loops,
    find_unused_tools,
    find_overlapping_tools,
    render_loop_report,
    render_unused_report,
    render_overlap_report,
)
from agent_vitals.drift import detect_all_drift, render_drift_report
from agent_vitals.install import run_install
from agent_vitals.primer import list_hosts
from agent_vitals.render import (
    health_summary,
    render_burnout,
    render_doctor,
    render_shadow,
    watch_burnout,
    watch_shadow,
)
from agent_vitals.sessions import (
    discover_sessions,
    filter_sessions,
    render_sessions_table,
)
from agent_vitals.scanners import scan_all
from agent_vitals.stamp import touch

if TYPE_CHECKING:
    from agent_vitals.cost import TokenBucket, ToolTokenUsage
    from agent_vitals.sessions import SessionInfo


app = typer.Typer(
    name="agent-vitals",
    help=(
        "agent-vitals: solo-dev observability for AI agent stacks. "
        "Run `av install` to set up; `av doctor` for a health check; "
        "`av mcp` to start the server; `av hooks install` for v0.3.0 pre-action gates."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
console = Console()




def _model_downgrade_suggestions(by_host_project: dict[str, dict[str, TokenBucket]]) -> list[str]:
    """Suggest model downgrades when high-tier models are used for low-output work."""
    suggestions = []
    downgrades = {
        "claude-opus-4": "claude-sonnet-4",
        "claude-opus-4-7": "claude-sonnet-4",
        "claude-opus-4-8": "claude-sonnet-4",
        "gpt-4o": "gpt-4o-mini",
        "o1": "o1-mini",
    }
    for host, by_proj in by_host_project.items():
        for proj, bucket in by_proj.items():
            model = bucket.model or "_default"
            if model not in downgrades:
                continue
            # Low output = <100 output tokens total for the project
            if bucket.output_tokens < 100:
                cheaper = downgrades[model]
                savings = bucket.cost_usd() - bucket.cost_usd(cheaper)
                if savings > 0.01:
                    suggestions.append(
                        f"{proj}: using {model} but only {bucket.output_tokens} output tokens — "
                        f"downgrade to {cheaper} to save ~${savings:.2f}"
                    )
    return suggestions


def _compaction_suggestions(sessions: list[SessionInfo]) -> list[str]:
    """Suggest compaction for very large sessions."""
    suggestions = []
    for s in sessions:
        if s.size_bytes > 10 * 1024 * 1024:  # > 10MB
            suggestions.append(
                f"{s.path.name}: {s.size_bytes / (1024 * 1024):.1f} MiB — consider compacting or archiving"
            )
        elif s.event_count is not None and s.event_count > 5000:
            suggestions.append(
                f"{s.path.name}: {s.event_count} events — consider compacting"
            )
    return suggestions[:10]



def _token_suggestions(usage: dict[str, ToolTokenUsage]) -> list[str]:
    """Generate token optimization suggestions based on tool usage patterns."""
    suggestions = []
    items = sorted(usage.values(), key=lambda u: u.total, reverse=True)
    
    if not items:
        return suggestions
    
    # Find dominant tools (>30% of total tokens)
    total_all = sum(u.total for u in items)
    for u in items[:5]:
        if u.total / total_all > 0.30:
            suggestions.append(
                f"{u.tool_name} dominates token usage ({u.total / total_all * 100:.1f}%) — "
                f"consider caching results or reducing call frequency"
            )
    
    # Find high average tools (>100K tokens/call)
    for u in items:
        if u.avg_total > 100_000:
            suggestions.append(
                f"{u.tool_name} averages {u.avg_total:,.0f} tokens/call — "
                f"investigate if full output is always needed"
            )
    
    # Find tools with high output ratio (>50% output tokens)
    for u in items[:10]:
        if u.total > 0 and u.output_tokens / u.total > 0.5:
            suggestions.append(
                f"{u.tool_name} is output-heavy ({u.output_tokens / u.total * 100:.1f}%) — "
                f"consider truncating or summarizing responses"
            )
    
    # Find tools with many calls but low output (potential batching)
    for u in items[:15]:
        if u.calls > 100 and u.output_tokens < u.input_tokens * 0.1:
            suggestions.append(
                f"{u.tool_name} has {u.calls} calls but low output ratio — "
                f"consider batching requests"
            )
    
    return suggestions[:10]

hooks_app = typer.Typer(
    name="hooks",
    help="v0.3.0 pre-action hooks — gate crontab / systemctl on fresh vitals stamp.",
    add_completion=False,
)
app.add_typer(hooks_app, name="hooks")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"agent-vitals {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """agent-vitals: make your AI agent less stupid."""
    if ctx.invoked_subcommand is None and not version:
        touch()
        records = scan_all()
        agents, cc = burn_all(days=7)
        console.rule("[bold cyan]agent-vitals[/bold cyan]")
        console.print()
        console.print(health_summary(records, cc, agents))
        console.print()
        console.print(
            "[dim]first time? run [bold]av install[/bold] for an interactive setup.[/dim]"
        )
        console.print(
            "[dim]commands: [bold]install[/bold] · [bold]doctor[/bold] · [bold]detect[/bold] · "
            "[bold]mcp[/bold] · [bold]shadow[/bold] · [bold]burnout[/bold] · "
            "[bold]drift[/bold] · [bold]cost[/bold] · [bold]sessions[/bold] · "
            "[bold]snapshot[/bold] · [bold]loops[/bold] · [bold]unused[/bold] · "
            "[bold]hooks[/bold] · --help[/dim]"
        )


# ---------- top-level commands ----------


@app.command()
def install(
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: install defaults."),
    only: str = typer.Option(
        None,
        "--only",
        help="Comma-separated components to install (e.g. 'mcp,hooks').",
    ),
    hosts: str = typer.Option(
        None,
        "--hosts",
        help="Comma-separated host names to install for (default: all detected).",
    ),
) -> None:
    """Interactive installer — pick components, pick hosts, install."""
    only_list = [c.strip() for c in only.split(",")] if only else None
    hosts_list = [h.strip() for h in hosts.split(",")] if hosts else None
    result = run_install(yes=yes, only=only_list, hosts_filter=hosts_list)
    if "no_hosts_detected" in (result.notes or []):
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """One-shot health check with actionable recommendations."""
    touch()
    records = scan_all()
    agents, cc = burn_all(days=7)
    render_doctor(records, cc, agents, console)


@app.command()
def detect() -> None:
    """List installed agent hosts we know how to wire up."""
    hosts = list_hosts()
    if not hosts:
        console.print("[yellow]No supported agent hosts detected.[/yellow]")
        raise typer.Exit(0)
    from rich.table import Table
    table = Table(title="detected agent hosts", header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("mcp config")
    for h in hosts:
        table.add_row(h.name, str(h.config_path))
    console.print(table)


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport). Other agents call this."""
    from agent_vitals.mcp_server import main as run_mcp_server

    run_mcp_server()


# ---------- shadow / burnout ----------


@app.command()
def shadow(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh every 2s."),
    interval: float = typer.Option(2.0, "--interval", help="Watch interval in seconds."),
) -> None:
    """Show everything scheduled or configured to act on your behalf.

    Call this before infra changes, before recommending MCP installs, or
    whenever you need to know what's running on the user's machine.
    """
    if watch:
        watch_shadow(console, interval=interval)
        return
    touch()
    records = scan_all()
    render_shadow(records, console, as_json=json_output)


@app.command()
def burnout(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh every 30s."),
    interval: float = typer.Option(30.0, "--interval", help="Watch interval in seconds."),
) -> None:
    """Show agent task completion / abandonment metrics for the last N days.

    Call this after long tasks, to compare your run against baseline, or
    when you suspect the agent is degrading.
    """
    if watch:
        watch_burnout(console, days=days, interval=interval)
        return
    touch()
    agents, cc = burn_all(days=days)
    render_burnout(agents, cc, days, console)


# ---------- drift / cost / sessions / snapshot (v0.4.0) ----------


@app.command()
def drift() -> None:
    """Find inconsistencies across detected agent hosts (MCP drift, skills, hooks).

    Call this when the user asks about cross-tool config consistency, or
    before recommending config changes across multiple hosts.
    """
    findings = detect_all_drift()
    console.print(render_drift_report(findings).rstrip())


@app.command()
def cost() -> None:
    """Token spend tracker — parse session JSONLs, group by project, estimate cost.

    Uses the actual model observed in each session for pricing when available,
    falling back to Sonnet-class defaults for unknown models. Includes model
    downgrade suggestions when high-tier models are used for low-output work.

    Call this for monthly budget review, or whenever the bill looks high.
    """
    by_host = scan_all_sessions()
    console.print(render_cost_report(by_host).rstrip())
    # Model downgrade suggestions
    suggestions = _model_downgrade_suggestions(by_host)
    if suggestions:
        console.print()
        console.print("[bold yellow]model downgrade suggestions[/bold yellow]")
        for s in suggestions:
            console.print(f"  {s}")


@app.command()
def tokens(
    limit: int = typer.Option(20, "--limit", "-n", help="Max tools to show."),
    suggest: bool = typer.Option(False, "--suggest", help="Show optimization suggestions."),
) -> None:
    """Token-heavy tool identification — which tools burn the most tokens.

    Parses session JSONLs and attributes token usage to each tool call.
    Shows total and average tokens per tool, so you can spot expensive
    tools that might benefit from caching, batching, or replacement.

    Call this after a cost spike, or when you want to optimize token usage.
    """
    usage = scan_tool_tokens()
    console.print(render_tokens_report(usage, limit=limit).rstrip())
    if suggest:
        console.print()
        console.print("[bold yellow]token optimization suggestions[/bold yellow]")
        sugg = _token_suggestions(usage)
        for s in sugg:
            console.print(f"  {s}")


@app.command()
def loops(
    limit: int = typer.Option(20, "--limit", "-n", help="Max findings to show."),
) -> None:
    """Detect doom-loop patterns in agent sessions (repeated identical tool calls, file edits).

    A doom loop is the same exact operation repeated many times with no
    progress. We detect two flavors:

    1. Exact repetition: the same Bash command string run 20+ times, or
       the same Edit (same old_string + new_string) applied 10+ times.
    2. Soft loops: the same command *structure* (literals replaced by
       placeholders) repeated 20+ times. Catches loops where the agent
       varies paths, flags, or arguments but keeps the same pattern.

    Polling commands (ps, pgrep, pidof) are excluded — waiting for a
    process to exit is legitimate, not a loop. File edits are compared by
    content, not just count, so progressive changes to the same file are
    not flagged.

    Call this after a session is "taking forever" or cost spikes.
    """
    findings = find_loops()
    console.print(render_loop_report(findings, limit=limit).rstrip())


@app.command()
def ssh(
    limit: int = typer.Option(20, "--limit", "-n", help="Max findings to show."),
) -> None:
    """Detect SSH polling loops — repeated SSH commands to the same host.

    SSH polling is a common doom loop pattern where an agent repeatedly
    SSHes into a remote host waiting for some state change. This detector
    identifies such patterns and suggests fixes like adding timeouts,
    backoff, or using proper monitoring.

    Call this after a session with heavy SSH usage, or when token bill is
    unexplained.
    """
    findings = find_loops()
    ssh_findings = [f for f in findings if f.kind == "ssh_poll"]
    if not ssh_findings:
        console.print("ssh: no SSH polling loops detected\n")
        return
    lines = [f"ssh: {len(ssh_findings)} SSH polling loop(s) flagged", ""]
    lines.append(f"  {'host':<13} {'count':>6}  {'target':<50}  detail")
    lines.append(f"  {'-'*13} {'-'*6}  {'-'*50}  ------")
    for f in ssh_findings[:limit]:
        target = f.target if len(f.target) <= 50 else f.target[:47] + "..."
        lines.append(f"  {f.host:<13} {f.count:>6}  {target:<50}  {f.detail}")
    if len(ssh_findings) > limit:
        lines.append(f"  ... and {len(ssh_findings) - limit} more")
    console.print("\n".join(lines) + "\n")


@app.command()
def unused() -> None:
    """Find MCP tools registered but never called.

    Reports at two granularities:
    - Unused servers: MCP servers with zero observed tool calls (~5KB
      waste per turn for the server manifest).
    - Used tools: individual tools within active servers, with call counts.

    Every registered tool is part of every agent turn's context (function
    name + JSON schema). On average, unused tools cost 5-10KB per turn of
    pure context overhead. Removing them is a free speedup.

    Call this after installing a new MCP server, or weekly.
    """
    findings = find_unused_tools()
    console.print(render_unused_report(findings).rstrip())


@app.command()
def overlap() -> None:
    """Detect overlapping MCP tools across servers.

    Finds MCP servers that share tool names or have similar tool names,
    suggesting possible redundancy. Removing overlapping tools reduces
    per-turn context weight.

    Call this after installing new MCP servers, or when context feels bloated.
    """
    overlaps = find_overlapping_tools()
    console.print(render_overlap_report(overlaps).rstrip())


@app.command()
def coach(
    session_path: Path = typer.Option(None, "--session", help="Specific session JSONL to analyze."),
    model_tier: str = typer.Option("small", "--model", help="Model tier: small/medium/large."),
    format: str = typer.Option("text", "--format", help="Output format: text or json."),
    harness: bool = typer.Option(False, "--harness", help="Output a complete system prompt instead of session analysis."),
) -> None:
    """Generate optimized system prompts for small models.

    Analyzes your actual session data to extract opus-level operational
    patterns: proven tool sequences, failure recoveries, context efficiency
    rules, and tool selection discipline. This is how you make a small model
    perform like Claude Code / Opus / Fable — not by changing the model,
    but by giving it a better playbook derived from your own successful runs.

    Based on reverse-engineering of Claude Code harness, Opus 4.x, and
    Fable 5 system prompts. The gap between small and large models is NOT
    reasoning — it is context quality, tool selection, and prompt structure.

    Call this when you want to make your current model smarter without
    changing it, or when the user asks for a system prompt optimization.
    """
    
    if harness:
        # Output complete harness prompt
        prompt = coach_mod.generate_harness_prompt(model_tier=model_tier)
        console.print(prompt)
        return
    
    if session_path:
        sessions = [session_path]
    else:
        sessions = discover_sessions()
    
    if not sessions:
        console.print("coach: no sessions found\n")
        return
    
    # Find a session with tool calls
    target = None
    for s in sessions:
        calls = coach_mod._extract_tool_calls_from_session(s.path)
        if calls:
            target = s.path
            break
    
    if target is None:
        console.print("coach: no sessions with tool calls found\n")
        return
    
    report = coach_mod.analyze_session(target, model_tier=model_tier)
    
    if format == "json":
        output = coach_mod.render_coaching_report(report, format="json")
        console.print_json(output)
    else:
        output = coach_mod.render_coaching_report(report, format="text")
        console.print(output)


@app.command()
def sessions(
    older_than: float = typer.Option(None, "--older-than", help="Filter: age in days."),
    larger_than: int = typer.Option(
        None, "--larger-than", help="Filter: size in bytes."
    ),
    host: str = typer.Option(None, "--host", help="Filter by host name."),
    sort: str = typer.Option("mtime", "--sort", help="Sort by 'mtime' or 'size'."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows to show."),
    suggest: bool = typer.Option(False, "--suggest", help="Show compaction suggestions."),
) -> None:
    """List agent session files (Claude Code, pi, ...) with age + size.

    Call this when the user asks about old session files, disk usage,
    or wants to audit session history.
    """
    found = discover_sessions()
    found = filter_sessions(
        found,
        older_than_days=older_than,
        larger_than_bytes=larger_than,
        host=host,
    )
    console.print(render_sessions_table(found, limit=limit, sort_by=sort).rstrip())
    if suggest:
        console.print()
        console.print("[bold yellow]compaction suggestions[/bold yellow]")
        sugg = _compaction_suggestions(found)
        for s in sugg:
            console.print(f"  {s}")


@app.command()
def compact(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview changes only."),
    older_than: float = typer.Option(None, "--older-than", help="Only compact sessions older than N days."),
    larger_than: int = typer.Option(10 * 1024 * 1024, "--larger-than", help="Only compact sessions larger than N bytes (default 10MB)."),
    keep_last: int = typer.Option(1000, "--keep-last", help="Keep the last N events in each session."),
) -> None:
    """Compact large session files by archiving old events.

    Creates a backup .jsonl.bak file, then keeps only the most recent
    events up to --keep-last. This reduces context bloat and speeds up
    future scans.
    """

    import shutil

    found = discover_sessions()
    if older_than is not None:
        found = [s for s in found if s.age_days >= older_than]
    found = [s for s in found if s.size_bytes >= larger_than]

    if not found:
        console.print("compact: no sessions match compaction criteria\n")
        return

    console.print(f"compact: {len(found)} session(s) eligible for compaction\n")

    total_savings = 0
    for s in found:
        backup = s.path.with_suffix(".jsonl.bak")
        if backup.exists():
            console.print(f"  [dim]skip[/dim] {s.path.name}: backup already exists (.jsonl.bak)")
            continue

        # Read all events
        events = []
        try:
            with s.path.open("rb") as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            continue

        if len(events) <= keep_last:
            console.print(f"  [dim]skip[/dim] {s.path.name}: {len(events)} events <= keep_last ({keep_last})")
            continue

        # Keep last N events
        kept = events[-keep_last:]
        removed = len(events) - len(kept)
        original_size = s.size_bytes

        if not dry_run:
            try:
                shutil.copy2(s.path, backup)
                # Write compacted version
                with s.path.open("w") as f:
                    for ev in kept:
                        f.write(json.dumps(ev) + "\n")
                new_size = s.path.stat().st_size
                saved = original_size - new_size
                total_savings += saved
                console.print(f"  [green]✓[/green] {s.path.name}: {removed} events removed, {saved / 1024:.1f}K saved")
            except OSError as e:
                console.print(f"  [red]✗[/red] {s.path.name}: {e}")
        else:
            # Estimate savings for dry run
            avg_event_size = original_size / len(events) if len(events) > 0 else 0
            estimated_saved = avg_event_size * removed
            total_savings += estimated_saved
            console.print(f"  [yellow]~[/yellow] {s.path.name}: {removed} events would be removed, ~{estimated_saved / 1024:.1f}K saved")

    if dry_run:
        console.print(f"\n[dim]Dry run: ~{total_savings / 1024:.1f}K total savings across {len(found)} files. Use --no-dry-run to apply.[/dim]")
    else:
        console.print(f"\n[green]✓[/green] Total saved: {total_savings / 1024:.1f}K across {len(found)} files")
        console.print("  Backups saved as .jsonl.bak — delete when satisfied")


@app.command()
def snapshot(
    label: str = typer.Option(None, "--label", help="Optional label to append to the archive name."),
    list_only: bool = typer.Option(False, "--list", help="Just list existing snapshots."),
) -> None:
    """Create a tar.gz of agent state (mcp configs, skills, hooks)."""
    if list_only:
        snaps = snap_mod.list_snapshots()
        console.print(snap_mod.render_snapshot_list(snaps).rstrip())
        return
    try:
        snap = snap_mod.create_snapshot(label=label)
    except RuntimeError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(1)
    console.print(
        f"[green]✓[/green] snapshot created: {snap.path} "
        f"({snap.size_bytes // 1024}K, {snap.num_files} files)"
    )
    console.print(f"  restore: tar -xzf {snap.path} -C /tmp/restore  # then copy files back manually")


# ---------- trace (v0.7.0) ----------


trace_app = typer.Typer(
    name="trace",
    help="Recorded trace replay + divergence diff for agent sessions.",
    no_args_is_help=True,
)
app.add_typer(trace_app, name="trace")


@trace_app.command("list")
def trace_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows to show."),
    source: str | None = typer.Option(None, "--source", "-s", help="Filter by source (claude|pi)."),
) -> None:
    """List discoverable sessions with source type and line counts."""
    rows = trace_mod.list_sessions()
    if source:
        rows = [(p, s, c) for p, s, c in rows if s == source.lower()]
    if not rows:
        console.print(f"trace: no sessions found{' for source ' + source if source else ''}\n")
        return
    rows = rows[:limit]
    lines = ["  path                              source  lines", "  --------------------------------  ------  -----"]
    for path, src, count in rows:
        lines.append(f"  {path:<31}  {src:<6}  {count}")
    console.print("\n".join(lines) + "\n")


@trace_app.command("summary")
def trace_summary(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
) -> None:
    """One-shot trace summary: turns, tools, errors, wall duration."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    if not events:
        console.print("trace: no parseable events\n")
        return
    stats = trace_mod.summary(events)
    console.print(f"trace summary · {p.name}\n")
    console.print(f"  events : {stats['events']}")
    console.print(f"  turns  : {stats['turns']}")
    console.print(f"  tools  : {stats['tools']}")
    console.print(f"  results: {stats['results']}")
    console.print(f"  errors : {stats['errors']}")
    console.print(f"  wall   : {trace_mod._format_duration(stats['wall_ms'])}")
    if stats["tools"]:
        console.print(f"  avg tool: {trace_mod._format_duration(stats['avg_tool_ms'])}")


@trace_app.command("replay")
def trace_replay(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
    limit: int = typer.Option(200, "--limit", "-n", help="Max events to show."),
) -> None:
    """Sequential replay of a session trace (no payloads)."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    shown = events[:limit]
    console.print(trace_mod.replay(shown).rstrip())
    if len(events) > limit:
        console.print(f"\n[dim]... truncated to {limit} events[/dim]")


@trace_app.command("diff")
def trace_diff(
    session_a: str = typer.Argument(..., help="Path to first session JSONL."),
    session_b: str = typer.Argument(..., help="Path to second session JSONL."),
) -> None:
    """Structural diff between two session traces (no payloads)."""
    pa = Path(session_a)
    pb = Path(session_b)
    if not pa.exists():
        console.print(f"[red]trace: file not found: {session_a}[/red]")
        raise typer.Exit(1)
    if not pb.exists():
        console.print(f"[red]trace: file not found: {session_b}[/red]")
        raise typer.Exit(1)
    events_a = trace_mod.trace_events(pa)
    events_b = trace_mod.trace_events(pb)
    result = trace_mod.diff(events_a, events_b)
    console.print(result.rstrip())

@trace_app.command("errors")
def trace_errors(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
) -> None:
    """Show only error events from a session trace."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    errs = trace_mod.errors(events)
    if not errs:
        console.print("trace: no errors found\n")
        return
    console.print(f"trace errors · {p.name} · {len(errs)} errors\n")
    console.print(trace_mod.replay(errs).rstrip())

@trace_app.command("profile")
def trace_profile(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
) -> None:
    """Per-tool breakdown: call count, error rate, avg duration."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    prof = trace_mod.profile(events)
    tools = prof.get("tools", [])
    if not tools:
        console.print("trace: no tool calls found\n")
        return
    console.print(f"trace profile · {p.name} · {len(tools)} tools\n")
    header = f"  {'tool':<20} {'calls':>6} {'errors':>7} {'err%':>6} {'avg':>8}"
    console.print(header)
    console.print("  " + "-" * (len(header.strip())))
    for row in tools:
        err_pct = f"{row['error_rate'] * 100:.0f}%"
        avg = trace_mod._format_duration(row["avg_ms"])
        console.print(
            f"  {row['tool']:<20} {row['calls']:>6} {row['errors']:>7} {err_pct:>6} {avg:>8}"
        )

@trace_app.command("suggest")
def trace_suggest(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
) -> None:
    """Actionable suggestions based on session trace data."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    suggestions = trace_mod.suggest(events)
    console.print(f"trace suggestions · {p.name}\n")
    for i, s in enumerate(suggestions, 1):
        console.print(f"  {i}. {s}")
    console.print()


@trace_app.command("grep")
def trace_grep(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
    pattern: str = typer.Argument(..., help="Substring to match (tool name or event type)."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max matches to show."),
) -> None:
    """Filter events by tool name or event type (case-insensitive)."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    matches = trace_mod.grep(events, pattern)[:limit]
    if not matches:
        console.print(f"trace: no matches for '{pattern}'\n")
        return
    console.print(trace_mod.replay(matches).rstrip())
    if len(trace_mod.grep(events, pattern)) > limit:
        console.print(f"\n[dim]... truncated to {limit} matches[/dim]")


@trace_app.command("export")
def trace_export(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
    output: str = typer.Option("trace.json", "--output", "-o", help="Output JSON path."),
) -> None:
    """Export normalized trace events to JSON."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    events = trace_mod.trace_events(p)
    out = Path(output)
    trace_mod.export_json(events, out)
    console.print(f"trace: exported {len(events)} events to {out}\n")


@trace_app.command("watch")
def trace_watch(
    session: str = typer.Argument(..., help="Path to a session JSONL file."),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Poll interval in seconds."),
) -> None:
    """Tail a session JSONL and print new events as they arrive."""
    p = Path(session)
    if not p.exists():
        console.print(f"[red]trace: file not found: {session}[/red]")
        raise typer.Exit(1)
    console.print(f"trace: watching {p} (Ctrl-C to stop)\n")
    try:
        for ev in trace_mod.watch(p, poll_interval=interval):
            marker = "✗" if ev.error else "·"
            tool = f" [{ev.tool_name}]" if ev.tool_name else ""
            line = f"  {marker} {ev.event_type}{tool}"
            console.print(line)
    except KeyboardInterrupt:
        console.print("\n[dim]trace: watch stopped[/dim]")
        raise typer.Exit(0)


# ---------- v0.3.0 hooks ----------



@hooks_app.command("install")
def hooks_install(
    auto_rc: bool = typer.Option(True, "--rc/--no-rc", help="Auto-append PATH snippet to shell rc files."),
) -> None:
    """Install pre-action hook wrappers for crontab and systemctl."""
    results = hooks_mod.install_wrappers()
    console.print("[bold]agent-vitals hooks — install[/bold]\n")
    any_installed = False
    for name, status in results.items():
        marker = "[green]\u2713[/green]" if status in ("installed", "reactivated") else "[dim]\u2013[/dim]"
        console.print(f"  {marker} {name:<10} {status}")
        if status in ("installed", "reactivated"):
            any_installed = True
    if not any_installed:
        console.print("\n[dim]nothing new to install (already wired up).[/dim]")
    console.print(f"\n  installed at: {hooks_mod.hook_dir()}")
    if auto_rc:
        ok, msg = hooks_mod.auto_add_to_shell_rc(force=False)
        if ok:
            console.print("[green]\u2713 shell rc updated.[/green] open a new terminal to apply.")
        else:
            console.print(f"[dim]{msg}[/dim]")
    console.print("\n[dim]snippet to add to your shell rc manually:[/dim]")
    console.print(hooks_mod.shell_path_snippet().rstrip())


@hooks_app.command("uninstall")
def hooks_uninstall() -> None:
    """Remove pre-action hook wrappers."""
    removed = hooks_mod.uninstall_wrappers()
    if not removed:
        console.print("[dim]no hooks were installed.[/dim]")
    else:
        console.print(f"[green]\u2713 removed:[/green] {', '.join(removed)}")
        console.print("\n[dim]consider also removing the PATH snippet from your shell rc[/dim]")


@hooks_app.command("status")
def hooks_status() -> None:
    """Show freshness stamp + wrapper install state."""
    console.print(hooks_mod.status_report().rstrip())


@hooks_app.command("enable")
def hooks_enable() -> None:
    """Re-activate previously disabled wrappers."""
    changed = hooks_mod.set_wrappers_state(enable=True)
    if not changed:
        console.print("[dim]no disabled wrappers to re-enable.[/dim]")
    else:
        console.print(f"[green]\u2713 re-enabled:[/green] {', '.join(changed)}")


@hooks_app.command("disable")
def hooks_disable() -> None:
    """Temporarily disable hook wrappers (without removing)."""
    changed = hooks_mod.set_wrappers_state(enable=False)
    if not changed:
        console.print("[dim]no active wrappers to disable.[/dim]")
    else:
        console.print(f"[yellow]\u2713 disabled:[/yellow] {', '.join(changed)}")


@hooks_app.command(
    "gate",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    hidden=True,
)
def hooks_gate(ctx: typer.Context, binary: str) -> None:
    """Internal: invoked by wrapper scripts installed at ~/.local/bin/av-hooks/."""
    rc = hooks_mod.gate(binary, list(ctx.args))
    raise typer.Exit(rc)


# ---------- deprecated: kept for backward compat ----------


@app.command(name="init", hidden=True)
def init_deprecated() -> None:
    """Deprecated: use `av install` instead. Kept for backward compat."""
    console.print("[yellow]`av init` is deprecated. Run `av install` instead.[/yellow]")
    console.print("[dim]  `av install` is the new interactive installer.[/dim]")


if __name__ == "__main__":
    app()
