"""Token spend / cost tracker.

Parses session JSONLs (Claude Code, pi, etc.) for token-usage events,
aggregates by host / project / day, and estimates cost. Best-effort:
JSONL formats vary, fields differ across agent versions, so we fail
soft and report what we can.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Approximate USD per 1M tokens. Conservative defaults; override per model.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic (per 1M tokens)
    "claude-opus-4":           {"input": 15.0,  "output": 75.0,  "cache_read": 1.5,   "cache_write": 18.75},
    "claude-opus-4-8":         {"input": 15.0,  "output": 75.0,  "cache_read": 1.5,   "cache_write": 18.75},
    "claude-sonnet-4":         {"input": 3.0,   "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-haiku-4":          {"input": 0.80,  "output": 4.0,   "cache_read": 0.08,  "cache_write": 1.0},
    "claude-3.5-sonnet":       {"input": 3.0,   "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-3.5-haiku":        {"input": 0.80,  "output": 4.0,   "cache_read": 0.08,  "cache_write": 1.0},
    # OpenAI
    "gpt-4o":                  {"input": 2.5,   "output": 10.0,  "cache_read": 1.25},
    "gpt-4o-mini":             {"input": 0.15,  "output": 0.60},
    "o1":                      {"input": 15.0,  "output": 60.0},
    "o1-mini":                 {"input": 3.0,   "output": 12.0},
    # Default for unknown models
    "_default":                {"input": 3.0,   "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
}

# Skip records with very large string values (inline base64 images, etc.).
_MAX_RECORD_BYTES = 200_000

def _should_skip_record(line: str) -> bool:
    return len(line) > _MAX_RECORD_BYTES




@dataclass
class TokenBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str | None = None  # last observed model for this bucket

    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def cost_usd(self, model: str | None = None) -> float:
        # Use bucket's own model if available, otherwise fall back to arg/default
        m = model or self.model or "_default"
        p = MODEL_PRICING.get(m, MODEL_PRICING["_default"])
        cost = 0.0
        cost += (self.input_tokens / 1_000_000) * p.get("input", 0)
        cost += (self.output_tokens / 1_000_000) * p.get("output", 0)
        cost += (self.cache_read_tokens / 1_000_000) * p.get("cache_read", 0)
        cost += (self.cache_write_tokens / 1_000_000) * p.get("cache_write", 0)
        return cost

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total(),
        }


def _extract_usage_from_message(message: dict) -> TokenBucket | None:
    """Claude Code stores usage in `message.usage`."""
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    b = TokenBucket()
    b.input_tokens = int(usage.get("input_tokens", 0) or 0)
    b.output_tokens = int(usage.get("output_tokens", 0) or 0)
    b.cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
    b.cache_write_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
    if b.total() == 0:
        return None
    return b


def scan_claude_code_session(path: Path) -> tuple[TokenBucket, str | None]:
    """Parse a Claude Code session JSONL. Returns (bucket, model)."""
    bucket = TokenBucket()
    model: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                if _should_skip_record(raw_line):
                    continue
                try:
                    rec = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                m = rec.get("message")
                if isinstance(m, dict):
                    b = _extract_usage_from_message(m)
                    if b is not None:
                        bucket.input_tokens += b.input_tokens
                        bucket.output_tokens += b.output_tokens
                        bucket.cache_read_tokens += b.cache_read_tokens
                        bucket.cache_write_tokens += b.cache_write_tokens
                if not model and isinstance(m, dict):
                    mdl = m.get("model")
                    if isinstance(mdl, str):
                        model = mdl
    except OSError:
        pass
    return bucket, model

def scan_pi_session(path: Path) -> tuple[TokenBucket, str | None]:
    """Parse a pi session JSONL. Returns (bucket, model).

    pi sessions use a different JSONL structure: each record has
    type='message' and the message is in the 'message' key.
    """
    bucket = TokenBucket()
    model: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                if _should_skip_record(raw_line):
                    continue
                try:
                    rec = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") != "message":
                    continue
                msg = rec.get("message", {})
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    bucket.input_tokens += int(usage.get("input", 0) or 0)
                    bucket.output_tokens += int(usage.get("output", 0) or 0)
                    bucket.cache_read_tokens += int(usage.get("cacheRead", 0) or 0)
                    bucket.cache_write_tokens += int(usage.get("cacheWrite", 0) or 0)
                if not model:
                    mdl = msg.get("model")
                    if isinstance(mdl, str):
                        model = mdl
    except OSError:
        pass
    return bucket, model


def scan_pi_run_history(path: Path) -> TokenBucket:
    """Parse pi's run-history.jsonl. Doesn't have per-call token counts
    in current versions, so we return an empty bucket for now.
    Future: enrich with token counts when the format supports them.
    """
    return TokenBucket()




@dataclass
class ToolTokenUsage:
    """Token usage for a single tool."""
    tool_name: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def avg_total(self) -> float:
        return self.total / self.calls if self.calls else 0.0


def _extract_tools_from_message(message: dict) -> list[str]:
    """Extract tool names from an assistant message. Handles both formats."""
    tools: list[str] = []
    if not isinstance(message, dict):
        return tools
    content = message.get("content")
    if not isinstance(content, list):
        return tools
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name", "")
            if isinstance(name, str) and name:
                tools.append(name)
        elif btype == "toolCall":
            name = block.get("name", "")
            if isinstance(name, str) and name:
                tools.append(name)
    return tools


def scan_tool_tokens(sessions: Iterable[Path] | None = None) -> dict[str, ToolTokenUsage]:
    """Scan sessions and return token usage per tool.

    For each assistant message that contains tool calls, the message's
    token usage is split equally among the tools in that message.
    This gives a rough approximation of per-token cost.
    """
    from agent_vitals.sessions import SESSION_ROOTS

    if sessions is None:
        sessions = []
        for _, root in SESSION_ROOTS:
            if root.is_dir():
                sessions.extend(root.rglob("*.jsonl"))

    out: dict[str, ToolTokenUsage] = {}
    for path in sessions:
        try:
            with path.open("rb") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    # pi format: record has type='message'
                    if rec.get("type") == "message":
                        inner = rec.get("message", {})
                        if isinstance(inner, dict) and inner.get("role") == "assistant":
                            tools = _extract_tools_from_message(inner)
                            if tools:
                                usage = inner.get("usage")
                                if isinstance(usage, dict):
                                    inp = int(usage.get("input", 0) or 0)
                                    out_tok = int(usage.get("output", 0) or 0)
                                    cr = int(usage.get("cacheRead", 0) or 0)
                                    cw = int(usage.get("cacheWrite", 0) or 0)
                                    share_inp = inp // len(tools)
                                    share_out = out_tok // len(tools)
                                    share_cr = cr // len(tools)
                                    share_cw = cw // len(tools)
                                    for tool in tools:
                                        if tool not in out:
                                            out[tool] = ToolTokenUsage(tool_name=tool)
                                        out[tool].calls += 1
                                        out[tool].input_tokens += share_inp
                                        out[tool].output_tokens += share_out
                                        out[tool].cache_read_tokens += share_cr
                                        out[tool].cache_write_tokens += share_cw
                    # Claude Code format: record has 'message' but not type='message'
                    elif isinstance(rec.get("message"), dict):
                        msg = rec.get("message")
                        if msg.get("role") == "assistant":
                            tools = _extract_tools_from_message(msg)
                            if tools:
                                usage = msg.get("usage")
                                if isinstance(usage, dict):
                                    inp = int(usage.get("input_tokens", 0) or 0)
                                    out_tok = int(usage.get("output_tokens", 0) or 0)
                                    cr = int(usage.get("cache_read_input_tokens", 0) or 0)
                                    cw = int(usage.get("cache_creation_input_tokens", 0) or 0)
                                    share_inp = inp // len(tools)
                                    share_out = out_tok // len(tools)
                                    share_cr = cr // len(tools)
                                    share_cw = cw // len(tools)
                                    for tool in tools:
                                        if tool not in out:
                                            out[tool] = ToolTokenUsage(tool_name=tool)
                                        out[tool].calls += 1
                                        out[tool].input_tokens += share_inp
                                        out[tool].output_tokens += share_out
                                        out[tool].cache_read_tokens += share_cr
                                        out[tool].cache_write_tokens += share_cw
        except OSError:
            continue
    return out


def render_tokens_report(usage: dict[str, ToolTokenUsage], limit: int = 20) -> str:
    """Render token usage report grouped by tool."""
    if not usage:
        return "tokens: no token-usage data found in scanned sessions\n"
    items = sorted(usage.values(), key=lambda u: u.total, reverse=True)
    lines = [f"tokens: {len(items)} tool(s) with observed token usage\n"]
    lines.append(f"  {'tool':<35} {'calls':>6}  {'total':>12}  {'avg':>10}  {'input':>12}  {'output':>10}")
    lines.append(f"  {'-'*35} {'-'*6}  {'-'*12}  {'-'*10}  {'-'*12}  {'-'*10}")
    for u in items[:limit]:
        tool = u.tool_name if len(u.tool_name) <= 35 else u.tool_name[:32] + "..."
        lines.append(
            f"  {tool:<35} {u.calls:>6}  {u.total:>12,}  {u.avg_total:>10,.0f}  {u.input_tokens:>12,}  {u.output_tokens:>10,}"
        )
    if len(items) > limit:
        lines.append(f"  ... and {len(items) - limit} more")
    return "\n".join(lines) + "\n"

def scan_all_sessions(
    sessions: Iterable[Path] | None = None,
    *,
    include_claude: bool = True,
    include_pi: bool = True,
) -> dict[str, dict[str, TokenBucket]]:
    """Scan session files and return a nested dict:
       {host: {project: TokenBucket}}.
    """
    if sessions is None:
        sessions = []
        if include_claude:
            # Claude Code sessions are under ~/.claude/projects/. The directory
            # layout is ~/.claude/projects/<project>/<uuid>.jsonl. Filter to
            # that pattern to avoid confusing pi session dirs (which live under
            # ~/.pi/agent/sessions/) with claude code dirs.
            cc_root = Path.home() / ".claude" / "projects"
            if cc_root.is_dir():
                for p in cc_root.rglob("*.jsonl"):
                    if "/.claude/projects/" in str(p):
                        sessions.append(p)
        if include_pi:
            # pi stores sessions under ~/.pi/agent/sessions/ with run-history
            # in ~/.pi/agent/run-history.jsonl. run-history doesn't have token
            # usage data; the session dirs may or may not, depending on version.
            pi_root = Path.home() / ".pi" / "agent" / "sessions"
            if pi_root.is_dir():
                for p in pi_root.rglob("*.jsonl"):
                    sessions.append(p)

    out: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    for s in sessions:
        if "/.claude/projects/" in str(s):
            bucket, model = scan_claude_code_session(s)
            host = "claude-code"
            # Project: path is like .../projects/<project>/<uuid>.jsonl
            try:
                rel = s.relative_to(Path.home() / ".claude" / "projects")
                project = rel.parts[0] if rel.parts else None
            except ValueError:
                project = s.parent.name
        elif "/.pi/agent/sessions/" in str(s):
            bucket, model = scan_pi_session(s)
            host = "pi"
            # Project for pi: use the session dir name (e.g. --home-example-user--)
            project = s.parent.name
        else:
            continue
        if bucket.total() == 0:
            continue
        merged = out[host].get(project) or TokenBucket()
        merged.input_tokens += bucket.input_tokens
        merged.output_tokens += bucket.output_tokens
        merged.cache_read_tokens += bucket.cache_read_tokens
        merged.cache_write_tokens += bucket.cache_write_tokens
        # Record the model if we have one. For mixed-model sessions we
        # keep the last observed model as a rough approximation.
        if model:
            merged.model = model
        out[host][project] = merged
    return out


def total_cost_estimate(
    by_host_project: dict[str, dict[str, TokenBucket]],
    model_pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Rough cost estimate. Uses each bucket's observed model when available."""
    model_pricing = model_pricing or MODEL_PRICING
    total = 0.0
    for host, by_proj in by_host_project.items():
        for proj, bucket in by_proj.items():
            total += bucket.cost_usd()
    return total


