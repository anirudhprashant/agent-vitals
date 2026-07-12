"""Discover everything scheduled or configured to act on your behalf.

Scanners return ShadowRecord lists. Each scanner fails gracefully on
missing tools / unreadable files — the union is the full shadow surface.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


@dataclass
class ShadowRecord:
    name: str
    source: str  # "cron" | "systemd" | "mcp" | "skill"
    schedule: str
    target: str
    kill_hint: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_run(cmd: list[str], timeout: int = 5) -> str | None:
    """Run a command, return stdout or None on any failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _path_exists(p: str) -> bool:
    if p.startswith("/"):
        return Path(p).exists()
    return shutil.which(p.split()[0]) is not None


# ---------- cron ----------


def scan_crontab() -> list[ShadowRecord]:
    """Scan the user's crontab."""
    out = _safe_run(["crontab", "-l"])
    if not out:
        return []
    records: list[ShadowRecord] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Skip env assignments (PATH=..., SHELL=..., etc.)
        if re.match(r"^[A-Z_][A-Z0-9_]*\s*=", line):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]
        # Name = first path segment or first word
        first = command.split()[0]
        name = Path(first).name if first.startswith("/") else first
        note = ""
        if not _path_exists(first):
            note = f"⚠ target missing: {first}"
        records.append(
            ShadowRecord(
                name=name,
                source="cron",
                schedule=schedule,
                target=command,
                kill_hint=f"crontab -e  # remove line for {name}",
                note=note,
            )
        )
    return records


# ---------- systemd user timers ----------


def _format_left(left_us: int | float) -> str:
    s = float(left_us) / 1_000_000
    if s < 60:
        return f"in {int(s)}s"
    if s < 3600:
        return f"in {int(s // 60)}m"
    if s < 86400:
        return f"in {s / 3600:.1f}h"
    return f"in {s / 86400:.1f}d"


def scan_systemd_timers() -> list[ShadowRecord]:
    """Scan systemd user timers (gracefully empty on non-systemd / macOS).

    Note: on systemd v255 the JSON `left` field equals `next` (an absolute
    microsecond timestamp), not a duration. We compute remaining time from
    `next_usec - now_usec` to stay correct across systemd versions.
    """
    out = _safe_run(["systemctl", "--user", "list-timers", "--output=json", "--no-pager"])
    if not out:
        return []
    try:
        timers = json.loads(out)
    except json.JSONDecodeError:
        return []
    now_usec = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    records: list[ShadowRecord] = []
    for t in timers:
        unit = t.get("unit", "")
        if not unit.endswith(".timer"):
            continue
        name = unit[: -len(".timer")]
        activates = t.get("activates", "")
        # systemd v255 emits "left" == "next" (an absolute timestamp).
        # Compute the real remaining time ourselves.
        next_usec = int(t.get("next") or 0)
        remaining_usec = next_usec - now_usec if next_usec else 0
        schedule = _format_left(remaining_usec)
        note = ""
        # Look for the .service unit file
        svc_paths = [
            Path.home() / f".config/systemd/user/{activates}",
            Path(f"/etc/systemd/user/{activates}"),
            Path(f"/usr/lib/systemd/user/{activates}"),
        ]
        if not any(p.exists() for p in svc_paths):
            note = f"⚠ service unit not found: {activates}"
        records.append(
            ShadowRecord(
                name=name,
                source="systemd",
                schedule=schedule,
                target=f"{unit} → {activates}" if activates else unit,
                kill_hint=f"systemctl --user disable --now {unit}",
                note=note,
            )
        )
    return records


# ---------- MCP servers ----------


def _read_mcp_json(path: Path) -> list[ShadowRecord]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    servers = data.get("mcpServers", {}) or {}
    records: list[ShadowRecord] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("type") == "http":
            target = cfg.get("url", "")
            schedule = "always-on (http)"
        else:
            cmd = cfg.get("command", "")
            args = " ".join(cfg.get("args", []))
            target = f"{cmd} {args}".strip()
            schedule = "always-on (lazy)" if cfg.get("lifecycle") == "lazy" else "always-on"
        records.append(
            ShadowRecord(
                name=name,
                source="mcp",
                schedule=schedule,
                target=target,
                kill_hint=f"remove '{name}' from {path}",
                note=f"from {path}",
            )
        )
    return records


def scan_mcp_servers() -> list[ShadowRecord]:
    """Scan known MCP server config locations.

    No dedupe across hosts — each agent host (pi / Claude Code / Cursor /
    OpenCode) needs its own registration of every MCP server, so the same
    name appearing in two configs is normal, not a duplicate. The `from <path>`
    note in each record tells the user where the registration lives.
    """
    candidates = [
        Path.home() / ".pi/agent/mcp.json",
        Path.home() / ".claude/.mcp.json",
        Path.home() / ".cursor/mcp.json",
        Path.home() / ".config/claude/mcp.json",
    ]
    raw: list[ShadowRecord] = []
    for path in candidates:
        raw.extend(_read_mcp_json(path))
    return raw


# ---------- agent skills ----------


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def scan_skills() -> list[ShadowRecord]:
    """Scan agent skills for schedule/cron frontmatter triggers."""
    skills_dir = Path.home() / ".claude/skills"
    if not skills_dir.exists():
        return []
    records: list[ShadowRecord] = []
    for skill_md in skills_dir.glob("*/SKILL.md"):
        try:
            text = skill_md.read_text(errors="replace")
        except OSError:
            continue
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        schedule = meta.get("schedule") or meta.get("cron") or meta.get("interval")
        if not schedule:
            continue
        name = skill_md.parent.name
        records.append(
            ShadowRecord(
                name=name,
                source="skill",
                schedule=str(schedule),
                target=str(skill_md),
                kill_hint=f"remove schedule field from {skill_md.name}",
            )
        )
    return records


# ---------- union ----------


def scan_all() -> list[ShadowRecord]:
    """Run every scanner and return the combined list, grouped by source."""
    records: list[ShadowRecord] = []
    records.extend(scan_crontab())
    records.extend(scan_systemd_timers())
    records.extend(scan_mcp_servers())
    records.extend(scan_skills())
    # Stable order: source, then name
    records.sort(key=lambda r: (r.source, r.name))
    return records