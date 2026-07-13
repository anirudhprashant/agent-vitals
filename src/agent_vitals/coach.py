"""Coach module: small-model performance optimization.

Analyzes session data to generate optimized system prompts, tool-use
playbooks, and operational strategies that make small models perform
like opus/fable-class models. Based on reverse-engineering of Claude
Code, Opus 4.x, and Fable 5 system prompts.

Key insight: the gap between small and large models is NOT reasoning
capacity — it is context quality, tool selection, and prompt structure.
This module extracts what actually works from your session data and
turns it into reusable prompt fragments and playbooks.
"""


from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Skip records with very large string values (inline base64 images, etc.).
_MAX_RECORD_BYTES = 200_000

def _should_skip_record(line: str) -> bool:
    return len(line) > _MAX_RECORD_BYTES




@dataclass
class ToolSequence:
    """A sequence of tool calls observed in a session."""
    tools: tuple[str, ...]
    count: int = 1
    success: bool = True
    avg_tokens: float = 0.0


@dataclass
class FailurePattern:
    """A recurring failure pattern."""
    tool: str
    error_indicator: str
    count: int
    recovery_tools: list[str] = field(default_factory=list)


@dataclass
class CoachingReport:
    """Generated coaching recommendations for small-model performance."""
    optimized_prompt_fragments: list[str] = field(default_factory=list)
    tool_playbooks: dict[str, list[str]] = field(default_factory=dict)
    failure_recoveries: list[FailurePattern] = field(default_factory=list)
    token_budget_tips: list[str] = field(default_factory=list)
    model_specific_tips: dict[str, list[str]] = field(default_factory=dict)


def _extract_tool_calls_from_session(path: Path) -> list[dict[str, Any]]:
    """Extract tool call records from a session JSONL file."""
    calls = []
    try:
        with path.open("rb") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                
                # Claude Code format
                msg = rec.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                calls.append({
                                    "tool": block.get("name", ""),
                                    "input": block.get("input", {}),
                                    "format": "claude_code",
                                })
                
                # pi format
                if rec.get("type") == "message":
                    inner = rec.get("message", {})
                    if isinstance(inner, dict) and inner.get("role") == "assistant":
                        content = inner.get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "toolCall":
                                    args = block.get("arguments", {})
                                    tool_name = args.get("tool", "")
                                    calls.append({
                                        "tool": tool_name,
                                        "input": {k: v for k, v in args.items() if k != "tool"},
                                        "format": "pi",
                                    })
    except OSError:
        pass
    return calls


def _extract_tool_sequences(calls: list[dict[str, Any]], window: int = 3) -> list[ToolSequence]:
    """Extract common tool call sequences."""
    sequences: dict[tuple[str, ...], list[float]] = defaultdict(list)
    
    for i in range(len(calls) - window + 1):
        seq = tuple(call["tool"] for call in calls[i:i + window])
        # Simple success heuristic: if followed by another tool call, likely succeeded
        success = i + window < len(calls)
        sequences[seq].append(1.0 if success else 0.0)
    
    result = []
    for seq, successes in sequences.items():
        result.append(ToolSequence(
            tools=seq,
            count=len(successes),
            success=sum(successes) / len(successes) > 0.5,
            avg_tokens=0.0,  # Could be enhanced with actual token data
        ))
    return sorted(result, key=lambda s: s.count, reverse=True)


def _detect_failure_patterns(calls: list[dict[str, Any]]) -> list[FailurePattern]:
    """Detect repeated tool calls that might indicate failures."""
    patterns: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    
    # Look for repeated identical tool calls (potential retry loops)
    i = 0
    while i < len(calls):
        tool = calls[i]["tool"]
        cmd = calls[i].get("input", {}).get("command", "")
        # Find consecutive identical calls
        j = i + 1
        while j < len(calls) and calls[j]["tool"] == tool and calls[j].get("input", {}).get("command", "") == cmd:
            j += 1
        if j - i >= 3:  # 3+ identical calls = potential failure loop
            # Look for what comes after the retry block
            recovery = []
            if j < len(calls):
                recovery = [calls[j]["tool"]]
            patterns[(tool, "identical_retry")].append(recovery)
        i = j
    
    result = []
    for (tool, error_type), recoveries in patterns.items():
        # Find most common recovery pattern
        recovery_counter = Counter(tuple(r) for r in recoveries)
        most_common = list(recovery_counter.most_common(1)[0][0]) if recovery_counter else []
        result.append(FailurePattern(
            tool=tool,
            error_indicator=f"{error_type} (repeated {sum(1 for r in recoveries if len(r) > 0)}x)",
            count=len(recoveries),
            recovery_tools=most_common,
        ))
    return result


