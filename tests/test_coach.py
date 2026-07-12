"""Tests for agent_vitals.coach."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_vitals import coach


def _make_session(events: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)


class TestExtractToolCalls(unittest.TestCase):
    def test_claude_code_format(self):
        session = _make_session([
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x.py"}},
                        {"type": "tool_use", "name": "Edit", "input": {"path": "/tmp/x.py", "old_string": "a", "new_string": "b"}},
                    ]
                }
            }
        ])
        calls = coach._extract_tool_calls_from_session(session)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["tool"], "Read")
        self.assertEqual(calls[1]["tool"], "Edit")
        session.unlink()

    def test_pi_format(self):
        session = _make_session([
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "toolCall", "arguments": {"tool": "read", "path": "/tmp/x.py"}},
                    ]
                }
            }
        ])
        calls = coach._extract_tool_calls_from_session(session)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "read")
        session.unlink()


class TestFailurePatternDetection(unittest.TestCase):
    def test_detects_retry_loop(self):
        calls = [
            {"tool": "Bash", "input": {"command": "ssh remote-host-1 ls"}},
            {"tool": "Bash", "input": {"command": "ssh remote-host-1 ls"}},
            {"tool": "Bash", "input": {"command": "ssh remote-host-1 ls"}},
            {"tool": "Read", "input": {"path": "/tmp/x"}},
        ]
        failures = coach._detect_failure_patterns(calls)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].tool, "Bash")
        self.assertIn("Read", failures[0].recovery_tools)

    def test_no_false_positive(self):
        calls = [
            {"tool": "Read", "input": {"path": "/tmp/a.py"}},
            {"tool": "Edit", "input": {"path": "/tmp/a.py"}},
            {"tool": "Read", "input": {"path": "/tmp/b.py"}},
        ]
        failures = coach._detect_failure_patterns(calls)
        self.assertEqual(len(failures), 0)


class TestTokenBudgetTips(unittest.TestCase):
    def test_dominant_tool_tip(self):
        calls = [{"tool": "Bash", "input": {"command": "echo hi"}}] * 10 + \
               [{"tool": "Read", "input": {"path": "/tmp/x"}}] * 2
        tips = coach._generate_token_budget_tips(calls)
        self.assertTrue(any("Bash" in t for t in tips))

    def test_low_diversity_tip(self):
        calls = [{"tool": "Bash", "input": {"command": "ls /tmp"}}] * 10
        tips = coach._generate_token_budget_tips(calls)
        self.assertTrue(any("Low command diversity" in t for t in tips))


class TestCoachingReport(unittest.TestCase):
    def test_analyze_empty_session(self):
        session = _make_session([])
        report = coach.analyze_session(session)
        self.assertEqual(report.optimized_prompt_fragments, [])
        session.unlink()

    def test_analyze_produces_fragments(self):
        # Create multiple similar sequences to trigger playbook generation
        events = []
        for _ in range(3):
            events.append({
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/a.py"}},
                        {"type": "tool_use", "name": "Edit", "input": {"path": "/tmp/a.py"}},
                        {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/a.py"}},
                        {"type": "tool_use", "name": "Edit", "input": {"path": "/tmp/a.py"}},
                    ]
                }
            })
        session = _make_session(events)
        report = coach.analyze_session(session, model_tier="small")
        self.assertTrue(len(report.optimized_prompt_fragments) > 0)
        self.assertTrue(len(report.tool_playbooks) > 0)
        session.unlink()

    def test_render_text_format(self):
        report = coach.CoachingReport(
            optimized_prompt_fragments=["## Test Fragment\nTest content"],
            tool_playbooks={"Read": ["Step 1: Read", "Step 2: Edit"]},
            token_budget_tips=["Test tip"],
        )
        rendered = coach.render_coaching_report(report, format="text")
        self.assertIn("COACHING REPORT", rendered)
        self.assertIn("Test Fragment", rendered)
        self.assertIn("Test tip", rendered)


if __name__ == "__main__":
    unittest.main()


class TestHarnessPrompt(unittest.TestCase):
    def test_generate_harness_prompt_small(self):
        prompt = coach.generate_harness_prompt(model_tier="small")
        self.assertIn("Never claim inability", prompt)
        self.assertIn("Fetch primary sources", prompt)
        self.assertIn("Log without asking", prompt)
        self.assertIn("SSH", prompt)
        self.assertIn("<2KB", prompt)
        self.assertIn("Blunt, dry", prompt)

    def test_generate_harness_prompt_medium(self):
        prompt = coach.generate_harness_prompt(model_tier="medium")
        self.assertIn("Delegate", prompt)

    def test_generate_harness_prompt_large(self):
        prompt = coach.generate_harness_prompt(model_tier="large")
        self.assertIn("Context Engineering", prompt)

    def test_harness_prompt_length(self):
        prompt = coach.generate_harness_prompt()
        # Should be under 2KB to respect the cap
        self.assertLess(len(prompt), 3000)


