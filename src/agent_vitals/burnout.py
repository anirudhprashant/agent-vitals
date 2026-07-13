"""Agent task completion / abandonment metrics.

Sources:
  - ~/.pi/agent/run-history.jsonl  (pi-subagent invocations, clean schema)
  - ~/.claude/projects/*/*.jsonl    (Claude Code session graph)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Skip records with very large string values (inline base64 images, etc.).
_MAX_RECORD_BYTES = 200_000

def _should_skip_record(line: str) -> bool:
    return len(line) > _MAX_RECORD_BYTES




# ---------- pi subagent history ----------


@dataclass
class AgentStats:
    agent: str
    runs: int = 0
    ok: int = 0
    failed: int = 0
    total_duration_s: float = 0.0
    max_duration_s: float = 0.0
    last_run_ts: float = 0.0
    recent_runs: list[dict] = field(default_factory=list)

    @property
    def avg_duration_s(self) -> float:
        return self.total_duration_s / self.runs if self.runs else 0.0

    @property
    def completion_rate(self) -> float:
        return self.ok / self.runs if self.runs else 0.0

    @property
    def trend(self) -> str:
        if len(self.recent_runs) < 4:
            return "—"
        half = len(self.recent_runs) // 2
        first = self.recent_runs[:half]
        second = self.recent_runs[half:]
        first_ok = sum(1 for r in first if r["status"] == "ok") / len(first)
        second_ok = sum(1 for r in second if r["status"] == "ok") / len(second)
        delta = second_ok - first_ok
        if delta > 0.1:
            return "↑ improving"
        if delta < -0.1:
            return "↓ degrading"
        return "→ stable"


def _normalize_duration_ms(value) -> float:
    """Heuristic: pi run-history records duration; could be ms or s depending on version."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return v / 1000.0 if v > 10_000 else v  # >10000 almost certainly ms


def scan_pi_run_history(days: int = 7) -> list[AgentStats]:
    """Parse ~/.pi/agent/run-history.jsonl."""
    path = Path.home() / ".pi/agent/run-history.jsonl"
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    by_agent: dict[str, AgentStats] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                if _should_skip_record(raw_line):
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = float(rec.get("ts", 0))
                if ts and ts < cutoff:
                    continue
                agent = str(rec.get("agent", "unknown"))
                stats = by_agent.setdefault(agent, AgentStats(agent=agent))
                stats.runs += 1
                status = str(rec.get("status", "unknown"))
                if status == "ok":
                    stats.ok += 1
                elif status in ("error", "failed"):
                    stats.failed += 1
                dur_s = _normalize_duration_ms(rec.get("duration"))
                stats.total_duration_s += dur_s
                stats.max_duration_s = max(stats.max_duration_s, dur_s)
                stats.last_run_ts = max(stats.last_run_ts, ts)
                stats.recent_runs.append({"status": status, "duration_s": dur_s, "ts": ts})
    except OSError:
        return []
    # Trim and sort recent_runs (keep last 20)
    for stats in by_agent.values():
        stats.recent_runs.sort(key=lambda r: r["ts"])
        stats.recent_runs = stats.recent_runs[-20:]
    return sorted(by_agent.values(), key=lambda s: -s.runs)


# ---------- Claude Code sessions ----------


def scan_claude_code_sessions(days: int = 7) -> dict:
    """High-level stats from Claude Code session JSONL files.

    Only counts records that represent actual agent activity: assistant,
    user, tool_use, and tool_result. Metadata lines (mode, permission-mode,
    system, hook_success, etc.) are ignored so the event count reflects
    real turns, not session plumbing.
    """
    projects_dir = Path.home() / ".claude/projects"
    if not projects_dir.exists():
        return {"sessions": 0, "events": 0, "largest_session": 0, "stuck_sessions": []}
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    session_count = 0
    event_count = 0
    largest = 0
    stuck_sessions: list[dict] = []
    _EVENT_TYPES = {"assistant", "user", "tool_use", "tool_result"}
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue
            session_count += 1
            lines = 0
            last_event_type: str | None = None
            try:
                with jsonl.open(encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(rec, dict):
                            continue
                        if rec.get("type") in _EVENT_TYPES:
                            lines += 1
                            last_event_type = rec.get("type")
            except OSError:
                continue
            event_count += lines
            if lines > largest:
                largest = lines
            # Heuristic: 200+ events = probably stuck in a loop
            if lines >= 200:
                stuck_sessions.append(
                    {
                        "session": jsonl.stem,
                        "project": project_dir.name,
                        "events": lines,
                        "last_type": last_event_type,
                    }
                )
    stuck_sessions.sort(key=lambda s: -s["events"])
    return {
        "sessions": session_count,
        "events": event_count,
        "largest_session": largest,
        "stuck_sessions": stuck_sessions,
    }


# ---------- union ----------


def burn_all(days: int = 7) -> tuple[list[AgentStats], dict]:
    agents = scan_pi_run_history(days)
    cc = scan_claude_code_sessions(days)
    return agents, cc