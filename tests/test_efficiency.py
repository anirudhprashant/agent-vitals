"""Tests for agent_vitals.efficiency (loops, unused tools, ET metric)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_vitals import efficiency as eff


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _make_session(events: list[dict]) -> Path:
    """Create a temporary session JSONL file with the given events."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)

# ---------- effective_tokens ----------


class TestEffectiveTokens(unittest.TestCase):
    def test_empty_bucket(self):
        self.assertEqual(eff.effective_tokens(0, 0), 0.0)

    def test_default_model_multiplier(self):
        # input only, default multiplier 1.0
        self.assertEqual(eff.effective_tokens(input_tokens=1000, output_tokens=0), 1000.0)

    def test_output_weighted_4x(self):
        # output weighted 4x vs input
        self.assertEqual(eff.effective_tokens(input_tokens=0, output_tokens=100), 400.0)

    def test_cache_read_underweighted(self):
        # cache_read weighted 0.1x
        self.assertEqual(eff.effective_tokens(input_tokens=0, output_tokens=0, cache_read_tokens=1000), 100.0)

    def test_cache_write_treated_as_input(self):
        # cache_write (creation_input_tokens) is fresh work, weighted like input
        self.assertEqual(eff.effective_tokens(input_tokens=0, cache_write_tokens=500), 500.0)

    def test_opus_multiplier(self):
        # Opus has 5x multiplier
        self.assertEqual(
            eff.effective_tokens(input_tokens=100, output_tokens=10, model="claude-opus-4"),
            5.0 * (100 + 0 + 0 + 40)  # 5 * 140 = 700
        )

    def test_haiku_multiplier(self):
        # Haiku has 0.25x multiplier
        self.assertEqual(
            eff.effective_tokens(input_tokens=100, output_tokens=10, model="claude-haiku-4"),
            0.25 * 140  # 35
        )

    def test_unknown_model_falls_back_to_default(self):
        # unknown model uses _default multiplier (1.0)
        self.assertEqual(
            eff.effective_tokens(input_tokens=100, output_tokens=10, model="gpt-99-unknown"),
            140.0,
        )


# ---------- find_loops ----------


