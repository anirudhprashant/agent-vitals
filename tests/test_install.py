"""Tests for agent_vitals.install — the interactive installer."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

from agent_vitals import install as ins
from agent_vitals.primer import AgentHost


def _make_fake_hosts() -> list[AgentHost]:
    return [
        AgentHost(name="pi", config_path=Path("/tmp/pi/mcp.json"),
                  config_format="json", skill_dir=None, rule_file=None),
        AgentHost(name="Claude Code", config_path=Path("/tmp/cc/.mcp.json"),
                  config_format="json", skill_dir=None, rule_file=None),
    ]


class TestCheckComponents(unittest.TestCase):
    def test_all_valid(self):
        valid, invalid = ins._check_components(["mcp", "priming", "hooks"])
        self.assertEqual(set(valid), {"mcp", "priming", "hooks"})
        self.assertEqual(invalid, [])

    def test_some_invalid(self):
        valid, invalid = ins._check_components(["mcp", "bogus", "priming", "wat"])
        self.assertEqual(set(valid), {"mcp", "priming"})
        self.assertEqual(set(invalid), {"bogus", "wat"})

    def test_all_invalid(self):
        valid, invalid = ins._check_components(["foo", "bar"])
        self.assertEqual(valid, [])
        self.assertEqual(set(invalid), {"foo", "bar"})


class TestRunInstall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Patch detect_hosts to return fakes
        self._hosts_patch = mock.patch(
            "agent_vitals.install.detect_hosts", lambda: _make_fake_hosts()
        )
        self._hosts_patch.start()
        self.addCleanup(self._hosts_patch.stop)
        # Patch init_all to be a no-op
        self._init_patch = mock.patch(
            "agent_vitals.install.init_all", lambda: [
                (h, {"mcp": "already configured", "skill": "already installed"})
                for h in _make_fake_hosts()
            ]
        )
        self._init_patch.start()
        self.addCleanup(self._init_patch.stop)
        # Patch snapshot to be a no-op
        self._snap_patch = mock.patch(
            "agent_vitals.install.snap_mod.create_snapshot",
            lambda **kw: type("S", (), {"path": Path("/tmp/fake-snap.tar.gz"),
                                         "size_bytes": 1024, "num_files": 5})()
        )
        self._snap_patch.start()
        self.addCleanup(self._snap_patch.stop)
        # Patch hooks to be a no-op
        self._hooks_patch = mock.patch(
            "agent_vitals.install.hooks_mod.install_wrappers",
            lambda **kw: {"crontab": "already_active", "systemctl": "already_active"}
        )
        self._hooks_patch.start()
        self.addCleanup(self._hooks_patch.stop)
        self._auto_rc_patch = mock.patch(
            "agent_vitals.install.hooks_mod.auto_add_to_shell_rc",
            lambda **kw: (True, "added to ~/.bashrc")
        )
        self._auto_rc_patch.start()
        self.addCleanup(self._auto_rc_patch.stop)
        self.printer = mock.MagicMock()

    def test_no_hosts_detected(self):
        with mock.patch("agent_vitals.install.detect_hosts", lambda: []):
            result = ins.run_install(yes=True, printer=self.printer)
        self.assertIn("no_hosts_detected", result.notes)
        self.printer.assert_any_call("  no supported agent hosts detected.")

    def test_yes_installs_defaults(self):
        result = ins.run_install(yes=True, printer=self.printer)
        self.assertEqual(set(result.components_installed),
                         set(ins.DEFAULT_COMPONENTS))
        # All detected hosts should be in result.hosts_wired
        self.assertEqual(set(result.hosts_wired), {"pi", "Claude Code"})

    def test_only_filter(self):
        result = ins.run_install(yes=True, only=["hooks"], printer=self.printer)
        self.assertEqual(result.components_installed, ["hooks"])

    def test_only_filter_with_invalid(self):
        result = ins.run_install(yes=True, only=["hooks", "garbage"],
                                 printer=self.printer)
        # garbage ignored, only hooks installed
        self.assertEqual(result.components_installed, ["hooks"])
        # Printer was called with a warning about garbage
        warn_calls = [c for c in self.printer.call_args_list
                      if "garbage" in str(c)]
        self.assertGreater(len(warn_calls), 0)

    def test_hosts_filter(self):
        result = ins.run_install(yes=True, hosts_filter=["pi"], printer=self.printer)
        self.assertEqual(result.hosts_wired, ["pi"])

    def test_hosts_filter_with_unknown(self):
        result = ins.run_install(yes=True, hosts_filter=["pi", "nope"],
                                 printer=self.printer)
        # "nope" ignored, only "pi" installed
        self.assertEqual(result.hosts_wired, ["pi"])
        warn_calls = [c for c in self.printer.call_args_list
                      if "nope" in str(c)]
        self.assertGreater(len(warn_calls), 0)

    def test_snapshot_optional(self):
        # Without snapshot
        result = ins.run_install(yes=True, only=["mcp"], printer=self.printer)
        self.assertIsNone(result.snapshot_path)
        # With snapshot
        result = ins.run_install(yes=True, only=["mcp", "snapshot"],
                                 printer=self.printer)
        self.assertIsNotNone(result.snapshot_path)

    def test_install_result_defaults(self):
        # When no hosts are detected, result has no_hosts_detected in notes.
        with mock.patch("agent_vitals.install.detect_hosts", lambda: []):
            result = ins.run_install(yes=True, printer=self.printer)
        self.assertEqual(result.hosts_wired, [])
        self.assertEqual(result.components_installed, [])
        self.assertIn("no_hosts_detected", result.notes)


class TestInteractivePrompts(unittest.TestCase):
    def test_yes_with_default(self):
        # The _yes function returns True for empty input when default_yes
        with mock.patch("builtins.input", return_value=""):
            self.assertTrue(ins._yes("Continue?", default_yes=True))
            self.assertFalse(ins._yes("Continue?", default_yes=False))

    def test_yes_with_yes_input(self):
        with mock.patch("builtins.input", return_value="y"):
            self.assertTrue(ins._yes("Continue?", default_yes=False))

    def test_yes_with_no_input(self):
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(ins._yes("Continue?", default_yes=True))

    def test_yes_eof_returns_default(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertTrue(ins._yes("Continue?", default_yes=True))
            self.assertFalse(ins._yes("Continue?", default_yes=False))

    def test_choose_default(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(ins._choose("Pick:", ["a", "b", "c"], default="b"), "b")

    def test_choose_by_number(self):
        with mock.patch("builtins.input", return_value="2"):
            self.assertEqual(ins._choose("Pick:", ["a", "b", "c"]), "b")

    def test_choose_by_name(self):
        with mock.patch("builtins.input", return_value="c"):
            self.assertEqual(ins._choose("Pick:", ["a", "b", "c"]), "c")


if __name__ == "__main__":
    unittest.main()
