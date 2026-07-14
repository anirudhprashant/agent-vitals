"""Stamp: the freshness signal that powers v0.3.0 hooks.

Single-purpose module: any vitals call (MCP tool, CLI render, anything)
calls `stamp.touch()` to record the call timestamp. Hooks in PATH call
`stamp.age()` and `stamp.should_gate()` to decide whether a mutation is
allowed.

Default freshness window: 60s. Override with VITALS_GATE_WINDOW env var.
Bypass entirely with VITALS_BYPASS=1.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_WINDOW_SECONDS = 60
STAMP_DIR = Path.home() / ".cache" / "agent-vitals"
STAMP_PATH = STAMP_DIR / "last-vitals-call"


def stamp_path() -> Path:
    """Path to the stamp file. Override via VITALS_STAMP_PATH env var for tests."""
    override = os.environ.get("VITALS_STAMP_PATH")
    return Path(override) if override else STAMP_PATH


def window_seconds() -> int:
    """Freshness window. Override via VITALS_GATE_WINDOW env var."""
    try:
        return int(os.environ.get("VITALS_GATE_WINDOW", str(DEFAULT_WINDOW_SECONDS)))
    except ValueError:
        return DEFAULT_WINDOW_SECONDS


def bypass() -> bool:
    """Should all gates be bypassed? Set VITALS_BYPASS=1 in the environment."""
    return os.environ.get("VITALS_BYPASS", "").strip() in {"1", "true", "yes"}


def touch(path: Path | None = None) -> None:
    """Record that vitals was just called. Idempotent. Never raises."""
    p = path or stamp_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{int(time.time())}\n")
    except OSError:
        # Stamp failure must never break a vitals call — fail silent.
        pass


def read_age(path: Path | None = None) -> float | None:
    """Seconds since last vitals call. None if stamp missing or unreadable."""
    p = path or stamp_path()
    try:
        raw = p.read_text().strip()
        ts = int(raw.splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0.0, time.time() - ts)


def describe_age(age: float | None) -> str:
    """Human description of an age value."""
    if age is None:
        return "never"
    if age < 1:
        return "just now"
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age // 60)}m{int(age % 60)}s ago"
    return f"{int(age // 3600)}h{int((age % 3600) // 60)}m ago"


def should_gate(window: int | None = None) -> tuple[bool, str]:
    """Should the current mutation be refused?

    Returns (gated, reason). gated=True means the caller should REFUSE.
    gated=False means the caller may proceed.

    Always returns False if VITALS_BYPASS=1.
    """
    if bypass():
        return False, "bypassed via VITALS_BYPASS=1"
    age = read_age()
    win = window if window is not None else window_seconds()
    if age is None:
        return True, "no vitals call on record — run any vitals tool first (e.g. `av doctor`)"
    if age > win:
        return True, (
            f"vitals stamp is {describe_age(age)} old — exceeds {win}s window. "
            f"Run `av doctor` (or any vitals tool) and retry."
        )
    return False, f"vitals stamp fresh ({describe_age(age)})"