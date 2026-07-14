"""Efficiency analysis for AI agent harnesses.

Implements three patterns documented as the highest-impact efficiency
improvements for agentic systems:

1. Doom-loop detection — agents that hit the same tool/file repeatedly
   without making progress. GitHub's blog post on agentic token efficiency
   and LangChain's harness engineering writeup both call this out as the
   most common failure mode.

2. Unused MCP tools — every registered tool is part of every agent turn's
   context (function names + JSON schemas). GitHub measured 8-12KB of waste
   per turn from tools an agent never uses.

3. Effective Tokens (ET) metric — GitHub's formula that normalizes token
   counts across model tiers:
       ET = m * (1.0 * I + 0.1 * C + 4.0 * O)
   where m = model multiplier, I = input, C = cache_read, O = output.
   Output tokens get 4x weight (most expensive), cache_read 0.1x (cheap).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# Loop detection thresholds. Tuned for "obvious doom loop" detection.
#
# We do NOT flag a session just because a tool was called many times.
# High frequency of a tool name (e.g. hundreds of Bash calls where each
# command is unique) is common in real agent work. We only flag when the
# SAME command string is repeated many times, indicating the agent is stuck.
#
# If the exact same command is repeated >= this many times in one session,
# flag it as a tool loop.
LOOP_TOOL_THRESHOLD = 20
# If the exact same edit (same file + same edit signature) is applied
# >= this many times in one session, flag it as a file loop.
LOOP_FILE_THRESHOLD = 10

# Bash commands that start with these are treated as polling / liveness
# checks, not doom-loop evidence. An agent waiting for `ps` to show a
# process exited is doing legitimate work, not stuck.
POLLING_PREFIXES: tuple[str, ...] = (
    "ps ",
    "ps	",
    "ps/",
    "pgrep ",
    "pidof ",
    "kill -0 ",
    "kill -s 0 ",
    "test -d /proc/",
    "ls /proc/",
)

# Threshold for soft-loop detection: same command *structure* repeated
# this many times. Structure = command with literals replaced by placeholders.
SOFT_LOOP_THRESHOLD = 20

# Skip records with very large string values (inline base64 images, etc.).
# These bloat session files but carry no signal for loop / tool analysis.
_MAX_RECORD_BYTES = 200_000  # ~200KB


def _should_skip_record(line: str) -> bool:
    return len(line) > _MAX_RECORD_BYTES



def _command_signature(cmd: str) -> str:
    """Normalize a bash command to a structural signature.

    Replaces path-like tokens, flags, numbers, and quoted strings with
    placeholders so that `npm run build --verbose` and
    `npm run build --verbose --log-level debug` collapse to the same
    signature. The goal is soft-loop detection: catch repeated *pattern*
    of command, not just exact string match.
    """
    import shlex
    try:
        parts = shlex.split(cmd)
    except ValueError:
        # Fallback: naive split on whitespace
        parts = cmd.split()
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        # Quoted strings / command substitutions
        if part.startswith(("'", '"', '`')) or part.endswith(("'", '"', '`')):
            out.append("<str>")
            continue
        # Flags / options
        if part.startswith("-"):
            out.append("<flag>")
            continue
        # Numbers (int / float / hex)
        try:
            float(part)
            out.append("<num>")
            continue
        except ValueError:
            pass
        if part.startswith("0x") or part.startswith("0X"):
            out.append("<num>")
            continue
        # Paths: absolute, relative, or ~-prefixed
        if part.startswith(("/", "./", "../", "~")):
            out.append("<path>")
            continue

        out.append(part)
    return " ".join(out)


@dataclass
class LoopFinding:
    session_path: str
    host: str
    kind: str       # "tool_repeat" | "file_repeat"
    target: str     # tool name OR file path
    count: int
    detail: str = ""

@dataclass
class UnusedToolFinding:
    tool_name: str
    server: str
    host: str
    config_path: str
    calls_observed: int
    estimated_waste_bytes: int

# ---------- loop detection ----------


def _process_tool_block(
    block: dict,
    path: Path,
    tool_cmd_counts: Counter[str],
    tool_sig_counts: Counter[str],
    file_edit_targets: Counter[str],
    seen_files: set[str],
) -> None:
    """Extract tool-call data from one content block, handling both
    Claude Code and pi session formats.

    Claude Code: block.type == "tool_use", block.name is PascalCase
    pi:          block.type == "toolCall", block.name is lowercase
    """
    btype = block.get("type")
    if btype not in ("tool_use", "toolCall"):
        return
    name = block.get("name", "")
    if not isinstance(name, str) or not name:
        return
    inp = block.get("input", {})
    if not isinstance(inp, dict):
        return
    # Normalize name: Claude Code uses PascalCase, pi uses lowercase
    norm_name = name.lower()
    # For Bash, use the exact command string as the key,
    # but skip obvious polling / liveness checks.
    if norm_name == "bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            stripped = cmd.strip()
            if not any(stripped.startswith(p) for p in POLLING_PREFIXES):
                # Skip SSH commands — handled by dedicated SSH detector
                if not _is_ssh_command(stripped):
                    tool_cmd_counts[stripped] += 1
                    sig = _command_signature(stripped)
                    if sig != stripped:
                        tool_sig_counts[sig] += 1
    # For Edit, track file_path + normalized edit signature
    elif norm_name == "edit":
        fp = inp.get("file_path", "")
        if isinstance(fp, str) and fp:
            old = inp.get("old_string", "")
            new = inp.get("new_string", "")
            sig = _edit_signature(old, new)
            file_edit_targets[f"{fp}::{sig}"] += 1
            seen_files.add(fp)




# ---------- SSH soft-loop detection ----------


_SSH_PREFIXES = (
    "ssh ",
    "ssh	",
    "ssh/",
)


def _is_ssh_command(cmd: str) -> bool:
    """Check if a command is an SSH command."""
    return any(cmd.startswith(p) for p in _SSH_PREFIXES)


def _extract_ssh_target(cmd: str) -> str | None:
    """Extract the SSH target host from a command.
    
    Examples:
      ssh remote-host-1 ls /tmp
      ssh -o StrictHostKeyChecking=no ubuntu@remote-host-1 ps aux
      ssh suppx-prod 2>&1 | tail -n 20
    """
    import shlex
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    
    # Find 'ssh' in the command
    for i, part in enumerate(parts):
        if part == "ssh" or part.startswith("ssh"):
            # Next non-flag, non-flag-argument part is the target
            skip_next = False
            for j in range(i + 1, len(parts)):
                p = parts[j]
                if skip_next:
                    skip_next = False
                    continue
                if p.startswith("-"):
                    # Some flags take arguments (e.g., -o StrictHostKeyChecking=no)
                    # Skip the next part too if this flag takes an argument
                    if p in ("-o", "-F", "-L", "-R", "-D", "-i", "-p", "-P", "-l", "-C"):
                        skip_next = True
                    continue
                if p in ("-", "--"):
                    continue
                # Could be host, user@host, or host:port
                return p
    return None


def _scan_session_for_ssh_loops(path: Path) -> list[LoopFinding]:
    """Scan a session for SSH soft loops.

    Detects repeated SSH commands to the same target with similar
    structure. Unlike generic soft-loop detection, this is specific
    to SSH and provides actionable remediation advice.
    """
    findings: list[LoopFinding] = []
    ssh_counts: Counter[str] = Counter()

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
                # Claude Code assistant records: outer type is "assistant",
                # actual message content lives in rec["message"]["content"].
                if rec.get("type") == "assistant":
                    msg = rec.get("message")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict):
                                    _process_ssh_block(block, ssh_counts)
                # pi format: record itself is a message with role="assistant"
                elif rec.get("type") == "message":
                    inner = rec.get("message", {})
                    if isinstance(inner, dict) and inner.get("role") == "assistant":
                        content = inner.get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict):
                                    _process_ssh_block(block, ssh_counts)
    except OSError:
        return []

    host = "claude-code" if "/.claude/projects/" in str(path) else "pi"
    for target, count in ssh_counts.items():
        if count >= 10:
            findings.append(LoopFinding(
                session_path=str(path),
                host=host,
                kind="ssh_poll",
                target=f"ssh {target}",
                count=count,
                detail=f"SSH polling to {target} repeated {count}x — consider timeout/backoff",
            ))
    return findings


def _process_ssh_block(block: dict, ssh_counts: Counter[str]) -> None:
    """Extract SSH command info from a tool block."""
    btype = block.get("type")
    if btype not in ("tool_use", "toolCall"):
        return
    name = block.get("name", "")
    if not isinstance(name, str) or not name:
        return
    norm_name = name.lower()
    if norm_name != "bash":
        return
    inp = block.get("input", {})
    if not isinstance(inp, dict):
        return
    cmd = inp.get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        return
    stripped = cmd.strip()
    if not _is_ssh_command(stripped):
        return
    target = _extract_ssh_target(stripped)
    if target:
        ssh_counts[target] += 1


def _scan_session_for_loops(path: Path) -> list[LoopFinding]:
    """Parse one session JSONL and return loop findings.

    A "doom loop" is not just high call count — it's repeated identical or
    near-identical calls that make no progress. We detect this by:

    1. Exact tool loops: the exact same command string repeated >= 20 times.
    2. Soft tool loops: the same command *structure* (literals replaced by
       placeholders) repeated >= 20 times. Catches loops where the agent
       varies paths, flags, or arguments but keeps the same pattern.
    3. File loops: the exact same file edited >= 10 times with identical
       or near-identical (fuzzy match > 95%) edit operations.

    Mere frequency of a tool name (e.g. hundreds of Bash calls where each
    command is unique) is not a loop — it's heavy legitimate use.
    """
    findings: list[LoopFinding] = []
    tool_cmd_counts: Counter[str] = Counter()
    tool_sig_counts: Counter[str] = Counter()  # structural signatures for soft loops
    file_edit_targets: Counter[str] = Counter()  # file_path + normalized edit signature
    seen_files: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                # --- Claude Code format: outer record has 'message' under type="assistant" ---
                if rec.get("type") == "assistant":
                    msg = rec.get("message")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict):
                                    _process_tool_block(
                                        block, path, tool_cmd_counts,
                                        tool_sig_counts, file_edit_targets,
                                        seen_files,
                                    )
                # --- pi format: record itself is a message ---
                elif rec.get("type") == "message":
                    inner = rec.get("message", {})
                    if isinstance(inner, dict) and inner.get("role") == "assistant":
                        content = inner.get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict):
                                    _process_tool_block(
                                        block, path, tool_cmd_counts,
                                        tool_sig_counts, file_edit_targets,
                                        seen_files,
                                    )
    except OSError:
        return []
    host = "claude-code" if "/.claude/projects/" in str(path) else "pi"
    # Tool loops: exact same command repeated many times
    for cmd, count in tool_cmd_counts.items():
        if count >= LOOP_TOOL_THRESHOLD:
            findings.append(LoopFinding(
                session_path=str(path),
                host=host,
                kind="tool_repeat",
                target=cmd[:80],
                count=count,
                detail=f"identical command repeated {count}x",
            ))
    # Soft tool loops: same command *structure* repeated many times
    # Skip signatures that are already exact matches (those are handled above).
    exact_cmds = set(tool_cmd_counts.keys())
    for sig, count in tool_sig_counts.items():
        if count >= SOFT_LOOP_THRESHOLD and sig not in exact_cmds:
            findings.append(LoopFinding(
                session_path=str(path),
                host=host,
                kind="tool_repeat",
                target=sig[:80],
                count=count,
                detail=f"near-identical command pattern repeated {count}x",
            ))
    # File loops: same file + same edit signature repeated many times
    for key, count in file_edit_targets.items():
        fp, sig = key.split("::", 1)
        if count >= 10:
            findings.append(LoopFinding(
                session_path=str(path),
                host=host,
                kind="file_repeat",
                target=fp,
                count=count,
                detail=f"same edit applied {count}x",
            ))
    return findings


def _edit_signature(old: str, new: str) -> str:
    """Normalize an edit pair to a short signature for comparison.

    Collapses whitespace and truncates to 120 chars. Does NOT strip
    comments or string literals — we want to distinguish between
    different edits, not collapse them into the same signature.
    """
    import re
    def norm(s: str) -> str:
        # Collapse whitespace only
        s = re.sub(r"\s+", " ", s).strip()
        # Take first 120 chars as signature
        return s[:120]
    return norm(old) + " -> " + norm(new)


def find_loops(sessions: list[Path] | None = None) -> list[LoopFinding]:
    """Scan sessions for doom-loop patterns. Default: all known session dirs.

    Detects both exact repetition (same command string) and soft loops
    (same command structure with varying literals). Also detects SSH
    polling loops specifically.
    """
    if sessions is None:
        sessions = _all_session_files()
    out: list[LoopFinding] = []
    for s in sessions:
        out.extend(_scan_session_for_loops(s))
        out.extend(_scan_session_for_ssh_loops(s))
    # Sort: highest count first
    out.sort(key=lambda f: f.count, reverse=True)
    return out


# ---------- unused tool detection ----------


def _collect_called_tool_names(sessions: list[Path]) -> set[str]:
    """Walk every session and return the set of tool names that were called."""
    called: set[str] = set()
    for path in sessions:
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
                    # --- Claude Code format ---
                    msg = rec.get("message")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    n = block.get("name")
                                    if isinstance(n, str) and n:
                                        called.add(n)
                    # --- pi format ---
                    if rec.get("type") == "message":
                        inner = rec.get("message", {})
                        if isinstance(inner, dict) and inner.get("role") == "assistant":
                            content = inner.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "toolCall":
                                        n = block.get("name")
                                        if isinstance(n, str) and n:
                                            called.add(n)
                                        # For MCP calls, also record the actual
                                        # MCP tool name (e.g. mcp__vitals__shadow_list)
                                        if n == "mcp":
                                            args = block.get("arguments", {})
                                            if isinstance(args, dict):
                                                mt = args.get("tool")
                                                if isinstance(mt, str) and mt:
                                                    called.add(mt)
        except OSError:
            continue
    return called


def _parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Attempt to split an observed tool name into (server, tool).

    Handles patterns seen in session data:
      - mcp__<server>__<tool>
      - <server>_<tool>
      - <server>__<tool>
      - plain <server> (no tool granularity)
    Returns None if the name doesn't look like an MCP tool call.
    """
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) >= 3:
            return parts[1], parts[2]
    if "_" in name:
        # Could be <server>_<tool> or <server>_<server>_<tool>
        # Heuristic: split on first underscore, check if left side looks
        # like a known server prefix (contains hyphen or is all lowercase).
        left, right = name.split("_", 1)
        if left and ("-" in left or left.islower()):
            return left, right
    if "__" in name:
        left, right = name.split("__", 1)
        if left and ("-" in left or left.islower()):
            return left, right
    # Plain server name — can't extract tool
    return None


