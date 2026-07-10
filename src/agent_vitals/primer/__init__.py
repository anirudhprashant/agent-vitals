"""Detect installed agent hosts and install agent-vitals priming.

`av init` runs this:
1. Scan for known agent hosts (pi, Claude Code, OpenCode, Cursor, Codex CLI)
2. For each detected host:
   a. Merge agent-vitals MCP entry into its mcp.json (idempotent)
   b. Install the SKILL.md / rule snippet in the right location
3. Print a summary of what was done

Re-running `av init` is safe — existing entries are skipped, never duplicated.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Marker used to detect our own prior installation in a rule file.
SKILL_MARKER = "<!-- agent-vitals: installed by `av init` -->"
MCP_SERVER_NAME = "agent-vitals"
MCP_COMMAND = "av"
MCP_ARGS = ["mcp"]


@dataclass
class AgentHost:
    name: str
    config_path: Path | None  # mcp config path; None if not detected
    config_format: str        # "json" or "toml"
    skill_dir: Path | None    # directory for SKILL.md; None if N/A
    rule_file: Path | None    # file for rule snippet (OpenCode/Cursor/Codex)


def detect_hosts() -> list[AgentHost]:
    """Return all detected agent hosts on this machine."""
    home = Path.home()
    candidates: list[AgentHost] = [
        AgentHost(
            name="pi",
            config_path=home / ".pi/agent/mcp.json",
            config_format="json",
            skill_dir=home / ".claude/skills/agent-vitals",
            rule_file=None,
        ),
        AgentHost(
            name="Claude Code",
            config_path=home / ".claude/.mcp.json",
            config_format="json",
            skill_dir=home / ".claude/skills/agent-vitals",
            rule_file=None,
        ),
        AgentHost(
            name="Cursor",
            config_path=home / ".cursor/mcp.json",
            config_format="json",
            skill_dir=None,
            rule_file=home / ".cursor/rules/agent-vitals.md",
        ),
        AgentHost(
            name="OpenCode",
            config_path=home / ".config/opencode/mcp.json",
            config_format="json",
            skill_dir=None,
            rule_file=home / ".config/opencode/AGENTS.md",
        ),
        AgentHost(
            name="Codex CLI",
            config_path=home / ".codex/config.toml",
            config_format="toml",
            skill_dir=None,
            rule_file=home / ".codex/AGENTS.md",
        ),
    ]
    return [h for h in candidates if h.config_path is not None and h.config_path.exists()]


# ---------- JSON configs ----------


def _read_json(path: Path) -> dict | None:
    """Read JSON; return None if the file isn't valid JSON (defensive)."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_json_mcp(config_path: Path) -> str:
    """Merge agent-vitals MCP entry into a JSON mcp.json. Returns status."""
    data = _read_json(config_path)
    if data is None:
        return "skip — not valid JSON (use a TOML-aware installer)"
    if not isinstance(data, dict):
        return f"skip — unexpected JSON shape: {type(data).__name__}"
    servers = data.setdefault("mcpServers", {})
    if MCP_SERVER_NAME in servers:
        return "already configured"
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(config_path, backup)
    servers[MCP_SERVER_NAME] = {
        "type": "stdio",
        "command": MCP_COMMAND,
        "args": MCP_ARGS,
        "lifecycle": "lazy",
    }
    _write_json(config_path, data)
    return "added"


# ---------- TOML configs ----------


def install_toml_mcp(config_path: Path) -> str:
    """Append [mcp_servers.agent-vitals] to a TOML config. Returns status."""
    raw = config_path.read_text()
    section_header = f"[mcp_servers.{MCP_SERVER_NAME}]"
    if section_header in raw:
        return "already configured"
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(config_path, backup)
    block = (
        f"\n{section_header}\n"
        f'type = "stdio"\n'
        f'command = "{MCP_COMMAND}"\n'
        f"args = {json.dumps(MCP_ARGS)}\n"
    )
    with config_path.open("a") as f:
        f.write(block)
    return "added"


def install_mcp_entry(host: AgentHost) -> str:
    """Dispatch to JSON or TOML installer based on host.config_format."""
    if host.config_format == "json":
        return install_json_mcp(host.config_path)  # type: ignore[arg-type]
    if host.config_format == "toml":
        return install_toml_mcp(host.config_path)  # type: ignore[arg-type]
    return f"unknown config_format: {host.config_format}"


# ---------- skill / rule ----------


def _read_asset(name: str) -> str:
    """Read an asset file bundled with the package."""
    pkg_root = Path(__file__).parent
    return (pkg_root / "assets" / name).read_text()


def install_skill(skill_dir: Path) -> str:
    """Install SKILL.md into the given directory. Returns status string."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    target = skill_dir / "SKILL.md"
    if target.exists():
        return "already installed"
    target.write_text(_read_asset("agent-vitals/SKILL.md"))
    return "installed"


def _rule_block() -> str:
    """Plain-text version of the skill, for hosts that don't use SKILL.md."""
    body = _read_asset("agent-vitals/SKILL.md")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end > 0:
            body = body[end + 4 :].lstrip()
    return f"{SKILL_MARKER}\n\n{body}\n"


def install_rule(rule_file: Path) -> str:
    """Append the rule to the rule file (creating it if needed). Returns status."""
    rule_file.parent.mkdir(parents=True, exist_ok=True)
    block = _rule_block()
    if rule_file.exists():
        existing = rule_file.read_text()
        if SKILL_MARKER in existing:
            return "already installed"
        rule_file.write_text(existing.rstrip() + "\n\n" + block)
        return "appended"
    rule_file.write_text(block)
    return "installed"


# ---------- public entry ----------


def init_all() -> list[tuple[AgentHost, dict[str, str]]]:
    """Detect hosts and install everywhere. Returns per-host action summary."""
    hosts = detect_hosts()
    results: list[tuple[AgentHost, dict[str, str]]] = []
    for host in hosts:
        actions: dict[str, str] = {}
        if host.config_path is not None:
            actions["mcp"] = install_mcp_entry(host)
        if host.skill_dir is not None:
            actions["skill"] = install_skill(host.skill_dir)
        if host.rule_file is not None:
            actions["rule"] = install_rule(host.rule_file)
        results.append((host, actions))
    return results


def list_hosts() -> list[AgentHost]:
    """Public: what hosts we know how to detect."""
    return detect_hosts()