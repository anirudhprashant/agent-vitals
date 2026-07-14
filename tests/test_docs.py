"""Keep the README and landing page honest.

Every number in the docs had drifted from the code: the version badge was a
release behind, the LOC badge said ~4000 against ~6000 actual, the test count
said 275 against 295, and the landing page claimed "5 MCP tools" in one place
and "13 MCP tools" in another. Nothing checked, so nothing stayed true.

These tests fail when a claim stops matching the thing it describes. They
assert on counts that are cheap to derive; prose is left alone.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.main import get_command

from agent_vitals import __version__
from agent_vitals.cli import app

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
LANDING = ROOT / "docs" / "index.html"

DOCS = [pytest.param(README, id="README.md"), pytest.param(LANDING, id="index.html")]


def _command_count() -> int:
    return len(get_command(app).commands)


def _mcp_tool_count() -> int:
    src = (ROOT / "src" / "agent_vitals" / "mcp_server.py").read_text()
    return len(re.findall(r"^@mcp\.tool", src, re.M))


@pytest.mark.parametrize("doc", DOCS)
def test_command_count_claim(doc: Path) -> None:
    """"N CLI commands" matches the CLI."""
    actual = _command_count()
    for claimed in re.findall(r"(\d+) CLI commands", doc.read_text()):
        assert int(claimed) == actual, (
            f"{doc.name} claims {claimed} CLI commands; app registers {actual}"
        )


@pytest.mark.parametrize("doc", DOCS)
def test_mcp_tool_count_claim(doc: Path) -> None:
    """"N MCP tools" matches the @mcp.tool decorators."""
    actual = _mcp_tool_count()
    for claimed in re.findall(r"(\d+) MCP tools", doc.read_text()):
        assert int(claimed) == actual, (
            f"{doc.name} claims {claimed} MCP tools; mcp_server registers {actual}"
        )


def _collected_test_count() -> int:
    """How many tests pytest actually collects.

    Counting `def test_` underreports badly — most of this suite is
    parametrized, so 10 functions expand to hundreds of cases. Ask pytest.
    `--collect-only` does not execute anything, so this cannot recurse.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if not m:
        pytest.skip("could not determine collected test count")
    return int(m.group(1))


@pytest.mark.parametrize("doc", DOCS)
def test_test_count_claim(doc: Path) -> None:
    """"N tests" is not wildly stale.

    Exact-matching would fail every time someone adds a test, so this only
    catches drift big enough to be a lie (>10%).
    """
    actual = _collected_test_count()
    for claimed in re.findall(r"(\d+) tests", doc.read_text()):
        c = int(claimed)
        assert abs(c - actual) <= max(actual * 0.1, 5), (
            f"{doc.name} claims {c} tests; pytest collects {actual}"
        )


def test_readme_version_badge() -> None:
    """The version badge tracks __version__."""
    badges = re.findall(r"badge/version-v([\d.]+)-", README.read_text())
    assert badges, "no version badge found in README"
    for b in badges:
        assert b == __version__, f"README badge says v{b}; package is v{__version__}"


def test_readme_tables_render() -> None:
    """Markdown tables have matching header/separator column counts.

    A 3-column header over a 2-column separator silently renders as plain
    text on GitHub — which is exactly what the 'What it scans' table did.
    """
    lines = README.read_text().splitlines()
    for i, line in enumerate(lines):
        if not re.fullmatch(r"\s*\|(?:\s*:?-+:?\s*\|)+\s*", line):
            continue
        header = lines[i - 1]
        if not header.strip().startswith("|"):
            continue
        assert header.count("|") == line.count("|"), (
            f"README.md:{i + 1}: table separator has {line.count('|') - 1} columns "
            f"but header has {header.count('|') - 1} — renders broken on GitHub"
        )


def test_documented_commands_exist() -> None:
    """Commands shown in the README are real, and real ones are shown.

    Guards both directions: docs promising a command that does not exist,
    and a shipped command nobody documented.
    """
    text = README.read_text()
    documented = {m for m in re.findall(r"^av ([a-z]+)", text, re.M)}
    registered = set(get_command(app).commands)
    # `av` alone (bare summary) is documented as plain `av`, not `av <cmd>`.
    assert not (documented - registered), (
        f"README documents non-existent command(s): {sorted(documented - registered)}"
    )
    assert not (registered - documented), (
        f"shipped but undocumented command(s): {sorted(registered - documented)}"
    )


@pytest.mark.parametrize("doc", DOCS)
def test_no_stale_deprecated_promotion(doc: Path) -> None:
    """Deprecated commands are not advertised as features.

    `av init` is a back-compat alias for `av install`; the landing page's
    command list should not sell it.
    """
    cmd = get_command(app).commands.get("init")
    assert cmd is not None and "deprecated" in (cmd.help or "").lower(), (
        "av init is no longer deprecated — update this test and the docs"
    )
    if doc is LANDING:
        assert "init · install" not in doc.read_text(), (
            "landing page advertises deprecated `av init` in its command list"
        )
