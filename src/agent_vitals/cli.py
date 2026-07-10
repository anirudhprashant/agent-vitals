"""Typer CLI entry point.

CLI is the verification + install surface. The real product is the MCP server
that other agents call. See `av mcp` to start it, `av init` to wire it into
your agent hosts, `av hooks install` for v0.3.0 pre-action gates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from agent_vitals import __version__
from agent_vitals import hooks as hooks_mod
from agent_vitals import stamp as stamp_mod
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
from agent_vitals.stamp import describe_age, read_age, touch


app = typer.Typer(
    name="agent-vitals",
    help=(
        "agent-vitals: solo-dev observability for AI agent stacks. "
        "Run `av init` to wire the MCP server into your agent hosts. "
        "Run `av mcp` to start the server. Run `av doctor` for a health check. "
        "Run `av hooks install` for v0.3.0 pre-action gates."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
console = Console()


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
            "[dim]first time? run [bold]av init[/bold] to wire the MCP server into your agents.[/dim]"
        )
        console.print(
            "[dim]v0.3.0? run [bold]av hooks install[/bold] to gate crontab / systemctl.[/dim]"
        )
        console.print(
            "[dim]commands: [bold]init[/bold] · [bold]doctor[/bold] · [bold]detect[/bold] · "
            "[bold]mcp[/bold] · [bold]shadow[/bold] · [bold]burnout[/bold] · "
            "[bold]hooks[/bold] · --help[/dim]"
        )


@app.command()
def init(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without changing anything."),
) -> None:
    """Detect installed agent hosts and install MCP + priming everywhere."""
    hosts = list_hosts()
    if not hosts:
        console.print(
            "[yellow]No supported agent hosts detected.[/yellow]\n"
            "I look for: pi (~/.pi/agent/mcp.json), Claude Code "
            "(~/.claude/.mcp.json), Cursor (~/.cursor/mcp.json), "
            "OpenCode (~/.config/opencode/mcp.json), Codex CLI "
            "(~/.codex/config.toml)."
        )
        raise typer.Exit(1)
    from rich.table import Table

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
    from rich.table import Table as T2
    out = T2(header_style="bold cyan")
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
    from rich.table import Table
    table = Table(title="detected agent hosts", header_style="bold cyan")
    table.add_column("host", style="bold")
    table.add_column("mcp config")
    for h in hosts:
        table.add_row(h.name, str(h.config_path))
    console.print(table)


@app.command()
def doctor() -> None:
    """One-shot health check with actionable recommendations."""
    touch()
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
    touch()
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
    touch()
    agents, cc = burn_all(days=days)
    render_burnout(agents, cc, days, console)


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport). Other agents call this."""
    from agent_vitals.mcp_server import main as run_mcp_server

    run_mcp_server()


# ---------- v0.3.0 hooks ----------


@hooks_app.command("install")
def hooks_install(
    bin_names: str = typer.Option(
        "crontab,systemctl",
        "--only",
        help="Comma-separated list of binaries to gate. Default: crontab,systemctl.",
    ),
    auto_rc: bool = typer.Option(
        True,
        "--rc/--no-rc",
        help="Auto-append the PATH snippet to ~/.bashrc and ~/.zshrc if present.",
    ),
) -> None:
    """Install pre-action hook wrappers for crontab and systemctl."""
    bins = tuple(b.strip() for b in bin_names.split(",") if b.strip())
    results = hooks_mod.install_wrappers(bins)
    console.print("[bold]agent-vitals hooks — install[/bold]")
    console.print()
    any_installed = False
    for name, status in results.items():
        if status in ("installed", "reactivated"):
            any_installed = True
            console.print(f"  [green]✓[/green] {name:<10} {status}")
        elif status == "already_active":
            console.print(f"  [dim]–[/dim] {name:<10} {status}")
        elif status == "template_missing":
            console.print(f"  [red]✗[/red] {name:<10} {status} (template file missing in package)")
    console.print()
    console.print(f"  installed at: {hooks_mod.hook_dir()}")
    console.print()
    console.print("[bold]next:[/bold] prepend this to PATH so the wrappers shadow the real binaries.")
    console.print("  (open a NEW terminal after editing your shell config)")
    console.print()
    snippet = hooks_mod.shell_path_snippet()
    console.print(snippet.rstrip())

    if auto_rc:
        ok, msg = hooks_mod.auto_add_to_shell_rc(force=False)
        if ok:
            console.print("[green]✓ auto-added to:[/green]")
            console.print(msg)
            console.print("\n[dim]open a new terminal to load the new PATH[/dim]")
        else:
            console.print(f"[dim]skipped rc auto-edit: {msg}[/dim]")
            console.print("[dim]add the snippet above to ~/.bashrc or ~/.zshrc manually if you want[/dim]")

    console.print()
    age = read_age()
    if age is None:
        console.print("[yellow]note:[/yellow] no vitals call on record yet.")
        console.print("  hooks will refuse mutations until an agent or `av doctor` refreshes the stamp.")
    else:
        console.print(f"[green]stamp:[/green] {describe_age(age)} old — hooks are armed.")


@hooks_app.command("uninstall")
def hooks_uninstall() -> None:
    """Remove pre-action hook wrappers."""
    removed = hooks_mod.uninstall_wrappers()
    if not removed:
        console.print("[dim]no hooks were installed.[/dim]")
    else:
        console.print(f"[green]✓ removed:[/green] {', '.join(removed)}")
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
        console.print(f"[green]✓ re-enabled:[/green] {', '.join(changed)}")


@hooks_app.command("disable")
def hooks_disable() -> None:
    """Temporarily disable hook wrappers (without removing)."""
    changed = hooks_mod.set_wrappers_state(enable=False)
    if not changed:
        console.print("[dim]no active wrappers to disable.[/dim]")
    else:
        console.print(f"[yellow]✓ disabled:[/yellow] {', '.join(changed)}  (wrappers bypass the gate while in .disabled form)")


@hooks_app.command(
    "gate",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    hidden=True,
)
def hooks_gate(ctx: typer.Context, binary: str) -> None:
    """Internal: invoked by wrapper scripts installed at ~/.local/bin/av-hooks/."""
    rc = hooks_mod.gate(binary, list(ctx.args))
    raise typer.Exit(rc)


if __name__ == "__main__":
    app()