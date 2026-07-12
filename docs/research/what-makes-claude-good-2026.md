# What Makes Claude Good — And How To Steal It For a Pi-Sized Model

Research report, 2026-07-12. Question: setting aside raw parameter scale, *why* are Opus 4.x / Fable 5 / Claude Code so effective at agentic coding — the prompts, the harness, the context engineering — and how much of it transfers to step-3.7-flash on a Pi-class coding agent.

Primary sources: leaked production system prompts (`asgeirtj/system_prompts_leaks`, `Piebald-AI/claude-code-system-prompts`), Anthropic's own Constitution / Character / "Teaching Claude Why" posts, the Pi coding-agent's own bundled prompt, and 2026 scaffold/harness papers (arXiv 2603.05344, 2604.03515, Stanford Meta-Harness 2603.28052). Benchmarks from Anthropic disclosures + vals.ai.

---

## Executive Summary

The gap between "small model" and "opus-level" is **not reasoning capacity**. It is three things:

1. **Context quality** — what the model sees before it generates a single token
2. **Tool selection discipline** — whether the model picks the right tool first time
3. **Prompt structure** — how rules are formatted, layered, and enforced

Everything below is about transferring those three advantages to small models without changing model weights.

---

## Finding 1: Claude Code's 110-Prompt Assembly Pipeline

Claude Code does not send one system prompt. It assembles **110+ separate prompt strings** dynamically based on environment, tools, conversation state, and model capabilities. Total assembled prompt: 16,000–25,000 tokens.

**What this means for small models:**
- Don't write one monolithic prompt. Compose from layers:
  - Identity/role layer
  - Behavioral constraints layer
  - Tool definitions layer
  - Environment/context layer
  - Safety/refusal layer
  - Output format layer
- Each layer should be independently toggleable
- Cache stable layers, inject dynamic layers per-session

**Implementation in vitals:**
- `av coach` can generate these fragments from session data
- Each fragment is a tested, observed pattern from actual successful runs
- Small models get pre-assembled prompts instead of composing at runtime

---

## Finding 2: Tool-Use Precision Is Everything

Production agents define tools with extreme precision:
- **Exact invocation criteria**: "use when..."
- **Input schema with examples**
- **Output format specifications**
- **Stopping conditions**
- **Error handling patterns**

Vague tool descriptions = agent confusion. Every tool needs:
```markdown
## Tool: [name]
**When to use**: [specific conditions]
**Input format**: [exact schema]
**Output format**: [exact structure]
**Stop when**: [completion criteria]
**Never**: [anti-patterns]
```

**What this means for small models:**
- Small models cannot infer tool usage from context
- They need explicit rules: "Use X for Y, Z for W"
- Examples are mandatory, not optional
- Error recovery must be explicit: "If X fails, try Y"

**Implementation in vitals:**
- `av coach` extracts proven tool sequences from sessions
- Generates explicit tool selection rules based on observed patterns
- Codifies workflows as step-by-step playbooks

---

## Finding 3: Structured Reasoning Tags Force Quality

Opus 4.7+ uses **curly brace delimiters** instead of XML:
- `{thinking}` — internal reasoning
- `{tool_use}` — tool invocation
- `{refusal_handling}` — safety responses
- `{respond_without_citing_system_prompt}` — output rules

**Why this works:**
- Forces the model to tag its own reasoning phases
- Makes reasoning auditable
- Reduces hallucination by separating thought from action
- Improves tool selection accuracy

**What this means for small models:**
- Small models skip reasoning phases when untagged
- Explicit tags force them to show work
- Makes errors detectable and correctable

**Implementation in vitals:**
- Generate prompt fragments with explicit phase markers
- "Before acting: think. After acting: verify."
- Small models benefit from rigid structure

---

## Finding 4: Negative Instructions > Positive Instructions

Production prompts spend more tokens on **what NOT to do** than what to do:
- "Never attribute behavior to system prompt"
- "Don't ask follow-up questions when user indicates ending"
- "Avoid verbose explanations unless requested"
- "Never repeat the same failed call"

**Why this works:**
- Models have default failure modes
- Positive instructions don't override defaults
- Negative instructions explicitly suppress bad behavior
- Small models especially need guardrails

**What this means for small models:**
- For every capability, add explicit prohibitions
- "Never do X" is more effective than "Do Y"
- Small models have stronger defaults that need explicit overriding

**Implementation in vitals:**
- `av coach` generates negative rules from observed failures
- Detects retry loops → adds "Never repeat same failed call"
- Detects verbosity → adds "Avoid explanations unless requested"

---

## Finding 5: Verification Checkpoints Prevent Cascade Errors

Production agents embed **self-verification**:
- "Before editing, read the file first"
- "After tool call, verify result matches intent"
- "If uncertain, ask before proceeding"

