"""Snapshot (backup) of agent state.

Creates a timestamped tarball of every agent config we know about:
  - mcp.json files (all hosts)
  - SKILL.md frontmatter (pi/Claude Code skills)
  - hook wrappers
  - this project's own state

Stored under ~/agent-vitals-snapshots/. Restore is manual (extract
the tarball) — we don't auto-write back to live configs.
"""

from __future__ import annotations

import os
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from agent_vitals.primer import detect_hosts
from agent_vitals.hooks import hook_dir

SNAPSHOT_ROOT = Path.home() / "agent-vitals-snapshots"


@dataclass
class SnapshotInfo:
    path: Path
    size_bytes: int
    mtime: float
    num_files: int

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "num_files": self.num_files,
        }


def _default_targets() -> list[Path]:
    """Files and dirs to include in a snapshot."""
    targets: list[Path] = []
    # MCP configs (all detected hosts)
    for host in detect_hosts():
        if host.config_path is not None and host.config_path.exists():
            targets.append(host.config_path)
    # Hook wrappers
    hdir = hook_dir()
    if hdir.is_dir():
        targets.append(hdir)
    # Skills store (Claude Code)
    skills = Path.home() / ".claude" / "skills"
    if skills.is_dir():
        targets.append(skills)
    return targets


def _sanitize_tarinfo(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Make tar entries safe: strip absolute paths, drop uid/gid."""
    ti.uid = 0
    ti.gid = 0
    ti.uname = ""
    ti.gname = ""
    # Convert absolute path to relative (relative to parent of SNAPSHOT_ROOT)
    name = ti.name.lstrip("/")
    if name.startswith(str(SNAPSHOT_ROOT) + "/"):
        # Strip the snapshot root prefix
        rel = os.path.relpath(ti.name, SNAPSHOT_ROOT)
        if not rel.startswith(".."):
            ti.name = rel
    return ti


def create_snapshot(
    label: str | None = None,
    targets: list[Path] | None = None,
) -> SnapshotInfo:
    """Create a tar.gz of agent state. Returns the new snapshot's info."""
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label_part = f"-{label}" if label else ""
    name = f"agent-vitals-snapshot-{ts}{label_part}.tar.gz"
    out_path = SNAPSHOT_ROOT / name

    targets = targets if targets is not None else _default_targets()
    if not targets:
        raise RuntimeError(
            "no snapshot targets found — install at least one agent first"
        )

    file_count = 0
    with tarfile.open(out_path, "w:gz") as tf:
        for t in targets:
            if not t.exists():
                continue
            if t.is_file():
                tf.add(str(t), arcname=f"snapshot/{t.name}", filter=_sanitize_tarinfo)
                file_count += 1
            elif t.is_dir():
                base = t.parent
                for child in t.rglob("*"):
                    if child.is_file() and SNAPSHOT_ROOT not in child.parents:
                        try:
                            arc = f"snapshot/{child.relative_to(base)}"
                        except ValueError:
                            continue
                        tf.add(str(child), arcname=arc, filter=_sanitize_tarinfo)
                        file_count += 1
    stat = out_path.stat()
    return SnapshotInfo(
        path=out_path,
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
        num_files=file_count,
    )


def list_snapshots() -> list[SnapshotInfo]:
    """Return all snapshots, newest first."""
    if not SNAPSHOT_ROOT.is_dir():
        return []
    out: list[SnapshotInfo] = []
    for f in SNAPSHOT_ROOT.glob("agent-vitals-snapshot-*.tar.gz"):
        try:
            stat = f.stat()
        except OSError:
            continue
        # Cheap file count via reading the tar
        try:
            with tarfile.open(f, "r:gz") as tf:
                count = sum(1 for _ in tf.getmembers() if _.isfile())
        except (tarfile.TarError, OSError):
            count = 0
        out.append(SnapshotInfo(
            path=f,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            num_files=count,
        ))
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def render_snapshot_list(snapshots: list[SnapshotInfo], limit: int = 10) -> str:
    if not snapshots:
        return f"snapshots: none in {SNAPSHOT_ROOT}\n"
    lines = [f"snapshots: {len(snapshots)} archive(s) in {SNAPSHOT_ROOT}"]
    lines.append(f"  {'date':<22}  {'size':>10}  {'files':>6}  path")
    lines.append(f"  {'-'*22}  {'-'*10}  {'-'*6}  ----")
    for s in snapshots[:limit]:
        date = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(s.mtime))
        lines.append(f"  {date:<22}  {s.size_bytes/1024:>8.1f}K  {s.num_files:>6}  {s.path.name}")
    if len(snapshots) > limit:
        lines.append(f"  ... and {len(snapshots) - limit} more")
    return "\n".join(lines) + "\n"


__all__ = [
    "SnapshotInfo",
    "SNAPSHOT_ROOT",
    "create_snapshot",
    "list_snapshots",
    "render_snapshot_list",
]
