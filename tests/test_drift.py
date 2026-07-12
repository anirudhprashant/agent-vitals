"""Tests for agent_vitals.drift."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_vitals import drift as d


def _write_mcp_json(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}))


class TestDetectMcpDrift(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Patch detect_hosts to return our fake hosts
        from agent_vitals.primer import AgentHost
        self.pi_cfg = self.tmp / "pi" / "mcp.json"
        self.cc_cfg = self.tmp / "cc" / ".mcp.json"
        self.fake_hosts = [
            AgentHost(name="pi", config_path=self.pi_cfg, config_format="json",
                      skill_dir=None, rule_file=None),
            AgentHost(name="Claude Code", config_path=self.cc_cfg, config_format="json",
                      skill_dir=None, rule_file=None),
        ]
        self._patch = mock.patch("agent_vitals.drift.detect_hosts", lambda: self.fake_hosts)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_no_drift_when_only_one_host(self):
        _write_mcp_json(self.pi_cfg, {"foo": {"command": "bar"}})
        self.fake_hosts = [self.fake_hosts[0]]  # remove cc
        findings = d.detect_mcp_drift()
        self.assertEqual(findings, [])

    def test_no_drift_when_targets_match(self):
        # Same target, both hosts → low-severity duplicate (intentional multi-host)
        for cfg in (self.pi_cfg, self.cc_cfg):
            _write_mcp_json(cfg, {"foo": {"command": "bar", "args": ["--x"]}})
        findings = d.detect_mcp_drift()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "low")
        self.assertEqual(findings[0].kind, "mcp_dup")

    def test_high_severity_when_targets_differ(self):
        for cfg, cmd in [(self.pi_cfg, "/usr/bin/foo"), (self.cc_cfg, "foo")]:
            _write_mcp_json(cfg, {"foo": {"command": cmd}})
        findings = d.detect_mcp_drift()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "high")
        self.assertEqual(findings[0].kind, "mcp_drift")

    def test_http_servers_compared_by_url(self):
        for cfg in (self.pi_cfg, self.cc_cfg):
            _write_mcp_json(cfg, {"remote": {"type": "http", "url": "https://api.example.com"}})
        findings = d.detect_mcp_drift()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "low")

    def test_http_urls_differ_is_drift(self):
        _write_mcp_json(self.pi_cfg,  {"remote": {"type": "http", "url": "https://api-a.example.com"}})
        _write_mcp_json(self.cc_cfg,  {"remote": {"type": "http", "url": "https://api-b.example.com"}})
        findings = d.detect_mcp_drift()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "high")

    def test_missing_config_files_are_skipped(self):
        # No config files at all
        findings = d.detect_mcp_drift()
        self.assertEqual(findings, [])

    def test_corrupt_json_is_skipped(self):
        self.pi_cfg.parent.mkdir(parents=True, exist_ok=True)
        self.pi_cfg.write_text("{this is not valid json")
        _write_mcp_json(self.cc_cfg, {"foo": {"command": "bar"}})
        # pi_cfg is corrupt but cc_cfg is fine; should not crash
        findings = d.detect_mcp_drift()
        # No duplicate found because pi was skipped; only one valid host
        self.assertEqual(findings, [])

    def test_multiple_servers_at_once(self):
        # Two servers in the same config, multiple drift types
        for cfg in (self.pi_cfg, self.cc_cfg):
            _write_mcp_json(cfg, {
                "matching": {"command": "same"},
                "diff_cmd": {"command": cfg.name},  # different per host
                "diff_args": {"command": "x", "args": [cfg.name]},  # different args
            })
        findings = d.detect_mcp_drift()
        kinds = {f.kind for f in findings}
        names = {f.name for f in findings}
        self.assertIn("matching", names)  # same target → mcp_dup
        self.assertIn("diff_cmd", names)
        self.assertIn("diff_args", names)
        self.assertEqual(kinds, {"mcp_drift", "mcp_dup"})


class TestRenderDriftReport(unittest.TestCase):
    def test_empty_findings(self):
        out = d.render_drift_report([])
        self.assertIn("no inconsistencies", out)

    def test_severity_grouping(self):
        findings = [
            d.DriftFinding(severity="high", kind="mcp_drift", name="x", detail="..."),
            d.DriftFinding(severity="low", kind="mcp_dup", name="y", detail="..."),
        ]
        out = d.render_drift_report(findings)
        self.assertIn("[high]", out)
        self.assertIn("[low]", out)
        self.assertLess(out.index("[high]"), out.index("[low]"))


class TestDriftFindingSerialization(unittest.TestCase):
    def test_to_dict(self):
        f = d.DriftFinding(severity="high", kind="mcp_drift", name="foo", detail="bar")
        d_dict = f.to_dict()
        self.assertEqual(d_dict, {"severity": "high", "kind": "mcp_drift", "name": "foo", "detail": "bar"})


if __name__ == "__main__":
    unittest.main()