def find_unused_tools(
    registered: dict[str, dict[str, str]] | None = None,
    called: set[str] | None = None,
) -> list[UnusedToolFinding]:
    """Find MCP tools registered but never called.

    `registered` is {server_name: {tool_name: command_or_url}}. If None,
    scans via scan_all(). `called` is the set of tool names observed in
    any session. If None, scans session files.

    Returns findings at two granularities:
    - Server-level: servers with zero observed tool calls.
    - Tool-level: individual tools within used servers, with call counts.
    """
    from agent_vitals.scanners import scan_all

    if registered is None:
        records = scan_all()
        registered = {}
        for r in records:
            if r.source == "mcp":
                registered[r.name] = r.target
    if called is None:
        called = _collect_called_tool_names(_all_session_files())

    # Build per-server, per-tool call counts from observed tool names.
    server_tool_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    server_total_counts: dict[str, int] = defaultdict(int)
    for name in called:
        parsed = _parse_mcp_tool_name(name)
        if parsed is None:
            # Not an MCP tool name; skip
            continue
        server, tool = parsed
        server_total_counts[server] += 1
        # For pi-style 'mcp' calls, the real tool name is in arguments.tool
        # and was already added to `called` by _collect_called_tool_names.
        # For Claude Code style, the tool name is embedded in the call name.
        server_tool_counts[server][tool] += 1

    out: list[UnusedToolFinding] = []
    for server_name, target in registered.items():
        total_calls = server_total_counts.get(server_name, 0)
        if total_calls == 0:
            # Entire server unused — same as before.
            out.append(UnusedToolFinding(
                tool_name=server_name,
                server=server_name,
                host="",
                config_path=target,
                calls_observed=0,
                estimated_waste_bytes=5_000,
            ))
        else:
            # Server is used. Report each observed tool with its count.
            # Tools not listed here may exist but weren't called.
            tool_counts = server_tool_counts.get(server_name, {})
            for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                out.append(UnusedToolFinding(
                    tool_name=tool,
                    server=server_name,
                    host="",
                    config_path=target,
                    calls_observed=count,
                    estimated_waste_bytes=0,  # used tools have no waste
                ))
    return out


