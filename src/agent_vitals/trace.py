"""Trace module: normalize agent session JSONLs into replayable + diffable traces.

v1 is read-only and content-agnostic:
  - parses Claude Code and pi session JSONLs
  - emits normalized TraceEvent records (no prompt/tool payloads)
  - replays sessions as structured timelines
  - diffs two sessions to find the first divergence point

No new capture hooks. No instrumentation. Works on existing on-disk logs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator
from agent_vitals.sessions import discover_sessions

# ---------- normalized model ----------


@dataclass
class TraceEvent:
    source: str  # "claude" | "pi"
    event_type: str  # "user" | "assistant" | "tool_use" | "tool_result" | "system"
    timestamp: float
    parent_uuid: str | None
    tool_name: str | None
    tool_id: str | None
    duration_ms: float | None
    error: bool

    @property
    def key(self) -> str | None:
        """Stable identity for pairing tool_use ↔ tool_result."""
        if self.tool_id:
            return self.tool_id
        if self.tool_name:
            return self.tool_name
        return None


# ---------- Claude adapter ----------


def _parse_claude_event(line: str) -> list[TraceEvent]:
    """Parse one Claude Code JSONL line into zero or more TraceEvents.

    Content payloads are intentionally ignored. We only read structural
    keys: type, parentUuid, uuid, timestamp, message.content[] types.
    """
    out: list[TraceEvent] = []
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return out

    ev_type = ev.get("type")
    if not ev_type:
        return out

    ts = _parse_ts(ev.get("timestamp"))
    parent = ev.get("parentUuid")
    uuid = ev.get("uuid")
    session_id = ev.get("sessionId")

    # skip sidechain events
    if ev.get("isSidechain"):
        return out

    if ev_type in ("user", "assistant"):
        # top-level turn event
        out.append(
            TraceEvent(
                source="claude",
                event_type=ev_type,
                timestamp=ts,
                parent_uuid=parent,
                tool_name=None,
                tool_id=None,
                duration_ms=None,
                error=False,
            )
        )
        # extract tool_use blocks from assistant content
        if ev_type == "assistant":
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        out.append(
                            TraceEvent(
                                source="claude",
                                event_type="tool_use",
                                timestamp=ts,
                                parent_uuid=uuid,
                                tool_name=item.get("name"),
                                tool_id=item.get("id"),
                                duration_ms=None,
                                error=False,
                            )
                        )

    elif ev_type == "tool_use":
        # sometimes emitted as top-level events; treat same as extracted blocks
        out.append(
            TraceEvent(
                source="claude",
                event_type="tool_use",
                timestamp=ts,
                parent_uuid=parent,
                tool_name=ev.get("name"),
                tool_id=uuid,
                duration_ms=None,
                error=False,
            )
        )

    # tool results live on user events with toolUseResult
    if "toolUseResult" in ev:
        raw = ev["toolUseResult"]
        error = False
        if isinstance(raw, str):
            # error string result (stderr / traceback)
            error = "Error" in raw or "Traceback" in raw or "exit code" in raw.lower()
        elif isinstance(raw, dict):
            error = bool(raw.get("interrupted") or raw.get("exitCode") not in (None, 0, "0"))
        out.append(
            TraceEvent(
                source="claude",
                event_type="tool_result",
                timestamp=ts,
                parent_uuid=parent,
                tool_name=None,
                tool_id=uuid,
                duration_ms=None,
                error=error,
            )
        )

    # system-ish metadata: ignore mode/permission-mode for trace
    if ev_type in ("mode", "permission-mode", "last-prompt", "ai-title"):
        out.clear()

    return out


# ---------- pi adapter ----------


def _parse_pi_event(line: str) -> list[TraceEvent]:
    """Parse one pi agent session JSONL line into zero or more TraceEvents.

    Pi format:
      type=message  message={role, content:[{type,text}|{type,toolCall,id,name,arguments}], ...}
      type=model_change / thinking_level_change / session
    """
    out: list[TraceEvent] = []
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return out

    ev_type = ev.get("type")
    if ev_type != "message":
        return out

    msg = ev.get("message") or {}
    role = msg.get("role")
    ts = float(msg.get("timestamp", 0))
    content = msg.get("content")

    # toolResult messages have role="toolResult" at the message level
    if role == "toolResult":
        raw = content
        if isinstance(raw, str):
            error = "Error" in raw or "Traceback" in raw or "exit code" in raw.lower()
        elif isinstance(raw, list):
            # flatten text blocks to detect errors
            texts = " ".join(
                item.get("text", "")
                for item in raw
                if isinstance(item, dict) and item.get("type") == "text"
            )
            error = "Error" in texts or "Traceback" in texts or "exit code" in texts.lower()
        else:
            error = bool(raw.get("isError") or raw.get("interrupted")) if isinstance(raw, dict) else False
        out.append(
            TraceEvent(
                source="pi",
                event_type="tool_result",
                timestamp=ts,
                parent_uuid=ev.get("parentId"),
                tool_name=msg.get("toolName"),
                tool_id=msg.get("toolCallId"),
                duration_ms=None,
                error=error,
            )
        )
        return out

    if role not in ("user", "assistant"):
        return out

    if not isinstance(content, list):
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")

        if item_type == "toolCall":
            out.append(
                TraceEvent(
                    source="pi",
                    event_type="tool_use",
                    timestamp=ts,
                    parent_uuid=ev.get("parentId"),
                    tool_name=item.get("name"),
                    tool_id=item.get("id"),
                    duration_ms=None,
                    error=False,
                )
            )

    if not any(e.event_type in ("tool_use", "tool_result") for e in out):
        out.append(
            TraceEvent(
                source="pi",
                event_type=role,
                timestamp=ts,
                parent_uuid=ev.get("parentId"),
                tool_name=None,
                tool_id=None,
                duration_ms=None,
                error=False,
            )
        )

    return out

# ---------- parser registry ----------


_PARSERS: dict[str, tuple[str, callable]] = {
    # host marker → (source_label, parser)
    ".claude/projects": ("claude", _parse_claude_event),
    ".pi/agent/sessions": ("pi", _parse_pi_event),
}


def _detect_source(path: Path) -> tuple[str, callable] | None:
    rel = str(path)
    for marker, info in _PARSERS.items():
        if marker in rel:
            return info
    return None


# ---------- public API ----------


def _parse_ts(raw) -> float:
    """Parse timestamp into epoch float. Supports epoch numbers and ISO 8601 strings."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        raw = raw.strip()
        # ISO 8601 first
        if "T" in raw or "-" in raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                pass
        # epoch-as-string fallback
        try:
            return float(raw)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def iter_trace(session_path: Path) -> Iterator[TraceEvent]:
    """Yield normalized TraceEvents for one session JSONL."""
    src = _detect_source(session_path)
    if src is None:
        return
    _source_label, parser = src
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield from parser(line)
    except OSError:
        return