class TestFindLoops(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_no_sessions(self):
        self.assertEqual(eff.find_loops(sessions=[]), [])

    def test_session_with_repeated_tool(self):
        session = self.tmp / "claude.jsonl"
        # 20 identical Bash commands — should flag as a tool loop
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo stuck"}},
            ]}},
        ] * 20
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        bash_findings = [f for f in findings if f.target == "echo stuck"]
        self.assertEqual(len(bash_findings), 1)
        self.assertEqual(bash_findings[0].count, 20)
        self.assertEqual(bash_findings[0].kind, "tool_repeat")

    def test_polling_commands_excluded(self):
        session = self.tmp / "claude.jsonl"
        # 28 identical ps polls — should NOT flag as a tool loop
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {
                    "command": "ps -p 368759 > /dev/null 2>&1 && echo RUNNING || echo DONE"
                }},
            ]}},
        ] * 28
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        self.assertEqual(findings, [])

    def test_session_with_repeated_file_edits(self):
        session = self.tmp / "claude.jsonl"
        # 10 identical edits to the same file — should flag as a file loop
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "/tmp/foo.py",
                    "old_string": "x = 1\n",
                    "new_string": "x = 2\n",
                }},
            ]}},
        ] * 10
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        file_findings = [f for f in findings if f.target == "/tmp/foo.py"]
        self.assertEqual(len(file_findings), 1)
        self.assertEqual(file_findings[0].count, 10)
        self.assertEqual(file_findings[0].kind, "file_repeat")

    def test_progressive_file_edits_not_flagged(self):
        session = self.tmp / "claude.jsonl"
        # 12 edits to the same file, but each edit is unique — no loop
        events = []
        for i in range(12):
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "/tmp/foo.py",
                    "old_string": f"x = {i}\n",
                    "new_string": f"x = {i+1}\n",
                }},
            ]}})
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        self.assertEqual(findings, [])

    def test_mixed_file_edits_only_identical_cluster_flagged(self):
        session = self.tmp / "claude.jsonl"
        # 12 total edits: 10 identical + 2 unique. Only the cluster of 10
        # should be flagged.
        events = []
        # 10 identical edits (the loop)
        for _ in range(10):
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "/tmp/foo.py",
                    "old_string": "a = 1\n",
                    "new_string": "a = 2\n",
                }},
            ]}})
        # 2 progressive edits
        for i in range(2):
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "/tmp/foo.py",
                    "old_string": f"b = {i}\n",
                    "new_string": f"b = {i+1}\n",
                }},
            ]}})
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        file_findings = [f for f in findings if f.target == "/tmp/foo.py"]
        self.assertEqual(len(file_findings), 1)
        self.assertEqual(file_findings[0].count, 10)
        self.assertEqual(file_findings[0].kind, "file_repeat")

    def test_below_threshold_not_flagged(self):
        session = self.tmp / "claude.jsonl"
        # 19 identical Bash calls — below tool threshold (20)
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
            ]}},
        ] * 19
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        self.assertEqual(findings, [])

    def test_corrupt_session_lines_skipped(self):
        session = self.tmp / "claude.jsonl"
        # 20 valid identical Bash calls, with 1 corrupt line in between
        lines = ['{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo stuck"}}]}}'] * 20
        lines.insert(10, "not valid json")
        session.write_text("\n".join(lines))
        findings = eff.find_loops(sessions=[session])
        bash = [f for f in findings if f.target == "echo stuck"]
        self.assertEqual(len(bash), 1)
        self.assertEqual(bash[0].count, 20)

    def test_results_sorted_by_count_desc(self):
        session = self.tmp / "claude.jsonl"
        # Tool A: 20 calls, Tool B: 25 calls. Sorted, B should be first.
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "A", "input": {}},
            ]}},
        ] * 20 + [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "B", "input": {}},
            ]}},
        ] * 25
        _write_jsonl(session, events)
        findings = eff.find_loops(sessions=[session])
        if len(findings) >= 2:
            self.assertGreaterEqual(findings[0].count, findings[1].count)

    def test_host_detection(self):
        # Files under .claude/projects/ are claude-code
        session_cc = self.tmp / ".claude" / "projects" / "p" / "s.jsonl"
        events = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo stuck"}},
        ]}}] * 20
        _write_jsonl(session_cc, events)
        findings = eff.find_loops(sessions=[session_cc])
        self.assertTrue(all(f.host == "claude-code" for f in findings))


# ---------- find_unused_tools ----------


class TestFindUnusedTools(unittest.TestCase):
    def test_no_registered(self):
        self.assertEqual(eff.find_unused_tools(registered={}, called=set()), [])

    def test_all_used(self):
        registered = {"filesystem": {"command": "x"}}
        called = {"mcp__filesystem__read_file"}
        findings = eff.find_unused_tools(registered=registered, called=called)
        # Server is used; should report per-tool usage, not empty
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].tool_name, "read_file")
        self.assertEqual(findings[0].server, "filesystem")

    def test_some_unused(self):
        registered = {
            "filesystem": {"command": "x"},
            "github":    {"command": "y"},
        }
        # Only filesystem tools were called
        called = {"mcp__filesystem__read_file", "mcp__filesystem__write_file"}
        findings = eff.find_unused_tools(registered=registered, called=called)
        # github is unused (server-level), filesystem tools are used (tool-level)
        servers = [f.server for f in findings if f.calls_observed == 0]
        tools = [f.tool_name for f in findings if f.calls_observed > 0]
        self.assertIn("github", servers)
        self.assertNotIn("filesystem", servers)
        self.assertIn("read_file", tools)
        self.assertIn("write_file", tools)

    def test_empty_called_set_marks_everything_unused(self):
        registered = {"a": {}, "b": {}}
        findings = eff.find_unused_tools(registered=registered, called=set())
        self.assertEqual(len(findings), 2)

    def test_waste_estimate(self):
        findings = eff.find_unused_tools(registered={"a": {}}, called=set())
        self.assertEqual(len(findings), 1)
        # Estimated ~5KB per unused tool
        self.assertEqual(findings[0].estimated_waste_bytes, 5_000)


