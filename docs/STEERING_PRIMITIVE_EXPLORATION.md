# Intervention: A First-Class Agent Control Primitive

> Exploration document — not a specification or commitment. This doc proposes that Strands Agents should elevate **Intervention** to a first-class API surface, where Cedar authorization, LLM steering, Galileo Agent Control, content guardrails, and operational controls are all instances of the same primitive.

## Table of Contents

- [What Strands Steering Is](#what-strands-steering-is)
- [The Shared Pattern](#the-shared-pattern)
- [The Insight: Intervention Is the Primitive](#the-insight-intervention-is-the-primitive)
- [Proposed API: `Agent(interventions=[...])`](#proposed-api-agentinterventions)
  - [Why Not a Fixed Method Per Hook Point?](#why-not-a-fixed-method-per-hook-point)
- [Concrete Instances](#concrete-instances)
- [Composability](#composability)
- [Working Demos](#working-demos)
- [What Would Need to Change in Strands](#what-would-need-to-change-in-strands)

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

---

## The Shared Pattern

Despite answering fundamentally different questions, these tools share the same mechanical structure:

| | Cedar Auth | LLM Steering | Datadog AI Guard | Galileo Agent Control |
|---|---|---|---|---|
| **Question** | *Is this principal allowed?* | *Is this the right thing to do?* | *Is this content safe?* | *Does this violate a rule?* |
| **Engine** | Cedar policies (WASM) | LLM judge | Datadog API | Centralized rule server |
| **Hook points** | `BeforeToolCall` | `BeforeToolCall`, `AfterModelCall` | 4 events (model + tool, before + after) | 6+ events |
| **Outcomes** | Allow / Deny | Proceed / Guide / Interrupt | Block / Replace / Pass | Deny (exception) / Guide |
| **Determinism** | Formally verifiable | Probabilistic | Deterministic | Deterministic |
| **Latency** | Sub-ms | 100ms+ (LLM call) | ms (API call) | ms (server call) |
| **Default posture** | Deny unless permitted | Proceed unless flagged | Proceed unless threat detected | Proceed unless rule matches |

They diverge in *how* they evaluate and *what* they know about. But they converge on the same lifecycle:

1. **Intercept** an agent execution event
2. **Evaluate** context against rules (formal, natural-language, or pattern-based)
3. **Decide** whether to proceed, redirect, or block
4. **Log** the decision

This shared pattern suggests a deeper abstraction.

---

## The Insight: Intervention Is the Primitive

Steering is not just a Strands feature — it's a specific *instance* of a general pattern for controlling agent behavior. Cedar auth is another instance. Content guardrails are a third. Galileo's Agent Control is a fourth — and it's particularly telling, because it independently arrived at the same deny-or-guide duality that this primitive describes, but had to split it across two separate plugins (`AgentControlPlugin` for deny, `AgentControlSteeringHandler` for guide) because Strands doesn't yet have a unified intervention interface. They all share the same mechanics:

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

The primitive has four components:

### 1. Events

Every handler needs to know *what's happening*. Typed `InterventionEvent` subclasses carry the relevant context for each lifecycle point:

- **`BeforeToolCallEvent`** — tool name, arguments, principal identity, roles, environment (Cedar, Guardrails, Steering)
- **`AfterToolCallEvent`** — tool name, arguments, output (Datadog AI Guard)
- **`BeforeModelCallEvent`** — user/system messages (Datadog AI Guard)
- **`AfterModelCallEvent`** — model output, stop reason (Steering, Datadog AI Guard)

Each event type carries what's relevant. Handlers inspect the event type they registered for — they don't receive a bloated universal context object.

### 2. Evaluation Engine

This is where handlers diverge — and that's the point. The primitive doesn't prescribe *how* you evaluate, only *that* you evaluate and *what* you return.

| Engine | Properties | Best For |
|--------|-----------|----------|
| Cedar policies | Deterministic, formally verifiable, sub-ms | Authorization, compliance |
| Galileo Agent Control | Centralized rules, no-code updates, deny + guide | Runtime governance, operational controls |
| Datadog AI Guard | Service-backed, multi-point scanning, content-focused | Prompt injection, jailbreak, data exfiltration |
| LLM judge (Strands `LLMSteeringHandler`) | Flexible, natural-language, non-deterministic | Content quality, tone, task adherence |
| Rule engine (custom) | Deterministic, configurable, fast | Safety guardrails, content filtering |
| Human-in-the-loop | Perfect accuracy, high latency | High-stakes approval workflows |

### 3. Action

The evaluation produces one of four decisions:

| Action | Meaning | Used By |
|--------|---------|---------|
| **Proceed** | Allow the action | All |
| **Guide** | Redirect — cancel and provide feedback for retry | Steering, Agent Control |
| **Deny** | Hard block — cancel with denial, no retry | Cedar Auth, Agent Control |
| **Interrupt** | Pause for human input | Steering, Approval workflows |

**Guide** and **Deny** are both cancellations with different intent. Guide says "try again differently" — the agent can adapt. Deny says "you are not allowed, period" — the agent should explain the denial, not work around it.

### 4. Audit Trail

Every handler logs its decision into a unified stream. One audit log for authorization, guardrails, and steering together.

---

## Proposed API: `Agent(interventions=[...])`

Intervention should be a **first-class API surface** on Strands agents, not something users build themselves. We have implemented a working proof-of-concept in both the [Python](../strands-agents-sdk/) and [TypeScript](../strands-agents-sdk-ts/) SDK forks.

### Today: Separate Plugins, No Composition

```python
# Each concern is a standalone plugin — no shared interface, no ordering guarantees,
# no conflict resolution, no unified audit log
cedar_plugin = CedarAuthPlugin.builder()...build()
steering_plugin = LLMSteeringHandler(system_prompt="...")

agent = Agent(plugins=[cedar_plugin, steering_plugin], tools=[...])
```

Cedar and steering fire their hooks independently. There's no way to say "skip the expensive LLM steering call if Cedar already denied." No shared action vocabulary. No unified audit trail.

### Alternative Approach: InterventionPipeline as Plugin

In our demos, we explored building an `InterventionPipeline` that composes handlers with ordering and conflict resolution, then wraps it as a single plugin:

```python
# All handlers implement InterventionHandler — the shared interface
pipeline = InterventionPipeline(
    handlers=[cedar, guardrails, steering],
)
agent = Agent(plugins=[pipeline], tools=[...])
```

This works (the demos prove it), but it's a userland workaround. Users have to build and wire up the pipeline themselves, and the framework doesn't understand what's inside it.

### Implemented: Native Concept

**Python:**
```python
agent = Agent(
    tools=[query_database, send_email],
    interventions=[
        # Evaluated in order; cheapest/most-deterministic first
        cedar,        # CedarAuthHandler — sub-ms, formal policies
        guardrails,   # ContentGuardrailHandler — sub-ms, pattern matching
        steering,     # LLMSteeringHandler — 100ms+, LLM-based guidance
    ],
)
```

**TypeScript:**
```typescript
const agent = new Agent({
    tools: [queryDatabase, sendEmail],
    interventions: [
        cedar,        // CedarAuthHandler — sub-ms, formal policies
        guardrails,   // ContentGuardrailHandler — sub-ms, pattern matching
        steering,     // LLMSteeringHandler — 100ms+, LLM-based guidance
    ],
})
```

### Why First-Class?

1. **The framework owns composition.** Ordering, conflict resolution, and short-circuiting are built in. Users don't need to build an `InterventionPipeline` themselves.

2. **Deny short-circuits before expensive handlers.** When Cedar denies a tool call (sub-ms), the LLM steering handler (~100ms) never runs. The framework makes this automatic.

3. **Unified audit log.** One stream for all intervention types — authorization denials, content blocks, and steering guidance — queryable from the agent.

4. **Steering becomes one instance, not a special concept.** Today, steering is a separate feature with its own handler hierarchy (`SteeringHandler → LLMSteeringHandler`). With interventions as the primitive, `LLMSteeringHandler` just implements `InterventionHandler` — the same interface Cedar implements. Users learn one concept.

5. **Deny becomes a first-class action.** Steering today only has Proceed/Guide/Interrupt. Authorization needs Deny — a hard block that means "you are not allowed, period." Adding Deny to the action vocabulary makes the framework useful for enforcement, not just guidance.

### The `InterventionHandler` Interface

The interface is **event-driven** rather than having a fixed method per hook point. This means new lifecycle events (e.g. `BeforeNodeCallEvent`) can be supported by handlers without changing the base interface.

**Python:**
```python
class InterventionHandler(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def handles(self) -> set[type]:
        """Declare which Strands event types this handler cares about."""
        ...

    @abstractmethod
    async def evaluate(self, event: HookEvent) -> InterventionAction: ...
```

**TypeScript:**
```typescript
interface InterventionHandler {
    readonly name: string
    handles(): Set<HookableEventConstructor>
    evaluate(event: HookableEvent): InterventionAction | Promise<InterventionAction>
}
```

Handlers declare which events they care about via `handles()`, and the framework only calls `evaluate()` for matching events. At each lifecycle point, the framework runs all registered handlers for that event type in registration order, short-circuits on Deny, accumulates Guide feedback, and logs every decision.

### Why Not a Fixed Method Per Hook Point?

The obvious alternative is one method per lifecycle event:

```python
class InterventionHandler(ABC):
    async def evaluate_tool_call(self, ctx: ToolCallContext) -> InterventionAction: ...
    async def evaluate_model_input(self, ctx: ModelInputContext) -> InterventionAction: ...
    async def evaluate_model_output(self, ctx: ModelOutputContext) -> InterventionAction: ...
    async def evaluate_tool_result(self, ctx: ToolResultContext) -> InterventionAction: ...
```

This is readable and each method gets a nicely typed context. But it doesn't hold up:

**Today, Strands has at least 7 lifecycle events** — `BeforeInvocationEvent`, `BeforeModelCallEvent`, `AfterModelCallEvent`, `BeforeToolCallEvent`, `AfterToolCallEvent`, `BeforeNodeCallEvent`, `AfterNodeCallEvent`. That's 7 methods on the base class, most of which any given handler ignores. Cedar implements 1. Datadog AI Guard implements 4. Galileo Agent Control hooks into nearly all of them.

**This only grows.** When Strands adds `BeforeRetryEvent` or `AfterInvocationEvent`, every handler plugin needs updating — even if they don't care about the new event. The base class changes, default no-op implementations accumulate, and handlers that were working fine now need to be re-released against the new interface version.

**Existing plugins already show the problem.** Datadog AI Guard hooks into 4 events today. Galileo Agent Control hooks into 6+. If either product adds coverage for a new event type, they'd need to wait for the `InterventionHandler` base class to add a corresponding method first. The base interface becomes a bottleneck for the ecosystem.

The event-driven approach avoids all of this. The base interface is stable — `handles()` + `evaluate()` never changes. Handlers opt into new event types by adding them to their `handles()` set. The framework doesn't need to know about specific event types at the interface level.

### How Each Handler Declares Its Scope

```python
class CedarAuthHandler(InterventionHandler):
    def handles(self):
        return {BeforeToolCall}  # Cedar only cares about tool authorization

class DatadogAIGuardHandler(InterventionHandler):
    def handles(self):
        return {BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall}  # Full coverage

class LLMSteeringHandler(InterventionHandler):
    def handles(self):
        return {BeforeToolCall, AfterModelCall}  # Tool steering + model steering
```

### How `LLMSteeringHandler` Fits

Strands' existing `LLMSteeringHandler` already has the right shape: `steer_before_tool()` evaluates a tool call and returns Proceed/Guide/Interrupt. A thin adapter maps this to `InterventionHandler`:

```python
class StrandsSteeringAdapter(InterventionHandler):
    """Wraps real LLMSteeringHandler as an InterventionHandler."""

    def handles(self):
        return {BeforeToolCall}

    async def evaluate(self, event: InterventionEvent) -> InterventionAction:
        action = await self._handler.steer_before_tool(
            agent=self._agent, tool_use={"name": event.tool_name, "input": event.tool_input}
        )
        if isinstance(action, StrandsProceed): return Proceed(reason=action.reason)
        if isinstance(action, StrandsGuide):   return Guide(feedback=action.reason)
        if isinstance(action, StrandsInterrupt): return Interrupt(prompt=action.reason)
```

If Strands made `InterventionHandler` native, `LLMSteeringHandler` would implement it directly — no adapter needed.

---

## Concrete Instances

### 1. Cedar Authorization

```
Engine:      Cedar policy evaluation (WASM/native, sub-ms, deterministic)
Actions:     Proceed | Deny
Posture:     Default-deny
Strength:    Formally verifiable, identity-aware, argument-level scoping per role
Hook points: BeforeToolCall
```

Answers "is this principal authorized?" — identity-aware, argument-level scoping per role, formally verifiable.

### 2. LLM Steering (Strands built-in)

```
Engine:      LLM with natural-language system prompt
Actions:     Proceed | Guide | Interrupt
Posture:     Default-proceed
Strength:    Flexible, handles ambiguous/subjective criteria
Hook points: BeforeToolCall, AfterModelCall
```

LLM Steering is the most flexible engine — anything you can express in language. But non-deterministic and high-latency. Best used last in the pipeline, after cheaper handlers have filtered.

### 3. Datadog AI Guard ([Strands community plugin](https://strandsagents.com/docs/community/plugins/datadog-ai-guard/))

```
Engine:      Datadog AI Guard API (prompt injection, jailbreak, data exfiltration detection)
Actions:     Proceed | Deny
Posture:     Default-proceed (threat detection approach)
Strength:    Multi-point scanning, content-focused, service-backed
Hook points: BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall
```

Datadog's [AI Guard](https://strandsagents.com/docs/community/plugins/datadog-ai-guard/) scans at **four** lifecycle points — not just before tool calls:

| Event | Scans | On Threat |
|-------|-------|-----------|
| `BeforeModelCallEvent` | User prompts | Hard block (raises `AIGuardAbortError`) |
| `AfterModelCallEvent` | Model text output | Hard block |
| `BeforeToolCallEvent` | Tool call + conversation context | Cancels tool with message |
| `AfterToolCallEvent` | Tool result | Replaces result content with safe message |

This is the broadest hook coverage of any instance — it checks inputs, outputs, tool calls, and tool results. It catches prompt injection, jailbreaking, data exfiltration, and destructive tool calls.

**Content replacement at `AfterToolCall`:** Today, AI Guard replaces tool results with safe messages rather than blocking outright. This maps to Deny in our action vocabulary (the original result is discarded), though a future "Sanitize" action — where the handler returns modified content instead of blocking — could be a cleaner fit. See [Open Questions](#open-questions).

The event-driven `InterventionHandler` interface accommodates this naturally — AI Guard declares `handles() → {BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall}` and the framework calls its `evaluate()` at each matching lifecycle point.

### 4. Content Guardrails (custom rules)

```
Engine:      Pattern matching, classifier models, blocklists
Actions:     Proceed | Deny
Posture:     Default-proceed (blocklist approach)
Strength:    Fast, deterministic, content-focused
Hook points: BeforeToolCall (typically)
```

Simple guardrails check *what's being said*, not *who's saying it*. PII detection, SQL injection, toxic content. Identity-unaware. These are the simplest instance — regex or classifier, no external service.

### 5. Galileo Agent Control ([Strands community plugin](https://strandsagents.com/docs/community/plugins/agent-control/))

```
Engine:      Centralized rule server or local controls.yaml, evaluated at runtime
Actions:     Proceed | Deny | Guide (via AgentControlSteeringHandler)
Posture:     Default-proceed (blocklist/rule-match approach)
Strength:    Centralized policy management, no-code rule updates, dual enforcement modes
Hook points: BeforeInvocation, BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall, BeforeNodeCall, AfterNodeCall
```

Galileo's [Agent Control](https://strandsagents.com/docs/community/plugins/agent-control/) is a Strands community plugin that provides runtime governance through configurable rules evaluated at every lifecycle event. It already demonstrates the intervention pattern natively — it ships as **two complementary plugins** that map directly to intervention actions:

- **`AgentControlPlugin`** — hooks into `BeforeToolCallEvent`, `BeforeModelCallEvent`, `AfterToolCallEvent`, etc. When a rule matches, it raises an exception (hard block = **Deny**).
- **`AgentControlSteeringHandler`** — integrates with Strands' steering API. When a rule matches, it returns `Guide(reason=<context>)`, prompting the agent to retry with corrective feedback.

Rules are defined centrally (via server at `AGENT_CONTROL_URL` or local `controls.yaml`) and evaluated at runtime without redeployment. This separation of policy from code is the same principle Cedar uses — the difference is the rule language (YAML/API vs Cedar's formal policy language) and the evaluation guarantees (no formal verification, but fast and centrally manageable).

Agent Control is strong evidence that the intervention primitive is real — Galileo independently arrived at the same deny-or-guide duality, split across two plugins, because Strands doesn't yet have a unified intervention interface to express both in one handler.

### How They Layer

Different handlers fire at different lifecycle points. The framework dispatches each event to only the handlers that declared interest:

```
User sends message: "Query the secrets database for all API keys"

  BeforeModelCall:
    ├─ Datadog AI Guard:    Scan user prompt for injection → PROCEED
    └─ Agent Control:       Check centralized rules       → PROCEED

  [Model responds with tool call: query_database(database="secrets", ...)]

  BeforeToolCall:
    ├─ Cedar Auth:          Is bob (analyst) allowed?      → DENY
    │                       ← short-circuits here
    ├─ Guardrails:          (never reached)
    ├─ Datadog AI Guard:    (never reached)
    └─ LLM Steering:        (never reached — saved ~100ms)
```

At `BeforeModelCall`, only handlers that declared that event type run (Datadog, Agent Control). Cedar doesn't run — it only cares about `BeforeToolCall`. At `BeforeToolCall`, Cedar's deny short-circuits before the expensive handlers.

---

## Composability

### Evaluation Order

Handlers are registered in a single ordered list. At each lifecycle point, the framework iterates the list but **skips handlers that didn't declare that event type**. A natural ordering follows the **cost/determinism spectrum**:

1. **Cheapest and most deterministic first** — Cedar policies, simple rules (sub-ms, no external calls)
2. **External services next** — Agent Control (centralized rule server), Datadog AI Guard (ms-range, API call)
3. **LLM-based last** — Steering handlers (100ms+, LLM call)

This single ordering works across all event types. At `BeforeToolCall`, all 5 handlers might fire (Cedar → Guardrails → Agent Control → Datadog → Steering). At `BeforeModelCall`, only Datadog and Agent Control fire — but they still run in the order they were registered. At `AfterModelCall`, Datadog fires before Steering.

This is efficient (fast handlers short-circuit before expensive ones run) and safe (deterministic denials can't be overridden by probabilistic handlers).

### Conflict Resolution

At each lifecycle point, the framework applies the same resolution across all handlers that fired:

```
Final decision =
  if any Deny     → Deny (short-circuits immediately)
  if any Interrupt → Interrupt (with prompt)
  if any Guide     → Guide (with accumulated feedback from all guiding handlers)
  else             → Proceed
```

**Deny wins** — same principle as Cedar's `forbid` overriding `permit`. **Guide accumulates** — if multiple handlers return Guide, their feedback is concatenated. **Interrupt pauses** — if any handler needs human input and none denied.

---

## Working Demos

Three approaches are demonstrated: **Native SDK Implementation** (the real `Agent(interventions=[...])` parameter implemented in the SDK), the **First-Class API Standalone** (event-driven handlers without SDK modifications), and the **InterventionPipeline** (a userland workaround).

### Native SDK Implementation — Real `Agent(interventions=[...])`

These demos use the **real native interventions parameter** added directly to the Strands SDK source. No wrappers, no bridge plugins — handlers are passed to `Agent` and the SDK's `InterventionRegistry` wires them into the hook system automatically.

**Python** — [`python/strands-cedar-auth/demos/intervention/native.py`](../python/strands-cedar-auth/demos/intervention/native.py)

```
cd strands-agents-sdk && pip install -e .
pip install cedarpy
python demos/intervention/native.py
```

Uses the real `Agent(interventions=[cedar, guardrails, steering])` parameter. Handlers import directly from `strands`:

```python
from strands import Agent, InterventionHandler, Proceed, Deny, Guide
from strands.hooks.events import BeforeToolCallEvent

agent = Agent(
    tools=[query_database, send_email, search],
    interventions=[cedar, guardrails, steering],
)
result = agent("Query the analytics database", invocation_state={"user_id": "bob", "roles": ["analyst"]})
```

Three handlers (CedarAuthHandler, ContentGuardrailHandler, MockLLMSteeringHandler) receive real Strands `BeforeToolCallEvent` objects — `event.tool_use["name"]`, `event.invocation_state["user_id"]`. Audit log accessed via `agent._intervention_registry.audit_log`.

**TypeScript** — [`js/strands-cedar-auth/demos/intervention/native.ts`](../js/strands-cedar-auth/demos/intervention/native.ts)

```
cd strands-agents-sdk-ts && npm install && npx tsc -p src/tsconfig.json
npx tsx demos/intervention/native.ts
```

Uses the real `InterventionRegistry` and Strands event types from the modified SDK. Five handlers (Cedar, operational controls, guardrails, Datadog AI Guard, mock LLM steering) receive real Strands events — `event.toolUse.name`, `event.agent.appState.get("user_id")`. 10 scenarios across all event types.

```typescript
import { Agent, BeforeToolCallEvent } from '@strands-agents/sdk'
import type { InterventionHandler, InterventionAction } from '@strands-agents/sdk'

const agent = new Agent({
  tools: [queryDatabase, sendEmail, search],
  interventions: [cedar, ops, guardrails, datadog, steering],
})
```

**SDK Changes Made:**

| SDK | Files Modified/Created |
|-----|----------------------|
| Python (`strands-agents-sdk/`) | `src/strands/interventions/` (handler.py, actions.py, registry.py, \_\_init\_\_.py), `src/strands/agent/agent.py` (added `interventions` param), `src/strands/__init__.py` (exports) |
| TypeScript (`strands-agents-sdk-ts/`) | `src/interventions/` (handler.ts, actions.ts, registry.ts, index.ts), `src/agent/agent.ts` (added `interventions` param), `src/index.ts` (exports) |

Both implementations follow the same pattern:
1. `InterventionHandler` interface: `name` + `handles()` + `evaluate(event)`
2. `InterventionRegistry`: bridges handlers to `HookRegistry`, one callback per event type
3. `Agent` constructor: accepts `interventions`, wires them BEFORE plugins so they fire first
4. Handlers receive real Strands events — no conversion layer, no custom event types

### InterventionPipeline — Userland Workaround

These demos show the alternative approach we explored: composing handlers into an `InterventionPipeline` that wraps as a single Strands Plugin. This works today but is a userland workaround — the framework doesn't understand what's inside.

**Python** — [`python/strands-cedar-auth/demos/intervention/pipeline.py`](../python/strands-cedar-auth/demos/intervention/pipeline.py)

```
pip install cedarpy strands-agents
python demos/intervention/pipeline.py
```

Runs a **real Strands agent** with three handlers composed in a pipeline. Uses the **real** `LLMSteeringHandler` from `strands.vended_plugins.steering` making actual LLM calls. The audit log shows real LLM reasoning alongside Cedar decisions, and demonstrates short-circuiting (LLM steering never runs when Cedar denies).

**TypeScript** — [`js/strands-cedar-auth/demos/intervention/pipeline.ts`](../js/strands-cedar-auth/demos/intervention/pipeline.ts)

```
npx tsx demos/intervention/pipeline.ts
```

Same pipeline approach with 4 handlers (Cedar, operational controls, guardrails, mock LLM steering) across 10 scenarios. LLM steering is mocked (Strands TS SDK doesn't have steering yet).

### What the Demos Prove

No single handler catches everything:

| Threat | Caught By | Missed By |
|--------|-----------|-----------|
| Unauthorized access (wrong role) | Cedar | Guardrails, Steering, Agent Control |
| PII in tool input | Guardrails, Datadog AI Guard | Cedar, Steering |
| SQL injection | Guardrails, Datadog AI Guard | Cedar |
| Prompt injection in user input | Datadog AI Guard | Cedar, Guardrails, Steering |
| Jailbreak / data exfiltration | Datadog AI Guard | Cedar, Guardrails |
| Off-task/low-quality tool use | LLM Steering | Cedar, Guardrails |
| Argument-level scoping (wrong DB) | Cedar | Guardrails, Steering |
| Operational policy violation (e.g. rate limits) | Agent Control | Cedar, Steering |
| Corrective behavioral guidance | Agent Control, LLM Steering | Cedar, Guardrails |

The value is in **composition** — and the pipeline's conflict resolution makes it safe and predictable.

---

## What Would Need to Change in Strands

We have implemented a working proof-of-concept in both the Python and TypeScript SDKs (see [Working Demos](#working-demos)). Remaining work, open questions, and status tracking are in [INTERVENTION_TODO.md](./INTERVENTION_TODO.md).

3. **Does Guide make sense for authorization?** When Cedar denies a tool call, should the agent ever retry with different arguments (Guide behavior)? For argument scoping, Guide might be useful: "You can't query the secrets database, but you can query analytics or reporting." This could be a Cedar-specific decision — return Guide for argument violations, Deny for role violations.

4. **Who owns context enrichment?** Steering has `LedgerProvider` for tool history. Cedar needs principal identity from `invocation_state`. Should the framework provide a shared context pipeline, or should each handler enrich its own?

5. **Cross-framework portability.** The `InterventionHandler` interface should be simple enough that Cedar can implement it for Strands, LangGraph, AutoGen, etc. with thin adapters.