def trace_events(session_path: Path) -> list[TraceEvent]:
    return list(iter_trace(session_path))


def replay(events: list[TraceEvent]) -> str:
    """Render a sequential replay of events, human-readable."""
    lines: list[str] = [f"trace replay · {len(events)} events"]
    last_ts = None
    for i, ev in enumerate(events, 1):
        delta = ""
        if last_ts is not None and ev.timestamp:
            delta = f" (+{ev.timestamp - last_ts:.2f}s)"
        last_ts = ev.timestamp

        marker = "✗" if ev.error else "·"
        tool = f" [{ev.tool_name}]" if ev.tool_name else ""
        dur = f" {ev.duration_ms:.0f}ms" if ev.duration_ms is not None else ""
        lines.append(f"  {i:>4}{marker} {ev.event_type}{tool}{dur}{delta}")
    return "\n".join(lines) + "\n"


def diff(a: list[TraceEvent], b: list[TraceEvent]) -> str:
    """Find the first structural divergence between two traces.

    Tool_use/tool_result pairs are collapsed into single structural events
    so the diff compares execution plans, not result noise.
    Durations are inferred before comparison.
    """
    struct_a = _structural(_infer_durations(a))
    struct_b = _structural(_infer_durations(b))
    max_len = max(len(struct_a), len(struct_b))
    for i in range(max_len):
        ev_a = struct_a[i] if i < len(struct_a) else None
        ev_b = struct_b[i] if i < len(struct_b) else None

        if ev_a is None or ev_b is None:
            shorter = "B" if ev_a else "A"
            return (
                f"divergence at step {i + 1}\n"
                f"  trace {shorter} ended early "
                f"({len(struct_a)} vs {len(struct_b)} events)\n"
                f"  common prefix: {i} events ({_pct(i, max_len)}%)"
            )

        if ev_a.event_type != ev_b.event_type:
            return (
                f"divergence at step {i + 1}\n"
                f"  A: {ev_a.event_type} {_fmt(ev_a)}\n"
                f"  B: {ev_b.event_type} {_fmt(ev_b)}\n"
                f"  common prefix: {i} events ({_pct(i, max_len)}%)"
            )

        if ev_a.tool_name != ev_b.tool_name:
            return (
                f"divergence at step {i + 1}\n"
                f"  A: {ev_a.event_type} {_fmt(ev_a)}\n"
                f"  B: {ev_b.event_type} {_fmt(ev_b)}\n"
                f"  common prefix: {i} events ({_pct(i, max_len)}%)"
            )

        if ev_a.error != ev_b.error:
            return (
                f"divergence at step {i + 1} (error status)\n"
                f"  A: {ev_a.event_type} {_fmt(ev_a)} error={ev_a.error}\n"
                f"  B: {ev_b.event_type} {_fmt(ev_b)} error={ev_b.error}\n"
                f"  common prefix: {i} events ({_pct(i, max_len)}%)"
            )

    return (
        f"no divergence\n"
        f"  {len(struct_a)} structural events · identical plan\n"
        f"  payloads not compared"
    )


