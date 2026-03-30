# Steering as a Unified Agent Intervention Primitive

> Exploration document — not a specification or commitment. This doc investigates whether Strands Agents' **Steering** concept generalizes into a primitive that could subsume Cedar authorization, content guardrails, and other agent control mechanisms.

## Table of Contents

- [What Strands Steering Is](#what-strands-steering-is)
- [How Cedar Auth Maps to Steering](#how-cedar-auth-maps-to-steering)
- [The Unified Intervention Primitive](#the-unified-intervention-primitive)
- [Concrete Instances](#concrete-instances)
- [Composability](#composability)
- [What This Means for the Cedar Plugin](#what-this-means-for-the-cedar-plugin)
- [Open Questions](#open-questions)

---

## What Strands Steering Is

Strands Agents' [Steering](https://strandsagents.com/docs/user-guide/concepts/plugins/steering/) is a modular prompting system that replaces monolithic "do everything" system prompts with **just-in-time contextual guidance**. Instead of front-loading 30+ steps into a single prompt (which models tend to ignore), steering injects relevant instructions at the moment they matter.

### Two Intervention Points

**Tool Steering** — intercepts *before* a tool executes and returns one of:

| Action | Effect |
|--------|--------|
| **Proceed** | Tool executes normally |
| **Guide** | Tool call cancelled; contextual feedback injected into the conversation so the model can retry with better instructions |
| **Interrupt** | Execution pauses for human input |

**Model Steering** — evaluates *after* a model response and returns one of:

| Action | Effect |
|--------|--------|
| **Proceed** | Accept the response as-is |
| **Guide** | Discard the response, inject guidance, and retry |

### How It Works Mechanically

1. **Context accumulation** — `SteeringContextCallback` handlers listen to hook events (`BeforeToolCallEvent`, `AfterToolCallEvent`) and update a shared `steering_context` dictionary. The built-in `LedgerProvider` tracks tool call history with inputs, outputs, timing, and execution status.

2. **Evaluation** — The primary implementation is `LLMSteeringHandler`, which passes the accumulated context plus a developer-authored `system_prompt` to an LLM that decides the steering action. This means guidance is expressed in **natural language**, not formal rules.

3. **Intervention** — The handler returns a `ToolSteeringAction` or `ModelSteeringAction` that the framework executes.

```python
from strands.vended_plugins.steering import LLMSteeringHandler

handler = LLMSteeringHandler(
    system_prompt="""
    You are providing guidance to ensure emails maintain a cheerful, positive tone.
    """
)
agent = Agent(tools=[send_email], plugins=[handler])
```

### Key Properties

- **Content-aware**: Steering evaluates *what the agent is doing* and *how well* — tone, task adherence, safety
- **LLM-evaluated**: The default handler uses an LLM to make decisions, trading latency for flexibility
- **Stateful**: Context accumulates across the agent loop, enabling multi-step reasoning about agent behavior
- **Currently Python-only**

---

## How Cedar Auth Maps to Steering

The Cedar authorization plugin (`cedar_auth_plugin.ts`) and Strands Steering share a surprising amount of mechanical structure despite answering fundamentally different questions.

### Structural Comparison

| Dimension | Strands Steering | Cedar Auth Plugin |
|-----------|-----------------|-------------------|
| **Hook point** | `BeforeToolCallEvent` (tool steering) | `BeforeToolCallEvent` |
| **Context gathered** | Tool history, inputs/outputs, timing, session metadata | Principal identity, roles, tool arguments, timestamp, environment, rate limit counters |
| **Evaluation engine** | LLM with natural-language guidance | Cedar policy engine with formal Cedar policies |
| **Possible outcomes** | Proceed / Guide / Interrupt | Allow / Deny |
| **On denial/guide** | Injects feedback for model to retry | Cancels tool call with denial message |
| **Audit** | Via LedgerProvider | Via `auditLog[]` |
| **Determinism** | Non-deterministic (LLM-based) | Fully deterministic (formal policy evaluation) |
| **Latency** | High (LLM call per decision) | Sub-millisecond (WASM policy evaluation) |

### Where They Diverge

**The question they answer:**
- Steering: *"Is this the right thing for the agent to do right now, given the task context?"*
- Cedar Auth: *"Is this principal allowed to perform this action on this resource?"*

**Evaluation guarantees:**
- Steering is probabilistic — the LLM might make different decisions on the same input
- Cedar is formally verifiable — you can statically prove properties like "no principal outside Role::admin can reach delete_record"

**Default posture:**
- Steering defaults to **proceed** (only intervenes when guidance triggers)
- Cedar defaults to **deny** (only proceeds when an explicit permit matches)

### Where They Converge

Both are **intervention layers** that:
1. Accumulate context from agent execution
2. Evaluate that context against rules (formal or informal)
3. Decide whether to let the action proceed, redirect it, or block it
4. Produce an audit trail of decisions

This shared pattern suggests a deeper abstraction.

---

## The Unified Intervention Primitive

What if "steering" is not just a Strands feature, but a **general pattern** for controlling agent behavior? We can abstract the shared mechanics into a primitive:

### The Pattern

```
┌─────────────────────────────────────────────────────┐
│                  Agent Execution Loop                │
│                                                     │
│  Model decides → [Intervention Point] → Execute     │
│                        │                            │
│              ┌─────────┴─────────┐                  │
│              │  Intervention      │                  │
│              │  Handler           │                  │
│              │                   │                  │
│              │  1. Gather context │                  │
│              │  2. Evaluate       │                  │
│              │  3. Act            │                  │
│              │  4. Log            │                  │
│              └───────────────────┘                  │
└─────────────────────────────────────────────────────┘
```

### Four Components

**1. Context Accumulation**

Every intervention handler needs to know *what's happening*. Context sources include:

- **Identity**: Who is the principal? What roles do they have? (Cedar)
- **Tool state**: What tool is being called, with what arguments? What tools were called before? (Steering, Cedar)
- **Content**: What did the model say? What is the user's intent? (Steering, Guardrails)
- **Environment**: Production vs staging? Time of day? Deploy freeze? (Cedar, Agent Control)
- **Session**: How many calls this session? What's the cumulative cost? (Rate limiting)

A unified primitive would define a `Context` type that any handler can read from and write to:

```typescript
interface InterventionContext {
  // Identity
  principal?: { type: string; id: string; roles: string[] };

  // Current action
  tool?: { name: string; input: Record<string, unknown> };
  modelResponse?: { content: string; stopReason: string };

  // History
  toolHistory: Array<{ name: string; input: unknown; output: unknown; durationMs: number }>;

  // Environment
  environment?: string;
  timestamp: Date;

  // Session
  sessionMetadata: Record<string, unknown>;

  // Extensible
  [key: string]: unknown;
}
```

**2. Evaluation Engine**

This is where handlers diverge — and that's the point. The primitive doesn't prescribe *how* you evaluate, only *that* you evaluate and *what* you return.

| Engine | Properties | Best For |
|--------|-----------|----------|
| Cedar policies | Deterministic, formally verifiable, sub-ms | Authorization, compliance rules |
| LLM judge | Flexible, natural-language, non-deterministic | Content quality, tone, task adherence |
| Rule engine (Datadog, custom) | Deterministic, configurable, fast | Safety guardrails, content filtering |
| Human-in-the-loop | Perfect accuracy, high latency | High-stakes actions, approval workflows |

**3. Action**

The evaluation produces a decision. Across all systems, we see four actions:

| Action | Meaning | Used By |
|--------|---------|---------|
| **Proceed** | Allow the action | All |
| **Guide** | Redirect — cancel and provide feedback for the model to retry | Steering |
| **Deny** | Hard block — cancel with a denial message, no retry | Cedar Auth |
| **Interrupt** | Pause for human input | Steering, Approval workflows |

Note: **Guide** and **Deny** are both cancellations, but with different intent. Guide says "try again differently"; Deny says "you are not allowed, period." This distinction matters because Guide keeps the agent in the loop (it can adapt), while Deny terminates that path (the agent should explain the denial, not work around it).

```typescript
type InterventionAction =
  | { type: "proceed" }
  | { type: "guide"; feedback: string }   // cancel + retry with guidance
  | { type: "deny"; reason: string }      // hard block, no retry
  | { type: "interrupt"; prompt: string }; // pause for human
```

**4. Audit Trail**

Every handler logs its decision. The primitive standardizes this:

```typescript
interface InterventionRecord {
  handler: string;           // "cedar-auth", "tone-steering", "datadog-guardrail"
  timestamp: string;
  action: InterventionAction;
  context: Partial<InterventionContext>;  // what was evaluated
  evaluation: unknown;       // handler-specific detail (policy IDs, LLM reasoning, rule matches)
}
```

---

## Concrete Instances

### 1. Cedar Authorization

```
Context:   principal (identity + roles) + tool (name + args) + environment + session
Engine:    Cedar policy evaluation (WASM, sub-ms, deterministic)
Actions:   Proceed | Deny
Posture:   Default-deny
Strength:  Formally verifiable, identity-aware, argument-level scoping
```

Cedar is a **tool-level intervention** that answers "is this principal authorized?" It's the only instance that brings identity into the equation. Its formal verification property (you can statically prove "no analyst can reach delete_record") is unique and valuable — other engines can't offer this.

### 2. LLM Steering (Strands built-in)

```
Context:   tool history + current tool/model output + session metadata (via LedgerProvider)
Engine:    LLM with natural-language system prompt
Actions:   Proceed | Guide | Interrupt
Posture:   Default-proceed
Strength:  Flexible, handles ambiguous/subjective criteria, natural-language rules
```

LLM Steering is both a **tool-level** and **model-level** intervention. It's the most flexible engine (anything you can express in language), but non-deterministic and high-latency (requires an LLM call per evaluation).

### 3. Datadog AI Guardrails / Safety Rules

```
Context:   model output content + tool arguments + predefined rule patterns
Engine:    Pattern matching, classifier models, blocklists
Actions:   Proceed | Deny | Guide
Posture:   Default-proceed (blocklist approach)
Strength:  Fast, deterministic, integrated with observability
```

Safety guardrails are typically **content-focused interventions** — checking for PII leakage, toxic content, prompt injection attempts, or policy violations. They don't care about identity; they care about what's being said or done.

### 4. Agent Control (rate limiting, environment gating, approval workflows)

```
Context:   session counters + environment flags + time + approval status
Engine:    Simple rule evaluation (comparisons, thresholds)
Actions:   Proceed | Deny | Interrupt
Posture:   Varies
Strength:  Simple, fast, operational
```

Agent control mechanisms are **operational interventions** — enforcing rate limits, blocking destructive operations in production, requiring human approval for high-stakes actions. These are often the simplest rules but among the most critical.

### How They Layer

In a production agent, you might want all four running simultaneously:

```
Model decides to call delete_record(table="users")
    │
    ├─ [1] Cedar Auth:      Is this principal allowed to delete_record?
    │                        → DENY (analyst role, not admin) ← stops here
    │
    ├─ [2] Agent Control:   Are we in production? Is there a deploy freeze?
    │                        → DENY (production environment) ← would stop here
    │
    ├─ [3] Datadog Guard:   Does the input contain PII? Is this a known-bad pattern?
    │                        → PROCEED
    │
    └─ [4] LLM Steering:    Is this the right thing to do given the task context?
                             → GUIDE ("The user asked for a report, not data deletion")
```

---

## Composability

Multiple intervention handlers need to work together. This raises questions about ordering, conflict resolution, and short-circuiting.

### Evaluation Order

A natural ordering follows the **cost/determinism spectrum**:

1. **Cheapest and most deterministic first** — Cedar policies, simple rules (sub-ms, no external calls)
2. **External services next** — Datadog guardrails, classifier models (ms-range, network call)
3. **LLM-based last** — Steering handlers (100ms+, LLM call)

This ordering is efficient (fast handlers short-circuit before expensive ones run) and safe (deterministic denials can't be overridden by probabilistic handlers).

### Conflict Resolution

**Deny wins.** If any handler returns Deny, the action is blocked regardless of what other handlers say. This is the same principle as Cedar's own `forbid` overriding `permit`.

**Guide accumulates.** If no handler denies but multiple handlers return Guide, their feedback can be concatenated and presented to the model together.

**Interrupt pauses.** If any handler returns Interrupt (and none denied), execution pauses for human input.

```
Final decision =
  if any Deny     → Deny (with combined reasons)
  if any Interrupt → Interrupt (with prompt)
  if any Guide     → Guide (with accumulated feedback)
  else             → Proceed
```

### Registration API (Sketch)

```typescript
const agent = new Agent({
  tools: [...],
  interventions: [
    // Evaluated in order; fast/deterministic first
    CedarAuthHandler.builder()
      .role("admin", { tools: ["*"] })
      .role("analyst", { tools: ["search", "query_database"] })
      .build(),

    AgentControlHandler.builder()
      .rateLimit("send_email", { maxPerSession: 10 })
      .denyToolsInEnv("production", ["delete_record", "drop_table"])
      .build(),

    DatadogGuardrailHandler.from({
      apiKey: process.env.DD_API_KEY,
      rules: ["pii-detection", "prompt-injection"],
    }),

    LLMSteeringHandler.from({
      systemPrompt: "Ensure the agent stays on task and maintains professional tone.",
    }),
  ],
});
```

---

## What This Means for the Cedar Plugin

### Option A: Cedar as a SteeringHandler

The Cedar plugin could implement the `SteeringHandler` interface, making it mechanically compatible with the steering system. This would mean:

- Cedar becomes one handler in a chain, not a standalone plugin
- The framework handles ordering and conflict resolution
- Other steering handlers can compose with Cedar naturally

```typescript
class CedarAuthHandler extends SteeringHandler {
  evaluateToolCall(context: InterventionContext): ToolSteeringAction {
    const result = cedar.isAuthorized({
      principal: context.principal,
      action: { type: "Action", id: `use_tool::${context.tool.name}` },
      resource: { type: "Tool", id: context.tool.name },
      context: this.buildCedarContext(context),
      policies: this.policies,
      entities: this.entities,
    });

    if (result.response.decision === "allow") {
      return { type: "proceed" };
    } else {
      // Deny, not Guide — authorization failures shouldn't trigger retries
      return { type: "deny", reason: `Not authorized: ...` };
    }
  }
}
```

**Pros:**
- Composable with other handlers
- Framework handles the intervention lifecycle
- Single audit stream for all intervention types

**Cons:**
- Steering is currently Python-only; the Cedar plugin is TypeScript
- Steering's `ToolSteeringAction` doesn't have a `Deny` variant (only Proceed/Guide/Interrupt) — the distinction between Guide ("try differently") and Deny ("you can't, period") matters for authorization
- Coupling to Strands' steering API when the Cedar plugin could work with other agent frameworks too

### Option B: A New Shared Primitive (Recommended for Exploration)

Rather than fitting Cedar into Strands' existing steering, propose a new **Intervention** primitive at a lower level that both steering and authorization implement. This would be:

- **Framework-level**: Part of the Strands SDK plugin interface, not specific to steering
- **Action-type aware**: Supports Proceed, Guide, Deny, and Interrupt as distinct actions
- **Engine-agnostic**: The evaluation function is a black box — Cedar, LLM, Datadog, or custom
- **Composable**: The framework evaluates handlers in order with defined conflict resolution

This approach doesn't force Cedar to become a "steering handler" (which implies content-awareness and LLM evaluation). Instead, steering handlers and authorization handlers are both **intervention handlers** — different species of the same genus.

### What Would Need to Change in Strands

For this to work, Strands would need:

1. A generalized `InterventionHandler` interface alongside (or replacing) `SteeringHandler`
2. A `Deny` action type in addition to Proceed/Guide/Interrupt
3. A composition mechanism that evaluates multiple handlers with defined ordering and conflict resolution
4. TypeScript support (steering is currently Python-only)

---

## Working Demo

A runnable proof-of-concept lives at [`js/strands-cedar-auth/intervention.ts`](../js/strands-cedar-auth/intervention.ts). It implements the full intervention primitive with four concrete handlers composed in a pipeline:

```
npx tsx intervention.ts
```

### What the Demo Shows

The pipeline evaluates handlers in cost/determinism order — **Cedar Auth** (sub-ms, formal) first, then **Operational Control** (sub-ms, simple rules), then **Content Guardrail** (pattern matching), then **LLM Steering** (mocked; would be an LLM call in production). Each handler only runs if the previous ones returned Proceed.

**10 scenarios demonstrate the layering:**

| Scenario | Handler that decides | Action |
|----------|---------------------|--------|
| Admin queries secrets DB | Cedar Auth | Proceed (admin allowed) |
| Analyst queries secrets DB | Cedar Auth | **Deny** (analyst restricted to analytics/reporting) |
| Analyst queries analytics | All four | Proceed (everyone agrees) |
| Admin deletes record in production | Operational Control | **Deny** (Cedar allows admin, but ops blocks prod deletes) |
| Admin deletes record in staging | All four | Proceed |
| Analyst sends email with PII | Content Guardrail | **Deny** (SSN pattern detected — Cedar+ops allowed it) |
| Analyst sends short email | LLM Steering | **Guide** ("email too short, add context") |
| Analyst sends proper email | All four | Proceed |
| Unknown user (no roles) | Cedar Auth | **Deny** (default-deny, no matching permit) |
| Analyst query with SQL injection | Content Guardrail | **Deny** (Cedar allowed the tool, guardrail caught content) |

### Key Insight from the Demo

No single handler catches everything. Cedar catches unauthorized access but not PII or SQL injection. Guardrails catch dangerous content but don't know who the user is. Operational controls catch environment violations but not argument-level restrictions. LLM steering catches task-relevance issues none of the others consider. The value is in composition — and the pipeline's conflict resolution (deny wins, guide accumulates) makes composition safe and predictable.

### What Cedar Uniquely Provides (vs. Agent Control)

The demo highlights a question: what does Cedar add that simple agent control rules can't? The answer is visible in the scenarios:

- **Identity-aware decisions**: The same tool (`query_database`) gives different results for `alice` (admin) vs `bob` (analyst). Operational controls don't distinguish principals.
- **Argument-level scoping per role**: Admin can query `secrets`, analyst cannot — same tool, different permissions based on who you are AND what you're querying.
- **Formal verification**: You can statically prove "no principal outside Role::admin can query the secrets database" without running the agent. No other handler in the pipeline offers this.

Simple operational controls (rate limits, env gating) are sufficient when identity doesn't matter. Cedar's value is specifically the **identity x action x arguments** matrix plus static analysis.

---

## Open Questions

1. **Is steering the right home for this?** Steering has connotations of "guidance" — nudging the agent in a direction. Authorization is not guidance; it's enforcement. Should they share an interface, or should there be a higher-level `InterventionHandler` that both implement?

2. **Does Guide make sense for authorization?** When Cedar denies a tool call, should the agent retry with different arguments (Guide behavior), or should it accept the denial and explain it to the user (Deny behavior)? For some cases (argument scoping), Guide might actually be useful: "You can't query the secrets database, but you can query analytics or reporting."

3. **Latency budget.** Stacking multiple intervention handlers adds latency. Cedar is sub-ms, but adding an LLM steering call on every tool invocation is expensive. Should the framework support conditional evaluation (e.g., only run LLM steering if cheaper handlers all returned Proceed)?

4. **Who owns the context?** Steering's `LedgerProvider` and Cedar's context builder both gather information from the same hook events. Should there be a shared context object that all handlers read from, or should each handler maintain its own?

5. **Cross-framework portability.** Cedar's value proposition includes working across agent frameworks (not just Strands). Tightly coupling to Strands' intervention primitive could limit this. Should the Cedar plugin implement a framework-agnostic interface that adapts to Strands' intervention system?

6. **TypeScript parity.** Strands Steering is Python-only today. The Cedar plugin is TypeScript. Any unified primitive needs to work in both languages, or the Cedar plugin would need a Python port first.
