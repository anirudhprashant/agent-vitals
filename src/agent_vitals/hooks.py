"""Hook logic for v0.3.0 pre-action gates.

Two binaries are gated:

- `crontab` — anything not a pure read.
- `systemctl --user` — only mutating subcommands (enable, disable, start, stop,
  restart, reload, kill, mask, unmask, daemon-reload, etc.).

Reads are NEVER gated. The user can always do `crontab -l`, `systemctl status`,
etc. without touching vitals.

The `gate()` function is invoked by wrapper scripts installed at
`~/.local/bin/av-hooks/<bin>` (one template, bin-name from $0). The wrapper
forks to `av hooks gate <bin> ...argv`, which checks the stamp and either
delegates to the real binary (allowed) or prints a refusal and exits 126.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agent_vitals.stamp import describe_age, read_age, should_gate


# ---------- mutation detection ----------

# Args accepted by crontab that DO NOT mutate state.
_CRON_READ_FLAGS = {"-l", "-h", "--help", "-V", "--version"}


def crontab_is_mutation(argv: list[str]) -> bool:
    """Detect whether crontab args attempt a mutation.

    Algorithm:
      1. Bare `crontab` (no args) is a mutation (opens editor).
      2. If any flag is a known mutation flag (-e, -r, -i), it's a mutation.
      3. If a known read flag (-l, -h, --help, -V, --version) is anywhere,
         it's a read.
      4. Otherwise conservative: positional args, unknown flags, or only
         `-u <user>` (no other read intent visible) → mutation.

    Examples (mutations):
      crontab, crontab -e, crontab -r, crontab -i, crontab -u root,
      crontab <file>, crontab -, crontab -u root -e, crontab -le,
      crontab -X

    Examples (reads):
      crontab -l, crontab -l -u root, crontab -u root -l, --help, -V
    """
    args = list(argv)
    if not args:
        return True  # bare crontab opens the editor

    known_mutation = {"-e", "-r", "-i"}

    # Strip -u <value> pairs so the value isn't mistaken for a flag.
    options: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-u":
            if i + 1 >= len(args):
                return True  # -u with no value: malformed → conservative mutation
            i += 2
            continue
        options.append(a)
        i += 1

    # Look for any mutating or read flag. Order: mutation beats read.
    for o in options:
        # Positional argument (no leading -) → mutation.
        if not o.startswith("-"):
            return True
        # Long option
        if o.startswith("--"):
            if o in _CRON_READ_FLAGS:
                return False  # explicit read intent
            return True  # unknown long → conservative
        # Bundled short options like -lu, -le, -lh
        if len(o) > 2:
            saw_read = False
            for ch in o[1:]:
                flag = f"-{ch}"
                if flag in known_mutation:
                    return True
                # Bundled -u doesn't make sense in crontab; treat as mutation
                if flag == "-u":
                    return True
                if flag not in _CRON_READ_FLAGS:
                    return True  # unknown short flag → mutation
                saw_read = True
            if saw_read:
                return False
            continue
        # Single short flag
        if o in known_mutation:
            return True
        if o in _CRON_READ_FLAGS:
            return False
        return True  # unknown short → conservative

    # No flags left after stripping -u pairs → conservative mutation.
    return True


# systemctl subcommands that read-only operations.
_SYSTEMCTL_READ_SUBCOMMANDS = {
    "status", "show", "cat", "list-units", "list-unit-files",
    "list-jobs", "list-dependencies", "list-sockets",
    "list-timers", "list-machines", "list-installed", "list",
    "is-active", "is-enabled", "is-failed", "is-system-running",
    "help", "version", "get-property", "list-unit-names",
}

# Subcommands that change service/unit state. ADD to this list to expand
# the gate's coverage to new subcommands.
_SYSTEMCTL_MUTATING_SUBCOMMANDS = {
    # Lifecycle
    "start", "stop", "restart", "try-restart", "reload",
    "reload-or-restart", "reload-or-try-restart",
    # Unit enablement
    "enable", "disable", "reenable", "preset", "preset-all",
    "mask", "unmask",
    # System state
    "daemon-reload", "daemon-reexec",
    # Direct invocation
    "kill",
    # Filesystem links for unit files
    "link", "unset-environment",
}

# Power-management subcommands that are intentionally NOT gated. A user
# must always be able to reboot/poweroff even when vitals is stale.
# Add to _SYSTEMCTL_MUTATING_SUBCOMMANDS if you want to gate them.
_SYSTEMCTL_POWER_MGMT = {
    "poweroff", "reboot", "halt", "suspend", "hibernate",
    "hybrid-sleep", "kexec", "rescue", "emergency-action",
    "cancel-action", "suspend-then-hibernate",
}

# systemd global flags. BOOLEAN_FLAGS take no value; VALUE_FLAGS consume one.
_SYSTEMCTL_BOOLEAN_FLAGS = {
    "--user", "--global", "--system", "--no-block",
    "--no-ask-password", "--quiet", "--no-warn", "--full",
    "--recursive", "--no-reload", "--failed", "--all",
    "--show-types", "--legend", "--no-pager",
    "--plain", "--runtime", "--firmware",
    "--allow-vendor", "--force", "--ask-password",
    "--check-inhibitors", "--read-only",
}
_SYSTEMCTL_VALUE_FLAGS = {
    "--root", "--image", "-H", "--host",
    "--property", "-p",
    "--filter-property", "--firmware-setup",
    "--boot-loader-menu", "--boot-loader-entry",
    "--firmware-loader", "--kill-who", "--signal",
    "--after", "--before", "--since", "--until",
    "--job-mode", "--watermark",
    "--output", "-o",
}


def systemctl_is_mutation(argv: list[str]) -> bool:
    """Did these systemctl args attempt a state mutation?

    Reads (list-*, status, show, cat, is-*, help, etc.) are not mutations.
    Power-management verbs (reboot, poweroff, etc.) are intentionally
    NOT gated — see _SYSTEMCTL_POWER_MGMT.
    """
    args = list(argv)
    i = 0
    # Strip global flags, consuming values for value-flags.
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        # `--foo=value` form: one arg, no further consumption.
        if "=" in a:
            i += 1
            continue
        if a in _SYSTEMCTL_VALUE_FLAGS:
            # Consume the next arg as the value (if there is one).
            if i + 1 >= len(args):
                return True  # value flag with no value — conservative
            i += 2
            continue
        if a in _SYSTEMCTL_BOOLEAN_FLAGS:
            i += 1
            continue
        # Unknown flag — conservative mutation.
        return True

    if i >= len(args):
        # bare `systemctl` with no subcommand = overall status read
        return False
    sub = args[i]
    if sub.startswith("-"):
        # bare flags with no subcommand = status-ish
        return False
    if sub in _SYSTEMCTL_READ_SUBCOMMANDS:
        return False
    if sub in _SYSTEMCTL_POWER_MGMT:
        return False  # explicit allow — NOT gated
    if sub in _SYSTEMCTL_MUTATING_SUBCOMMANDS:
        return True
    # Unknown subcommand — conservative: treat as mutation.
    return True


def is_mutation(binary: str, argv: list[str]) -> bool:
    """Top-level dispatch."""
    if binary == "crontab":
        return crontab_is_mutation(argv)
    if binary == "systemctl":
        return systemctl_is_mutation(argv)
    # Unknown binary — conservative: gate.
    return True


# ---------- real binary resolution ----------


# System paths tried as a final fallback when PATH lookup can't find the
# real binary (e.g. user doesn't have /usr/bin in their PATH).
_SYSTEM_PATHS = ("/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin", "/sbin")


def resolve_real_binary(bin_name: str) -> str | None:
    """Find the real binary that our wrapper in `~/.local/bin/av-hooks/` shadows.

    Strips the hook dir from PATH first, then falls back to system paths.
    """
    path = os.environ.get("PATH", "")
    hook_dir_str = str(hook_dir())
    cleaned_parts = [p for p in path.split(":") if p and p != hook_dir_str]
    cleaned = ":".join(cleaned_parts)
    found = shutil.which(bin_name, path=cleaned)
    if found:
        return found
    # Last-resort: scan system paths.
    for prefix in _SYSTEM_PATHS:
        candidate = Path(prefix) / bin_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# ---------- gate (called by wrappers) ----------


def gate(binary: str, argv: list[str]) -> int:
    """Called from a wrapper script via `av hooks gate <bin> ...`.

    1. Read-only invocations bypass the gate entirely.
    2. Otherwise check the stamp:
       - VITALS_BYPASS=1 → delegate
       - stamp fresh (within VITALS_GATE_WINDOW, default 60s) → delegate
       - otherwise → print refusal, exit 126.

    Returns the real process exit code (0 / 1 / etc.) on delegation, or
    126 on refusal.
    """
    if not is_mutation(binary, argv):
        # Reads are unconditional.
        real = resolve_real_binary(binary)
        if real is None:
            sys.stderr.write(f"av-hooks: cannot find real `{binary}` binary\n")
            return 127
        return subprocess.call([real, *argv])

    gated, reason = should_gate()
    if not gated:
        real = resolve_real_binary(binary)
        if real is None:
            sys.stderr.write(f"av-hooks: cannot find real `{binary}` binary\n")
            return 127
        return subprocess.call([real, *argv])

    # REFUSE.
    age = read_age()
    age_str = describe_age(age) if age is not None else "never"
    argv_str = " ".join(argv)
    sys.stderr.write(
        f"\n  ⚡ agent-vitals hook: refused `{binary} {argv_str}`\n"
        f"\n"
        f"  reason:   {reason}\n"
        f"  stamp:    {age_str}\n"
        f"\n"
        f"  refresh:  call any vitals tool (the MCP server does this automatically\n"
        f"            for pi/Claude Code/etc.) or run `av doctor` to refresh now.\n"
        f"  bypass:   VITALS_BYPASS=1 {binary} {argv_str}\n"
        f"  status:   av hooks status\n"
        f"\n"
    )
    return 126


# ---------- install / uninstall / status ----------


def hook_dir() -> Path:
    return Path.home() / ".local" / "bin" / "av-hooks"


WRAPPER_BINARY_NAMES: tuple[str, ...] = ("crontab", "systemctl")


def _template_path() -> Path:
    """Where the bundled wrapper.sh template lives in the installed package."""
    import agent_vitals
    return Path(agent_vitals.__file__).parent / "install" / "hooks" / "wrapper.sh"


def install_wrappers(bin_names: tuple[str, ...] = WRAPPER_BINARY_NAMES) -> dict[str, str]:
    """Install wrapper scripts in `~/.local/bin/av-hooks/<bin>`.

    Idempotent. If a .disabled file exists, removing it (re-activate).

    Returns: dict mapping bin name → status (`installed` | `reactivated` |
    `already_active` | `template_missing`).
    """
    src = _template_path()
    if not src.exists():
        return {n: "template_missing" for n in bin_names}
    template = src.read_text()
    target_dir = hook_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for name in bin_names:
        active = target_dir / name
        disabled = target_dir / f"{name}.disabled"
        already = active.exists()
        if disabled.exists():
            disabled.unlink()
            results[name] = "reactivated"
        elif already:
            results[name] = "already_active"
        else:
            active.write_text(template)
            active.chmod(0o755)
            results[name] = "installed"
    return results


def uninstall_wrappers(bin_names: tuple[str, ...] = WRAPPER_BINARY_NAMES) -> list[str]:
    """Remove all wrappers (active + .disabled). Returns names removed."""
    target_dir = hook_dir()
    removed: list[str] = []
    for name in bin_names:
        for suffix in ("", ".disabled"):
            p = target_dir / f"{name}{suffix}"
            if p.exists():
                p.unlink()
                removed.append(name)
    return sorted(set(removed))


def set_wrappers_state(enable: bool, bin_names: tuple[str, ...] = WRAPPER_BINARY_NAMES) -> list[str]:
    """Enable (rename .disabled → active) or disable (rename active → .disabled)."""
    target_dir = hook_dir()
    changed: list[str] = []
    for name in bin_names:
        active = target_dir / name
        disabled = target_dir / f"{name}.disabled"
        if enable and disabled.exists():
            disabled.rename(active)
            active.chmod(0o755)
            changed.append(name)
        elif not enable and active.exists():
            active.rename(disabled)
            changed.append(name)
    return changed


def status_report() -> str:
    """Plain-text status used by `av hooks status`."""
    age = read_age()
    if age is None:
        stamp_line = "stamp:    none on record"
    else:
        stamp_line = f"stamp:    {describe_age(age)} old"

    bypass = os.environ.get("VITALS_BYPASS", "").strip() in {"1", "true", "yes"}
    bypass_line = f"bypass:   {'ON (VITALS_BYPASS=1) — gates disabled' if bypass else 'off'}"

    win = os.environ.get("VITALS_GATE_WINDOW", "60")
    window_line = f"window:   {win}s"

    d = hook_dir()
    wrappers: list[str] = []
    if d.is_dir():
        for f in sorted(d.iterdir()):
            name = f.name
            if name.endswith(".disabled"):
                wrappers.append(f"  - {name.removesuffix('.disabled')} (DISABLED — bypasses gate)")
            elif os.access(f, os.X_OK):
                wrappers.append(f"  - {name} (active)")
    if not wrappers:
        wrappers = ["  (none — install via `av hooks install`)"]

    return (
        "agent-vitals hooks status\n"
        "\n"
        f"  {stamp_line}\n"
        f"  {bypass_line}\n"
        f"  {window_line}\n"
        f"  install:  {d}\n"
        "  wrappers:\n"
        + "\n".join(wrappers)
        + "\n"
    )


def shell_path_snippet() -> str:
    """One-liner the user adds to their shell config for PATH precedence."""
    d = hook_dir()
    return (
        "# agent-vitals hooks — gates crontab and systemctl on a fresh vitals stamp.\n"
        "# Install via: av hooks install.  Remove via: av hooks uninstall.\n"
        f'export PATH="{d}:$PATH"\n'
    )


def auto_add_to_shell_rc(force: bool = False) -> tuple[bool, str]:
    """Attempt to add the PATH snippet to ~/.bashrc and ~/.zshrc (idempotently).

    Returns (changed, message). Uses a marker comment to detect prior inserts.
    Idempotent by construction.
    """
    marker = "# >>> agent-vitals hooks >>>"
    end_marker = "# <<< agent-vitals hooks <<<"
    snippet = f'{marker}\n{shell_path_snippet().rstrip()}\n{end_marker}\n'
    paths = [Path.home() / ".bashrc", Path.home() / ".zshrc"]
    changed_any = False
    log: list[str] = []
    for rc in paths:
        if rc.exists():
            text = rc.read_text()
            if marker in text and not force:
                log.append(f"  {rc}: already wired (use force=True to overwrite)")
                continue
            if marker in text and force:
                # Remove old block, then append new
                import re
                text = re.sub(
                    re.escape(marker) + r".*?" + re.escape(end_marker) + r"\n",
                    "",
                    text,
                    flags=re.DOTALL,
                )
            new_text = text.rstrip() + "\n\n" + snippet
            rc.write_text(new_text)
            changed_any = True
            log.append(f"  {rc}: appended PATH snippet")
        # If the rc doesn't exist, skip silently — user may not use that shell.
    return changed_any, "\n".join(log) if log else "no shell rc files updated"


# Suppress noisy re-export warning for unused names we expose for callers.
__all__ = [
    "WRAPPER_BINARY_NAMES",
    "crontab_is_mutation",
    "systemctl_is_mutation",
    "is_mutation",
    "gate",
    "install_wrappers",
    "uninstall_wrappers",
    "set_wrappers_state",
    "status_report",
    "hook_dir",
    "resolve_real_binary",
    "shell_path_snippet",
    "auto_add_to_shell_rc",
]