def _structural(events: list[TraceEvent]) -> list[TraceEvent]:
    """Collapse tool_use/tool_result pairs into single structural events."""
    result: list[TraceEvent] = []
    i = 0
    while i < len(events):
        ev = events[i]
        if ev.event_type == "tool_result" and result and result[-1].event_type == "tool_use" and result[-1].tool_id == ev.tool_id:
            # merge into previous tool_use
            result[-1].error = ev.error or result[-1].error
            if ev.duration_ms is not None:
                result[-1].duration_ms = ev.duration_ms
            i += 1
            continue
        result.append(ev)
        i += 1
    return result

def _fmt(ev: TraceEvent) -> str:
    parts = []
    if ev.tool_name:
        parts.append(ev.tool_name)
    if ev.duration_ms is not None:
        parts.append(f"{ev.duration_ms:.0f}ms")
    if ev.error:
        parts.append("error")
    return " ".join(parts) if parts else "(turn)"


def _pct(common: int, total: int) -> str:
    if total == 0:
        return "0"
    return f"{common / total * 100:.0f}%"


def _format_duration(ms: float) -> str:
    """Format milliseconds as human-readable string."""
    if ms <= 0:
        return "0ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    hours = mins / 60
    return f"{hours:.1f}h"


def _infer_durations(events: list[TraceEvent]) -> list[TraceEvent]:
    """Pair tool_use with following tool_result to infer duration_ms."""
    pending: dict[str, TraceEvent] = {}
    out: list[TraceEvent] = []
    for ev in events:
        if ev.event_type == "tool_use" and ev.tool_id:
            pending[ev.tool_id] = ev
            out.append(ev)
        elif ev.event_type == "tool_result" and ev.tool_id and ev.tool_id in pending:
            tu = pending.pop(ev.tool_id)
            dur = max(0.0, (ev.timestamp - tu.timestamp) * 1000)
            ev.duration_ms = dur
            out.append(ev)
        else:
            out.append(ev)
    return out


def summary(events: list[TraceEvent]) -> dict:
    """Aggregate stats for a trace."""
    events = _infer_durations(events)
    tools = [e for e in events if e.event_type == "tool_use"]
    results = [e for e in events if e.event_type == "tool_result"]
    errors = [e for e in results if e.error]
    turns = [e for e in events if e.event_type in ("user", "assistant")]

    durations = [e.duration_ms for e in results if e.duration_ms is not None]
    wall_ms = 0.0
    if events:
        first = events[0].timestamp
        last = events[-1].timestamp
        wall_ms = max(0.0, (last - first) * 1000)

    return {
        "events": len(events),
        "turns": len(turns),
        "tools": len(tools),
        "results": len(results),
        "errors": len(errors),
        "wall_ms": wall_ms,
        "avg_tool_ms": sum(durations) / len(durations) if durations else 0.0,
    }