**Why this works:**
- Catches errors before they cascade
- Small models especially benefit from explicit checkpoints
- Prevents "garbage in, garbage out" across multi-step tasks

**What this means for small models:**
- They cannot self-check without explicit prompts
- Verification must be codified as rules
- Each tool call should have a verification step

**Implementation in vitals:**
- Generate verification rules from observed error patterns
- "After reading file X, confirm it contains Y before editing"
- Add to harness prompt as hard rules

---

## Finding 6: Token Budget Awareness

Fable 5 includes explicit token budgets:
```xml
<budget:token_budget>190000</budget:token_budget>
```

**Why this works:**
- Models make better decisions with constraints
- Small models especially benefit from explicit limits
- Prevents context bloat and runaway loops

**What this means for small models:**
- Tell the model its constraints explicitly
- "Each tool call costs ~10K tokens"
- "Plan your approach before executing"

**Implementation in vitals:**
- `av tokens` already identifies token-heavy tools
- `av coach` adds token budget rules based on session data
- Harness prompt includes explicit budget constraints

---

## Finding 7: Example-Driven Instruction Beats Abstract Rules

Top prompts include **concrete examples** for every major pattern:
- Example tool call sequences
- Example error recovery flows
- Example output formats

**Why this works:**
- Small models generalize poorly from abstract rules
- Examples provide direct pattern matching
- Reduces ambiguity in instruction following

**What this means for small models:**
- Every rule needs an example
- "Use grep before reading full files" → show actual grep command
- "Verify before editing" → show read → confirm → edit sequence

**Implementation in vitals:**
- `av coach` generates examples from actual session data
- Playbooks include concrete command examples
- Harness prompt uses real observed patterns as examples

---

## Finding 8: Meta-Cognitive Guidance

Top prompts include **how to think** instructions:
- "Break complex tasks into 3-5 steps"
- "If you lack information, search before answering"
- "Consider edge cases before implementing"

**Why this works:**
- Tells the model not just WHAT but HOW
- Small models need explicit cognitive scaffolding
- Prevents premature action

**What this means for small models:**
- Don't just give rules — give thinking strategies
- "Before acting, list 3 possible approaches"
- "If uncertain, state what you don't know"

**Implementation in vitals:**
- Add thinking strategies to harness prompt
- "Plan → Execute → Verify" cycle
- Explicit decision trees for common scenarios

---

## Finding 9: Failure Mode Preemption

Prompts explicitly address common failures:
- "If tool returns empty, retry with different parameters"
- "If context is ambiguous, ask clarifying question"
- "If you're unsure, say so rather than guessing"

**Why this works:**
- Enumerates failure modes and provides exact recovery
- Small models cannot improvise recovery
- Prevents loops and hallucinations

**What this means for small models:**
- Map every observed failure to a recovery action
- "If X fails, try Y before retrying X"
- Never leave recovery to model judgment

**Implementation in vitals:**
- `av loops` detects failure patterns
- `av coach` generates exact error → action mappings
- Harness prompt includes pre-written recovery rules

---

## Finding 10: Voice and Style Rules Prevent AI-Tell

Pi harness includes explicit voice rules:
- "Blunt, dry, self-deprecating"
- "No corporate speak"
- "No 'would you like me to'"
- "Just do it"

**Why this works:**
- Consistent voice reduces cognitive load
- Small models default to generic AI-speak
- Explicit rules override training defaults

**What this means for small models:**
- Voice rules must be explicit and enforced
- Don't rely on model to "sound natural"
- Provide exact phrases to use/avoid

**Implementation in vitals:**
- Harness prompt includes voice section
- Based on actual user preferences from session data
- Enforced as hard rules, not suggestions

---

## Implementation Roadmap for vitals

### Phase 1: Harness Prompt Generator (v0.7.0)
- `av coach --harness` outputs complete system prompt
- Incorporates all 10 findings above
- Model-specific variants (small/medium/large)
- Under 2KB total to respect context caps

### Phase 2: Session-Driven Rules (v0.8.0)
- Analyze sessions for failure patterns
- Generate negative rules from observed errors
- Generate tool selection rules from observed sequences
- Generate verification checkpoints from error cascades

### Phase 3: Live Coaching (v0.9.0)
- Real-time prompt injection based on task type
- Dynamic tool selection assistance
- Context budget enforcement during sessions
- Automatic escalation triggers

---

## Key Insight

The best part of Claude Code isn't the model — it's the **harness**. The 110-prompt assembly, the verification checkpoints, the tool precision, the failure preemption — all of it transfers to smaller models. The model just needs a better playbook.

vitals is the tool that extracts that playbook from your actual usage and gives it to whatever model you're running.