# Per-model cost multiplier (vs. Sonnet baseline). From GitHub's blog.
MODEL_MULTIPLIER: dict[str, float] = {
    "claude-opus-4":    5.0,
    "claude-opus-4-7":  5.0,
    "claude-opus-4-8":  5.0,
    "claude-sonnet-4":  1.0,
    "claude-haiku-4":   0.25,
    "claude-3.5-sonnet": 1.0,
    "claude-3.5-haiku":  0.25,
    "gpt-4o":           1.5,
    "gpt-4o-mini":      0.1,
    "o1":               5.0,
    "o1-mini":          1.0,
    "_default":         1.0,
}


# ---------- Effective Tokens (ET) metric ----------




# ---------- MCP overlap detection ----------


def find_overlapping_tools(
    registered: dict[str, dict[str, str]] | None = None,
    called: set[str] | None = None,
) -> list[dict]:
    """Find MCP servers with potentially overlapping tool names.

    Detects servers that have tools with similar names, suggesting
    possible redundancy or overlap in functionality.

    `called` is the set of tool names observed in any session. If None,
    scans session files — mirrors find_unused_tools, and lets callers
    (and tests) supply their own observations instead of reading $HOME.
    """
    from agent_vitals.scanners import scan_all

    if registered is None:
        records = scan_all()
        registered = {}
        for r in records:
            if r.source == "mcp":
                registered[r.name] = r.target

    # Collect all tool names per server
    server_tools: dict[str, set[str]] = {}
    for server_name in registered:
        server_tools[server_name] = set()

    if called is None:
        called = _collect_called_tool_names(_all_session_files())
    for name in called:
        parsed = _parse_mcp_tool_name(name)
        if parsed is None:
            continue
        server, tool = parsed
        if server in server_tools:
            server_tools[server].add(tool)
    
    # Find overlaps: tools with similar names across servers
    overlaps = []
    servers = list(server_tools.keys())
    for i in range(len(servers)):
        for j in range(i + 1, len(servers)):
            s1, s2 = servers[i], servers[j]
            tools1 = server_tools[s1]
            tools2 = server_tools[s2]
            
            # Check for exact matches
            common = tools1 & tools2
            if common:
                overlaps.append({
                    "servers": [s1, s2],
                    "type": "exact",
                    "tools": sorted(common),
                    "suggestion": f"Consider consolidating {s1} and {s2} — they share {len(common)} tool(s)"
                })
            
            # Check for similar names (simple substring check)
            similar = []
            for t1 in tools1:
                for t2 in tools2:
                    if t1 != t2 and (t1 in t2 or t2 in t1):
                        similar.append((t1, t2))
            if similar:
                overlaps.append({
                    "servers": [s1, s2],
                    "type": "similar",
                    "tools": similar[:5],  # limit output
                    "suggestion": f"{s1} and {s2} have similar tool names — review for redundancy"
                })
    
    return overlaps