def profile(events: list[TraceEvent]) -> dict:
    """Tool-call breakdown: counts, error rates, avg duration per tool."""
    events = _infer_durations(events)
    tools: dict[str, list[TraceEvent]] = {}
    for ev in events:
        if ev.event_type == "tool_use" and ev.tool_name:
            tools.setdefault(ev.tool_name, []).append(ev)

    rows = []
    for name, evs in sorted(tools.items(), key=lambda kv: -len(kv[1])):
        results = [e for e in events if e.event_type == "tool_result" and e.tool_id in {e2.tool_id for e2 in evs}]
        errors = [e for e in results if e.error]
        durations = [e.duration_ms for e in results if e.duration_ms is not None]
        rows.append({
            "tool": name,
            "calls": len(evs),
            "results": len(results),
            "errors": len(errors),
            "error_rate": len(errors) / len(results) if results else 0.0,
            "avg_ms": sum(durations) / len(durations) if durations else 0.0,
        })
    return {"tools": rows}


def grep(events: list[TraceEvent], pattern: str) -> list[TraceEvent]:
    """Filter events by tool name or event type (case-insensitive substring)."""
    p = pattern.lower()
    return [e for e in events if p in e.event_type.lower() or (e.tool_name and p in e.tool_name.lower())]


def export_json(events: list[TraceEvent], path: Path) -> None:
    """Write normalized trace events to a JSON file."""
    data = [
        {
            "source": e.source,
            "event_type": e.event_type,
            "timestamp": e.timestamp,
            "parent_uuid": e.parent_uuid,
            "tool_name": e.tool_name,
            "tool_id": e.tool_id,
            "duration_ms": e.duration_ms,
            "error": e.error,
        }
        for e in events
    ]
    path.write_text(json.dumps(data, indent=2, default=str))


def watch(session_path: Path, poll_interval: float = 1.0) -> Iterator[TraceEvent]:
    """Tail a session JSONL and yield new events as they are written."""
    src = _detect_source(session_path)
    if src is None:
        return
    _source_label, parser = src
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as f:
            # yield existing events first
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield from parser(line)
            # now tail for new events
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        yield from parser(line)
                else:
                    time.sleep(poll_interval)
    except OSError:
        return


def errors(events: list[TraceEvent]) -> list[TraceEvent]:
    """Return only error events from a trace."""
    return [e for e in events if e.error]


def suggest(events: list[TraceEvent]) -> list[str]:
    """Generate actionable suggestions from session trace data."""
    events = _infer_durations(events)
    stats = summary(events)
    prof = profile(events)
    suggestions: list[str] = []

    tools = {row["tool"]: row for row in prof.get("tools", [])}

    # error-driven suggestions
    if stats["errors"] > 0:
        worst = max(tools.values(), key=lambda r: r["errors"]) if tools else None
        if worst and worst["error_rate"] > 0.3:
            suggestions.append(
                f"{worst['tool']} is failing {worst['error_rate']*100:.0f}% of the time "
                f"({worst['errors']}/{worst['results']} results). "
                f"Add retry logic or check preconditions before calling it."
            )

    # duration-driven suggestions
    slow_tools = [r for r in tools.values() if r["avg_ms"] > 5000]
    if slow_tools:
        names = ", ".join(r["tool"] for r in slow_tools)
        suggestions.append(
            f"Slow tools detected: {names}. "
            f"Consider parallelizing calls or caching results."
        )

    # bash-heavy sessions
    bash = tools.get("Bash")
    if bash and bash["calls"] > 50 and bash["avg_ms"] < 100:
        suggestions.append(
            f"Bash is called {bash['calls']} times with avg {_format_duration(bash['avg_ms'])}. "
            f"You may be polling. Consider event-driven alternatives or `sleep` consolidation."
        )

    # tool diversity
    if len(tools) == 1:
        suggestions.append(
            "Only one tool type used. Consider expanding your toolkit "
            "or checking if you're over-relying on a single tool."
        )

    # high error count
    if stats["errors"] > 10:
        suggestions.append(
            f"{stats['errors']} errors in this session. "
            f"Run `av trace errors <session>` to inspect failure patterns."
        )

    if not suggestions:
        suggestions.append("Session looks healthy. No immediate improvements suggested.")

    return suggestions


# ---------- CLI helpers ----------


def list_sessions() -> list[tuple[str, str, int]]:
    """Return (session_path, host_label, event_count) for every discoverable session."""
    rows: list[tuple[str, str, int]] = []
    for si in discover_sessions():
        src = _detect_source(Path(si.path))
        if src is None:
            continue
        label, _ = src
        try:
            count = sum(1 for _ in open(si.path, "rb") if _.endswith(b"\n"))
        except OSError:
            count = 0
        rows.append((str(si.path), label, count))
    return rows