# ---------- renderers ----------


class TestRenderers(unittest.TestCase):
    def test_render_loop_empty(self):
        out = eff.render_loop_report([])
        self.assertIn("no doom-loop", out)

    def test_render_loop_includes_table(self):
        findings = [
            eff.LoopFinding(
                session_path="/x.jsonl", host="claude-code", kind="tool_repeat",
                target="Bash", count=42, detail="called 42x",
            )
        ]
        out = eff.render_loop_report(findings, limit=10)
        self.assertIn("1 pattern", out)
        self.assertIn("Bash", out)
        self.assertIn("42", out)

    def test_render_loop_limit_truncation(self):
        findings = [
            eff.LoopFinding(
                session_path=f"/s{i}.jsonl", host="claude-code", kind="tool_repeat",
                target=f"T{i}", count=10 + i, detail="",
            )
            for i in range(50)
        ]
        out = eff.render_loop_report(findings, limit=5)
        self.assertIn("and 45 more", out)

    def test_render_unused_empty(self):
        out = eff.render_unused_report([])
        self.assertIn("every registered tool was used", out)

    def test_render_unused_shows_count(self):
        findings = [
            eff.UnusedToolFinding(
                tool_name="x", server="x", host="", config_path="/x",
                calls_observed=0, estimated_waste_bytes=5000,
            )
        ]
        out = eff.render_unused_report(findings)
        self.assertIn("1 server(s)", out)
        self.assertIn("zero observed calls", out)


if __name__ == "__main__":
    unittest.main()


class TestSSHLoops(unittest.TestCase):
    def test_detects_ssh_polling(self):
        session = _make_session([
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ssh remote-host-1 ls /tmp"}}],
            }}
        ] * 15)  # 15 identical SSH calls
        findings = eff.find_loops(sessions=[session])
        ssh_findings = [f for f in findings if f.kind == "ssh_poll"]
        self.assertEqual(len(ssh_findings), 1)
        self.assertEqual(ssh_findings[0].target, "ssh remote-host-1")
        self.assertEqual(ssh_findings[0].count, 15)

    def test_no_ssh_loop_below_threshold(self):
        session = _make_session([
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ssh remote-host-1 ls /tmp"}}],
            }}
        ] * 5)  # Only 5 calls
        findings = eff.find_loops(sessions=[session])
        ssh_findings = [f for f in findings if f.kind == "ssh_poll"]
        self.assertEqual(len(ssh_findings), 0)

    def test_extract_ssh_target(self):
        cmd = "ssh -o StrictHostKeyChecking=no ubuntu@remote-host-1 ps aux"
        target = eff._extract_ssh_target(cmd)
        self.assertEqual(target, "ubuntu@remote-host-1")


class TestOverlapDetection(unittest.TestCase):
    def test_no_overlap_when_empty(self):
        findings = eff.find_overlapping_tools(registered={})
        self.assertEqual(findings, [])

    def test_detects_exact_overlap(self):
        registered = {
            "firecrawl": {"command": "x"},
            "mempalace": {"command": "y"},
        }
        called = {"mcp__firecrawl__search", "mcp__mempalace__search"}
        findings = eff.find_overlapping_tools(registered=registered, called=called)
        # Should detect that firecrawl and mempalace both have 'search'
        self.assertTrue(any(f["type"] == "exact" for f in findings))

    def test_detects_similar_names(self):
        registered = {
            "brave-search": {"command": "x"},
            "firecrawl": {"command": "y"},
        }
        called = {"mcp__brave-search__web_search", "mcp__firecrawl__search"}
        findings = eff.find_overlapping_tools(registered=registered, called=called)
        self.assertTrue(any(f["type"] == "similar" for f in findings))


