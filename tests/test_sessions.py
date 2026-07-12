"""Tests for agent_vitals.sessions."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_vitals import sessions as s


def _make_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


class TestDiscoverSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Override SESSION_ROOTS to point at our tmp dir
        self._patch = unittest.mock.patch.object(
            s, "SESSION_ROOTS", [
                ("test-host", self.tmp / "sessions"),
            ]
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.sessions_dir = self.tmp / "sessions"

    def test_empty(self):
        self.assertEqual(s.discover_sessions(), [])

    def test_single_session(self):
        _make_jsonl(self.sessions_dir / "p1" / "abc.jsonl", [
            {"type": "user", "timestamp": time.time()},
            {"type": "assistant", "timestamp": time.time()},
        ])
        sessions = s.discover_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].host, "test-host")
        self.assertEqual(sessions[0].project, "p1")
        self.assertIsNotNone(sessions[0].event_count)
        self.assertGreater(sessions[0].size_bytes, 0)

    def test_multiple_sessions_across_dirs(self):
        for proj in ("p1", "p2", "p3"):
            _make_jsonl(self.sessions_dir / proj / f"{proj}.jsonl", [
                {"type": "user", "timestamp": time.time()},
            ])
        self.assertEqual(len(s.discover_sessions()), 3)

    def test_missing_root_dir(self):
        # Override the patch to point at a non-existent root
        s.SESSION_ROOTS[0] = ("test-host", self.tmp / "does-not-exist")
        self.assertEqual(s.discover_sessions(), [])


class TestSessionInfoProperties(unittest.TestCase):
    def test_age_days(self):
        # Manually create a SessionInfo with known mtime
        old = time.time() - 86400 * 30  # 30 days ago
        si = s.SessionInfo(
            host="h", path=Path("/tmp/x"), project="p",
            size_bytes=100, mtime=old, event_count=5,
            first_event_ts=None, last_event_ts=None,
        )
        self.assertAlmostEqual(si.age_days, 30.0, places=1)

    def test_to_dict(self):
        si = s.SessionInfo(
            host="h", path=Path("/tmp/x"), project="p",
            size_bytes=100, mtime=time.time(), event_count=5,
            first_event_ts=None, last_event_ts=None,
        )
        d = si.to_dict()
        self.assertEqual(d["host"], "h")
        self.assertEqual(d["size_bytes"], 100)
        self.assertEqual(d["event_count"], 5)
        self.assertIn("age_days", d)


class TestFilterSessions(unittest.TestCase):
    def _mk(self, age_days: float, size: int) -> s.SessionInfo:
        return s.SessionInfo(
            host="h", path=Path("/tmp/x"), project="p",
            size_bytes=size, mtime=time.time() - age_days * 86400,
            event_count=1, first_event_ts=None, last_event_ts=None,
        )

    def test_filter_by_age(self):
        sessions = [self._mk(5, 100), self._mk(20, 100), self._mk(60, 100)]
        result = s.filter_sessions(sessions, older_than_days=10)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(s.age_days >= 10 for s in result))

    def test_filter_by_size(self):
        sessions = [self._mk(5, 100), self._mk(5, 5000), self._mk(5, 50000)]
        result = s.filter_sessions(sessions, larger_than_bytes=1000)
        self.assertEqual(len(result), 2)

    def test_filter_by_host(self):
        sessions = [
            s.SessionInfo(host="a", path=Path("/x"), project=None, size_bytes=1,
                          mtime=0, event_count=1, first_event_ts=None, last_event_ts=None),
            s.SessionInfo(host="b", path=Path("/x"), project=None, size_bytes=1,
                          mtime=0, event_count=1, first_event_ts=None, last_event_ts=None),
        ]
        result = s.filter_sessions(sessions, host="a")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].host, "a")

    def test_combined_filters(self):
        sessions = [self._mk(5, 100), self._mk(20, 5000), self._mk(60, 100)]
        result = s.filter_sessions(sessions, older_than_days=10, larger_than_bytes=1000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].size_bytes, 5000)


class TestRenderSessionsTable(unittest.TestCase):
    def _mk(self, host: str, size: int, age_days: float, project: str | None = "p") -> s.SessionInfo:
        return s.SessionInfo(
            host=host, path=Path("/tmp/x"), project=project,
            size_bytes=size, mtime=time.time() - age_days * 86400,
            event_count=1, first_event_ts=None, last_event_ts=None,
        )

    def test_empty(self):
        out = s.render_sessions_table([])
        self.assertIn("none found", out)

    def test_sorts_by_mtime_by_default(self):
        sessions = [self._mk("a", 100, 1), self._mk("b", 100, 100)]
        out = s.render_sessions_table(sessions, limit=10)
        # a (newer) should appear before b (older)
        self.assertLess(out.index("a"), out.index("b"))

    def test_sorts_by_size(self):
        sessions = [self._mk("a", 100, 1), self._mk("b", 5000, 1)]
        out = s.render_sessions_table(sessions, limit=10, sort_by="size")
        # The data rows are the ones with a size like "4.9K" or "0.1K".
        # Sort-by-size means b (5000K → 4.9K) appears before a (100K → 0.1K).
        lines = out.splitlines()
        b_line = next((l for l in lines if "b" in l and "K" in l), None)
        a_line = next((l for l in lines if "a" in l and "K" in l), None)
        self.assertIsNotNone(b_line)
        self.assertIsNotNone(a_line)
        self.assertLess(lines.index(b_line), lines.index(a_line))

    def test_limit(self):
        sessions = [self._mk(f"host{i}", 100, 1) for i in range(50)]
        out = s.render_sessions_table(sessions, limit=5)
        self.assertIn("and 45 more", out)

    def test_total_size_in_header(self):
        sessions = [self._mk("a", 1024 * 1024, 1), self._mk("b", 2 * 1024 * 1024, 1)]
        out = s.render_sessions_table(sessions, limit=10)
        # Total: 3 MiB
        self.assertIn("3.0 MiB", out)


if __name__ == "__main__":
    unittest.main()
