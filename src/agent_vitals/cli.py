"""Typer CLI entry point.

CLI is the verification + install surface. The real product is the MCP server
that other agents call. See `av mcp` to start it, `av init` to wire it into
your agent hosts.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent_vitals import __version__
from agent_vitals.burnout import burn_all
from agent_vitals.primer import init_all, list_hosts
from agent_vitals.render import (
    health_summary,
    render_burnout,
    render_doctor,
    render_shadow,
    watch_burnout,
    watch_shadow,
)
from agent_vitals.scanners import scan_all


app = typer.Typer(
    name="agent-vitals",
    help=(
        "agent-vitals: solo-dev observability for AI agent stacks. "
        "Run `av init` to wire the MCP server into your agent hosts. "
        "Run `av mcp` to start the server. Run `av doctor` for a health check."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
console = Console()


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
        records = scan_all()
        agents, cc = burn_all(days=7)
        console.rule("[bold cyan]agent-vitals[/bold cyan]")
        console.print()
        console.print(health_summary(records, cc, agents))
        console.print()
        console.print(
            "[dim]first time? run [bold]av init[/bold] to wire the MCP server into your agents.[/dim]"
        )
        console.print(
            "[dim]commands: [bold]init[/bold] · [bold]doctor[/bold] · [bold]detect[/bold] · "
            "[bold]mcp[/bold] · [bold]shadow[/bold] · [bold]burnout[/bold] · --help[/dim]"
        )


@app.command()
def init(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without changing anything."),
) -> None:
    """Detect installed agent hosts and install MCP + priming everywhere."""
    hosts = list_hosts()
    if not hosts:
        console.print(
            Panel(
                "[yellow]No supported agent hosts detected.[/yellow]\n\n"
                "I look for: pi (~/.pi/agent/mcp.json), Claude Code "
                "(~/.claude/.mcp.json), Cursor (~/.cursor/mcp.json), "
                "OpenCode (~/.config/opencode/mcp.json), Codex CLI "
                "(~/.codex/config.toml).\n\n"
                "Install one of those, then re-run [bold]av init[/bold].",
                title="nothing to wire up",
                border_style="yellow",
            )
        )
        raise typer.Exit(1)
    table = Table(title=f"detected {len(hosts)} agent host(s)", header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("config")
    table.add_column("status")
    for h in hosts:
        table.add_row(h.name, str(h.config_path), "[green]detected[/green]")
    console.print(table)
    console.print()
    if dry_run:
        console.print("[dim]--dry-run: not changing anything[/dim]")
        raise typer.Exit(0)
    results = init_all()
    console.print("[bold]installing:[/bold]")
    console.print()
    out = Table(header_style="bold cyan")
    out.add_column("host", style="bold")
    out.add_column("mcp config")
    out.add_column("skill/rule")
    for host, actions in results:
        out.add_row(
            host.name,
            actions.get("mcp", "[dim]—[/dim]"),
            actions.get("skill") or actions.get("rule") or "[dim]—[/dim]",
        )
    console.print(out)
    console.print()
    console.print(
        "[green]✓ done.[/green] Restart your agent host so it picks up the new MCP server. "
        "[dim]Run [bold]av doctor[/bold] to verify.[/dim]"
    )


@app.command()
def detect() -> None:
    """List installed agent hosts we know how to wire up."""
    hosts = list_hosts()
    if not hosts:
        console.print("[yellow]No supported agent hosts detected.[/yellow]")
        raise typer.Exit(0)
    table = Table(title="detected agent hosts", header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("mcp config")
    for h in hosts:
        table.add_row(h.name, str(h.config_path))
    console.print(table)


@app.command()
def doctor() -> None:
    """One-shot health check with actionable recommendations."""
    records = scan_all()
    agents, cc = burn_all(days=7)
    render_doctor(records, cc, agents, console)


@app.command()
def shadow(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh every 2s."),
    interval: float = typer.Option(2.0, "--interval", help="Watch interval in seconds."),
) -> None:
    """List everything scheduled or configured to act on your behalf."""
    if watch:
        watch_shadow(console, interval=interval)
        return
    records = scan_all()
    render_shadow(records, console, as_json=json_output)


@app.command()
def burnout(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh every 30s."),
    interval: float = typer.Option(30.0, "--interval", help="Watch interval in seconds."),
) -> None:
    """Show agent task completion / abandonment metrics."""
    if watch:
        watch_burnout(console, days=days, interval=interval)
        return
    agents, cc = burn_all(days=days)
    render_burnout(agents, cc, days, console)


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport). Other agents call this."""
    from agent_vitals.mcp_server import main as run_mcp_server

    run_mcp_server()


if __name__ == "__main__":
    app()