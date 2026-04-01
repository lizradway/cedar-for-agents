# Intervention: A First-Class Agent Control Primitive

> Exploration document — not a specification or commitment. This doc proposes that Strands Agents should elevate **Intervention** to a first-class API surface, where Cedar authorization, LLM steering, Galileo Agent Control, content guardrails, and operational controls are all instances of the same primitive.

## Table of Contents

- [Background: Existing Agent Control Layers](#background-existing-agent-control-layers)
- [The Insight: Intervention Is the Primitive](#the-insight-intervention-is-the-primitive)
- [Proposed API: `Agent(interventions=[...])`](#proposed-api-agentinterventions)
- [Composability](#composability)
- [Working Demos](#working-demos)
- Appendices: [A (Concrete Instances)](#appendix-a-concrete-instances) · [B (Interface Design Rationale)](#appendix-b-interface-design-rationale) · [C (Coverage Matrix)](#appendix-c-coverage-matrix) · [D (Demo Details)](#appendix-d-demo-details)

---

## Background: Existing Agent Control Layers

Several independent tools already control agent behavior at runtime. Each is a Strands plugin (or could be), and each answers a different question.

### Strands Steering

[Steering](https://strandsagents.com/docs/user-guide/concepts/plugins/steering/) intercepts agent execution at two lifecycle points — before a tool call and after a model response — and returns **Proceed**, **Guide** (cancel + retry with feedback), or **Interrupt** (pause for human input). The default `LLMSteeringHandler` uses an LLM to make these decisions based on a developer-authored system prompt.

```python
from strands.vended_plugins.steering import LLMSteeringHandler

handler = LLMSteeringHandler(
    system_prompt="Ensure emails maintain a cheerful, positive tone."
)
agent = Agent(tools=[send_email], plugins=[handler])
```

Content-aware, flexible, and stateful — but non-deterministic and high-latency (~100ms+ per LLM call).

### Cedar Authorization

[Cedar](https://www.cedarpolicy.com/) is an open-source policy language by AWS, purpose-built for authorization. It evaluates Allow/Deny decisions against principals, actions, resources, and context. As an agent control layer, it answers "is this principal authorized to use this tool with these arguments?" — sub-ms, formally verifiable, and deterministic.

```python
cedar = CedarAuthHandler.builder()
    .role("analyst", tools=["search", "query_database"])
    .restrict("query_database", allowed_values={"database": ["analytics"]})
    .build()
```

### Datadog AI Guard

[Datadog AI Guard](https://strandsagents.com/docs/community/plugins/datadog-ai-guard/) is a Strands community plugin that scans for prompt injection, jailbreaking, and data exfiltration at **four** lifecycle points — before/after model calls and before/after tool calls. The broadest hook coverage of any existing control layer.

```python
from ddtrace.appsec.ai_guard import AIGuardStrandsPlugin

guard = AIGuardStrandsPlugin(
    detailed_error=True,            # append AI Guard reason to blocked messages
    raise_error_on_tool_calls=True, # raise instead of replacing tool results
)
agent = Agent(tools=[search, send_email], plugins=[guard])
# Requires: DD_AI_GUARD_ENABLED=true, DD_API_KEY, DD_APP_KEY
```

### Galileo Agent Control

[Galileo Agent Control](https://strandsagents.com/docs/community/plugins/agent-control/) is a Strands community plugin that provides runtime governance through configurable rules (via a centralized server or local `controls.yaml`). It hooks into **6+ lifecycle events** and ships as two separate plugins — `AgentControlPlugin` for hard blocks and `AgentControlSteeringHandler` for corrective guidance — because Strands doesn't yet have a unified interface for both.

```python
from agent_control.integrations.strands import AgentControlPlugin, AgentControlSteeringHandler

# Hard blocks (Deny) — raises exception when a rule matches
blocker = AgentControlPlugin(agent_name="my-agent")

# Corrective guidance (Guide) — returns feedback for retry when a rule matches
guide = AgentControlSteeringHandler(agent_name="my-agent")

agent = Agent(tools=[search, send_email], plugins=[blocker, guide])
# Requires: AGENT_CONTROL_URL (default: http://localhost:8000)
```

### The Problem

Each of these is a standalone plugin with its own interface. There's no shared action vocabulary, no ordering guarantees, no way to say "skip the expensive LLM steering call if Cedar already denied," and no unified audit trail.

---

## The Insight: Intervention Is the Primitive

Cedar authorization, LLM steering, Datadog AI Guard, Galileo Agent Control, and content guardrails all answer different questions — but they share the same mechanical structure:

| | Cedar Auth | LLM Steering | Datadog AI Guard | Galileo Agent Control |
|---|---|---|---|---|
| **Question** | *Is this principal allowed?* | *Is this the right thing to do?* | *Is this content safe?* | *Does this violate a rule?* |
| **Engine** | Cedar policies (WASM) | LLM judge | Datadog API | Centralized rule server |
| **Hook points** | `BeforeToolCall` | `BeforeToolCall`, `AfterModelCall` | 4 events | 6+ events |
| **Latency** | Sub-ms | 100ms+ | ms | ms |

They all: **intercept** an agent event, **evaluate** against rules, **decide** (proceed, redirect, or block), and **log** the decision. This shared lifecycle is the primitive — **Intervention**. Each tool is an instance.

The primitive has four components:

**Events** — Typed event subclasses (`BeforeToolCallEvent`, `AfterModelCallEvent`, etc.) carry relevant context. Handlers only receive events they registered for.

**Action** — Four decisions:

| Action | Meaning |
|--------|---------|
| **Proceed** | Allow |
| **Deny** | Hard block, no retry |
| **Guide** | Cancel + feedback for retry |
| **Interrupt** | Pause for human input |

**Deny** is new — steering today only has Proceed/Guide/Interrupt. Authorization needs a hard block that means "you are not allowed, period."

**Evaluation Engine** — Each instance uses a different engine (Cedar policies, LLM judge, API call, regex). The primitive doesn't prescribe how you evaluate, only what you return. See [Appendix A](#appendix-a-concrete-instances) for details on each.

**Audit Trail** — Every handler logs its decision into a unified stream.

Galileo's Agent Control is strong evidence this primitive is real — it independently arrived at the same deny-or-guide duality, but had to split it across two separate plugins (`AgentControlPlugin` for deny, `AgentControlSteeringHandler` for guide) because Strands lacks a unified intervention interface.

---

## Proposed API: `Agent(interventions=[...])`

Today, each control layer is a standalone plugin with no shared interface, no ordering guarantees, and no unified audit log:

```python
agent = Agent(plugins=[cedar_plugin, steering_plugin], tools=[...])
# Cedar and steering fire independently — no way to skip steering when Cedar denies
```

With interventions as a first-class parameter:

```python
agent = Agent(
    tools=[query_database, send_email],
    interventions=[
        cedar,        # sub-ms, formal policies
        guardrails,   # sub-ms, pattern matching
        steering,     # 100ms+, LLM-based guidance
    ],
)
```

```typescript
const agent = new Agent({
    tools: [queryDatabase, sendEmail],
    interventions: [cedar, guardrails, steering],
})
```

**Why first-class?** The framework owns composition — ordering, short-circuiting (Cedar denies in sub-ms, steering never runs), conflict resolution, and a unified audit log are all built in. Steering becomes one instance of `InterventionHandler`, not a special concept.

### The `InterventionHandler` Interface

The interface is **event-driven** — handlers declare which lifecycle events they care about, and the framework only calls them for matching events:

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

New event types can be supported without changing the base interface. For the rationale behind this design (vs. fixed methods per hook point), see [Appendix B](#appendix-b-interface-design-rationale).

---

## Composability

Handlers are evaluated in registration order, cheapest first:

1. **Cedar, guardrails** — sub-ms, deterministic
2. **Agent Control, Datadog AI Guard** — ms-range, service calls
3. **LLM Steering** — 100ms+, LLM call

At each lifecycle point, only handlers that declared that event type run:

```
User: "Query the secrets database for all API keys"

  BeforeModelCall:
    ├─ Datadog AI Guard:    Scan prompt for injection  → PROCEED
    └─ Agent Control:       Check centralized rules    → PROCEED

  [Model responds: query_database(database="secrets", ...)]

  BeforeToolCall:
    ├─ Cedar Auth:          Is bob (analyst) allowed?  → DENY
    │                       ← short-circuits here
    ├─ Guardrails:          (never reached)
    ├─ Datadog AI Guard:    (never reached)
    └─ LLM Steering:        (never reached — saved ~100ms)
```

**Deny** short-circuits immediately. **Guide** accumulates across handlers. **Interrupt** pauses if no handler denied.

No single handler catches everything — the value is in composition. See [Appendix C](#appendix-c-coverage-matrix) for the full matrix.

---

## Working Demos

We implemented the native `Agent(interventions=[...])` parameter in both the Python and TypeScript Strands SDKs.

**Python** — [`demos/intervention/native.py`](../python/strands-cedar-auth/demos/intervention/native.py)

```python
from strands import Agent, InterventionHandler, Proceed, Deny, Guide
from strands.hooks.events import BeforeToolCallEvent

agent = Agent(
    tools=[query_database, send_email, search],
    interventions=[cedar, guardrails, steering],
)
result = agent("Query the analytics database", invocation_state={"user_id": "bob", "roles": ["analyst"]})
```

**TypeScript** — [`demos/intervention/native.ts`](../js/strands-cedar-auth/demos/intervention/native.ts)

```typescript
const agent = new Agent({
  tools: [queryDatabase, sendEmail, search],
  interventions: [cedar, ops, guardrails, datadog, steering],
})
```

Five handlers across all 4 event types, 10 scenarios including prompt injection, jailbreak detection, and steering guidance.

**SDK forks:**

| SDK | Fork |
|-----|------|
| Python | [lizradway/sdk-python@interventions](https://github.com/lizradway/sdk-python/tree/interventions) |
| TypeScript | [lizradway/sdk-typescript@interventions](https://github.com/lizradway/sdk-typescript/tree/interventions) |

See [Appendix D](#appendix-d-demo-details) for run instructions and the InterventionPipeline userland workaround.

---

<details>
<summary><strong>Appendix A: Concrete Instances</strong></summary>

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

The most flexible engine — anything you can express in language. Non-deterministic and high-latency. Best used last in the pipeline.

### 3. Datadog AI Guard ([Strands community plugin](https://strandsagents.com/docs/community/plugins/datadog-ai-guard/))

```
Engine:      Datadog AI Guard API (prompt injection, jailbreak, data exfiltration detection)
Actions:     Proceed | Deny
Posture:     Default-proceed (threat detection approach)
Strength:    Multi-point scanning, content-focused, service-backed
Hook points: BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall
```

Scans at **four** lifecycle points — the broadest hook coverage of any instance. The event-driven `InterventionHandler` interface accommodates this naturally.

### 4. Content Guardrails (custom rules)

```
Engine:      Pattern matching, classifier models, blocklists
Actions:     Proceed | Deny
Posture:     Default-proceed (blocklist approach)
Strength:    Fast, deterministic, content-focused
Hook points: BeforeToolCall (typically)
```

Checks *what's being said*, not *who's saying it*. PII detection, SQL injection, toxic content. Identity-unaware.

### 5. Galileo Agent Control ([Strands community plugin](https://strandsagents.com/docs/community/plugins/agent-control/))

```
Engine:      Centralized rule server or local controls.yaml, evaluated at runtime
Actions:     Proceed | Deny | Guide (via AgentControlSteeringHandler)
Posture:     Default-proceed (blocklist/rule-match approach)
Strength:    Centralized policy management, no-code rule updates, dual enforcement modes
Hook points: BeforeInvocation, BeforeModelCall, AfterModelCall, BeforeToolCall, AfterToolCall, BeforeNodeCall, AfterNodeCall
```

Ships as **two complementary plugins** — `AgentControlPlugin` (Deny) and `AgentControlSteeringHandler` (Guide) — because Strands doesn't yet have a unified intervention interface.

</details>

<details>
<summary><strong>Appendix B: Interface Design Rationale</strong></summary>

### Why Not a Fixed Method Per Hook Point?

The obvious alternative is one method per lifecycle event:

```python
class InterventionHandler(ABC):
    async def evaluate_tool_call(self, ctx: ToolCallContext) -> InterventionAction: ...
    async def evaluate_model_input(self, ctx: ModelInputContext) -> InterventionAction: ...
    async def evaluate_model_output(self, ctx: ModelOutputContext) -> InterventionAction: ...
    async def evaluate_tool_result(self, ctx: ToolResultContext) -> InterventionAction: ...
```

**Today, Strands has 7+ lifecycle events.** That's 7 methods on the base class, most of which any handler ignores (Cedar uses 1, Datadog uses 4). When Strands adds new events, every handler needs updating — even if they don't care.

The event-driven approach (`handles()` + `evaluate()`) is stable — handlers opt into new event types by adding them to their `handles()` set. The base interface never changes.

</details>

<details>
<summary><strong>Appendix C: Coverage Matrix</strong></summary>

| Threat | Caught By | Missed By |
|--------|-----------|-----------|
| Unauthorized access (wrong role) | Cedar | Guardrails, Steering, Agent Control |
| PII in tool input | Guardrails, Datadog AI Guard | Cedar, Steering |
| SQL injection | Guardrails, Datadog AI Guard | Cedar |
| Prompt injection in user input | Datadog AI Guard | Cedar, Guardrails, Steering |
| Jailbreak / data exfiltration | Datadog AI Guard | Cedar, Guardrails |
| Off-task/low-quality tool use | LLM Steering | Cedar, Guardrails |
| Argument-level scoping (wrong DB) | Cedar | Guardrails, Steering |
| Operational policy violation | Agent Control | Cedar, Steering |
| Corrective behavioral guidance | Agent Control, LLM Steering | Cedar, Guardrails |

</details>

<details>
<summary><strong>Appendix D: Demo Details</strong></summary>

### Native SDK Implementation

Both SDK forks add `InterventionHandler`, `InterventionRegistry`, and the `Agent(interventions=[...])` parameter:

| SDK | Fork | Key Files |
|-----|------|-----------|
| Python | [lizradway/sdk-python@interventions](https://github.com/lizradway/sdk-python/tree/interventions) | `src/strands/interventions/`, `src/strands/agent/agent.py`, `src/strands/__init__.py` |
| TypeScript | [lizradway/sdk-typescript@interventions](https://github.com/lizradway/sdk-typescript/tree/interventions) | `src/interventions/`, `src/agent/agent.ts`, `src/index.ts` |

```bash
# Python
cd strands-agents-sdk && pip install -e .
pip install cedarpy
python demos/intervention/native.py

# TypeScript
cd strands-agents-sdk-ts && npm install && npx tsc -p src/tsconfig.json
npx tsx demos/intervention/native.ts
```

### InterventionPipeline — Userland Workaround

Composes handlers into an `InterventionPipeline` that wraps as a single Strands Plugin. Works today without SDK changes.

- **Python** — [`demos/intervention/pipeline.py`](../python/strands-cedar-auth/demos/intervention/pipeline.py) — Real agent with real `LLMSteeringHandler` making actual LLM calls
- **TypeScript** — [`demos/intervention/pipeline.ts`](../js/strands-cedar-auth/demos/intervention/pipeline.ts) — Mock steering (Strands TS SDK doesn't have steering yet)

</details>
