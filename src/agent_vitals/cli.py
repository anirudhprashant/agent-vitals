"""Typer CLI entry point."""

from __future__ import annotations

import typer
from rich.console import Console

from agent_vitals import __version__
from agent_vitals.burnout import burn_all
from agent_vitals.render import render_burnout, render_shadow
from agent_vitals.scanners import scan_all


app = typer.Typer(
    name="agent-vitals",
    help="Solo-dev observability for AI agent stacks.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"agent-vitals {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """agent-vitals: what's running on your behalf, and how well it's running."""


@app.command()
def shadow(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List everything scheduled or configured to act on your behalf."""
    records = scan_all()
    render_shadow(records, console, as_json=json_output)


@app.command()
def burnout(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days."),
) -> None:
    """Show agent task completion / abandonment metrics."""
    agents, cc = burn_all(days=days)
    render_burnout(agents, cc, days, console)


if __name__ == "__main__":
    app()