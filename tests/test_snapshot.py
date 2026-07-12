"""Tests for agent_vitals.snapshot."""

from __future__ import annotations

import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_vitals import snapshot as s


class TestCreateSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Override SNAPSHOT_ROOT
        self._patch = mock.patch.object(s, "SNAPSHOT_ROOT", self.tmp / "snaps")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_no_targets_raises(self):
        # Patch _default_targets to return empty list
        with mock.patch.object(s, "_default_targets", lambda: []):
            with self.assertRaises(RuntimeError):
                s.create_snapshot()

    def test_creates_archive_with_files(self):
        # Create some target files
        target_dir = self.tmp / "configs"
        target_dir.mkdir()
        (target_dir / "a.json").write_text('{"x": 1}')
        (target_dir / "b.json").write_text('{"y": 2}')
        snap = s.create_snapshot(targets=[target_dir / "a.json", target_dir / "b.json"])
        self.assertTrue(snap.path.exists())
        self.assertGreater(snap.size_bytes, 0)
        self.assertEqual(snap.num_files, 2)
        # Verify content
        with tarfile.open(snap.path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers()]
            self.assertIn("snapshot/a.json", names)
            self.assertIn("snapshot/b.json", names)

    def test_creates_archive_with_directory(self):
        target_dir = self.tmp / "configs"
        target_dir.mkdir()
        (target_dir / "sub" / "deep").mkdir(parents=True)
        (target_dir / "sub" / "deep" / "file.txt").write_text("hi")
        snap = s.create_snapshot(targets=[target_dir])
        self.assertEqual(snap.num_files, 1)
        with tarfile.open(snap.path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers()]
            self.assertTrue(any("file.txt" in n for n in names))

    def test_label_appended_to_name(self):
        target = self.tmp / "x.json"
        target.write_text("{}")
        snap = s.create_snapshot(label="my-backup", targets=[target])
        self.assertIn("my-backup", snap.path.name)

    def test_ignores_missing_targets(self):
        # Target path doesn't exist
        snap = s.create_snapshot(targets=[self.tmp / "nonexistent.json"])
        self.assertEqual(snap.num_files, 0)


class TestListSnapshots(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._patch = mock.patch.object(s, "SNAPSHOT_ROOT", self.tmp / "snaps")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_empty(self):
        self.assertEqual(s.list_snapshots(), [])

    def test_lists_created_snapshots(self):
        target = self.tmp / "x.json"
        target.write_text("{}")
        s.create_snapshot(targets=[target], label="first")
        time.sleep(1.1)  # ensure different mtime
        s.create_snapshot(targets=[target], label="second")
        snaps = s.list_snapshots()
        self.assertEqual(len(snaps), 2)
        # Newest first
        self.assertIn("second", snaps[0].path.name)
        self.assertIn("first", snaps[1].path.name)

    def test_corrupt_archive_doesnt_crash(self):
        # Make a non-tarfile in the snapshot root
        fake = s.SNAPSHOT_ROOT / "agent-vitals-snapshot-bogus.tar.gz"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("not a tar")
        snaps = s.list_snapshots()
        # Corrupt one is included with num_files=0, doesn't crash
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].num_files, 0)


class TestRenderSnapshotList(unittest.TestCase):
    def test_empty(self):
        out = s.render_snapshot_list([])
        self.assertIn("none in", out)

    def test_shows_count(self):
        with mock.patch.object(s, "SNAPSHOT_ROOT", Path("/tmp/snap-test")):
            out = s.render_snapshot_list([])
        self.assertIn("none in /tmp/snap-test", out)


if __name__ == "__main__":
    unittest.main()
