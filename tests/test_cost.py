"""Tests for agent_vitals.cost."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_vitals import cost


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

class TestTokenBucket(unittest.TestCase):
    def test_total(self):
        b = cost.TokenBucket(input_tokens=100, output_tokens=50, cache_read_tokens=20, cache_write_tokens=10)
        self.assertEqual(b.total(), 180)

    def test_empty_total(self):
        self.assertEqual(cost.TokenBucket().total(), 0)

    def test_cost_default_pricing(self):
        b = cost.TokenBucket(input_tokens=1_000_000, output_tokens=0)
        # Default pricing: input $3/M
        self.assertAlmostEqual(b.cost_usd(), 3.0)

    def test_cost_with_output(self):
        b = cost.TokenBucket(input_tokens=0, output_tokens=1_000_000)
        # Default pricing: output $15/M
        self.assertAlmostEqual(b.cost_usd(), 15.0)

    def test_cost_with_cache(self):
        b = cost.TokenBucket(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
        # Default cache_read $0.30/M, cache_write $3.75/M
        expected = 0.30 + 3.75
        self.assertAlmostEqual(b.cost_usd(), expected, places=2)

    def test_to_dict(self):
        b = cost.TokenBucket(input_tokens=10, output_tokens=5)
        d = b.to_dict()
        self.assertEqual(d["input_tokens"], 10)
        self.assertEqual(d["output_tokens"], 5)
        self.assertEqual(d["total_tokens"], 15)


class TestExtractUsageFromMessage(unittest.TestCase):
    def test_returns_none_when_no_usage(self):
        self.assertIsNone(cost._extract_usage_from_message({}))

    def test_returns_none_when_usage_empty(self):
        b = cost._extract_usage_from_message({"usage": {}})
        self.assertIsNone(b)

    def test_extracts_claude_code_format(self):
        b = cost._extract_usage_from_message({
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 10,
            }
        })
        self.assertIsNotNone(b)
        self.assertEqual(b.input_tokens, 100)
        self.assertEqual(b.output_tokens, 50)
        self.assertEqual(b.cache_read_tokens, 20)
        self.assertEqual(b.cache_write_tokens, 10)


class TestScanClaudeCodeSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_nonexistent_file(self):
        bucket, model = cost.scan_claude_code_session(self.tmp / "nope.jsonl")
        self.assertEqual(bucket.total(), 0)
        self.assertIsNone(model)

    def test_empty_file(self):
        f = self.tmp / "empty.jsonl"
        f.write_text("")
        bucket, model = cost.scan_claude_code_session(f)
        self.assertEqual(bucket.total(), 0)

    def test_sums_across_events(self):
        f = self.tmp / "sess.jsonl"
        events = [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {
                "model": "claude-sonnet-4",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }},
            {"type": "assistant", "message": {
                "usage": {"input_tokens": 200, "output_tokens": 100},
            }},
        ]
        _write_jsonl(f, events)
        bucket, model = cost.scan_claude_code_session(f)
        self.assertEqual(bucket.input_tokens, 300)
        self.assertEqual(bucket.output_tokens, 150)
        self.assertEqual(model, "claude-sonnet-4")  # first seen

    def test_corrupt_lines_skipped(self):
        f = self.tmp / "sess.jsonl"
        f.write_text(
            '{"type":"assistant","message":{"usage":{"input_tokens":100}}}\n'
            "this is not json\n"
            '{"type":"assistant","message":{"usage":{"input_tokens":50}}}\n'
        )
        bucket, c2 = cost.scan_claude_code_session(f)
        self.assertEqual(bucket.input_tokens, 150)  # corrupt line skipped


class TestScanAllSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Override the Claude Code path
        self.cc_root = self.tmp / ".claude" / "projects" / "myproject"
        self.cc_root.mkdir(parents=True)
        # Also a pi root with a session
        self.pi_root = self.tmp / ".pi" / "agent" / "sessions"
        self.pi_root.mkdir(parents=True)

    def _write_cc_session(self, name: str, input_tokens: int) -> Path:
        f = self.cc_root / name
        _write_jsonl(f, [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {
                "usage": {"input_tokens": input_tokens, "output_tokens": 10},
            }},
        ])
        return f

    def _write_pi_session(self, name: str) -> Path:
        f = self.pi_root / name
        # pi sessions don't have Claude Code usage format
        _write_jsonl(f, [{"type": "subagent", "agent": "foo"}])
        return f

    def test_filters_pi_files_from_cc_scan(self):
        # Set HOME so cost module looks in our tmp
        import os
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        try:
            self._write_cc_session("a.jsonl", 100)
            self._write_pi_session("p1/pi-sess.jsonl")
            result = cost.scan_all_sessions()
            # Only the CC session should be counted
            self.assertIn("claude-code", result)
            self.assertIn("myproject", result["claude-code"])
            self.assertEqual(result["claude-code"]["myproject"].input_tokens, 100)
        finally:
            if old_home:
                os.environ["HOME"] = old_home

    def test_empty(self):
        import os
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        try:
            result = cost.scan_all_sessions()
            self.assertEqual(result, {})
        finally:
            if old_home:
                os.environ["HOME"] = old_home


class TestRenderCostReport(unittest.TestCase):
    def test_empty(self):
        out = cost.render_cost_report({})
        self.assertIn("no token-usage data", out)

    def test_summarizes_by_host_project(self):
        out = cost.render_cost_report({
            "claude-code": {
                "p1": cost.TokenBucket(input_tokens=1_000_000),
            }
        })
        self.assertIn("claude-code", out)
        self.assertIn("p1", out)
        self.assertIn("3.00", out)  # cost
        self.assertIn("total:", out)


if __name__ == "__main__":
    unittest.main()


class TestToolTokens(unittest.TestCase):
    def test_scan_tool_tokens_empty(self):
        usage = cost.scan_tool_tokens(sessions=[])
        self.assertEqual(usage, {})

    def test_scan_tool_tokens_single_tool(self):
        session = _make_session([
            {"message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Read", "input": {"command": "ls"}}],
                "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10}
            }}
        ])
        usage = cost.scan_tool_tokens(sessions=[session])
        self.assertIn("Read", usage)
        self.assertEqual(usage["Read"].calls, 1)
        self.assertEqual(usage["Read"].input_tokens, 100)
        self.assertEqual(usage["Read"].output_tokens, 50)

    def test_scan_tool_tokens_multiple_tools_in_message(self):
        session = _make_session([
            {"message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"command": "ls"}},
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "x"}}
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10}
            }}
        ])
        usage = cost.scan_tool_tokens(sessions=[session])
        # Usage is split equally among tools
        self.assertEqual(usage["Read"].input_tokens, 50)
        self.assertEqual(usage["Edit"].input_tokens, 50)
        self.assertEqual(usage["Read"].calls, 1)
        self.assertEqual(usage["Edit"].calls, 1)

    def test_render_tokens_report_empty(self):
        out = cost.render_tokens_report({})
        self.assertIn("no token-usage data", out)

    def test_render_tokens_report_shows_tools(self):
        usage = {
            "Read": cost.ToolTokenUsage(tool_name="Read", calls=10, input_tokens=1000, output_tokens=500),
            "Edit": cost.ToolTokenUsage(tool_name="Edit", calls=5, input_tokens=500, output_tokens=200),
        }
        out = cost.render_tokens_report(usage, limit=10)
        self.assertIn("Read", out)
        self.assertIn("Edit", out)
        self.assertIn("10", out)  # calls


class TestModelDowngradeSuggestions(unittest.TestCase):
    def test_suggests_downgrade_for_low_output_opus(self):
        from agent_vitals.cli import _model_downgrade_suggestions
        from agent_vitals.cost import TokenBucket
        by_host = {
            "claude-code": {
                "proj": TokenBucket(
                    input_tokens=1000,
                    output_tokens=50,  # < 100
                    model="claude-opus-4-8",
                )
            }
        }
        sugg = _model_downgrade_suggestions(by_host)
        self.assertEqual(len(sugg), 1)
        self.assertIn("claude-opus-4-8", sugg[0])
        self.assertIn("claude-sonnet-4", sugg[0])

    def test_no_suggestion_for_high_output(self):
        from agent_vitals.cli import _model_downgrade_suggestions
        from agent_vitals.cost import TokenBucket
        by_host = {
            "claude-code": {
                "proj": TokenBucket(
                    input_tokens=1000,
                    output_tokens=1000,  # > 100
                    model="claude-opus-4-8",
                )
            }
        }
        sugg = _model_downgrade_suggestions(by_host)
        self.assertEqual(sugg, [])

    def test_no_suggestion_for_unknown_model(self):
        from agent_vitals.cli import _model_downgrade_suggestions
        from agent_vitals.cost import TokenBucket
        by_host = {
            "claude-code": {
                "proj": TokenBucket(
                    input_tokens=1000,
                    output_tokens=50,
                    model="some-unknown-model",
                )
            }
        }
        sugg = _model_downgrade_suggestions(by_host)
        self.assertEqual(sugg, [])


class TestCompactionSuggestions(unittest.TestCase):
    def test_flags_large_session(self):
        from agent_vitals.cli import _compaction_suggestions
        from agent_vitals.sessions import SessionInfo
        s = SessionInfo(
            host="pi",
            path=Path("/tmp/big.jsonl"),
            project="test",
            size_bytes=20 * 1024 * 1024,  # 20MB
            mtime=time.time(),
            event_count=100,
            first_event_ts=None,
            last_event_ts=None,
        )
        sugg = _compaction_suggestions([s])
        self.assertEqual(len(sugg), 1)
        self.assertIn("20.0 MiB", sugg[0])

    def test_flags_many_events(self):
        from agent_vitals.cli import _compaction_suggestions
        from agent_vitals.sessions import SessionInfo
        s = SessionInfo(
            host="pi",
            path=Path("/tmp/big.jsonl"),
            project="test",
            size_bytes=1024,
            mtime=time.time(),
            event_count=6000,
            first_event_ts=None,
            last_event_ts=None,
        )
        sugg = _compaction_suggestions([s])
        self.assertEqual(len(sugg), 1)
        self.assertIn("6000 events", sugg[0])

    def test_skips_small_session(self):
        from agent_vitals.cli import _compaction_suggestions
        from agent_vitals.sessions import SessionInfo
        s = SessionInfo(
            host="pi",
            path=Path("/tmp/small.jsonl"),
            project="test",
            size_bytes=1024,
            mtime=time.time(),
            event_count=100,
            first_event_ts=None,
            last_event_ts=None,
        )
        sugg = _compaction_suggestions([s])
        self.assertEqual(sugg, [])



    def test_scan_tool_tokens_pi_format(self):
        session = _make_session([
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "toolCall", "name": "Read", "input": {"command": "ls"}}],
                "usage": {"input": 100, "output": 50, "cacheRead": 20, "cacheWrite": 10}
            }}
        ])
        usage = cost.scan_tool_tokens(sessions=[session])
        self.assertIn("Read", usage)
        self.assertEqual(usage["Read"].calls, 1)
        self.assertEqual(usage["Read"].input_tokens, 100)
        self.assertEqual(usage["Read"].output_tokens, 50)

    def test_scan_tool_tokens_mixed_formats(self):
        cc_session = _make_session([
            {"message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Read", "input": {"command": "ls"}}],
                "usage": {"input_tokens": 100, "output_tokens": 50}
            }}
        ])
        pi_session = _make_session([
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "toolCall", "name": "Edit", "input": {"file_path": "x"}}],
                "usage": {"input": 80, "output": 40}
            }}
        ])
        usage = cost.scan_tool_tokens(sessions=[cc_session, pi_session])
        self.assertIn("Read", usage)
        self.assertIn("Edit", usage)
        self.assertEqual(usage["Read"].input_tokens, 100)
        self.assertEqual(usage["Edit"].input_tokens, 80)


class TestTokenSuggestions(unittest.TestCase):
    def test_dominant_tool_suggestion(self):
        from agent_vitals.cli import _token_suggestions
        from agent_vitals.cost import ToolTokenUsage
        usage = {
            "Bash": ToolTokenUsage(tool_name="Bash", calls=1000, input_tokens=400_000, output_tokens=100_000),
            "Read": ToolTokenUsage(tool_name="Read", calls=100, input_tokens=50_000, output_tokens=10_000),
        }
        sugg = _token_suggestions(usage)
        self.assertTrue(any("Bash dominates" in s for s in sugg))

    def test_high_average_suggestion(self):
        from agent_vitals.cli import _token_suggestions
        from agent_vitals.cost import ToolTokenUsage
        usage = {
            "fetch_content": ToolTokenUsage(tool_name="fetch_content", calls=10, input_tokens=500_000, output_tokens=10_000),
        }
        sugg = _token_suggestions(usage)
        # When one tool dominates 100%, it gets the dominant suggestion instead
        self.assertTrue(any("dominates" in s for s in sugg))

    def test_output_heavy_suggestion(self):
        from agent_vitals.cli import _token_suggestions
        from agent_vitals.cost import ToolTokenUsage
        usage = {
            "LLM": ToolTokenUsage(tool_name="LLM", calls=10, input_tokens=10_000, output_tokens=90_000),
        }
        sugg = _token_suggestions(usage)
        self.assertTrue(any("output-heavy" in s for s in sugg))

    def test_batching_suggestion(self):
        from agent_vitals.cli import _token_suggestions
        from agent_vitals.cost import ToolTokenUsage
        usage = {
            "Read": ToolTokenUsage(tool_name="Read", calls=200, input_tokens=100_000, output_tokens=5_000),
        }
        sugg = _token_suggestions(usage)
        self.assertTrue(any("batching" in s for s in sugg))


class TestOverlapDetection(unittest.TestCase):
    def test_no_overlap_empty(self):
        from agent_vitals.efficiency import find_overlapping_tools
        findings = find_overlapping_tools(registered={})
        self.assertEqual(findings, [])

    def test_detects_exact_overlap(self):
        from agent_vitals.efficiency import find_overlapping_tools
        registered = {
            "firecrawl": {"command": "x"},
            "mempalace": {"command": "y"},
        }
        called = {"mcp__firecrawl__search", "mcp__mempalace__search"}
        findings = find_overlapping_tools(registered=registered)
        self.assertTrue(any(f["type"] == "exact" for f in findings))

    def test_detects_similar_names(self):
        from agent_vitals.efficiency import find_overlapping_tools
        registered = {
            "brave-search": {"command": "x"},
            "firecrawl": {"command": "y"},
        }
        called = {"mcp__brave-search__web_search", "mcp__firecrawl__search"}
        findings = find_overlapping_tools(registered=registered)
        self.assertTrue(any(f["type"] == "similar" for f in findings))




class TestCoachCLI(unittest.TestCase):
    def test_coach_command_runs(self):
        from typer.testing import CliRunner
        from agent_vitals.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["coach"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("COACHING REPORT", result.output)

    def test_coach_json_format(self):
        from typer.testing import CliRunner
        from agent_vitals.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["coach", "--format", "json"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("optimized_prompt_fragments", result.output)




class TestHarnessCLI(unittest.TestCase):
    def test_coach_harness_flag(self):
        from typer.testing import CliRunner
        from agent_vitals.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["coach", "--harness"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Never claim inability", result.output)

    def test_coach_harness_model_flag(self):
        from typer.testing import CliRunner
        from agent_vitals.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["coach", "--harness", "--model", "medium"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Delegate", result.output)


