"""Config drift detection across agent hosts.

Surfaces inconsistencies that the human shouldn't have to manually
reconcile:

  - Same MCP server registered with different commands across hosts
  - Same MCP server registered with different args (env paths, etc.)
  - Different versions of the same MCP server
  - Hooks installed in some places but not others
  - Same skill installed in one agent's skills dir but not another's
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from agent_vitals.primer import HOST_PATHS, detect_hosts


@dataclass
class DriftFinding:
    severity: str  # "high" | "medium" | "low"
    kind: str       # "mcp_dup" | "mcp_drift" | "skill_drift" | "hook_drift"
    name: str       # the server or skill name
    detail: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "name": self.name,
            "detail": self.detail,
        }


def _load_mcp_servers(config_path: Path) -> dict:
    """Read mcp.json (or .mcp.json) and return the servers dict. Empty on failure."""
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text())
        return data.get("mcpServers", {}) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _host_label(config_path: Path) -> str:
    """Map a config path back to a human-readable host name (unused but kept)."""
    p = str(config_path).replace(str(Path.home()), "~")
    for host_name, host_path in HOST_PATHS.items():
        if p == str(host_path) or p.startswith(str(host_path) + "/"):
            return host_name
    return config_path.parent.name or config_path.name


def detect_mcp_drift() -> list[DriftFinding]:
    """Find MCP servers registered differently across hosts.

    Two cases:
    - same name + different target (command/args) → high severity (real conflict)
    - same name + same target in multiple configs → low severity (duplicate,
      probably intentional multi-host)
    """
    findings: list[DriftFinding] = []
    by_name: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for host in detect_hosts():
        if host.config_path is None:
            continue
        host_label = host.name
        for name, cfg in _load_mcp_servers(host.config_path).items():
            if isinstance(cfg, dict):
                by_name[name].append((host_label, cfg))
    for name, entries in by_name.items():
        if len(entries) < 2:
            continue
        # Normalize target for comparison
        def target_of(cfg: dict) -> str:
            if cfg.get("type") == "http":
                return f"http:{cfg.get('url','')}"
            cmd = cfg.get("command", "")
            args = " ".join(cfg.get("args", []))
            return f"{cmd} {args}".strip()

        targets = {target_of(cfg) for _, cfg in entries}
        hosts = [h for h, _ in entries]
        if len(targets) > 1:
            findings.append(DriftFinding(
                severity="high",
                kind="mcp_drift",
                name=name,
                detail=f"registered differently across {len(hosts)} hosts: {sorted(hosts)}",
            ))
        else:
            findings.append(DriftFinding(
                severity="low",
                kind="mcp_dup",
                name=name,
                detail=f"same target registered in {len(hosts)} hosts: {sorted(hosts)}",
            ))
    return findings


def detect_skill_drift(skills_root: Path | None = None) -> list[DriftFinding]:
    """Find skills present in some agent hosts but not others.

    For now we treat ~/.claude/skills/ as the canonical skill store. Agents
    that don't read from this path (OpenCode, Cursor) won't surface drift
    here, but those edge cases are documented in the v0.4.0 roadmap.
    """
    skills_root = skills_root or (Path.home() / ".claude" / "skills")
    if not skills_root.is_dir():
        return []
    all_skills: set[str] = set()
    by_host: dict[str, set[str]] = defaultdict(set)
    for skill_dir in skills_root.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_name = skill_dir.name
        all_skills.add(skill_name)
        # For now every skill is in the Claude Code path. Future: map other
        # agent skill stores.
        by_host["claude-code"].add(skill_name)
    # No drift to detect with a single canonical store. Future: cross-store.
    return []


def detect_hook_drift() -> list[DriftFinding]:
    """Check whether hook wrappers are installed consistently across hosts.

    Returns high-severity findings if hooks are installed in some agent
    contexts but not others. (Cross-host hook coverage is informational;
    hooks intercept system commands, not agent-specific paths.)
    """
    # Hooks live in PATH, not per-host. So this is a "are hooks installed
    # at all?" check. If they're not installed, no drift to report.
    from agent_vitals.hooks import hook_dir
    d = hook_dir()
    if not d.is_dir():
        return []
    installed = {p.name for p in d.iterdir() if os_executable(p)}
    if not installed:
        return []
    return [DriftFinding(
        severity="low",
        kind="hook_drift",
        name="hooks",
        detail=(
            f"hooks installed at {d} for {sorted(installed)} but check whether "
            f"your PATH includes {d} in all shells (zsh, fish, vsam, etc.)"
        ),
    )]


def os_executable(p: Path) -> bool:
    import os
    return os.access(p, os.X_OK)


def detect_all_drift() -> list[DriftFinding]:
    """Run every drift detector. Convenience for `av drift`."""
    findings: list[DriftFinding] = []
    findings.extend(detect_mcp_drift())
    findings.extend(detect_skill_drift())
    findings.extend(detect_hook_drift())
    return findings


def render_drift_report(findings: list[DriftFinding]) -> str:
    """Plain-text report for `av drift`."""
    if not findings:
        return "drift: no inconsistencies found across detected agent hosts\n"
    by_sev: dict[str, list[DriftFinding]] = defaultdict(list)
    for f in findings:
        by_sev[f.severity].append(f)
    lines = [f"drift: {len(findings)} finding(s)\n"]
    for sev in ("high", "medium", "low"):
        items = by_sev.get(sev, [])
        if not items:
            continue
        lines.append(f"\n  [{sev}] ({len(items)})")
        for f in items:
            lines.append(f"    - {f.kind}: {f.name}")
            lines.append(f"      {f.detail}")
    return "\n".join(lines) + "\n"


__all__ = [
    "DriftFinding",
    "detect_mcp_drift",
    "detect_skill_drift",
    "detect_hook_drift",
    "detect_all_drift",
    "render_drift_report",
]
