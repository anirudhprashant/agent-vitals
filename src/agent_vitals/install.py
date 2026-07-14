"""Interactive installer for agent-vitals.

Replaces the older `av init` and `av hooks install` commands with a
single guided flow:

  av install                 # fully interactive
  av install --yes           # install defaults, no prompts
  av install --only=mcp,hooks  # only install these components

Components offered:
  - mcp         : register agent-vitals as an MCP server in detected hosts
  - priming     : drop the SKILL.md / rule so the agent knows when to call
  - hooks       : PATH wrappers around crontab / systemctl --user
  - snapshot    : take a backup of current agent state before changes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_vitals.primer import detect_hosts, init_all
from agent_vitals import hooks as hooks_mod
from agent_vitals import snapshot as snap_mod


# All available components, in the order presented to the user.
ALL_COMPONENTS: tuple[str, ...] = (
    "mcp",
    "priming",
    "hooks",
    "snapshot",
    "loops",
    "unused",
    "cost",
)

# Default selection (snapshot is recommended for safety).
DEFAULT_COMPONENTS: tuple[str, str, str, str] = ("mcp", "priming", "snapshot")

# What each component does, in one line. Shown in the menu.
COMPONENT_DESCRIPTIONS: dict[str, str] = {
    "mcp":      "register agent-vitals as an MCP server in detected hosts",
    "priming":  "drop a SKILL.md so the agent knows when to call vitals",
    "hooks":    "PATH wrappers around crontab / systemctl that gate mutations",
    "snapshot": "take a backup of current agent state before changes",
    "loops":    "doom-loop detection across all sessions (v0.6.0)",
    "unused":   "registered-but-unused MCP tool detector (v0.6.0)",
    "cost":     "token spend tracker with Effective Tokens metric (v0.6.0)",
}


@dataclass
class InstallResult:
    hosts_wired: list[str] = None  # type: ignore[assignment]
    components_installed: list[str] = None  # type: ignore[assignment]
    snapshot_path: str | None = None
    shell_rc_updated: bool = False
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.hosts_wired is None:
            self.hosts_wired = []
        if self.components_installed is None:
            self.components_installed = []
        if self.notes is None:
            self.notes = []


def _yes(prompt: str, *, default_yes: bool = True) -> bool:
    """Interactive yes/no prompt. Reads from stdin."""
    suffix = "Y/n" if default_yes else "y/N"
    try:
        ans = input(f"  {prompt} [{suffix}]: ").strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in {"y", "yes"}


def _choose(prompt: str, options: list[str], *, default: str | None = None) -> str:
    """Interactive menu choice. Returns the chosen option."""
    print(f"  {prompt}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if opt == default else ""
        print(f"    {i}. {opt}{marker}")
    try:
        ans = input(f"  choice [default: {default or options[0]}]: ").strip()
    except EOFError:
        return default or options[0]
    if not ans:
        return default or options[0]
    try:
        idx = int(ans) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return ans  # user typed an option name directly


def _check_components(components: list[str]) -> tuple[list[str], list[str]]:
    """Validate a list of components. Returns (valid, invalid)."""
    valid, invalid = [], []
    for c in components:
        if c in ALL_COMPONENTS:
            valid.append(c)
        else:
            invalid.append(c)
    return valid, invalid


def run_install(
    *,
    yes: bool = False,
    only: list[str] | None = None,
    hosts_filter: list[str] | None = None,
    printer: Callable[[str], None] = print,
) -> InstallResult:
    """Run the interactive installer (or non-interactive with --yes / --only).

    Returns an InstallResult describing what was done. The caller (CLI)
    is responsible for rendering the result for the user.
    """
    result = InstallResult()

    # 1. Detect
    hosts = detect_hosts()
    if not hosts:
        printer("  no supported agent hosts detected.")
        printer("  I look for: pi, Claude Code, Cursor, OpenCode, Codex CLI.")
        printer("  Install one of those, then re-run `av install`.")
        result.notes.append("no_hosts_detected")
        return result

    printer(f"  detected {len(hosts)} agent host(s):")
    for h in hosts:
        printer(f"    - {h.name:<12} {h.config_path}")

    # 2. Component selection
    if only:
        valid, invalid = _check_components(only)
        if invalid:
            printer(f"  warning: unknown components ignored: {invalid}")
        chosen = valid or list(DEFAULT_COMPONENTS)
    elif yes:
        chosen = list(DEFAULT_COMPONENTS)
    else:
        printer("")
        printer("  what to install (space-separated numbers, 'all', or 'default'):")
        for i, c in enumerate(ALL_COMPONENTS, 1):
            d = COMPONENT_DESCRIPTIONS.get(c, "")
            mark = " (default)" if c in DEFAULT_COMPONENTS else ""
            printer(f"    {i}. {c:<10} — {d}{mark}")
        try:
            raw = input("  components [default]: ").strip().lower()
        except EOFError:
            raw = ""
        if not raw or raw == "default":
            chosen = list(DEFAULT_COMPONENTS)
        elif raw == "all":
            chosen = list(ALL_COMPONENTS)
        else:
            chosen = []
            for tok in raw.replace(",", " ").split():
                if tok.isdigit() and 1 <= int(tok) <= len(ALL_COMPONENTS):
                    chosen.append(ALL_COMPONENTS[int(tok) - 1])
                elif tok in ALL_COMPONENTS:
                    chosen.append(tok)
            if not chosen:
                chosen = list(DEFAULT_COMPONENTS)
        chosen = list(dict.fromkeys(chosen))  # dedupe, preserve order
    result.components_installed = chosen
    printer(f"  selected: {', '.join(chosen)}")

    # 3. Host selection (filter the detected list)
    if hosts_filter:
        valid_host_names = {h.name for h in hosts}
        selected = [h for h in hosts if h.name in hosts_filter]
        bad = [n for n in hosts_filter if n not in valid_host_names]
        if bad:
            printer(f"  warning: unknown hosts ignored: {bad}")
    elif yes:
        selected = hosts
    else:
        printer("")
        printer("  which hosts to install for (comma-separated, 'all', or 'default')?")
        for i, h in enumerate(hosts, 1):
            printer(f"    {i}. {h.name}")
        try:
            raw = input("  hosts [default: all]: ").strip().lower()
        except EOFError:
            raw = "all"
        if not raw or raw == "all" or raw == "default":
            selected = hosts
        else:
            selected = []
            for tok in raw.replace(",", " ").split():
                if tok.isdigit() and 1 <= int(tok) <= len(hosts):
                    selected.append(hosts[int(tok) - 1])
                else:
                    for h in hosts:
                        if h.name.lower() == tok.lower():
                            selected.append(h)
                            break
            if not selected:
                selected = hosts
    result.hosts_wired = [h.name for h in selected]
    printer(f"  will install for: {', '.join(h.name for h in selected)}")

    # 4. Optional: take a snapshot first
    if "snapshot" in chosen:
        printer("")
        if yes or _yes("Take a snapshot of current agent state first?", default_yes=True):
            try:
                snap = snap_mod.create_snapshot(label="pre-install")
                result.snapshot_path = str(snap.path)
                printer(f"  snapshot: {snap.path}  ({snap.size_bytes//1024}K, {snap.num_files} files)")
            except Exception as e:
                printer(f"  snapshot failed: {e}")
                result.notes.append("snapshot_failed")

    # 5. MCP server + priming for each selected host
    if "mcp" in chosen or "priming" in chosen:
        # Run the existing init_all with the right subset
        # For simplicity, just run init_all on every host — the primer
        # functions are idempotent.
        per_host_actions = init_all()
        # Filter to only selected hosts
        per_host_actions = [(h, a) for h, a in per_host_actions if h.name in result.hosts_wired]
        if "mcp" in chosen:
            for h, actions in per_host_actions:
                status = actions.get("mcp", "?")
                printer(f"  [{h.name}] mcp: {status}")
        if "priming" in chosen:
            for h, actions in per_host_actions:
                status = actions.get("skill") or actions.get("rule") or "?"
                printer(f"  [{h.name}] priming: {status}")

    # 6. Hooks
    if "hooks" in chosen:
        printer("")
        install_hooks = yes or _yes(
            "Install pre-action hooks (PATH wrappers around crontab/systemctl)?",
            default_yes=False,  # hooks are the most invasive option
        )
        if install_hooks:
            results = hooks_mod.install_wrappers()
            for name, status in results.items():
                printer(f"  hooks: {name:<10} {status}")
            update_rc = yes or _yes(
                "Append the PATH snippet to ~/.bashrc and ~/.zshrc?",
                default_yes=True,
            )
            if update_rc:
                ok, msg = hooks_mod.auto_add_to_shell_rc(force=False)
                if ok:
                    printer("  shell rc: updated (open a new terminal to apply)")
                    printer("  " + msg.replace("\n", "\n  "))
                    result.shell_rc_updated = True
                else:
                    printer(f"  shell rc: skipped — {msg}")
                    printer("  manually add to your shell rc:")
                    printer(hooks_mod.shell_path_snippet().rstrip())
        else:
            printer("  hooks: skipped (you can run `av hooks install` later)")

    printer("")
    printer("  done. Run `av doctor` to verify the install.")
    return result


__all__ = [
    "ALL_COMPONENTS",
    "DEFAULT_COMPONENTS",
    "COMPONENT_DESCRIPTIONS",
    "InstallResult",
    "run_install",
]
