"""Smoke tests for the CLI entry points.

These exist because of a real bug: `av doctor`, `av burnout` and the bare
`av` summary all called `burn_all()` without importing it, raising
NameError on every invocation. 275 unit tests passed anyway, because they
exercised the modules directly and never went through cli.py.

Two layers here:

1. `test_no_undefined_names` — static check for the exact bug class
   (a name used at runtime but never imported). Catches it without
   needing to execute anything.
2. `test_command_runs` — actually invokes every zero-argument command.
   Static analysis can't see names resolved dynamically; running can.

Commands that require arguments are skipped: the goal is import/wiring
coverage, not behaviour coverage (the unit tests own that).
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import click
import pytest
from typer.main import get_command
from typer.testing import CliRunner

from agent_vitals.cli import app

SRC = Path(__file__).resolve().parent.parent / "src" / "agent_vitals"

runner = CliRunner()


def _zero_arg_commands() -> list[list[str]]:
    """Every command path that can be invoked with no arguments."""
    cmd = get_command(app)
    out: list[list[str]] = []

    def walk(node: click.Command, path: list[str]) -> None:
        if isinstance(node, click.Group):
            for name, sub in node.commands.items():
                walk(sub, path + [name])
            return
        # A command is safely invokable if no parameter is required.
        if not any(p.required for p in node.params):
            out.append(path)

    walk(cmd, [])
    return out


def _module_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _undefined_names(path: Path) -> list[str]:
    """Names loaded at runtime in a module that are never bound in it.

    Deliberately ignores annotations: the package uses
    `from __future__ import annotations`, so annotation-only names are
    lazy strings and never resolved at runtime.
    """
    tree = ast.parse(path.read_text(), filename=str(path))

    # Module-level dunders the interpreter injects into every module.
    bound: set[str] = set(dir(builtins)) | {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "__spec__",
        "__loader__",
        "__path__",
    }
    used: list[tuple[str, int]] = []

    class Binder(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for a in node.names:
                bound.add(a.asname or a.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for a in node.names:
                bound.add(a.asname or a.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            bound.add(node.name)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            bound.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            else:
                used.append((node.id, node.lineno))

        def visit_arg(self, node: ast.arg) -> None:
            bound.add(node.arg)
            # Skip node.annotation: lazy under `from __future__ import annotations`.

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)

        def visit_Global(self, node: ast.Global) -> None:
            bound.update(node.names)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            # Visit target/value but not the annotation.
            self.visit(node.target)
            if node.value:
                self.visit(node.value)

        def visit_arguments(self, node: ast.arguments) -> None:
            for a in [*node.posonlyargs, *node.args, *node.kwonlyargs]:
                self.visit_arg(a)
            if node.vararg:
                self.visit_arg(node.vararg)
            if node.kwarg:
                self.visit_arg(node.kwarg)
            for d in node.defaults:
                self.visit(d)
            for d in node.kw_defaults:
                if d:
                    self.visit(d)

    Binder().visit(tree)
    # Function return annotations live on the def node; `from __future__`
    # makes them lazy too, so anything only referenced there is fine.
    return sorted({n for n, _ in used if n not in bound})


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_no_undefined_names(path: Path) -> None:
    """Every runtime name in the package resolves to something."""
    missing = _undefined_names(path)
    assert not missing, f"{path.name}: name(s) used but never bound: {missing}"


@pytest.mark.parametrize("argv", _zero_arg_commands(), ids=lambda a: " ".join(a) or "(root)")
def test_command_runs(argv: list[str]) -> None:
    """Every zero-arg command executes without blowing up."""
    result = runner.invoke(app, argv)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"av {' '.join(argv)} raised {type(result.exception).__name__}: "
            f"{result.exception}"
        ) from result.exception
    assert result.exit_code == 0, f"av {' '.join(argv)} exited {result.exit_code}"


def test_help_lists_commands() -> None:
    """The root --help renders (catches a broken Typer wiring)."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output
