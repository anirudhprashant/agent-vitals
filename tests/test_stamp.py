"""Tests for agent_vitals.stamp.

Run with: python -m unittest tests.test_stamp
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_vitals import stamp as s


class TestStampTouch(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.stamp_path = self.tmp / "stamp"
        # Auto-cleanup
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["VITALS_STAMP_PATH"] = str(self.stamp_path)

    def test_touch_creates_file_with_epoch(self):
        before = int(time.time())
        s.touch()
        self.assertTrue(self.stamp_path.exists())
        content = self.stamp_path.read_text().strip()
        ts = int(content)
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, int(time.time()) + 2)


class TestStampReadAge(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.stamp_path = self.tmp / "stamp"
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["VITALS_STAMP_PATH"] = str(self.stamp_path)

    def test_read_age_returns_none_when_missing(self):
        self.assertIsNone(s.read_age())

    def test_read_age_short_after_touch(self):
        s.touch()
        age = s.read_age()
        self.assertIsNotNone(age)
        self.assertLess(age, 2.0)
        self.assertGreaterEqual(age, 0.0)

    def test_read_age_corrupt_returns_none(self):
        self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
        self.stamp_path.write_text("not a number\nalso not a number\n")
        self.assertIsNone(s.read_age())

    def test_read_age_garbage_with_valid_line(self):
        self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
        old_ts = int(time.time()) - 100
        self.stamp_path.write_text(f"{old_ts}\nsome other noise\n")
        age = s.read_age()
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 99)


class TestStampDescribeAge(unittest.TestCase):

    def test_describe_age_none(self):
        self.assertEqual(s.describe_age(None), "never")

    def test_describe_age_subsecond(self):
        self.assertEqual(s.describe_age(0.5), "just now")

    def test_describe_age_seconds(self):
        self.assertEqual(s.describe_age(42), "42s ago")

    def test_describe_age_minutes_seconds(self):
        self.assertEqual(s.describe_age(125), "2m5s ago")

    def test_describe_age_hours_minutes(self):
        self.assertEqual(s.describe_age(3725), "1h2m ago")


class TestStampShouldGate(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.stamp_path = self.tmp / "stamp"
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["VITALS_STAMP_PATH"] = str(self.stamp_path)
        os.environ.pop("VITALS_BYPASS", None)
        os.environ.pop("VITALS_GATE_WINDOW", None)

    def test_should_gate_no_stamp(self):
        gated, reason = s.should_gate()
        self.assertTrue(gated)
        self.assertIn("no vitals", reason.lower())

    def test_should_gate_fresh_stamp(self):
        s.touch()
        gated, reason = s.should_gate()
        self.assertFalse(gated)
        self.assertIn("fresh", reason.lower())

    def test_should_gate_old_stamp(self):
        os.environ["VITALS_GATE_WINDOW"] = "10"
        self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
        old_ts = int(time.time()) - 100
        self.stamp_path.write_text(f"{old_ts}\n")
        gated, reason = s.should_gate()
        self.assertTrue(gated)
        # Reason should mention "exceeds" the window and reference the age.
        self.assertIn("exceeds", reason)
        self.assertIn("10s window", reason)

    def test_should_gate_bypass_env(self):
        os.environ["VITALS_BYPASS"] = "1"
        # Even with no stamp, bypass allows
        gated, reason = s.should_gate()
        self.assertFalse(gated)
        self.assertIn("bypass", reason.lower())

    def test_should_gate_window_zero_always_blocks(self):
        os.environ["VITALS_GATE_WINDOW"] = "0"
        s.touch()
        gated, _ = s.should_gate()
        self.assertTrue(gated)

    def test_should_gate_invalid_window_falls_back(self):
        os.environ["VITALS_GATE_WINDOW"] = "not-a-number"
        s.touch()
        gated, _ = s.should_gate()
        self.assertFalse(gated)

    def test_should_gate_explicit_window_arg(self):
        s.touch()
        # With 0-second window, even a fresh stamp should fail
        gated, _ = s.should_gate(window=0)
        self.assertTrue(gated)
        gated, _ = s.should_gate(window=3600)
        self.assertFalse(gated)


class TestStampTouchIdempotent(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.stamp_path = self.tmp / "stamp"
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["VITALS_STAMP_PATH"] = str(self.stamp_path)

    def test_touch_overwrites_previous(self):
        self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
        ancient_ts = int(time.time()) - 86400  # 1 day ago
        self.stamp_path.write_text(f"{ancient_ts}\n")
        s.touch()
        age = s.read_age()
        self.assertIsNotNone(age)
        self.assertLess(age, 2.0)


class TestBypassValues(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.stamp_path = self.tmp / "stamp"
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["VITALS_STAMP_PATH"] = str(self.stamp_path)
        os.environ.pop("VITALS_BYPASS", None)

    def test_bypass_off_by_default(self):
        self.assertFalse(s.bypass())

    def test_bypass_env_one(self):
        os.environ["VITALS_BYPASS"] = "1"
        self.assertTrue(s.bypass())

    def test_bypass_env_true(self):
        os.environ["VITALS_BYPASS"] = "true"
        self.assertTrue(s.bypass())

    def test_bypass_env_yes(self):
        os.environ["VITALS_BYPASS"] = "yes"
        self.assertTrue(s.bypass())

    def test_bypass_env_zero_is_false(self):
        os.environ["VITALS_BYPASS"] = "0"
        self.assertFalse(s.bypass())

    def test_bypass_env_empty_is_false(self):
        os.environ["VITALS_BYPASS"] = ""
        self.assertFalse(s.bypass())

    def test_bypass_env_random_text_is_false(self):
        os.environ["VITALS_BYPASS"] = "yes please"
        self.assertFalse(s.bypass())


class TestStampPathOverride(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        # Make sure neither VITALS_STAMP_PATH nor default location interferes
        os.environ.pop("VITALS_STAMP_PATH", None)
        # Don't use HOME-relative default

    def test_stamp_path_returns_override(self):
        custom = self.tmp / "custom_stamp"
        with mock.patch.dict(os.environ, {"VITALS_STAMP_PATH": str(custom)}):
            self.assertEqual(s.stamp_path(), custom)

    def test_falls_back_to_default_when_no_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            # No VITALS_STAMP_PATH set; should fall back to default
            self.assertEqual(s.stamp_path(), s.STAMP_PATH)


if __name__ == "__main__":
    unittest.main()
