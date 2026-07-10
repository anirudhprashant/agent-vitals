"""Tests for agent_vitals.hooks — mutation detection + gate logic.

This module is the primary correctness check for v0.3.0 pre-action gates.
Every crontab and systemctl invocation under the sun needs to be classified
correctly: a hook that lets a write through is a security hole; a hook that
blocks a read is hostile UX.

Run with: python -m unittest tests.test_hooks
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock
from pathlib import Path

from agent_vitals import hooks as h
from agent_vitals import stamp as s


# ---------- crontab mutation detection ----------


class TestCrontabReads(unittest.TestCase):

    def test_bare_list_is_read(self):
        self.assertFalse(h.crontab_is_mutation(["-l"]))

    def test_list_after_user(self):
        self.assertFalse(h.crontab_is_mutation(["-u", "root", "-l"]))

    def test_user_before_list(self):
        self.assertFalse(h.crontab_is_mutation(["-l", "-u", "root"]))

    def test_help(self):
        self.assertFalse(h.crontab_is_mutation(["--help"]))

    def test_short_help(self):
        self.assertFalse(h.crontab_is_mutation(["-h"]))

    def test_version(self):
        self.assertFalse(h.crontab_is_mutation(["-V"]))


class TestCrontabMutations(unittest.TestCase):

    def test_bare_crontab_is_mutation(self):
        # No args = interactive edit, definitely a mutation
        self.assertTrue(h.crontab_is_mutation([]))

    def test_just_user_is_mutation(self):
        # -u root without -l is ambiguous; treat as mutation (conservative)
        self.assertTrue(h.crontab_is_mutation(["-u", "root"]))

    def test_edit_flag(self):
        self.assertTrue(h.crontab_is_mutation(["-e"]))

    def test_remove_flag(self):
        self.assertTrue(h.crontab_is_mutation(["-r"]))

    def test_install_from_file(self):
        self.assertTrue(h.crontab_is_mutation(["/tmp/mycrontab"]))

    def test_install_from_stdin(self):
        self.assertTrue(h.crontab_is_mutation(["-"]))

    def test_user_and_edit(self):
        # -u root -e — mutation even when user is specified
        self.assertTrue(h.crontab_is_mutation(["-u", "root", "-e"]))

    def test_unknown_long_option_is_mutation(self):
        # We don't recognise --foo, so be conservative
        self.assertTrue(h.crontab_is_mutation(["--foo"]))

    def test_unknown_short_option_is_mutation(self):
        # -X (unrecognised short) means someone is doing something we don't understand
        self.assertTrue(h.crontab_is_mutation(["-X"]))


# ---------- systemctl mutation detection ----------


class TestSystemctlReads(unittest.TestCase):

    def test_status_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["status", "nginx"]))

    def test_list_units_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["list-units"]))

    def test_list_unit_files_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["list-unit-files"]))

    def test_list_timers_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["list-timers"]))

    def test_is_active_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["is-active", "nginx"]))

    def test_is_enabled_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["is-enabled", "nginx"]))

    def test_show_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["show", "nginx"]))

    def test_cat_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["cat", "nginx.service"]))

    def test_help_is_read(self):
        self.assertFalse(h.systemctl_is_mutation(["help"]))

    def test_bare_systemctl_is_read(self):
        # No subcommand = overall status
        self.assertFalse(h.systemctl_is_mutation([]))

    def test_only_flags_is_read(self):
        # Just --user, no subcommand
        self.assertFalse(h.systemctl_is_mutation(["--user"]))

    def test_subcommand_with_value_flag(self):
        # systemctl -p ActiveState show nginx
        self.assertFalse(h.systemctl_is_mutation(["-p", "ActiveState", "show", "nginx"]))


class TestSystemctlMutations(unittest.TestCase):

    def test_enable(self):
        self.assertTrue(h.systemctl_is_mutation(["enable", "nginx.service"]))

    def test_disable(self):
        self.assertTrue(h.systemctl_is_mutation(["disable", "nginx.service"]))

    def test_start(self):
        self.assertTrue(h.systemctl_is_mutation(["start", "nginx"]))

    def test_stop(self):
        self.assertTrue(h.systemctl_is_mutation(["stop", "nginx"]))

    def test_restart(self):
        self.assertTrue(h.systemctl_is_mutation(["restart", "nginx"]))

    def test_reload(self):
        self.assertTrue(h.systemctl_is_mutation(["reload", "nginx"]))

    def test_user_enable(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "enable", "foo.service"]))

    def test_user_disable(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "disable", "foo.service"]))

    def test_user_start(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "start", "foo"]))

    def test_user_mask(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "mask", "foo"]))

    def test_user_unmask(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "unmask", "foo"]))

    def test_user_daemon_reload(self):
        self.assertTrue(h.systemctl_is_mutation(["--user", "daemon-reload"]))

    def test_daemon_reload(self):
        self.assertTrue(h.systemctl_is_mutation(["daemon-reload"]))

    def test_daemon_reexec(self):
        self.assertTrue(h.systemctl_is_mutation(["daemon-reexec"]))

    def test_reload_or_restart(self):
        self.assertTrue(h.systemctl_is_mutation(["reload-or-restart", "nginx"]))

    def test_reload_or_try_restart(self):
        self.assertTrue(h.systemctl_is_mutation(["reload-or-try-restart", "nginx"]))

    def test_try_restart(self):
        self.assertTrue(h.systemctl_is_mutation(["try-restart", "nginx"]))

    def test_kill(self):
        self.assertTrue(h.systemctl_is_mutation(["kill", "nginx"]))

    def test_reenable(self):
        self.assertTrue(h.systemctl_is_mutation(["reenable", "nginx"]))

    def test_preset(self):
        self.assertTrue(h.systemctl_is_mutation(["preset", "nginx"]))

    def test_link(self):
        self.assertTrue(h.systemctl_is_mutation(["link", "/path/to/nginx.service"]))

    def test_unknown_subcommand_is_mutation(self):
        # Conservatively treat unknown subcommand as mutation — better to
        # ask the user to refresh vitals than to silently let through.
        self.assertTrue(h.systemctl_is_mutation(["frobnicate", "nginx"]))

    def test_global_with_value_flag_then_subcommand(self):
        # systemctl --user enable foo
        self.assertTrue(h.systemctl_is_mutation(["-H", "host.example.com", "enable", "foo"]))

    def test_power_management_excluded_by_default(self):
        # reboot/poweroff are deliberately NOT in the mutating list —
        # see comment in hooks.py. Must not be mutation.
        self.assertFalse(h.systemctl_is_mutation(["reboot"]))
        self.assertFalse(h.systemctl_is_mutation(["poweroff"]))
        self.assertFalse(h.systemctl_is_mutation(["suspend"]))


# ---------- top-level dispatch ----------


class TestIsMutationDispatch(unittest.TestCase):

    def test_crontab_dispatch(self):
        self.assertTrue(h.is_mutation("crontab", ["-e"]))
        self.assertFalse(h.is_mutation("crontab", ["-l"]))

    def test_systemctl_dispatch(self):
        self.assertTrue(h.is_mutation("systemctl", ["enable", "x"]))
        self.assertFalse(h.is_mutation("systemctl", ["status", "x"]))

    def test_unknown_binary_defaults_to_mutation(self):
        # If we don't know what we're gating, gate it.
        self.assertTrue(h.is_mutation("mystery", ["anything"]))


# ---------- gate() end-to-end (with fake real binary) ----------


class TestGateReadsBypassStamp(unittest.TestCase):
    """Reads must NEVER be gated, regardless of stamp state."""

    def setUp(self):
        self._tmpdir_ctx = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.tmp = Path(self._tmpdir_ctx.name)
        # Make a fake real binary that just prints its args and exits 0.
        self.fake_bin = self.tmp / "crontab"
        self.fake_bin.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.fake_bin.chmod(0o755)
        # Stub resolve_real_binary to always return our fake.
        self._resolve_patch = mock.patch.object(
            h, "resolve_real_binary", lambda name: str(self.fake_bin)
        )
        self._resolve_patch.start()
        self.addCleanup(self._resolve_patch.stop)
        # Point VITALS_STAMP_PATH into tmp.
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        # Force gate to refuse (no stamp).
        os.environ.pop("VITALS_BYPASS", None)

    def test_read_bypasses_gate_with_no_stamp(self):
        rc = h.gate("crontab", ["-l"])
        self.assertEqual(rc, 0)
        # The gate function called our fake; we can't easily inspect its
        # output here without capturing stdout, but the rc=0 + no raise
        # confirms it delegated.

    def test_status_bypasses_gate(self):
        rc = h.gate("systemctl", ["status", "nginx"])
        self.assertEqual(rc, 0)

    def test_list_bypasses_gate(self):
        rc = h.gate("systemctl", ["list-units", "--user"])
        self.assertEqual(rc, 0)

    def test_help_bypasses_gate(self):
        rc = h.gate("crontab", ["--help"])
        self.assertEqual(rc, 0)


class TestGateRefusesMutationWithNoStamp(unittest.TestCase):

    def setUp(self):
        self._tmpdir_ctx = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.tmp = Path(self._tmpdir_ctx.name)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        os.environ.pop("VITALS_BYPASS", None)

    def test_refuses_crontab_edit_with_no_stamp(self):
        # No stamp file. crontab -e should be refused.
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = h.gate("crontab", ["-e"])
        self.assertEqual(rc, 126)
        self.assertIn("refused", buf.getvalue())
        self.assertIn("crontab", buf.getvalue())

    def test_refuses_systemctl_enable_with_no_stamp(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = h.gate("systemctl", ["--user", "enable", "foo"])
        self.assertEqual(rc, 126)


class TestGateAllowsFreshStamp(unittest.TestCase):

    def setUp(self):
        self._tmpdir_ctx = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.tmp = Path(self._tmpdir_ctx.name)
        # Fake real binary
        self.fake_bin = self.tmp / "crontab"
        self.fake_bin.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.fake_bin.chmod(0o755)
        # Stub resolve_real_binary to always return our fake.
        self._resolve_patch = mock.patch.object(
            h, "resolve_real_binary", lambda name: str(self.fake_bin)
        )
        self._resolve_patch.start()
        self.addCleanup(self._resolve_patch.stop)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        # Explicitly clear ALL vitals env vars to avoid cross-test leakage.
        for k in ("VITALS_BYPASS", "VITALS_GATE_WINDOW", "VITALS_STAMP_PATH"):
            os.environ.pop(k, None)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        # Touch stamp to make it fresh
        s.touch()

    def test_fresh_stamp_allows_mutation(self):
        # Stamp is fresh; crontab -e should pass through to the fake.
        rc = h.gate("crontab", ["-e"])
        self.assertEqual(rc, 0)


class TestGateBypass(unittest.TestCase):

    def setUp(self):
        self._tmpdir_ctx = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.tmp = Path(self._tmpdir_ctx.name)
        self.fake_bin = self.tmp / "crontab"
        self.fake_bin.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.fake_bin.chmod(0o755)
        # Stub resolve_real_binary to always return our fake.
        self._resolve_patch = mock.patch.object(
            h, "resolve_real_binary", lambda name: str(self.fake_bin)
        )
        self._resolve_patch.start()
        self.addCleanup(self._resolve_patch.stop)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        # No stamp, but bypass forces allowance
        os.environ["VITALS_BYPASS"] = "1"

    def tearDown(self):
        os.environ.pop("VITALS_BYPASS", None)

    def test_bypass_allows_with_no_stamp(self):
        rc = h.gate("crontab", ["-e"])
        self.assertEqual(rc, 0)


class TestGateStaleStamp(unittest.TestCase):

    def setUp(self):
        self._tmpdir_ctx = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.tmp = Path(self._tmpdir_ctx.name)
        self.fake_bin = self.tmp / "crontab"
        self.fake_bin.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.fake_bin.chmod(0o755)
        # Stub resolve_real_binary to always return our fake.
        self._resolve_patch = mock.patch.object(
            h, "resolve_real_binary", lambda name: str(self.fake_bin)
        )
        self._resolve_patch.start()
        self.addCleanup(self._resolve_patch.stop)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        os.environ.pop("VITALS_BYPASS", None)
        os.environ["VITALS_GATE_WINDOW"] = "10"
        # Write a stale stamp
        self.tmp.mkdir(parents=True, exist_ok=True)
        old_ts = int(time.time()) - 100
        (self.tmp / "stamp").write_text(f"{old_ts}\n")

    def tearDown(self):
        os.environ.pop("VITALS_GATE_WINDOW", None)

    def test_stale_stamp_refuses_mutation(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = h.gate("crontab", ["-e"])
        self.assertEqual(rc, 126)
        # Age is rendered via describe_age() so may be "1m40s"; check that
        # the gate message includes the essential info: "refused", the window,
        # and the bypass hint.
        self.assertIn("refused", buf.getvalue())
        self.assertIn("10s window", buf.getvalue())
        self.assertIn("VITALS_BYPASS=1 crontab -e", buf.getvalue())


# ---------- install / uninstall / enable / disable ----------


class TestInstallWrapperLifecycle(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Patch the hook dir to point at our tmp.
        self._home_patch = unittest.mock.patch.object(
            h, "hook_dir", lambda: self.tmp / "av-hooks"
        )
        self._home_patch.start()
        # Also patch Path.home in case anything reads it
        self._path_home = unittest.mock.patch.object(
            Path, "home", lambda *args: self.tmp
        )
        self._path_home.start()

    def tearDown(self):
        self._home_patch.stop()
        self._path_home.stop()

    def test_install_creates_wrappers_and_makes_them_executable(self):
        results = h.install_wrappers(("crontab", "systemctl"))
        d = self.tmp / "av-hooks"
        self.assertTrue(d.is_dir())
        for bin_name in ("crontab", "systemctl"):
            p = d / bin_name
            self.assertTrue(p.exists(), f"wrapper for {bin_name} not created")
            self.assertTrue(os.access(p, os.X_OK), f"wrapper for {bin_name} not executable")
            self.assertIn(results[bin_name], ("installed", "reactivated", "already_active"))

    def test_install_is_idempotent(self):
        first = h.install_wrappers(("crontab",))
        second = h.install_wrappers(("crontab",))
        self.assertEqual(first["crontab"], "installed")
        self.assertEqual(second["crontab"], "already_active")

    def test_uninstall_removes_wrappers(self):
        h.install_wrappers(("crontab", "systemctl"))
        removed = h.uninstall_wrappers()
        self.assertIn("crontab", removed)
        self.assertIn("systemctl", removed)
        self.assertFalse((self.tmp / "av-hooks" / "crontab").exists())
        self.assertFalse((self.tmp / "av-hooks" / "systemctl").exists())

    def test_disable_then_enable(self):
        h.install_wrappers(("crontab",))
        active = self.tmp / "av-hooks" / "crontab"
        disabled = self.tmp / "av-hooks" / "crontab.disabled"
        self.assertTrue(active.exists())
        # Disable
        changed = h.set_wrappers_state(enable=False, bin_names=("crontab",))
        self.assertIn("crontab", changed)
        self.assertFalse(active.exists())
        self.assertTrue(disabled.exists())
        # Enable
        changed = h.set_wrappers_state(enable=True, bin_names=("crontab",))
        self.assertIn("crontab", changed)
        self.assertTrue(active.exists())
        self.assertFalse(disabled.exists())

    def test_uninstall_cleans_disabled_too(self):
        h.install_wrappers(("crontab",))
        h.set_wrappers_state(enable=False, bin_names=("crontab",))
        self.assertTrue((self.tmp / "av-hooks" / "crontab.disabled").exists())
        removed = h.uninstall_wrappers()
        self.assertIn("crontab", removed)
        self.assertFalse((self.tmp / "av-hooks" / "crontab").exists())
        self.assertFalse((self.tmp / "av-hooks" / "crontab.disabled").exists())


# ---------- resolve_real_binary ----------


class TestResolveRealBinary(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._patch = unittest.mock.patch.object(h, "hook_dir", lambda: self.tmp / "av-hooks")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_finds_in_cleaned_path(self):
        fake = self.tmp / "myrealbin"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        # Pretend the wrapper dir is on PATH (and would contain a wrapper).
        # shutil.which with cleaned PATH should find fake anyway.
        # The helper strips hook_dir from PATH before searching.
        os.environ["PATH"] = f"{self.tmp}:/usr/bin:/bin"
        os.environ.pop("VITALS_STAMP_PATH", None)
        result = h.resolve_real_binary("myrealbin")
        # result could be the absolute path or somewhere in PATH
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("myrealbin"))

    def test_finds_in_system_fallback(self):
        # Even with PATH=/dev/null, the system fallback should find /usr/bin/sh
        os.environ["PATH"] = "/dev/null"
        result = h.resolve_real_binary("sh")
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("/sh"))


# ---------- status_report ----------


class TestStatusReport(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        os.environ["VITALS_STAMP_PATH"] = str(self.tmp / "stamp")
        os.environ.pop("VITALS_BYPASS", None)
        os.environ.pop("VITALS_GATE_WINDOW", None)

    def test_status_with_no_stamp_says_none(self):
        out = h.status_report()
        self.assertIn("none on record", out)
        self.assertIn("off", out)  # bypass line

    def test_status_with_fresh_stamp_shows_age(self):
        s.touch()
        out = h.status_report()
        self.assertIn("stamp:", out)
        self.assertIn("window:", out)
        self.assertIn("60", out)  # default window

    def test_status_with_bypass_env(self):
        os.environ["VITALS_BYPASS"] = "1"
        out = h.status_report()
        self.assertIn("ON", out)
        self.assertIn("gates disabled", out)


# ---------- shell_path_snippet / auto_add_to_shell_rc ----------


class TestShellPathSnippet(unittest.TestCase):

    def test_snippet_includes_hook_dir(self):
        out = h.shell_path_snippet()
        self.assertIn("av-hooks", out)
        self.assertIn("PATH=", out)
        self.assertIn("# agent-vitals hooks", out)

    def test_snippet_is_idempotent_safe_to_paste(self):
        # Idempotency is enforced by checking the marker before appending.
        pass  # marker is added by auto_add_to_shell_rc, not in raw snippet


class TestAutoAddShellRC(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Patch Path.home
        self._patch = unittest.mock.patch.object(Path, "home", lambda *args: self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_auto_add_creates_bashrc_when_present(self):
        bashrc = self.tmp / ".bashrc"
        bashrc.write_text("# existing content\n")
        ok, msg = h.auto_add_to_shell_rc(force=False)
        self.assertTrue(ok)
        new = bashrc.read_text()
        self.assertIn("# >>> agent-vitals hooks >>>", new)
        self.assertIn("existing content", new)

    def test_auto_add_idempotent_when_already_present(self):
        bashrc = self.tmp / ".bashrc"
        bashrc.write_text("# existing content\n")
        # First add
        ok1, _ = h.auto_add_to_shell_rc(force=False)
        self.assertTrue(ok1, "first add should succeed")
        # Second non-force should be skipped
        ok2, _ = h.auto_add_to_shell_rc(force=False)
        self.assertFalse(ok2, "second non-force add should be no-op")
        # force=True should re-add (replace existing block, not duplicate)
        ok3, _ = h.auto_add_to_shell_rc(force=True)
        self.assertTrue(ok3, "force=True should re-add")
        content = bashrc.read_text()
        self.assertEqual(
            content.count("# >>> agent-vitals hooks >>>"),
            1,
            "force=True should replace, not duplicate",
        )


if __name__ == "__main__":
    unittest.main()
