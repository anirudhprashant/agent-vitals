"""Tests for agent_vitals.burnout."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_vitals import burnout as b


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


class TestScanPiRunHistory(unittest.TestCase):
    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            f.flush()
            p = Path(f.name)
        with mock.patch.object(b.Path, "home", return_value=Path("/nonexistent")):
            # Patch the actual path lookup
            with mock.patch("agent_vitals.burnout.Path.home", return_value=p.parent):
                # We need to mock the file path directly
                pass
        # Easier: mock the whole function's path resolution
        with mock.patch("agent_vitals.burnout.Path.home") as mock_home:
            tmp = Path(tempfile.mkdtemp())
            mock_home.return_value = tmp
            hist = tmp / ".pi" / "agent" / "run-history.jsonl"
            hist.parent.mkdir(parents=True)
            hist.write_text("")
            result = b.scan_pi_run_history(days=7)
            self.assertEqual(result, [])

    def test_skips_corrupt_lines(self):
        with mock.patch("agent_vitals.burnout.Path.home") as mock_home:
            tmp = Path(tempfile.mkdtemp())
            mock_home.return_value = tmp
            hist = tmp / ".pi" / "agent" / "run-history.jsonl"
            hist.parent.mkdir(parents=True)
            hist.write_text(
                '{"agent":"a","status":"ok","ts":1.7e12}\n'
                "not json\n"
                '{"agent":"a","status":"ok","ts":1.7e12}\n'
            )
            result = b.scan_pi_run_history(days=7)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].agent, "a")
            self.assertEqual(result[0].runs, 2)

    def test_filters_by_days(self):
        with mock.patch("agent_vitals.burnout.Path.home") as mock_home:
            tmp = Path(tempfile.mkdtemp())
            mock_home.return_value = tmp
            hist = tmp / ".pi" / "agent" / "run-history.jsonl"
            hist.parent.mkdir(parents=True)
            old_ts = 1_000_000_000  # 2001
            recent_ts = 1.7e12  # 2023
            hist.write_text(
                json.dumps({"agent": "a", "status": "ok", "ts": old_ts}) + "\n"
                + json.dumps({"agent": "a", "status": "ok", "ts": recent_ts}) + "\n"
            )
            result = b.scan_pi_run_history(days=7)
            # Only recent should count
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].runs, 1)

    def test_counts_status(self):
        with mock.patch("agent_vitals.burnout.Path.home") as mock_home:
            tmp = Path(tempfile.mkdtemp())
            mock_home.return_value = tmp
            hist = tmp / ".pi" / "agent" / "run-history.jsonl"
            hist.parent.mkdir(parents=True)
            hist.write_text(
                json.dumps({"agent": "a", "status": "ok", "ts": 1.7e12}) + "\n"
                + json.dumps({"agent": "a", "status": "ok", "ts": 1.7e12}) + "\n"
                + json.dumps({"agent": "a", "status": "error", "ts": 1.7e12}) + "\n"
            )
            result = b.scan_pi_run_history(days=7)
            self.assertEqual(result[0].ok, 2)
            self.assertEqual(result[0].failed, 1)

    def test_sorted_by_runs(self):
        with mock.patch("agent_vitals.burnout.Path.home") as mock_home:
            tmp = Path(tempfile.mkdtemp())
            mock_home.return_value = tmp
            hist = tmp / ".pi" / "agent" / "run-history.jsonl"
            hist.parent.mkdir(parents=True)
            hist.write_text(
                json.dumps({"agent": "a", "status": "ok", "ts": 1.7e12}) + "\n"
                + json.dumps({"agent": "b", "status": "ok", "ts": 1.7e12}) + "\n"
                + json.dumps({"agent": "b", "status": "ok", "ts": 1.7e12}) + "\n"
                + json.dumps({"agent": "b", "status": "ok", "ts": 1.7e12}) + "\n"
            )
            result = b.scan_pi_run_history(days=7)
            self.assertEqual(result[0].agent, "b")
            self.assertEqual(result[0].runs, 3)


class TestScanClaudeCodeSessions(unittest.TestCase):
    def test_no_sessions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(b.Path, "home", return_value=Path(tmp)):
                result = b.scan_claude_code_sessions(days=7)
                self.assertEqual(result["sessions"], 0)
                self.assertEqual(result["events"], 0)
                self.assertEqual(result["stuck_sessions"], [])

    def test_counts_only_real_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / ".claude" / "projects" / "myproject"
            projects.mkdir(parents=True)
            sess = projects / "s1.jsonl"
            events = [
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
                {"type": "user", "message": {"content": "hello"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "bye"}]}},
                {"type": "mode", "mode": "normal"},
                {"type": "system", "message": {"content": "sys"}},
                {"type": "hook_success", "hookName": "test"},
            ]
            _write_jsonl(sess, events)
            with mock.patch.object(b.Path, "home", return_value=Path(tmp)):
                result = b.scan_claude_code_sessions(days=7)
                self.assertEqual(result["sessions"], 1)
                self.assertEqual(result["events"], 3)
                self.assertEqual(result["largest_session"], 3)

    def test_stuck_sessions_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / ".claude" / "projects" / "myproject"
            projects.mkdir(parents=True)
            events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}] * 200
            _write_jsonl(projects / "s1.jsonl", events)
            with mock.patch.object(b.Path, "home", return_value=Path(tmp)):
                result = b.scan_claude_code_sessions(days=7)
                self.assertEqual(len(result["stuck_sessions"]), 1)
                self.assertEqual(result["stuck_sessions"][0]["events"], 200)

    def test_filters_by_mtime_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / ".claude" / "projects" / "oldproj"
            projects.mkdir(parents=True)
            sess = projects / "old.jsonl"
            _write_jsonl(sess, [{"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}] * 10)
            old_time = 1_000_000_000  # 2001
            os.utime(sess, (old_time, old_time))
            with mock.patch.object(b.Path, "home", return_value=Path(tmp)):
                result = b.scan_claude_code_sessions(days=7)
                self.assertEqual(result["sessions"], 0)


if __name__ == "__main__":
    unittest.main()
