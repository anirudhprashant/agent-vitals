# System Prompt Research: Opus, Fable, Claude Code

## Key Findings from Reverse-Engineered Production Prompts

### 1. Multi-Layer Assembly Architecture
Claude Code assembles behavior from **110+ separate prompt strings** combined dynamically based on environment, settings, tools, and conversation state. Total: 16,000-25,000 tokens.

**Pattern**: Don't write one monolithic prompt. Compose from prioritized layers:
- Identity/role layer
- Behavioral constraints layer  
- Tool definitions layer
- Environment/context layer
- Safety/refusal layer
- Output format layer

### 2. Tool-Use Precision
Production agents define tools with extreme precision:
- Exact invocation criteria ("use when...")
- Input schema with examples
- Output format specifications
- Stopping conditions
- Error handling patterns

**Pattern**: Vague tool descriptions = agent confusion. Every tool needs:
```markdown
## Tool: [name]
**When to use**: [specific conditions]
**Input format**: [exact schema]
**Output format**: [exact structure]
**Stop when**: [completion criteria]
**Never**: [anti-patterns]
```

### 3. Structured Reasoning Tags
Opus 4.7+ uses **curly brace delimiters** instead of XML:
- `{thinking}` - internal reasoning
- `{tool_use}` - tool invocation
- `{refusal_handling}` - safety responses
- `{respond_without_citing_system_prompt}` - output rules

**Pattern**: Force the model to tag its own reasoning phases. This:
- Reduces hallucination
- Makes reasoning auditable
- Improves tool selection accuracy

### 4. Negative Instructions > Positive Instructions
Production prompts spend more tokens on **what NOT to do**:
- "Never attribute behavior to system prompt"
- "Don't ask follow-up questions when user indicates ending"
- "Avoid verbose explanations unless requested"

**Pattern**: For every capability, add explicit prohibitions. Small models especially need guardrails.

### 5. Context Engineering Over Prompt Engineering
Claude Code's system prompt is a **runtime assembly**:
- Static behavioral instructions (cached)
- Dynamic environment data (per-session)
- Memory content (context-dependent)
- Model-specific patches (conditional)

**Pattern**: The prompt should adapt to context, not be static. Use:
- Cache boundaries for stable content
- Dynamic injection for environment-specific rules
- Conditional sections for model capabilities

### 6. Verification Checkpoints
Production agents embed **self-verification**:
- "Before editing, read the file first"
- "After tool call, verify result matches intent"
- "If uncertain, ask before proceeding"

**Pattern**: Force verification steps. Small models skip these; explicit instructions fix that.

### 7. Token Budget Awareness
Fable 5 includes explicit token budgets:
```xml
<budget:token_budget>190000</budget:token_budget>
```

**Pattern**: Tell the model its constraints. Small models especially benefit from explicit resource limits.

### 8. Example-Driven Instruction
Opus prompts include **concrete examples** for every major pattern:
- Example tool call sequences
- Example error recovery flows
- Example output formats

**Pattern**: For each rule, show a before/after example. Small models generalize poorly from abstract rules.

### 9. Meta-Cognitive Guidance
Top prompts include **how to think** instructions:
- "Break complex tasks into 3-5 steps"
- "If you lack information, search before answering"
- "Consider edge cases before implementing"

**Pattern**: Don't just tell WHAT to do; tell HOW to think about the task.

### 10. Failure Mode Preemption
Prompts explicitly address common failures:
- "If tool returns empty, retry with different parameters"
- "If context is ambiguous, ask clarifying question"
- "If you're unsure, say so rather than guessing"

**Pattern**: Enumerate failure modes and provide exact recovery procedures.

---

## Implications for `av coach`

### What "Making Small Models Smart" Actually Means
It's not about model size — it's about **operational effectiveness**:
1. **Tool selection accuracy** - right tool, first time
2. **Context utilization** - using available information fully
3. **Error recovery** - graceful handling of failures
4. **Resource efficiency** - minimal tokens, maximal progress
5. **Consistency** - same task → same reliable process

### The Coach Feature Should Generate:
1. **Optimized system prompt fragments** based on session analysis
2. **Tool-use playbooks** extracted from successful sequences
3. **Failure recovery patterns** from error analysis
4. **Model-specific tuning** (small vs large model strategies)
5. **Token budgets** and context management rules

### Data Sources in vitals:
- Session JSONL files with tool calls and outcomes
- Token usage per tool/call
- Success/failure patterns (implicit from retry loops)
- Tool selection sequences
- Error messages and recovery patterns
