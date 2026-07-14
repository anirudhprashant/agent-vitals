"""Tests for agent_vitals.trace."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_vitals import trace as t


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _claude_user(parent_uuid=None, ts=None):
    return {
        "type": "user",
        "uuid": "u-1",
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "timestamp": ts or time.time(),
        "sessionId": "s-1",
        "message": {"role": "user", "content": "hello"},
    }


def _claude_assistant(parent_uuid=None, ts=None, tool_uses=None):
    msg = {"role": "assistant", "content": []}
    if tool_uses:
        for tu in tool_uses:
            msg["content"].append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": {},
            })
    else:
        msg["content"].append({"type": "text", "text": "ok"})
    return {
        "type": "assistant",
        "uuid": "a-1",
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "timestamp": ts or time.time(),
        "sessionId": "s-1",
        "message": msg,
    }


def _claude_tool_result(parent_uuid, tool_id, error=False, ts=None):
    raw = "Error: boom" if error else "ok"
    return {
        "type": "user",
        "uuid": tool_id,
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "timestamp": ts or time.time(),
        "sessionId": "s-1",
        "toolUseResult": raw if error else {"stdout": raw, "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False},
        "message": {"role": "user", "content": raw if error else ""},
    }


def _pi_message(role, parent_id=None, ts=None, tool_calls=None, text=None):
    content = []
    if tool_calls:
        for tc in tool_calls:
            content.append({
                "type": "toolCall",
                "id": tc["id"],
                "name": tc["name"],
                "arguments": {},
            })
    elif text:
        content.append({"type": "text", "text": text})
    else:
        content.append({"type": "text", "text": ""})
    return {
        "type": "message",
        "id": f"m-{int((ts or time.time()) * 1000)}",
        "parentId": parent_id,
        "timestamp": ts or time.time(),
        "message": {
            "role": role,
            "content": content,
            "timestamp": ts or time.time(),
        },
    }


class TestClaudeAdapter(unittest.TestCase):
    def test_user_turn(self):
        events = t._parse_claude_event(json.dumps(_claude_user()))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "user")
        self.assertEqual(events[0].source, "claude")

    def test_assistant_turn_with_tool_use(self):
        tu = [{"id": "tu-1", "name": "Read", "input": {}}]
        events = t._parse_claude_event(json.dumps(_claude_assistant(tool_uses=tu)))
        types = [e.event_type for e in events]
        self.assertIn("assistant", types)
        self.assertIn("tool_use", types)
    def test_tool_result_error_detection(self):
        events = t._parse_claude_event(json.dumps(_claude_tool_result("a-1", "tu-1", error=True)))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "user")
        self.assertEqual(events[1].event_type, "tool_result")
        self.assertTrue(events[1].error)

    def test_tool_result_success(self):
        events = t._parse_claude_event(json.dumps(_claude_tool_result("a-1", "tu-1", error=False)))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "user")
        self.assertEqual(events[1].event_type, "tool_result")
        self.assertFalse(events[1].error)


    def test_sidechain_filtered(self):
        ev = _claude_user()
        ev["isSidechain"] = True
        events = t._parse_claude_event(json.dumps(ev))
        self.assertEqual(len(events), 0)

    def test_metadata_events_ignored(self):
        for meta in ("mode", "permission-mode", "last-prompt", "ai-title"):
            events = t._parse_claude_event(json.dumps({"type": meta}))
            self.assertEqual(len(events), 0)


class TestPiAdapter(unittest.TestCase):
    def test_user_turn(self):
        ev = _pi_message("user", text="hello")
        events = t._parse_pi_event(json.dumps(ev))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "user")
        self.assertEqual(events[0].source, "pi")

    def test_assistant_turn_with_tool_call(self):
        tc = [{"id": "tc-1", "name": "Bash", "arguments": {}}]
        ev = _pi_message("assistant", tool_calls=tc)
        events = t._parse_pi_event(json.dumps(ev))
        types = [e.event_type for e in events]
        self.assertIn("tool_use", types)
        self.assertEqual(events[0].tool_name, "Bash")
        self.assertEqual(events[0].tool_id, "tc-1")

    def test_non_message_ignored(self):
        events = t._parse_pi_event(json.dumps({"type": "session"}))
        self.assertEqual(len(events), 0)


class TestEndToEnd(unittest.TestCase):
    def test_claude_session_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".claude/projects/p1" / "s-1.jsonl"
            _write_jsonl(p, [
                _claude_user(),
                _claude_assistant(tool_uses=[{"id": "tu-1", "name": "Read", "input": {}}]),
                _claude_tool_result("a-1", "tu-1", error=False),
            ])
            events = t.trace_events(p)
            # user + assistant + tool_use + user(tool_result carrier) + tool_result
            self.assertEqual(len(events), 5)
            types = [e.event_type for e in events]
            self.assertEqual(types[0], "user")
            self.assertEqual(types[1], "assistant")
            self.assertEqual(types[2], "tool_use")
            self.assertEqual(types[3], "user")
            self.assertEqual(types[4], "tool_result")

    def test_pi_session_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".pi/agent/sessions/p1" / "s-1.jsonl"
            _write_jsonl(p, [
                {"type": "session", "id": "s-1", "timestamp": time.time(), "version": 1, "cwd": "/tmp"},
                _pi_message("user", text="run tests"),
                _pi_message("assistant", tool_calls=[{"id": "tc-1", "name": "Bash", "arguments": {}}]),
            ])
            events = t.trace_events(p)
            self.assertTrue(any(e.event_type == "user" for e in events))
            self.assertTrue(any(e.tool_name == "Bash" for e in events))

        events = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid="a-1", tool_name="Read", tool_id="tu-1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=1.5, parent_uuid="a-1", tool_name=None, tool_id="tu-1", duration_ms=450.0, error=False),
        ]
        out = t.replay(events)
        self.assertIn("user", out)
        self.assertIn("Read", out)
        self.assertIn("450ms", out)

    def test_diff_identical(self):
        events = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="assistant", timestamp=1.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
        ]
        out = t.diff(events, events)
        self.assertIn("no divergence", out)

    def test_diff_divergence(self):
        a = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid="a", tool_name="Read", tool_id="t1", duration_ms=None, error=False),
        ]
        b = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid="a", tool_name="Bash", tool_id="t2", duration_ms=None, error=False),
        ]
        out = t.diff(a, b)
        self.assertIn("divergence at step 2", out)
        self.assertIn("Read", out)
        self.assertIn("Bash", out)

    def test_summary_stats(self):
        events = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=1.2, parent_uuid=None, tool_name=None, tool_id="t1", duration_ms=200.0, error=True),
        ]
        stats = t.summary(events)
        self.assertEqual(stats["events"], 3)
        self.assertEqual(stats["turns"], 1)
        self.assertEqual(stats["tools"], 1)
        self.assertEqual(stats["results"], 1)
        self.assertEqual(stats["errors"], 1)
        self.assertGreater(stats["wall_ms"], 0)

    def test_profile(self):
        events = [
            t.TraceEvent(source="claude", event_type="user", timestamp=0.0, parent_uuid=None, tool_name=None, tool_id=None, duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=1.3, parent_uuid=None, tool_name=None, tool_id="t1", duration_ms=300.0, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=2.0, parent_uuid=None, tool_name="Bash", tool_id="t2", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=2.5, parent_uuid=None, tool_name=None, tool_id="t2", duration_ms=500.0, error=True),
        ]
        prof = t.profile(events)
        tools = {row["tool"]: row for row in prof["tools"]}
        self.assertIn("Read", tools)
        self.assertIn("Bash", tools)
        self.assertEqual(tools["Read"]["calls"], 1)
        self.assertEqual(tools["Read"]["errors"], 0)
        self.assertEqual(tools["Bash"]["errors"], 1)
        self.assertAlmostEqual(tools["Bash"]["error_rate"], 1.0)

    def test_grep_by_tool_name(self):
        events = [
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=0.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Bash", tool_id="t2", duration_ms=None, error=False),
        ]
        matches = t.grep(events, "read")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tool_name, "Read")

    def test_grep_by_event_type(self):
        events = [
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=0.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=0.5, parent_uuid=None, tool_name=None, tool_id="t1", duration_ms=100.0, error=False),
        ]
        matches = t.grep(events, "tool_result")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].event_type, "tool_result")

    def test_export_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "trace.json"
            events = [
                t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            ]
            t.export_json(events, out)
            self.assertTrue(out.exists())
            import json
            data = json.loads(out.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["tool_name"], "Read")

    def test_suggest_errors(self):
        events = [
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Bash", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=1.5, parent_uuid=None, tool_name=None, tool_id="t1", duration_ms=500.0, error=True),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=2.0, parent_uuid=None, tool_name="Bash", tool_id="t2", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=2.5, parent_uuid=None, tool_name=None, tool_id="t2", duration_ms=500.0, error=True),
        ]
        suggestions = t.suggest(events)
        self.assertTrue(any("Bash is failing" in s for s in suggestions))

    def test_suggest_healthy(self):
        events = [
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=1.0, parent_uuid=None, tool_name="Read", tool_id="t1", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=1.1, parent_uuid=None, tool_name=None, tool_id="t1", duration_ms=100.0, error=False),
            t.TraceEvent(source="claude", event_type="tool_use", timestamp=2.0, parent_uuid=None, tool_name="Write", tool_id="t2", duration_ms=None, error=False),
            t.TraceEvent(source="claude", event_type="tool_result", timestamp=2.1, parent_uuid=None, tool_name=None, tool_id="t2", duration_ms=100.0, error=False),
        ]
        suggestions = t.suggest(events)
        self.assertTrue(any("healthy" in s.lower() for s in suggestions))

if __name__ == "__main__":
    unittest.main()