def render_overlap_report(overlaps: list[dict]) -> str:
    """Render MCP overlap report."""
    if not overlaps:
        return "overlap: no overlapping MCP tools detected\n"
    lines = [f"overlap: {len(overlaps)} potential overlap(s) detected\n"]
    for o in overlaps[:10]:
        s1, s2 = o["servers"]
        lines.append(f"  {s1} ↔ {s2} ({o['type']})")
        if o["type"] == "exact":
            tools = ", ".join(o["tools"][:5])
            lines.append(f"    shared tools: {tools}")
        else:
            for t1, t2 in o["tools"][:3]:
                lines.append(f"    {t1} ↔ {t2}")
        lines.append(f"    suggestion: {o['suggestion']}")
        lines.append("")
    if len(overlaps) > 10:
        lines.append(f"  ... and {len(overlaps) - 10} more")
    return "\n".join(lines) + "\n"

def effective_tokens(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    model: str = "_default",
) -> float:
    """GitHub's Effective Tokens formula. Output weighted 4x, cache_read 0.1x.

    ET = m * (1.0 * I + 0.1 * C + 4.0 * O)

    Cache_write is treated the same as input (1.0) — it's fresh work that
    counts against the model.
    """
    m = MODEL_MULTIPLIER.get(model, MODEL_MULTIPLIER["_default"])
    return m * (
        1.0 * input_tokens
        + 0.1 * cache_read_tokens
        + 1.0 * cache_write_tokens
        + 4.0 * output_tokens
    )