def total_effective_tokens(by_host_project: dict[str, dict[str, TokenBucket]]) -> float:
    """Sum of GitHub's Effective Tokens across all sessions/projects.

    ET = m * (I + 0.1 * C + 4.0 * O), where m = model multiplier
    from the bucket's observed model (defaults to Sonnet-class).
    """
    from agent_vitals.efficiency import effective_tokens
    total = 0.0
    for by_proj in by_host_project.values():
        for bucket in by_proj.values():
            total += effective_tokens(
                input_tokens=bucket.input_tokens,
                output_tokens=bucket.output_tokens,
                cache_read_tokens=bucket.cache_read_tokens,
                cache_write_tokens=bucket.cache_write_tokens,
                model=bucket.model or "_default",
            )
    return total


def render_cost_report(
    by_host_project: dict[str, dict[str, TokenBucket]],
    days: int = 7,
) -> str:
    """Plain-text cost report with ET (Effective Tokens) metric."""
    if not by_host_project:
        return "cost: no token-usage data found in scanned sessions\n"
    lines = ["cost: token usage by host / project (last scan; pricing by observed model)\n"]
    for host, by_proj in sorted(by_host_project.items()):
        lines.append(f"\n  [{host}]")
        for proj, bucket in sorted(by_proj.items()):
            total = bucket.total()
            cost = bucket.cost_usd()
            model_label = bucket.model or "sonnet-default"
            lines.append(
                f"    {proj:<32}  {total:>10,} tok   ~${cost:>6.2f}   ({model_label})"
            )
    grand_total = sum(b.total() for by_proj in by_host_project.values() for b in by_proj.values())
    grand_cost = total_cost_estimate(by_host_project)
    grand_et = total_effective_tokens(by_host_project)
    lines.append(f"\n  total: {grand_total:,} tokens, ~${grand_cost:.2f}")
    lines.append(f"  effective tokens (ET): {grand_et:,.0f}")
    lines.append("  (pricing uses observed model when available; ET uses GitHub's")
    lines.append("   formula: m * (1.0*I + 0.1*C + 1.0*W + 4.0*O))")
    return "\n".join(lines) + "\n"


__all__ = [
    "TokenBucket",
    "MODEL_PRICING",
    "scan_claude_code_session",
    "scan_pi_run_history",
    "scan_all_sessions",
    "total_cost_estimate",
    "total_effective_tokens",
    "render_cost_report",
]