def _generate_token_budget_tips(calls: list[dict[str, Any]]) -> list[str]:
    """Generate token budget optimization tips."""
    tips = []
    tool_counts = Counter(call["tool"] for call in calls)
    total = len(calls)
    
    if total == 0:
        return tips
    
    # Check for dominant tools
    for tool, count in tool_counts.most_common(5):
        pct = count / total * 100
        if pct > 30:
            tips.append(
                f"Tool '{tool}' accounts for {pct:.0f}% of calls. "
                "Consider batching or caching results."
            )
    
    # Check for repetitive patterns
    bash_cmds = [
        call.get("input", {}).get("command", "")
        for call in calls
        if call["tool"] in ("Bash", "bash")
    ]
    if bash_cmds:
        unique_cmds = len(set(bash_cmds))
        if unique_cmd_ratio := unique_cmds / len(bash_cmds) < 0.3:
            tips.append(
                f"Low command diversity in Bash calls ({unique_cmd_ratio:.0%} unique). "
                "Consolidate repeated commands or use loops."
            )
    
    return tips


def _generate_model_tips(calls: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Generate model-specific coaching tips.
    
    Small models fail differently than large models. They need:
    - Explicit tool selection rules (they can't infer from context)
    - Sequential workflows (they can't plan multi-step)
    - Verification checkpoints (they can't self-check)
    - Error recovery mappings (they can't improvise)
    - Token budgets (they can't estimate cost)
    """
    tips: dict[str, list[str]] = {
        "small": [],
        "medium": [],
        "large": [],
    }
    
    # Analyze tool diversity and complexity
    tool_counts = Counter(c["tool"] for c in calls)
    total_calls = len(calls)
    unique_tools = len(tool_counts)
    
    # Small models need explicit tool selection rules
    if total_calls > 10:
        top_tools = [t for t, _ in tool_counts.most_common(5)]
        tool_list = ", ".join(top_tools[:3])
        tips["small"].append(
            f"Tool selection discipline: observed {unique_tools} tools. "
            f"For small models, create explicit rules: "
            f"Use {tool_list} for common tasks."
        )
    
    # Check for Bash-heavy workflows (Claude Code pattern)
    bash_count = tool_counts.get("Bash", 0) + tool_counts.get("bash", 0)
    bash_pct = bash_count / total_calls * 100 if total_calls > 0 else 0
    
    if bash_pct > 50:
        tips["small"].append(
            f"Bash dominates ({bash_pct:.0f}% of calls). For small models, provide "
            f"explicit Bash command templates and shell safety rules. "
            f"Small models hallucinate flags and paths without examples."
        )
        tips["small"].append(
            "Add to system prompt: 'When using Bash, first run `cmd --help` if unsure "
            "about flags. Never chain commands without testing each one.'"
        )
    
    # Check for Read/Edit patterns (context reloading)
    read_count = tool_counts.get("Read", 0) + tool_counts.get("read", 0)
    edit_count = tool_counts.get("Edit", 0) + tool_counts.get("edit", 0)
    
    if read_count > edit_count * 2:
        tips["small"].append(
            f"High Read/Edit ratio ({read_count}/{edit_count}). Small models waste tokens "
            f"re-reading files. Add rule: 'After reading a file, remember its content for "
            f"the rest of the session. Never re-read the same file unless it changed.'"
        )
    
    # Check for sequential patterns (workflows)
    transitions = []
    for i in range(len(calls) - 1):
        transitions.append((calls[i]["tool"], calls[i+1]["tool"]))
    trans_counts = Counter(transitions)
    
    if trans_counts:
        top_trans = trans_counts.most_common(1)[0]
        tips["small"].append(
            f"Proven workflow detected: {top_trans[0][0]} → {top_trans[0][1]} "
            f"({top_trans[1]}x). Codify this as a sequence rule: "
            f"'When doing X, always follow with Y before Z.'"
        )
    
    # Check for retry loops (failure patterns)
    failure_count = sum(1 for i in range(len(calls) - 1) 
                        if calls[i]["tool"] == calls[i+1]["tool"] and 
                        calls[i].get("input") == calls[i+1].get("input"))
    if failure_count > 5:
        tips["small"].append(
            f"Detected {failure_count} identical retry blocks. Small models get stuck "
            f"in loops without explicit escape rules. Add to prompt: "
            f"'If a tool fails 3 times with the same input, try a completely different "
            f"approach or ask for help. Never repeat the same failed call.'"
        )
        tips["medium"].append(
            "Consider adding a recovery strategy section with exact error → action mappings."
        )
    
    # Token budget for small models
    if total_calls > 20:
        tips["small"].append(
            f"Session has {total_calls} tool calls. Small models benefit from explicit "
            f"token budgets: 'Each tool call costs ~10K tokens. Plan your approach "
            f"before executing. Prefer cheap tools (grep, glob) over expensive ones "
            f"(read full file).'"
        )
    
    # Pi-harness inspired rules
    tips["small"].append(
        "Never claim inability — attempt first. If a task looks doable with your "
        "tools, just do it. Report the real error if it fails, don't say 'I can't' "
        "before trying."
    )
    
    tips["small"].append(
        "Fetch primary sources before defending hard technical claims (specs, API "
        "limits, prices, dates). Training data is not a source. If the user asks "
        "'are you sure?' or 'check the internet', fetch the vendor doc first."
    )
    
    tips["small"].append(
        "Log without asking. Errors → problems, credentials → credentials room, "
        "infra/decisions → technical. Cite the drawer ID in the same turn. One "
        "sentence, no fuss."
    )
    
    tips["small"].append(
        "SSH/remote access: just try it. Use `ssh <alias> \"<cmd>\"` via bash. "
        "Don't claim inability to SSH — attempt the command and report the actual "
        "error if it fails."
    )
    
    tips["small"].append(
        "Cap your context. Keep system prompts under 2KB, rules under 60 lines. "
        "Redundant MCP tools cost 5-10KB per turn. Drop overlapping tools aggressively."
    )
    
    tips["small"].append(
        "Voice: blunt, dry, self-deprecating. No corporate speak, no 'would you "
        "like me to', no 'Sure, happy to help'. Just do it."
    )
    
    tips["small"].append(
        "Escalate hard slices to a larger model when you drift on complex multi-file "
        "logic. But attempt the task yourself first — don't immediately delegate."
    )
    
    return tips


def _generate_optimized_prompt_fragments(
    sequences: list[ToolSequence],
    failures: list[FailurePattern],
    model: str = "small",
) -> list[str]:
    """Generate optimized system prompt fragments based on observed patterns."""
    fragments = []
    
    # Fragment 1: Tool selection rules based on successful sequences
    if sequences:
        top_seq = sequences[0]
        if top_seq.success and top_seq.count >= 2:
            fragments.append(
                f"## Proven Tool Sequences\n"
                f"For optimal results, follow these observed successful patterns:\n"
                f"- When working with code: {' → '.join(top_seq.tools)}\n"
                f"Repeat this sequence for similar tasks."
            )
    
    # Fragment 2: Error recovery rules
    if failures:
        fragments.append(
            "## Error Recovery Rules\n"
            "If a tool fails or returns empty/error:\n"
        )
        for fp in failures[:3]:
            if fp.recovery_tools:
                fragments.append(
                    f"- {fp.tool} error → try {' or '.join(fp.recovery_tools)}"
                )
        fragments.append("- If uncertain, ask clarifying question before proceeding.")
    
    # Fragment 3: Model-specific guidance
    if model in ("small", "haiku", "mini"):
        fragments.append(
            "## Execution Discipline\n"
            "- Verify each tool result before next action\n"
            "- State your plan before executing\n"
            "- If uncertain, prefer asking over guessing\n"
            "- One tool call per turn unless results are trivial"
        )
    elif model in ("medium", "sonnet"):
        fragments.append(
            "## Efficiency Rules\n"
            "- Batch related tool calls when possible\n"
            "- Use grep/glob before reading full files\n"
            "- Cache reusable results in scratchpad"
        )
    
    # Fragment 4: Token budget awareness
    fragments.append(
        "## Token Budget\n"
        "- Each tool call costs tokens. Prefer cheap tools (grep, glob) over expensive ones (read full files).\n"
        "- If output exceeds 1000 lines, summarize or narrow scope.\n"
        "- Reuse previous results instead of re-querying."
    )
    
    return fragments


def _generate_tool_playbooks(sequences: list[ToolSequence]) -> dict[str, list[str]]:
    """Generate playbook snippets for common tool combinations."""
    playbooks = {}
    
    # Group sequences by first tool
    by_first = defaultdict(list)
    for seq in sequences:
        if seq.tools:
            by_first[seq.tools[0]].append(seq)
    
    # Generate playbooks for top tool combinations
    for tool, seqs in by_first.items():
        # Pick the best sequence for this starting tool
        best = max(seqs, key=lambda s: s.count * (1 if s.success else 0))
        if best.count >= 2 and best.success:
            playbooks[tool] = [
                f"Step 1: {best.tools[0]}",
                *[f"Step {i+2}: {t}" for i, t in enumerate(best.tools[1:])],
                f"Repeat pattern for similar tasks (observed {best.count}x in sessions)",
            ]
    
    return playbooks


def generate_harness_prompt(model_tier: str = "small") -> str:
    """Generate a complete system prompt based on pi harness patterns."""
    return """# System Prompt — Small-Model Performance Harness

## Hard Rules
1. Never claim inability. Attempt first. If a task looks doable, just do it.
2. Fetch primary sources before defending hard technical claims (specs, API limits, prices, dates).
3. Log without asking. Errors→problems, credentials→credentials, infra→technical. Cite drawer ID same turn.
4. SSH/remote: just try it. Use `ssh <alias> \"<cmd>\"` via bash. Report real errors.
5. Cap context: system prompt <2KB, rules <60 lines. Drop redundant tools.

## Voice
Blunt, dry, self-deprecating. No corporate speak, no 'would you like me to', no 'Sure, happy to help'. Just do it.

## Tool Discipline
- Prefer cheap tools (grep, glob) over expensive ones (read full file).
- Use Bash for shell, Read for files, Edit for changes.
- After reading a file, remember it — don't re-read unless changed.
- Verify each tool result before next action.

## Delegation
- Do it yourself: small, single-step, conversational.
- Delegate: multi-phase, parallel concerns, fresh-context win, token-budget protection.
- After subagent finishes: summarize in your own voice, never just paste output.

## Error Recovery
- If a tool fails 3 times with same input, try a completely different approach.
- Never repeat the same failed call.
- If uncertain, ask clarifying question before proceeding.

## Token Budget
- Each tool call costs tokens. Plan before executing.
- If output exceeds 1000 lines, summarize or narrow scope.
- Reuse previous results instead of re-querying.

## Verification
- Before editing, read the file first.
- After tool call, verify result matches intent.
- If you lack information, search before answering.

## Research Discipline
- When in doubt about a hard spec, fetch the vendor's official doc first, cite the exact line, then answer.
- Don't lean on training-data recall for sourceable specs.

## Context Engineering
- Keep system prompt lean and high-signal.
- Drop redundant MCP tools — each unused tool costs 5-10KB per turn.
"""

def analyze_session(path: Path, model_tier: str = "small") -> CoachingReport:
    """Analyze a session and generate coaching recommendations."""
    calls = _extract_tool_calls_from_session(path)
    if not calls:
        return CoachingReport()
    
    sequences = _extract_tool_sequences(calls)
    failures = _detect_failure_patterns(calls)
    token_tips = _generate_token_budget_tips(calls)
    model_tips = _generate_model_tips(calls)
    fragments = _generate_optimized_prompt_fragments(sequences, failures, model_tier)
    playbooks = _generate_tool_playbooks(sequences)
    
    return CoachingReport(
        optimized_prompt_fragments=fragments,
        tool_playbooks=playbooks,
        failure_recoveries=failures,
        token_budget_tips=token_tips,
        model_specific_tips=model_tips,
    )


def render_coaching_report(report: CoachingReport, format: str = "text") -> str:
    """Render coaching report in specified format."""
    if format == "text":
        lines = []
        lines.append("=" * 60)
        lines.append("COACHING REPORT")
        lines.append("=" * 60)
        
        if report.optimized_prompt_fragments:
            lines.append("\n## Optimized System Prompt Fragments")
            lines.append("Add these to your system prompt for improved performance:\n")
            for i, frag in enumerate(report.optimized_prompt_fragments, 1):
                lines.append(f"--- Fragment {i} ---")
                lines.append(frag)
                lines.append("")
        
        if report.tool_playbooks:
            lines.append("\n## Tool-Use Playbooks")
            lines.append("Proven sequences for common tasks:\n")
            for tool, steps in report.tool_playbooks.items():
                lines.append(f"### {tool} workflow")
                for step in steps:
                    lines.append(f"  {step}")
                lines.append("")
        
        if report.failure_recoveries:
            lines.append("\n## Failure Recovery Patterns")
            lines.append("Detected error patterns and fixes:\n")
            for fp in report.failure_recoveries:
                lines.append(f"  {fp.tool}: {fp.error_indicator}")
                if fp.recovery_tools:
                    lines.append(f"    → Try: {', '.join(fp.recovery_tools)}")
                lines.append("")
        
        if report.token_budget_tips:
            lines.append("\n## Token Budget Tips")
            for tip in report.token_budget_tips:
                lines.append(f"  • {tip}")
        
        if report.model_specific_tips:
            lines.append("\n## Model-Specific Tips")
            for tier, tips in report.model_specific_tips.items():
                if tips:
                    lines.append(f"\n  [{tier.upper()} models]")
                    for tip in tips:
                        lines.append(f"    • {tip}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
    
    elif format == "json":
        import dataclasses
        return json.dumps(dataclasses.asdict(report), indent=2)
    
    return ""