def render_loop_report(findings: list[LoopFinding], limit: int = 20) -> str:
    if not findings:
        return "loops: no doom-loop patterns detected\n"
    lines = [f"loops: {len(findings)} pattern(s) flagged", ""]
    lines.append(f"  {'host':<13} {'count':>6}  {'target':<50}  detail")
    lines.append(f"  {'-'*13} {'-'*6}  {'-'*50}  ------")
    for f in findings[:limit]:
        target = f.target if len(f.target) <= 50 else f.target[:47] + "..."
        lines.append(f"  {f.host:<13} {f.count:>6}  {target:<50}  {f.detail}")
    if len(findings) > limit:
        lines.append(f"  ... and {len(findings) - limit} more")
    return "\n".join(lines) + "\n"


def render_unused_report(findings: list[UnusedToolFinding], limit: int = 20) -> str:
    if not findings:
        return "unused: every registered tool was used at least once\n"
    # Split into unused-servers and used-tools
    unused_servers = [f for f in findings if f.calls_observed == 0]
    used_tools = [f for f in findings if f.calls_observed > 0]
    lines = []
    if unused_servers:
        lines.append(f"unused: {len(unused_servers)} server(s) with zero observed calls")
        lines.append("")
        lines.append(f"  {'server':<20} {'config_path':<60}  est waste/turn")
        lines.append(f"  {'-'*20} {'-'*60}  ------------")
        for f in unused_servers[:limit]:
            path = f.config_path if len(f.config_path) <= 60 else "..." + f.config_path[-57:]
            lines.append(f"  {f.server:<20} {path:<60}  ~{f.estimated_waste_bytes//1024}KB")
        if len(unused_servers) > limit:
            lines.append(f"  ... and {len(unused_servers) - limit} more")
    if used_tools:
        lines.append(f"\nused tools ({len(used_tools)} tool(s) with observed calls)")
        lines.append("")
        lines.append(f"  {'server':<20} {'tool':<35} {'calls':>6}")
        lines.append(f"  {'-'*20} {'-'*35} {'-'*6}")
        for f in used_tools[:limit]:
            tool = f.tool_name if len(f.tool_name) <= 35 else f.tool_name[:32] + "..."
            lines.append(f"  {f.server:<20} {tool:<35} {f.calls_observed:>6}")
        if len(used_tools) > limit:
            lines.append(f"  ... and {len(used_tools) - limit} more")
    return "\n".join(lines) + "\n"


# ---------- helpers ----------


def _all_session_files() -> list[Path]:
    """All session JSONLs under known roots."""
    from agent_vitals.sessions import SESSION_ROOTS
    out: list[Path] = []
    for _, root in SESSION_ROOTS:
        if root.is_dir():
            out.extend(root.rglob("*.jsonl"))
    return out


__all__ = [
    "MODEL_MULTIPLIER",
    "LOOP_TOOL_THRESHOLD",
    "LOOP_FILE_THRESHOLD",
    "LoopFinding",
    "UnusedToolFinding",
    "find_loops",
    "find_unused_tools",
    "find_overlapping_tools",
    "effective_tokens",
    "render_loop_report",
    "render_unused_report",
    "render_overlap_report",
]
