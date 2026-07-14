"""Old session management for AI agent harnesses.

Agent sessions accumulate on disk forever:
  - ~/.claude/projects/*/UUID.jsonl           (Claude Code)
  - ~/.pi/agent/sessions/...                  (pi)

After a few months you have tens of thousands of files, gigabytes of disk.
This module surfaces what's there and helps the user prune.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Candidate session roots. First one that exists wins per session.
SESSION_ROOTS: list[tuple[str, Path]] = [
    ("claude-code", Path.home() / ".claude" / "projects"),
    ("pi", Path.home() / ".pi" / "agent" / "sessions"),
    ("opencode", Path.home() / ".local" / "share" / "opencode" / "storage"),
    ("codex", Path.home() / ".codex" / "sessions"),
]


@dataclass
class SessionInfo:
    host: str
    path: Path
    project: str | None
    size_bytes: int
    mtime: float         # epoch seconds
    event_count: int | None
    first_event_ts: float | None
    last_event_ts: float | None

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.mtime) / 86400.0)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "path": str(self.path),
            "project": self.project,
            "size_bytes": self.size_bytes,
            "age_days": round(self.age_days, 1),
            "event_count": self.event_count,
        }


def _scan_jsonl(path: Path) -> tuple[int | None, float | None, float | None]:
    """Light scan: return (event_count, first_ts, last_ts) without full parse."""
    count = 0
    first_ts: float | None = None
    last_ts: float | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                count += 1
                if count > 5000:
                    # Don't keep counting past a reasonable cap.
                    break
                try:
                    rec = json.loads(line)
                    ts = rec.get("timestamp") or rec.get("ts")
                    if isinstance(ts, (int, float)):
                        ts_f = float(ts)
                        if first_ts is None:
                            first_ts = ts_f
                        last_ts = ts_f
                except (json.JSONDecodeError, ValueError):
                    pass
    except OSError:
        return None, None, None
    return count, first_ts, last_ts


def discover_sessions() -> list[SessionInfo]:
    """Walk known session roots and return SessionInfo for every .jsonl."""
    out: list[SessionInfo] = []
    for host, root in SESSION_ROOTS:
        if not root.is_dir():
            continue
        for jsonl in root.rglob("*.jsonl"):
            try:
                stat = jsonl.stat()
            except OSError:
                continue
            project = jsonl.parent.name if jsonl.parent != root else None
            count, first_ts, last_ts = _scan_jsonl(jsonl)
            out.append(SessionInfo(
                host=host,
                path=jsonl,
                project=project,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                event_count=count,
                first_event_ts=first_ts,
                last_event_ts=last_ts,
            ))
    return out


def filter_sessions(
    sessions: list[SessionInfo],
    *,
    older_than_days: float | None = None,
    larger_than_bytes: int | None = None,
    host: str | None = None,
) -> list[SessionInfo]:
    """Apply common filters used by `av sessions prune --older-than N`."""
    out = sessions
    if older_than_days is not None:
        out = [s for s in out if s.age_days >= older_than_days]
    if larger_than_bytes is not None:
        out = [s for s in out if s.size_bytes >= larger_than_bytes]
    if host is not None:
        out = [s for s in out if s.host == host]
    return out


def total_size(sessions: Iterable[SessionInfo]) -> int:
    return sum(s.size_bytes for s in sessions)


def render_sessions_table(
    sessions: list[SessionInfo],
    *,
    limit: int = 20,
    sort_by: str = "mtime",  # one of: "mtime" | "size"
) -> str:
    """Plain-text table used by `av sessions list`."""
    if not sessions:
        return "sessions: none found in known agent roots\n"
    items = list(sessions)
    if sort_by == "size":
        items.sort(key=lambda s: s.size_bytes, reverse=True)
    else:
        items.sort(key=lambda s: s.mtime, reverse=True)
    total = total_size(items)
    lines = [f"sessions: {len(items)} file(s), {total / (1024 * 1024):.1f} MiB total"]
    lines.append(f"  {'host':<13} {'age':>6}  {'size':>10}  {'events':>7}  project")
    lines.append(f"  {'-'*13} {'-'*6}  {'-'*10}  {'-'*7}  -------")
    for s in items[:limit]:
        evs = str(s.event_count) if s.event_count is not None else "?"
        proj = s.project or "-"
        lines.append(
            f"  {s.host:<13} {s.age_days:>5.1f}d {s.size_bytes/1024:>8.1f}K  {evs:>7}  {proj}"
        )
    if len(items) > limit:
        lines.append(f"  ... and {len(items) - limit} more")
    return "\n".join(lines) + "\n"


__all__ = [
    "SessionInfo",
    "SESSION_ROOTS",
    "discover_sessions",
    "filter_sessions",
    "total_size",
    "render_sessions_table",
]
