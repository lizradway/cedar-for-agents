# Cedar Authorization Plugin for Strands Agents SDK (Python)

## Table of Contents

- [Cedar Authorization Plugin for Strands Agents SDK (Python)](#cedar-authorization-plugin-for-strands-agents-sdk-python)
  - [Table of Contents](#table-of-contents)
  - [Business Proposal](#business-proposal)
  - [Concrete Use Cases](#concrete-use-cases)
    - [Autonomous coding agent — guardrails on powerful credentials](#autonomous-coding-agent--guardrails-on-powerful-credentials)
    - [Multi-user SaaS agent — shared agent, different users, same credentials](#multi-user-saas-agent--shared-agent-different-users-same-credentials)
    - [Where the industry is headed](#where-the-industry-is-headed)
  - [Problem](#problem)
    - [Why Not Just Create Different Agents With Different Tool Sets?](#why-not-just-create-different-agents-with-different-tool-sets)
      - [1. Same tool, different permissions on its arguments](#1-same-tool-different-permissions-on-its-arguments)
      - [2. The model loses the ability to explain denial](#2-the-model-loses-the-ability-to-explain-denial)
      - [3. Multi-tenant SaaS — you'd be building a policy engine anyway](#3-multi-tenant-saas--youd-be-building-a-policy-engine-anyway)
      - [4. Runtime conditions that don't exist at agent construction time](#4-runtime-conditions-that-dont-exist-at-agent-construction-time)
      - [5. Separation of concerns — who owns permissions?](#5-separation-of-concerns--who-owns-permissions)
      - [6. Multi-agent delegation and permission scoping](#6-multi-agent-delegation-and-permission-scoping)
    - [Summary](#summary)
    - [Why Not Just Use IAM / Application-Layer Auth?](#why-not-just-use-iam--application-layer-auth)
    - [Traditional apps vs. agents: the control flow changed](#traditional-apps-vs-agents-the-control-flow-changed)
    - [The principal problem](#the-principal-problem)
    - [The enforcement gap](#the-enforcement-gap)
    - [Where each auth layer stops](#where-each-auth-layer-stops)
    - [The tool-call boundary is the only chokepoint](#the-tool-call-boundary-is-the-only-chokepoint)
    - [How This Differs from Existing Control Plugins](#how-this-differs-from-existing-control-plugins)
    - [When you don't need this](#when-you-dont-need-this)
  - [Proposal](#proposal)
  - [How It Works](#how-it-works)
  - [How Does Identity Get Into the Agent?](#how-does-identity-get-into-the-agent)
    - [What Strands has today: `invocation_state`](#what-strands-has-today-invocation_state)
    - [How other frameworks handle identity and auth](#how-other-frameworks-handle-identity-and-auth)
    - [No SDK changes required](#no-sdk-changes-required)
    - [How identity flows in: your app puts it in `invocation_state`](#how-identity-flows-in-your-app-puts-it-in-invocation_state)
    - [Any auth mechanism works](#any-auth-mechanism-works)
    - [The principal can be a user, a service, or an agent](#the-principal-can-be-a-user-a-service-or-an-agent)
    - [What the plugin needs from you](#what-the-plugin-needs-from-you)
    - [Evaluation Mode](#evaluation-mode)
    - [Why Cedar over OPA](#why-cedar-over-opa)
  - [Cedar Model Mapping](#cedar-model-mapping)
    - [Example Policy](#example-policy)
  - [How the Authorization Request Is Built](#how-the-authorization-request-is-built)
    - [Principal](#principal)
    - [Action](#action)
    - [Resource](#resource)
    - [Context](#context)
  - [Schema Generation](#schema-generation)
  - [Static Verification](#static-verification)
    - [Why this matters](#why-this-matters)
    - [Verifier API](#verifier-api)
    - [CI/CD Integration](#cicd-integration)
    - [Relationship to schema auto-generation](#relationship-to-schema-auto-generation)
  - [Developer API](#developer-api)
    - [How to choose](#how-to-choose)
    - [Builder](#builder)
    - [Config file (`from_config`)](#config-file-from_config)
    - [Full Cedar (advanced)](#full-cedar-advanced)
  - [Where This Lives: cedar-for-agents Repository](#where-this-lives-cedar-for-agents-repository)
  - [Implementation](#implementation)
  - [Open Questions](#open-questions)


Business Proposal
Concrete Use Cases
Problem/ Gaps
        Why Not Just Create Different Agents With Different Tool Sets?
        Why Not Just Use IAM / Application-Layer Auth?
        How This Differs from Galileo and Datadog
Proposal
How It Works
        How Does Identity Get Into the Agent?
       How the Authorization Request Is Built
      Cedar Model Mapping
      Schema Generation
      Static Verification
      Evaluation Mode
Implementation
    Developer API
    Builder
    Config file (from_config)
    Full Cedar (advanced)
    Where This Lives: cedar-for-agents Repository
Design Decisions
    Why Cedar over OPA
Open Questions
Appendix


## Business Proposal

Strands agents run tools on behalf of users, but today there is no standard way to control *which* user can invoke *which* tool. Teams building multi-user agent applications are forced to roll their own authorization logic inside each tool or skip per-tool auth entirely. We propose shipping a Cedar authorization plugin for the Strands Python SDK that lets developers write declarative, auditable policies governing tool access — without modifying any tool code. This gives Strands a differentiated, production-grade authorization story that no other agent framework offers out of the box, and deepens integration with the AWS ecosystem (Cedar, Amazon Verified Permissions).


## Concrete Use Cases

### Autonomous coding agent — guardrails on powerful credentials

Autonomous agents like [strands-coder](https://github.com/agent-of-mkmeral/strands-coder) run with real credentials (GitHub PAT tokens, IAM roles, API keys) and make autonomous decisions about which tools to call. Today, the only thing restricting tool usage is prompt instructions — which the LLM can ignore, and prompt injection can override.

Cedar adds a concrete enforcement layer: every tool call is intercepted before execution, and a `forbid` policy can't be bypassed by the LLM. This is the difference between a sign on a door and a lock.

See [`DEMO_WALKTHROUGH.md`](./DEMO_WALKTHROUGH.md) for the full analysis, including:

- A real-world audit of strands-coder's tools (only 2 of 8 have any runtime guardrail checks)
- Six attack scenarios: prompt injection via malicious issues, cross-repo brand damage (this has already happened), GraphQL escape hatches, persistent backdoors via cron jobs, knowledge base poisoning, and self-modification
- Per-role Cedar policies: implementer (wildcard, repo-scoped), reviewer (read + comment only, no mutations), tester, refiner, doc-writer
- How Cedar's deny-by-default model means a new sub-agent type with no policy gets zero access — not full access

This is relevant **today**, not just in a multi-user future. Even a single developer using an autonomous agent needs guardrails on what the agent can do with powerful credentials.

### Multi-user SaaS agent — shared agent, different users, same credentials

Organizations are deploying AI agents as shared internal services — Slack bots, internal APIs, copilot features. The agent runs with a single set of credentials, but users have different roles and should see different things. Today there's no standard way to enforce this.

See [`DEMO_SAAS_WALKTHROUGH.md`](./DEMO_SAAS_WALKTHROUGH.md) for the full analysis, including:

- Three roles (admin, analyst, viewer) with layered constraints: RBAC, argument-level database scoping, rate-limited exports, environment-based restrictions, and a global `forbid` on the secrets database that overrides even admin's wildcard access
- Why database permissions alone don't solve this (the agent uses one connection for all users)
- How Cedar's `forbid` overrides `permit` model enables "no one should ever do X" policies in a system where some users have wildcard access

### Where the industry is headed

Large-scale multi-user SaaS agents are not yet common — most agents today are single-user or internal tools. But the trajectory is clear: as agents move from developer notebooks to production deployments serving many users, tool-level authorization becomes a hard requirement.

The plugin is designed for both ends of this spectrum:

- **Today**: Autonomous agents with powerful credentials need guardrails even for a single user. Cedar constrains what the agent can do with those credentials. See [`DEMO_WALKTHROUGH.md`](./DEMO_WALKTHROUGH.md).
- **Tomorrow**: Multi-user agents serving different roles through a shared deployment. Cedar provides the per-user authorization layer that IAM and API gateways can't. See [`DEMO_SAAS_WALKTHROUGH.md`](./DEMO_SAAS_WALKTHROUGH.md).

The same policy language and plugin architecture serves both. Starting with single-user guardrails today doesn't require rearchitecting when you add multi-user support later.

## Problem

AI agents that invoke tools on behalf of users need authorization guardrails. Today, developers either hard-code permission checks inside each tool or wrap agents in ad-hoc middleware. This leads to authorization logic that is scattered, hard to audit, and impossible to analyze statically. As agents gain access to higher-stakes tools (database writes, API calls, file deletion), the gap between "what the model can do" and "what the user is allowed to do" becomes a security liability.

### Why Not Just Create Different Agents With Different Tool Sets?

The obvious alternative to Cedar is: just make a different agent per role.

```python
analyst_agent = Agent(tools=[search, read_report])
admin_agent = Agent(tools=[search, read_report, delete_record, provision_account])
```

This is the right question, and for simple cases the answer is: **you should just do that.** Two roles, three tools, no conditional logic — make two agents and move on. Cedar is not for that case.

Cedar is for when tool-set swapping breaks down. Here's where that happens:

#### 1. Same tool, different permissions on its arguments

Tool-set swapping is binary: a tool is in the set or it isn't. But real authorization is often about *how* a tool is used, not *whether* it exists.

**Example**: Everyone gets `query_database`. But analysts can only query tables in their department. Managers can query across departments. Compliance can query anything but only in read-only mode.

You can't express this by including or excluding `query_database` — the tool is the same, the permission varies by who's calling it and what arguments they pass. You'd have to build three separate `query_database_analyst`, `query_database_manager`, `query_database_compliance` tools that are functionally identical except for a hard-coded permission check. That's just authorization with extra steps.

Cedar handles this naturally:

```cedar
// Analysts can query their own department's tables
permit (
  principal is Employee,
  action == Action::"query_database",
  resource
) when {
  context.table_name.department == principal.department
};

// Compliance can query anything, read-only
permit (
  principal in Team::"compliance",
  action == Action::"query_database",
  resource
) when {
  context.mode == "read_only"
};
```

#### 2. The model loses the ability to explain denial

When you remove a tool from the agent's tool set, the model doesn't know that capability exists. If a user asks "delete that record", the model will say something like "I don't have the ability to delete records" — which is wrong. The agent *can* delete records, this user just isn't allowed to.

With Cedar, the model sees all tools, attempts the call, gets a structured denial, and can tell the user: *"You don't have permission to delete records. Contact your admin to request access."* This is a better user experience and a more honest answer.

#### 3. Multi-tenant SaaS — you'd be building a policy engine anyway

You're building an agent platform. Customer A has paid for the premium tier (all tools). Customer B is on the free tier (read-only tools). Customer C has a custom contract (all tools except `export_data` because of their compliance requirements).

With tool-set swapping, your request handler now looks like:

```python
def get_agent_for_customer(customer_id):
    entitlements = db.get_entitlements(customer_id)
    tools = [t for t in ALL_TOOLS if t.name in entitlements.allowed_tools]
    # What about conditional permissions? Time-of-day restrictions?
    # What about per-user permissions within a customer?
    # What about audit logging of denied access?
    return Agent(tools=tools, ...)
```

You're dynamically constructing tool lists based on entitlements, conditions, and user attributes. That *is* a policy engine — just an ad-hoc, untested, unanalyzable one embedded in your routing code. Cedar replaces that with a purpose-built language that can be validated, tested, and audited.

#### 4. Runtime conditions that don't exist at agent construction time

Some authorization decisions depend on context that only exists at the moment of the tool call. These can't be handled by tool-set swapping because the agent is already constructed.

**Important caveat**: Cedar itself is **stateless** — it evaluates a single authorization request against policies and entities, and returns Allow or Deny. It doesn't track counters, timers, or approval workflows. For runtime conditions, the **plugin** is responsible for gathering state and passing it into the Cedar request as context. Cedar's job is to evaluate the policy against that context. The split is: plugin gathers facts, Cedar makes the decision.

Here's how each case works:

**Time-based: "Destructive tools only during business hours"**

The plugin passes the current timestamp as context. The policy checks it. Straightforward.

```cedar
forbid (
  principal,
  action in [Action::"delete_record", Action::"terminate_ec2_instance"],
  resource
) when {
  context.hour_utc < 9 || context.hour_utc > 17
};
```

The plugin's `BeforeToolCallEvent` hook does:
```python
context = {
    "hour_utc": datetime.utcnow().hour,
    "day_of_week": datetime.utcnow().strftime("%A"),
    **tool_arguments
}
```

This is simple and works well. The policy is readable, auditable, and testable in isolation.

**Environment-based: "No destructive tools during a deploy freeze"**

Same pattern — the plugin passes environment state as context.

```cedar
forbid (
  principal,
  action in [Action::"delete_record", Action::"provision_aws_account"],
  resource
) when {
  context.deploy_freeze == true
};
```

The plugin reads the freeze status from an environment variable, a feature flag service, or a config file. Cedar doesn't care where it comes from — it just evaluates the boolean.

**Rate-based: "Max 10 `send_email` calls per session"**

This is where the plugin does more work. Cedar is stateless, so the plugin must maintain counters externally and pass the current count as context.

```cedar
forbid (
  principal,
  action == Action::"send_email",
  resource
) when {
  context.session_tool_call_count >= 10
};
```

The plugin tracks tool call counts in memory (or Redis, or a database for distributed deployments) and injects the count into every authorization request:
```python
context = {
    "session_tool_call_count": self.call_counts[session_id].get("send_email", 0),
    **tool_arguments
}
```

This works, but it's honest to say the **heavy lifting is in the plugin, not in Cedar**. Cedar's role here is making the threshold (10) and the scope (per-session, per-tool) configurable via policy rather than hard-coded. If you want to change the limit from 10 to 20 or restrict it to specific roles, you edit the policy — no code change.

**Approval-based: "Purchases over $10,000 require manager approval"**

Cedar can express the condition cleanly:

```cedar
forbid (
  principal,
  action == Action::"submit_purchase_order",
  resource
) when {
  context.amount > 10000 && !context.has_manager_approval
};
```

But Cedar does **not** implement the approval workflow itself — it doesn't pause execution, notify a manager, and wait. The plugin (or a broader system) must:
1. Detect that the tool call would be denied pending approval (Cedar returns Deny with the relevant policy)
2. Trigger an out-of-band approval request (Slack message, email, UI prompt)
3. On approval, re-invoke with `context.has_manager_approval = true`

This is a real limitation to call out: Cedar handles the **decision** ("is this approved?") but not the **workflow** ("go get approval"). The approval flow requires additional infrastructure beyond the plugin. Cedar's value here is that the threshold ($10,000) and the approval requirement are expressed declaratively in policy rather than buried in application code.

**Summary of the pattern**: The plugin gathers runtime state → passes it as Cedar context → Cedar evaluates the policy → returns Allow/Deny. For simple context (time, environment flags), this is clean and the plugin barely does anything. For stateful context (counters, approval status), the plugin carries more weight and Cedar's role is primarily making the thresholds and conditions configurable via policy.

#### 5. Separation of concerns — who owns permissions?

With tool-set swapping, the person writing the API router / agent factory is encoding the permission model in Python code. This means:

- **Security teams can't review permissions** without reading your application code
- **Changing permissions requires a code change** — PR, review, deploy
- **Permissions aren't versionable as a standalone artifact** — they're scattered across constructors and if-statements
- **No static analysis** — you can't ask "which roles can reach `delete_record`?" without tracing through your code

Cedar makes permissions a **separate artifact** — a `.cedar` file that security teams can read, review, and analyze without understanding your Python codebase. This is the same reason web apps use IAM policies instead of hard-coding `if user.role == "admin"` in every route handler.

#### 6. Multi-agent delegation and permission scoping

In a Strands swarm or graph, Agent A (a coordinator) hands off work to Agent B (a specialist). Agent B has powerful tools. The question: should Agent B be able to use *all* its tools, or only the ones that Agent A's original user is allowed to trigger?

With tool-set swapping, Agent B has a fixed tool set — it doesn't know or care who Agent A's user is. With Cedar, the original user's identity propagates through the delegation chain, and Agent B's tools are gated by the same policies:

```cedar
// Agent B can only use tools that the originating user is allowed to use
// The principal is the original user, not the agent
permit (
  principal in Team::"cloud_platform",
  action == Action::"terminate_ec2_instance",
  resource
) when {
  resource.account_id in principal.managed_accounts
};
```

This works regardless of which agent in the chain actually calls the tool — the policy follows the user, not the agent.

### Summary

| | Tool-set swapping | Cedar |
|-|-------------------|-------|
| Binary include/exclude a tool | Yes | Yes |
| Gate based on tool arguments | No | Yes |
| Model can explain denial to user | No (tool is invisible) | Yes (tool is visible, denial is structured) |
| Dynamic per-tenant/per-user permissions | Requires custom routing code | Declarative policy |
| Runtime conditions (time, rate, env) | Requires per-call agent reconstruction | Native `when` clauses |
| Security team can review permissions | Must read Python code | Read `.cedar` files |
| Static analysis of permission set | No | Yes (automated reasoning) |
| Multi-agent permission propagation | No | Yes (principal follows the user) |

**The rule of thumb**: If your permission model is "role X gets tools A, B, C" and nothing more, use tool-set swapping. If you need *any* of the right column, you need a policy engine. Cedar is that engine.

### Why Not Just Use IAM / Application-Layer Auth?

"My agent runs with AWS credentials. I'll scope those with IAM policies. Or I'll check permissions in my API layer before calling the agent. Why do I need auth *inside* the agent?"

### Traditional apps vs. agents: the control flow changed

In a traditional app, the control flow is predictable. User clicks a button, your code runs a known function, you check permissions, done. Every action traces directly back to a user interaction.

Agents broke this. A user sends one message — "help me clean up our staging environment" — and the agent autonomously decides to call `list_instances`, then `terminate_ec2_instance` four times, then `delete_database`, then `send_email` to notify the team. **The user didn't ask for five of those six actions.** The agent decided them.

Your API gateway authorized the chat message. IAM allows the agent to call EC2 and RDS. Neither layer has any opinion about whether *this user* should be able to trigger `terminate_ec2_instance` through *this agent*. Both layers say "allowed" for every user, every time.

### The principal problem

IAM authorizes the **agent process**. Not the user.

Your agent has one IAM role. Ten users talk to it. When Alice (admin) asks the agent to delete a record and Bob (intern) asks the agent to delete a record, IAM sees the exact same principal making the exact same `DynamoDB:DeleteItem` call. Both succeed.

This isn't hypothetical — it's how every multi-user agent works today. The agent is a shared process. IAM sees the process, not the person behind it.

You could create separate IAM roles per user and assume them dynamically. But IAM condition keys don't map to "which tool did the agent choose to call" or "what arguments did it decide to pass." You'd end up managing N roles that imperfectly mirror your application's permission model, and you'd still have no coverage for tools that don't call AWS.

This is the same reason your web app doesn't rely solely on database credentials. The app connects to Postgres as one service account. Nobody says "just use Postgres roles for user auth" — the database sees one principal, not your users. You need an authorization layer that knows about users. For agents, that layer sits at the tool-call boundary.

### The enforcement gap

Your API gateway authorized the user's request. Your application code validated the input. Then you called `agent("clean up staging")` and handed control to a model.

What happens next is **not in your code**. The model decides which tools to call, in what order, with what arguments. It might:

- Call `query_database(database="production")` when the user should only access `staging`
- Chain `send_email` 20 times because the model thought it was being helpful
- Call `delete_record` on records the user never mentioned because the model inferred they were "related"
- Escalate from a read operation to a write operation because the model decided to "fix" something it found

None of these actions were in the user's original request. The application layer authorized the request. The agent made autonomous decisions after that. There is no existing layer that intercepts those decisions.

### Where each auth layer stops

| Auth layer | What it knows | What it doesn't know |
|------------|--------------|---------------------|
| API gateway | This user is authenticated and hit a valid endpoint | What the agent will do with their message |
| IAM | This process can call DynamoDB and S3 | Which user triggered this specific call |
| Database permissions | This connection can run SELECT and INSERT | Whether this user should see this particular row |
| Tool-level if/else checks | This specific tool's business rules | What other tools were called, rate limits across tools, unified audit trail |

Each layer has a blind spot that the others can't cover. IAM doesn't know about users. The API gateway doesn't know about tool calls. Database permissions don't cover non-database tools. Tool-level checks are scattered, inconsistent, and invisible to static analysis.

### The tool-call boundary is the only chokepoint

Every action an agent takes — AWS API call, internal service request, database query, file operation, third-party SaaS call, shell command — flows through the tool-call loop. It's the one point where you know: *who* is the user, *what* tool is being called, *with what arguments*, and *in what context* (time, environment, how many times this session).

This is what the plugin hooks into. It's the equivalent of middleware in a web framework — every request passes through it, and you can enforce policy uniformly without scattering auth checks across every handler.

Without it, you have two choices:

1. **Trust the agent.** Every user gets the agent's full capability set. Hope the model doesn't do anything inappropriate. This is the default today, and it's fine for demos and single-user tools. It's not fine for production multi-user agents with destructive tools.

2. **Roll your own.** Add permission checks inside each tool. Maintain a list of who can call what. Track rate limits manually. Build audit logging. Parse role information in every tool function. Congratulations — you've built a bespoke, untested, unanalyzable authorization system scattered across your codebase. Cedar replaces that with a purpose-built policy language that can be reviewed, versioned, tested, and statically verified as a standalone artifact.

### How This Differs from Existing Control Plugins

Strands already has plugin-based control mechanisms, and external platforms like Galileo and Datadog offer agent guardrails. None of them do authorization.

| | Strands Steering | Galileo / Datadog Guardrails | Cedar Auth Plugin |
|-|-----------------|---------------------------|-------------------|
| **Question answered** | *"Is the agent following the right procedure?"* — context-aware guidance, tone checks, workflow compliance | *"Is the agent's output safe and high-quality?"* — hallucination detection, toxicity, PII leakage, prompt injection | *"Is this user allowed to invoke this tool?"* — role-based, attribute-based, and relationship-based access control |
| **Decision model** | LLM-based evaluation (Proceed / Guide / Interrupt) | ML scoring (probabilistic, 0–1 thresholds) | Policy evaluation (deterministic Allow/Deny) |
| **What it gates** | Tool calls (cancel + feedback) and model outputs (discard + retry) | Model *outputs* after generation | Tool *invocations* before execution |
| **Identity-aware** | No — evaluates the *action*, not *who* is performing it | No — evaluates content, not who produced it | Yes — policies are written in terms of principals, roles, and resource ownership |
| **Static analysis** | No — LLM evaluations can't be formally verified | No — ML scorers can't be formally verified | Yes — Cedar supports automated reasoning (prove no user can reach a tool, detect policy conflicts) |
| **Bypassable** | Guidance only — the model can choose to ignore steering feedback | Depends on implementation | No — `forbid` policies are enforced at the framework level, before the tool executes. The model cannot override a denial. |

**Steering vs. Cedar**: Steering plugins ([docs](https://strandsagents.com/docs/user-guide/concepts/plugins/steering/)) guide the agent's *behavior* — "review this email for tone before sending," "follow these steps in order," "ask a human if you're unsure." They're about *how* the agent works, not *who* is allowed to do *what*. A steering plugin might cancel a `send_email` call because the tone is wrong; Cedar cancels it because *this user* doesn't have permission to send email. Steering is content-aware; Cedar is identity-aware. They hook into the same `BeforeToolCallEvent`, but they answer fundamentally different questions.

These are complementary layers. A production agent might use all three: Cedar to enforce *"can this user do this?"* before the tool runs, steering to ensure *"is the agent doing this correctly?"* during execution, and a guardrail platform to evaluate *"was the output safe?"* after the model responds.

### When you don't need this

Not every agent needs this:

- **Single-user agent** — no multi-tenancy, no principal mismatch
- **All tools are read-only** — no destructive operations, no sensitive data
- **Two roles, three tools, no conditional logic** — just build two agents with different tool sets

The plugin exists for the gap between "my API has auth" and "the agent is making autonomous decisions on behalf of different users with different permissions." If that gap doesn't exist in your system, you don't need it. But if you're building a multi-user agent with tools that have real-world consequences, that gap is where incidents happen.

## Proposal

**`CedarAuthPlugin`** is a first-party Strands plugin that uses the [Cedar policy language](https://github.com/cedar-policy/cedar) to enforce fine-grained, auditable authorization over every tool call an agent makes. Cedar is purpose-built for authorization: it is fast (bounded-latency evaluation), analyzable (automated reasoning can prove policy properties), and expressive enough to cover RBAC, ABAC, and ReBAC models in a single policy set.

## How It Works

The plugin hooks into the Strands agent lifecycle at two points:

| Hook | Event | What happens |
|------|-------|-------------|
| **Pre-tool gate** | `BeforeToolCallEvent` | Constructs a Cedar authorization request from the tool call context and evaluates it against the loaded policy set. If the decision is `Deny`, sets `event.cancel_tool` with a denial message — the tool never executes. |
| **Post-tool audit** | `AfterToolCallEvent` | Logs the authorization decision, tool result (or exception), and full request context to a structured audit trail. |

Because Strands plugins auto-register hooks via the `@hook` decorator, no changes to the core SDK or to individual tools are required. Authorization is orthogonal to tool implementation.

## How Does Identity Get Into the Agent?

### What Strands has today: `invocation_state`

Strands agents accept an `invocation_state` dict on every call. This dict flows through the entire lifecycle — every hook event and every tool can read it. Today, the SDK uses it for **framework internals only**:

| Key | Set by | Purpose |
|-----|--------|---------|
| `agent` | Agent / Tool executor | Reference to the Agent instance |
| `model` | Tool executor | Model instance |
| `messages` | Tool executor | Current conversation |
| `system_prompt` | Tool executor | Agent's system prompt |
| `tool_config` | Tool executor | Available tool specs |
| `event_loop_cycle_id` | Event loop | UUID for the current cycle |
| `request_state` | Event loop | State tracked across cycles |
| `event_loop_cycle_trace` | Event loop | Telemetry trace |
| `event_loop_cycle_span` | Event loop | OpenTelemetry span |

**There is no `user_id`, `principal`, `roles`, or any identity data.** The dict is designed to be caller-extensible — your app can put whatever it wants in there — but today nobody does, and there's no convention for it.

### How other frameworks handle identity and auth

**No agentic SDK has built-in tool-level authorization.** Identity propagation varies, but the authorization decision is always left to the application layer.

| Framework | Identity mechanism | Tool-level auth? |
|-----------|-------------------|-----------------|
| **ADK (Google)** | `user_id` is a **required parameter** on `Runner.run_async()`. Flows through `Session` → `InvocationContext` → tools access via `Context.user_id`. | No. ADK knows who the user is but doesn't gate which tools they can call. That's left to the app (tool-set swapping or custom checks inside tools). |
| **LangChain** | `config["metadata"]` dict on `RunnableConfig`. Generic dict that auto-propagates. No built-in identity keys. | No. LangGraph *Cloud* (the deployment platform, not the SDK) has `@auth.on.*` handlers — but that's a server-layer feature. |
| **CrewAI** | Nothing for tool execution. Has A2A inter-agent auth (OAuth, OIDC, mTLS) for agents talking to other agents. | No. User identity doesn't reach tool calls. |
| **AutoGen** | Nothing in core. Web UI (AutoGen Studio) has login middleware that doesn't reach agent execution. | No. |

**Why none of them have tool-level auth:**

1. **Most agents today are single-user.** Developer builds an agent, runs it locally or in a notebook. There's no "other user" to authorize against. Tool-level auth solves a multi-user problem, and most deployments aren't multi-user yet.

2. **The SDK vs. platform split.** The industry pattern is: SDKs are libraries for building agents, platforms/servers are where auth lives. LangChain (SDK) has no auth; LangGraph Cloud (platform) does. ADK (SDK) carries `user_id` but doesn't gate tools; Vertex AI Agent Engine (platform) handles access control.

3. **Auth is too application-specific for a framework opinion.** What "authorized" means varies wildly — HIPAA in healthcare, SOX in finance, role-based in enterprise IT. Frameworks avoid opinionated auth because the opinions would be wrong for most users.

4. **The market is early.** Agents haven't hit production multi-tenant deployments at scale yet. As they do, tool-level auth will become a hard requirement. But we're not there industry-wide yet.

**Should we break the pattern?**

Not in the core SDK — but a first-party plugin is the right layer to break it. The other SDKs are right that tool-level auth doesn't belong in the core library (too application-specific). But they're wrong to offer *nothing*. A Cedar plugin is opinionated (Cedar as the policy language) but optional (don't install it if you don't need it). The policies are application-specific; the plugin is generic. This gives Strands a real answer for "how do I deploy one agent to 1000 users with different permissions?" without forcing single-user agents to care.

### No SDK changes required

The plugin works entirely with existing Strands primitives:

- **`invocation_state`** — the developer passes identity in here (whatever keys they want)
- **`BeforeToolCallEvent`** — the plugin hooks here, reads `event.invocation_state`, and calls `event.cancel_tool` on denial
- **`principal` resolver** — a user-provided function that maps `invocation_state` to a Cedar principal string

The plugin's README documents what it expects in `invocation_state`. If the developer forgets to pass identity, the principal resolver raises a clear error. No new SDK types, no new parameters, no framework-level identity abstraction.

### How identity flows in: your app puts it in `invocation_state`

Strands is a library, not a server. It doesn't listen on a port, handle HTTP, or validate tokens. It runs inside *your* application — which is the thing that authenticates users. The flow:

```
User authenticates → [Your API layer] → extracts identity → passes into invocation_state → agent runs
```

```python
# FastAPI example — OAuth/JWT authentication happens in your middleware
@app.post("/chat")
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):
    result = agent(
        request.message,
        invocation_state={
            "user_id": user.id,          # from JWT "sub" claim
            "roles": user.roles,          # from JWT "groups" claim
            "team": user.team,
        }
    )
    return result

# Lambda example — IAM authentication via API Gateway
def handler(event, context):
    caller_arn = event["requestContext"]["identity"]["userArn"]
    result = agent(
        event["body"]["message"],
        invocation_state={
            "iam_role": caller_arn,
            "aws_account": caller_arn.split(":")[4],
        }
    )
```

The Cedar plugin then reads identity from `event.invocation_state` inside the `BeforeToolCallEvent` hook and constructs the Cedar principal. It doesn't validate tokens, check passwords, or talk to identity providers. It trusts that by the time a tool call happens, `invocation_state` contains a verified identity — the same trust boundary as any authorization middleware.

### Any auth mechanism works

| Mechanism | Who uses it | What goes in `invocation_state` |
|-----------|------------|-------------------------------|
| **OAuth / OIDC** | Web apps (Google, Okta, Auth0) | `user_id`, `roles`, `team` from JWT claims |
| **IAM roles** | AWS-native services (Lambda, ECS) | `iam_role` ARN from API Gateway context |
| **API keys** | Internal tools, prototypes | `user_id`, `roles` from DB lookup |
| **mTLS** | Service mesh (Istio, Linkerd) | Service identity from client certificate |
| **None** | Local dev, single-user CLI | Hardcoded: `principal=lambda ctx: 'User::"developer"'` |

Cedar doesn't care which one you use. It sees a principal string like `User::"alice@acme.com"` or `ServiceRole::"arn:aws:iam::123:role/pipeline"` — how that string was derived is your app's concern.

### The principal can be a user, a service, or an agent

| Principal type | When it applies | Example |
|----------------|-----------------|---------|
| **A human user** | Most common. User hits your app, app calls the agent. | `User::"alice@acme.com"` via OAuth |
| **An IAM role** | AWS-native. Service authenticated via IAM. | `ServiceRole::"arn:aws:iam::123:role/data-pipeline"` |
| **An agent** | Multi-agent. Agent A delegates to Agent B. | `Agent::"coordinator-agent"` |
| **A service** | Automated pipelines, cron jobs. | `Service::"nightly-report-generator"` |

### What the plugin needs from you

Pass `user_id` and `roles` in `invocation_state`. The plugin handles the rest — it reads the identity, constructs a Cedar principal (`User::"alice"`), and evaluates policies against it.

```python
agent("delete record 42", invocation_state={
    "user_id": "alice",
    "roles": ["admin"],
})
```

For non-standard identities (IAM roles, service accounts), the builder's `.principal(key, type)` method or a Full Cedar custom `principal_resolver` lets you change how identity is read. See the Developer API section for details.

### Evaluation Mode

The plugin evaluates Cedar policies locally via `cedarpy` (Rust-backed Python bindings). This provides in-process, zero-network evaluation with microsecond latency — suitable for development, testing, and production use.

**Future path to Amazon Verified Permissions (AVP)**: AVP is Cedar-as-a-service — the same policy language, hosted by AWS with centralized policy management and CloudTrail audit logging. A natural evolution would be to make the evaluation backend pluggable so the same plugin can call AVP's `IsAuthorized` API instead of local `cedarpy`. However, AVP adoption for agent authorization is nascent, and the primary value proposition of this plugin is Cedar-the-language (the builder API, policy generation, static analysis), not the hosting backend. We focus on local Cedar for now and will revisit AVP integration if demand materializes.

**WASM portability**: Cedar is written in Rust, which compiles to WASM natively via `wasm-bindgen`. The same `cedar-policy` crate that backs `cedarpy` (Rust → Python via PyO3) can target WASM → JS/TS. This means Cedar policies written for this Python plugin are directly portable to a TypeScript/WASM runtime — no policy rewrite, no language change. If the plugin (or a sibling plugin for another framework) moves to a WASM + TypeScript stack, the evaluation backend changes but the policies, entities, and builder-generated Cedar all remain identical. The `cedar-for-agents` repo already has JS packages (`cedar-analysis-mcp-server`), so there's prior art for Cedar in the JS ecosystem.

### Why Cedar over OPA

OPA (Open Policy Agent) with Rego is the most widely adopted policy engine — battle-tested, Kubernetes-native, huge ecosystem. It's the obvious alternative. Here's why we chose Cedar:

| | Cedar | OPA / Rego |
|-|-------|-----------|
| **Language** | Purpose-built for authorization. `principal`, `action`, `resource`, `context` are language primitives. | General-purpose policy language. Authorization concepts are conventions on `input`, not language primitives. Also used for admission control, data filtering, config validation. |
| **Formal verification** | Yes. Can mathematically prove "no intern can reach `delete_record` in production." | No. OPA evaluates queries — it can't reason about the policy set as a whole. |
| **Evaluation guarantees** | Bounded-latency. No recursion, no loops, no user-defined functions. Every evaluation terminates in bounded time. | Rego allows recursion and comprehensions. Evaluation time depends on policy complexity. |
| **Readability** | `permit(principal in Role::"admin", action, resource)` reads like English. | `allow { some role in input.roles; role == "admin" }` — functional, but requires learning Rego syntax. |
| **WASM story** | Rust core compiles to WASM natively via `wasm-bindgen`. Same crate backs Python (PyO3), JS/TS (WASM), or any WASM host. | First-class WASM support (Go → WASM). Production-tested. Slightly more mature WASM ecosystem today. |
| **Managed service** | Cedar → Amazon Verified Permissions (AVP). Same policies, hosted by AWS, CloudTrail integration. | OPA → Styra DAS (commercial SaaS). No AWS-native equivalent. |
| **Community** | ~1.4k GitHub stars. Smaller ecosystem, fewer tutorials, less third-party tooling. Backed by AWS/Amazon. | ~11.5k GitHub stars. CNCF graduated project. Large ecosystem, extensive integrations, broad production adoption. |

**Why is OPA so much more widely used?** OPA launched in 2016 (Cedar in 2023), solves a broader problem (general-purpose policy, not just authorization), and is CNCF cloud-neutral rather than AWS-associated. Its ecosystem advantage is real — but for the narrow question of "can this user call this tool," Cedar's purpose-built authorization model, formal verification, and bounded evaluation are a better fit than OPA's general-purpose power.

**Would we build an OPA plugin?** The plugin architecture (hook into `BeforeToolCallEvent`, evaluate policy, cancel on deny) is engine-agnostic. An `OpaAuthPlugin` would replace Cedar evaluation with OPA/WASM evaluation and Rego policies. The main loss would be formal verification and the builder's ability to generate statically analyzable policies. If a team already runs OPA across their stack and wants one policy engine for everything (Kubernetes admission + agent authorization), an OPA plugin would be a reasonable choice. We'd welcome it as a sibling package but won't build it ourselves — Cedar is the opinionated choice for this plugin.

## Cedar Model Mapping

Strands concepts map naturally onto Cedar's authorization model:

```
┌─────────────────────┬──────────────────────────────────────────┐
│ Cedar Concept        │ Strands Mapping                          │
├─────────────────────┼──────────────────────────────────────────┤
│ Principal            │ The end user (or service identity)       │
│                      │ invoking the agent                       │
├─────────────────────┼──────────────────────────────────────────┤
│ Action               │ One Cedar action per tool, auto-generated│
│                      │ from the tool's name (e.g.,              │
│                      │ Action::"use_tool::delete_record")       │
├─────────────────────┼──────────────────────────────────────────┤
│ Resource             │ The target of the tool call — could be   │
│                      │ the tool itself, or a domain object      │
│                      │ extracted from tool arguments             │
├─────────────────────┼──────────────────────────────────────────┤
│ Context              │ Tool input arguments + session metadata   │
│                      │ (timestamp, conversation ID, agent name)  │
├─────────────────────┼──────────────────────────────────────────┤
│ Entities             │ User/role hierarchy + tool groups,        │
│                      │ supplied by the application or an         │
│                      │ entity provider callback                  │
└─────────────────────┴──────────────────────────────────────────┘
```

### Example Policy

```cedar
// Allow analysts to search, deny destructive operations
permit (
  principal in Role::"analyst",
  action in Action::"use_tool::search_documents",
  resource
);

forbid (
  principal,
  action in Action::"use_tool::delete_record",
  resource
) when {
  context.environment == "production"
};
```

## How the Authorization Request Is Built

When the model calls a tool, the plugin intercepts the call in `BeforeToolCallEvent` and builds a Cedar authorization request with four parts: **principal**, **action**, **resource**, and **context**. Understanding where each part comes from is key to writing effective policies.

### Principal

Who is asking. Built from `invocation_state` by the principal resolver (see "What the plugin needs from you" above).

```
invocation_state = {"user_id": "alice", "roles": ["admin"]}
→ principal = User::"alice"
```

### Action

Which tool is being called. Auto-derived from the tool name — you don't configure this.

```
tool call: query_database(database="analytics")
→ action = Action::"use_tool::query_database"
```

### Resource

What the tool is acting on. By default, this is the tool itself:

```
→ resource = Tool::"query_database"
```

This default works when your policies are about **which tools** a role can use — which is most cases. You only need a custom `resource_resolver` when policies need to reference the **specific thing** a tool targets (a particular record, an S3 bucket, an EC2 instance).

The `resource_resolver` accepts four formats:

**Declarative dict** — a per-tool mapping of which argument to extract and what Cedar type to use. Tools not in the mapping fall back to `Tool::"tool_name"`.

```python
plugin = CedarAuthPlugin(
    policies=POLICIES,
    entities=ENTITIES,
    resource_resolver={
        "delete_record": {"key": "record_id", "type": "Record"},
        "terminate_instance": {"key": "instance_id", "type": "Instance"},
    },
)
```

**JSON or TOML file** — the same mapping, loaded from a config file. Useful for separating resource configuration from code.

```python
plugin = CedarAuthPlugin(
    policies=POLICIES,
    entities=ENTITIES,
    resource_resolver="./resources.json",
)
```

Where `resources.json`:
```json
{
  "delete_record": {"key": "record_id", "type": "Record"},
  "terminate_instance": {"key": "instance_id", "type": "Instance"}
}
```

Or `resources.toml`:
```toml
[resources.delete_record]
key = "record_id"
type = "Record"

[resources.terminate_instance]
key = "instance_id"
type = "Instance"
```

**Callable** — a function `(tool_name, tool_input) -> str` for full control when the dict format isn't enough.

```python
plugin = CedarAuthPlugin(
    policies=POLICIES,
    entities=ENTITIES,
    resource_resolver=lambda tool, args: f'Record::"{args["record_id"]}"' if tool == "delete_record" else f'Tool::"{tool}"',
)
```

All formats enable the same policies:

```cedar
// Only allow deleting records you own
permit (principal, action == Action::"use_tool::delete_record", resource)
when { resource.owner == principal };
```

Most users won't need a `resource_resolver`. The builder and config file APIs don't expose it — it's a Full Cedar feature.

### Context

Everything Cedar needs to make conditional decisions. The plugin builds this from three sources:

**1. Tool arguments** — copied directly from the model's tool call. If the model calls `query_database(database="analytics", mode="read_only")`, both `database` and `mode` appear in context. This is how Cedar policies can gate based on *how* a tool is used, not just *whether* it's called.

**2. Time enrichments** — the plugin adds these automatically on every request:
- `hour_utc` — current hour (0–23), for time-window policies
- `timestamp` — ISO 8601 timestamp, for audit trails

**3. State enrichments** — added when the relevant builder methods are used:
- `environment` — read from `invocation_state["environment"]`, for `.deny_tools_in_env()`
- `session_call_count` — the plugin's internal counter for this tool in this session, for `.rate_limit()`

The full context for a `query_database` call looks like:

```json
{
  "database": "analytics",
  "mode": "read_only",
  "hour_utc": 14,
  "timestamp": "2026-03-25T14:30:00Z",
  "environment": "production",
  "session_call_count": 2
}
```

Each field is available in Cedar policies as `context.<field>`:

```cedar
// Tool argument: restrict which databases can be queried
forbid (principal, action == Action::"use_tool::query_database", resource)
when { !(context.database == "analytics" || context.database == "reporting") };

// Time enrichment: only during business hours
forbid (principal, action, resource)
when { context.hour_utc < 9 || context.hour_utc >= 17 };

// State enrichment: rate limit
forbid (principal, action == Action::"use_tool::send_email", resource)
when { context.session_call_count >= 3 };
```

The builder methods (`.restrict()`, `.time_window()`, `.rate_limit()`, `.deny_tools_in_env()`) generate these policies for you. But understanding what's in context is useful if you drop to Full Cedar or want to know why a policy matched.

## Schema Generation

The plugin auto-generates a Cedar schema from the agent's registered tools at startup:

1. Walk `agent.tools` and extract each tool's name, docstring, and input schema (derived from type hints / Zod-style definitions).
2. Emit one Cedar `action` per tool, with a `context` record type mirroring the tool's input parameters.
3. Merge with a user-provided schema stub that defines principal types, resource types, and entity hierarchies.

This is analogous to what [`cedar-policy-mcp-schema-generator`](https://github.com/cedar-policy/cedar-for-agents) does for MCP tool descriptions, adapted for Strands tool definitions. The generated schema enables Cedar's **validator** to catch policy errors at deploy time (e.g., referencing a tool that doesn't exist, or a context attribute with the wrong type).

## Static Verification

Because Cedar policies are analyzable, the plugin exposes a **`CedarPolicyVerifier`** — a standalone verifier that validates policies against a schema and runs policy analysis checks. It requires no agent instance, no model, and no network — just policies and a schema. This makes it ideal for CI/CD pipelines, pre-commit hooks, and deploy gates.

### Why this matters

Most authorization systems are black boxes at deploy time — you find out a policy is wrong when a user gets denied (or worse, when they *don't* get denied). Cedar's formal model means you can prove properties about your policies before they reach production:

- **Schema validation**: Does the policy reference tools, actions, and context attributes that actually exist? Catches typos like `Action::"use_tool::delet_record"` and type errors like comparing a string to an integer.
- **Reachability**: "Is there any principal that can invoke `delete_record` in production?" — catches overly permissive policies.
- **Completeness**: "Are all tools covered by at least one permit policy?" — catches silent denials where a new tool is unreachable because nobody wrote a policy for it.
- **Redundancy**: "Does policy X shadow policy Y?" — simplifies policy sets by finding policies that have no effect.

### Verifier API

The verifier follows the same builder pattern as the plugin itself:

```python
from cedar_auth_plugin import CedarPolicyVerifier

# Option 1: Verify policies from files
verifier = (
    CedarPolicyVerifier.from_files(
        policies="./policies.cedar",
        schema="./schema.cedarschema",
    )
)

# Option 2: Verify policies generated by the builder
plugin = (
    CedarAuthPlugin.builder()
    .role("admin", tools=["*"])
    .role("analyst", tools=["search", "query_database"])
    .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]})
    .build()
)
verifier = CedarPolicyVerifier.from_plugin(plugin)

# Run checks
result = verifier.validate()       # schema validation — are policies well-formed?
result = verifier.check_reachability(
    action="use_tool::delete_record",
    context={"environment": "production"},
)  # can any principal reach this tool in this context?
result = verifier.check_completeness(
    tools=["search", "query_database", "delete_record", "send_email"],
)  # does every tool have at least one permit path?

# All-in-one for CI
verifier.assert_all()  # raises VerificationError with details on first failure
```

### CI/CD Integration

The verifier can run as a CLI command in your pipeline:

```bash
# GitHub Actions / any CI
cedar-strands verify \
  --policies ./policies.cedar \
  --schema ./schema.cedarschema \
  --check schema \
  --check completeness \
  --check "reachability:delete_record:environment=production"
```

Or as a Python script:

```python
# ci/verify_policies.py
import sys
from cedar_auth_plugin import CedarPolicyVerifier

verifier = CedarPolicyVerifier.from_files(
    policies="./policies.cedar",
    schema="./schema.cedarschema",
)

errors = verifier.validate()
if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)

print("All policy checks passed.")
```

### Relationship to schema auto-generation

The verifier is most useful when paired with a Cedar schema. The plugin can auto-generate a schema from the agent's registered tools at startup (see Open Question #5), which means the CI pipeline can:

1. Import the agent's tool definitions
2. Auto-generate the Cedar schema
3. Validate policies against that schema
4. Run reachability/completeness checks

This catches a common failure mode: a developer adds a new tool to the agent but forgets to write a policy for it. The completeness check flags it before deploy.

## Developer API

Three entry points, each with a distinct purpose. They all generate Cedar under the hood, so policies are always auditable regardless of which entry point you use.

### How to choose

| | Builder | Config file | Full Cedar |
|-|---|---|---|
| **Entry point** | `CedarAuthPlugin.builder()` | `CedarAuthPlugin.from_config()` | `CedarAuthPlugin()` |
| **When to use** | Simple RBAC through conditional constraints (arg restrictions, rate limits, time windows, env rules) | Same as builder, but config lives outside Python | Relationship-based access, custom entities, resource-level policies |
| **Cedar syntax required** | None | None | Yes |
| **Principal** | Configurable via `.principal(key, type)`, defaults to `user_id` | `[principal]` section in config | Dict `{"key": ..., "type": ...}` or custom function |
| **Resource** | Always `Tool::"tool_name"` | `[resources]` section in config | Custom `resource_resolver` (dict, JSON/TOML file, or function) |
| **Entities** | Auto-generated from roles | Auto-generated from roles | You provide them (`.json` file, list, or callback) |
| **Policies** | Auto-generated from builder methods | Auto-generated from config | You write them (`.cedar` file or inline string) |
| **Config lives in** | Python code | `.toml` / `.json` file | `.cedar` / `.json` files |

**The rule of thumb**: Start with the builder. Move to config file when you want authorization outside of code (different configs per environment, security team owns the file). Move to full Cedar when you need entity relationships or resource-level policies.

Because config file and Full Cedar both store policy in plain files, those files can live anywhere — git, S3, a config service. This naturally separates policy from code and opens a path to centralized management without AVP.

### Builder

From simple RBAC to conditional constraints — argument restrictions, rate limits, time windows, and environment rules. Everything is declarative — no Cedar syntax, no lambdas.

**Available builder methods:**

| Method | What it does |
|--------|-------------|
| `.principal(key, type)` | Set which `invocation_state` key holds the identity and what Cedar type to use. Defaults to `key="user_id"`, `type="User"`. |
| `.role(name, tools)` | Grant a role access to specific tools. Use `["*"]` for all tools. |
| `.restrict(tool, allowed_values, for_role)` | Restrict a tool's arguments to specific values. Optional `for_role` scopes the restriction to one role. |
| `.rate_limit(tool, max_per_session)` | Limit how many times a tool can be called per session. |
| `.time_window(hour_start, hour_end)` | Only allow tool calls during a UTC time window. |
| `.deny_tools_in_env(environment, tools)` | Block specific tools in a given environment. |

```python
plugin = (
    CedarAuthPlugin.builder()

    # Identity: read email from invocation_state instead of user_id
    .principal(key="email")

    # RBAC: role → tools
    .role("admin", tools=["*"])
    .role("analyst", tools=["search", "query_database", "send_email"])

    # Scope tool arguments globally: nobody can query_database outside these databases
    .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]})

    # Or scope to a specific role: only analysts are restricted
    .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]}, for_role="analyst")

    # Rate limit: max 3 send_email calls per session
    .rate_limit("send_email", max_per_session=3)

    # Time window: only allow tool calls 9am-5pm UTC
    .time_window(hour_start=9, hour_end=17)

    # Environment: block destructive tools in production
    .deny_tools_in_env("production", ["delete_record", "drop_table"])

    .build()
)

agent = Agent(plugins=[plugin], tools=[...])
agent("query the analytics db", invocation_state={
    "email": "bob@acme.com",
    "roles": ["analyst"],
    "environment": "production",
})
```

Each builder method generates the corresponding Cedar policy under the hood. The customer never writes Cedar, but it's all Cedar underneath — so the policies are auditable, analyzable, and composable.

**What each builder method generates:**

| Builder method | Generated Cedar |
|---------------|----------------|
| `.role("admin", ["*"])` | `permit (principal in Role::"admin", action, resource);` |
| `.role("analyst", ["search", "query_database"])` | `permit (principal in Role::"analyst", action in [Action::"use_tool::search", Action::"use_tool::query_database"], resource);` |
| `.restrict("query_database", {"database": ["analytics", "reporting"]})` | `forbid (principal, ...) when { !(context.database == "analytics" \|\| context.database == "reporting") };` — applies to all roles |
| `.restrict("query_database", {"database": [...]}, for_role="analyst")` | `forbid (principal in Role::"analyst", ...) when { ... };` — only restricts analysts, other roles unaffected |
| `.rate_limit("send_email", max_per_session=3)` | `forbid (...) when { context.session_call_count >= 3 };` (plugin tracks counter, passes it as context) |
| `.time_window(9, 17)` | `forbid (...) when { context.hour_utc < 9 \|\| context.hour_utc >= 17 };` |
| `.deny_tools_in_env("production", [...])` | `forbid (...) when { context.environment == "production" };` |

### Config file (`from_config`)

Same power as the builder, but the entire authorization setup lives in a TOML or JSON file instead of Python code. This means:

- **Permission changes don't require code changes** — edit the config, redeploy
- **Different configs per environment** — `cedar_auth.dev.toml`, `cedar_auth.prod.toml`
- **Security team owns the config** — they review TOML, not Python
- **Easy to diff and audit** — TOML changes are readable in PRs

```python
from cedar_auth_plugin import CedarAuthPlugin

plugin = CedarAuthPlugin.from_config("./cedar_auth.toml")
```

Where `cedar_auth.toml`:

```toml
[principal]
key = "email"
type = "User"

[roles]
admin = ["*"]
analyst = ["search", "query_database", "send_email"]

[resources.delete_record]
key = "record_id"
type = "Record"

[restrictions.query_database]
for_role = "analyst"
database = ["analytics", "reporting"]

[rate_limits]
send_email = 3

[time_window]
start = 9
end = 17

[deny_in_env.production]
tools = ["delete_record", "drop_table"]
```

Every section is optional. A minimal config is just `[roles]`:

```toml
[roles]
admin = ["*"]
analyst = ["search", "read_report"]
```

The config file supports the same features as the builder — roles, argument restrictions (with `for_role`), rate limits, time windows, environment denials, principal configuration, and resource resolvers. The plugin generates identical Cedar policies whether you use the builder or the config file.

### Full Cedar (advanced)

For anything the builder can't express — relationship-based access, custom entity hierarchies, resource-level policies, and custom principal resolution logic.

With Full Cedar, config lives in Cedar files — the same way you'd manage Cedar policies in any other Cedar deployment:

```
my-agent/
├── cedar/
│   ├── policies.cedar     # Cedar policies
│   └── entities.json      # Entity hierarchy
├── resources.json          # Resource resolver config (optional)
└── agent.py
```

**What Full Cedar adds over the builder/config file:**

- **Hand-written Cedar policies** — loaded from `.cedar` files. Relationship-based conditions, entity attributes, or anything the builder methods don't cover.
- **Custom entities** — loaded from `.json` files, or provided as a callable for dynamic entity resolution. You define the entity hierarchy directly rather than having the plugin auto-generate it from roles.
- **Custom `principal_resolver`** — accepts a dict `{"key": "iam_role", "type": "IamRole"}` (same format as the builder's `.principal()`) or a function for full control. The function form handles multi-field resolution, conditional types, or arbitrary logic.
- **Custom `resource_resolver`** — extracts domain-specific resources from tool arguments (e.g., `Record::"42"` instead of `Tool::"delete_record"`). Accepts a declarative dict, a JSON/TOML file path, or a callable.

**Loading from files:**

```python
from pathlib import Path
from cedar_auth_plugin import CedarAuthPlugin

# Policies and entities live in Cedar files, not Python strings
plugin = CedarAuthPlugin(
    policies=Path("./cedar/policies.cedar"),
    entities=Path("./cedar/entities.json"),
    resource_resolver="./resources.json",
)
```

`policies.cedar`:
```cedar
permit (
    principal in Team::"cloud_platform",
    action == Action::"use_tool::terminate_ec2_instance",
    resource
) when {
    resource.account_id in principal.managed_accounts
};
```

`entities.json`:
```json
[
    {"uid": {"type": "Team", "id": "cloud_platform"}, "attrs": {"managed_accounts": ["111111111111", "222222222222"]}, "parents": []},
    {"uid": {"type": "User", "id": "alice"}, "attrs": {}, "parents": [{"type": "Team", "id": "cloud_platform"}]}
]
```

The `policies` parameter accepts a `Path` (any extension) or a string ending in `.cedar` to load from file. Any other string is treated as inline Cedar. The `entities` parameter similarly accepts a file path (loaded as JSON) or a list/callable.

**Custom principal resolver examples:**

The `principal_resolver` parameter accepts either a dict or a function. The dict form mirrors the builder's `.principal(key, type)` and the config file's `[principal]` section. The function form is for complex logic.

```python
# Dict form (same as builder's .principal() and config's [principal])
principal_resolver={"key": "iam_role", "type": "IamRole"}
```

The function form takes `invocation_state` and returns a Cedar principal string like `User::"alice"` or `IamRole::"arn:..."`. Cedar uses this to determine which policies apply.

```python
# Simple: always a User
principal_resolver=lambda state: f'User::"{state["user_id"]}"'

# IAM role: for Lambda/ECS behind API Gateway
principal_resolver=lambda state: f'IamRole::"{state["iam_role"]}"'

# Multi-field: principal type depends on who's calling
def resolve_any(state):
    if "iam_role" in state:
        return f'IamRole::"{state["iam_role"]}"'
    elif "service_name" in state:
        return f'Service::"{state["service_name"]}"'
    return f'User::"{state["user_id"]}"'
```

**Full example with all features:**

```python
plugin = CedarAuthPlugin(
    policies=Path("./cedar/policies.cedar"),
    entities=my_entity_provider,         # callable, list, or file path
    principal_resolver=resolve_any,
    resource_resolver={                   # or "./resources.json"
        "terminate_ec2_instance": {"key": "instance_id", "type": "Instance"},
    },
)

agent = Agent(plugins=[plugin], tools=[...])
agent("terminate instance i-abc123", invocation_state={"user_id": "alice@acme.com"})
```

## Where This Lives: cedar-for-agents Repository

The plugin belongs in the [`cedar-for-agents`](https://github.com/cedar-policy/cedar-for-agents) repo, which exists specifically for "software at the intersection of Cedar and agents."

**Today the repo has:**
```
cedar-for-agents/
├── rust/
│   ├── mcp-tools-sdk/                         # Parse MCP tool descriptions
│   └── cedar-policy-mcp-schema-generator/     # MCP tools → Cedar schema
├── js/
│   └── cedar-analysis-mcp-server/             # MCP server for Cedar analysis
```

**The Strands plugin would add:**
```
cedar-for-agents/
├── rust/
│   └── ...
├── js/
│   └── ...
├── python/                                     # new
│   └── cedar-strands-agent-plugin/            # new — Strands plugin
```

**Why it fits:**

1. **Matches the repo's mission.** Strands is an agent framework. This is literally "Cedar for agents."
2. **Extends beyond MCP.** Today the repo only covers the MCP protocol layer (schema generation, analysis server). The Strands plugin adds runtime authorization — Cedar policies evaluated in the agent's tool-calling loop, not just schema generation.
3. **Schema generation patterns are reusable.** The `cedar-policy-mcp-schema-generator` generates Cedar schemas from MCP tool descriptions. The Strands plugin does the analogous thing from Python type hints. These could share patterns or the plugin could call the Rust crate via PyO3.
4. **Opens the `python/` ecosystem.** Once `python/` exists, other Python framework plugins (LangChain, ADK, CrewAI) have a natural home.

**What's new vs. what exists:**

| Existing (MCP-focused) | New (Strands plugin) |
|------------------------|---------------------|
| Generate Cedar schemas from tool descriptions | Generate Cedar schemas from Strands `@tool` type hints |
| Static — runs at build/deploy time | Runtime — evaluates policies on every tool call |
| Protocol-level (MCP tool JSON) | Framework-level (hooks into agent lifecycle) |
| No authorization decisions | Makes Allow/Deny decisions, cancels tool calls |
| No identity/principal concept | Maps `invocation_state` to Cedar principals |

**Contribution path:** Open an issue on `cedar-for-agents` proposing the `python/cedar-strands-agent-plugin` package. The package is installable standalone (`pip install cedar-strands-agent-plugin`), depends on `cedarpy` and `strands-agents`, and follows the repo's existing contribution process (issue first, changelog, Apache-2.0 license).

## Implementation

The implementation lives in `python/cedar-strands-agent-plugin/`. All demos run with `pip install cedarpy strands-agents`.

**Files:**

| File | What it does | How to run |
|------|-------------|------------|
| `cedar_auth_plugin.py` | Plugin with three entry points: builder, `from_config`, and full Cedar. Includes `CedarAuthBuilder`, `CedarAuthPlugin`, and `AuthzDecision`. | (library, not runnable) |
| `demo_simple.py` | Simple RBAC with the builder. 10 cases. No model needed. | `python demo_simple.py` |
| `demo_builder.py` | Tests all builder helpers (RBAC, arg scoping, rate limits, time window, env denial). 21 cases. No model needed. | `python demo_builder.py` |
| `demo_scoped_args_builder.py` | Same tool (`query_database`), different argument permissions per role using the builder's `for_role` parameter. 16 cases. No model needed. | `python demo_scoped_args_builder.py` |
| `demo_config.py` | Config-driven plugin from `cedar_auth.toml`. Tests RBAC, restrictions, rate limits, time windows, env denials, and resource resolution. 17 cases. | `python demo_config.py` |
| `cedar_auth.toml` | Example TOML config file for `from_config()`. | (config, not runnable) |
| `demo.py` | Full end-to-end with real Agent + model. 8 cases. Needs model provider. | `python demo.py` |

**`demo_builder.py` output (all 21 test cases pass):**
```
Test                                                    Expected   Actual
---------------------------------------------------------------------------
admin can search                                        ALLOW      ALLOW
admin can delete_record                                 ALLOW      ALLOW
analyst can search                                      ALLOW      ALLOW
analyst cannot delete_record                            DENY       DENY
analyst: query_database(analytics) allowed              ALLOW      ALLOW
analyst: query_database(reporting) allowed              ALLOW      ALLOW
analyst: query_database(production) DENIED              DENY       DENY
admin: query_database(production) also DENIED           DENY       DENY
send_email call 1/3: allowed                            ALLOW      ALLOW
send_email call 2/3: allowed                            ALLOW      ALLOW
send_email call 3/3: allowed                            ALLOW      ALLOW
send_email call 4/3: DENIED (rate limit)                DENY       DENY
send_email different session: allowed                   ALLOW      ALLOW
search at 12pm (in window): allowed                     ALLOW      ALLOW
search at 3am (outside window): DENIED                  DENY       DENY
search at 8am (before window): DENIED                   DENY       DENY
search at 5pm (at boundary, >= 17): DENIED              DENY       DENY
delete_record in dev: allowed (admin)                   ALLOW      ALLOW
delete_record in prod: DENIED                           DENY       DENY
drop_table in prod: DENIED                              DENY       DENY
search in prod: allowed (not in deny list)              ALLOW      ALLOW
---------------------------------------------------------------------------
Result: ALL PASSED
```

**Key design decisions:**

- **Three entry points**: Builder (common constraints) → Config file (TOML/JSON-driven) → full Cedar (anything). Each entry point generates Cedar under the hood, so policies are always auditable.
- **Builder generates Cedar policies**: Each `.restrict()`, `.rate_limit()`, `.time_window()`, `.deny_tools_in_env()` call generates the corresponding `forbid(...)` Cedar policy. The customer never writes Cedar syntax, but it's all Cedar underneath.
- **Plugin tracks stateful constraints**: Rate limits require counters. Cedar is stateless, so the plugin maintains call counts per session and passes the count as `context.session_call_count` into each Cedar evaluation. Cedar evaluates the threshold; the plugin manages the state. Session ID resolution falls back through `session_id` → `user_id` → `"_default"` (via `_get_session_id()`).
- **`cancel_tool`**: On denial, the plugin sets `event.cancel_tool` with a human-readable message. The model sees this as a tool error and can explain the denial to the user (confirmed working in `demo.py` with a real model).
- **Action naming**: `Action::"use_tool::{tool_name}"` — one Cedar action per tool, auto-derived from the tool's name.
- **Dynamic entities from `invocation_state`**: The `_dynamic_entities()` static method builds User entities from `invocation_state["user_id"]` and `invocation_state["roles"]` at runtime, with role membership expressed as parent relationships. No static entity JSON required for the simple/builder APIs.
- **Structured audit log**: Every authorization decision is recorded as an `AuthzDecision` dataclass with fields: `principal`, `action`, `resource`, `allowed`, `tool_name`, and `timestamp`. Accessible via the `plugin.audit_log` property.
- **Customizable resource resolution**: The `resource_resolver` parameter accepts a declarative dict (`{"delete_record": {"key": "record_id", "type": "Record"}}`), a JSON/TOML file path, or a callable `(tool_name, tool_input) -> str`. The dict format scales per-tool without growing if/else chains. Tools not in the mapping fall back to `Tool::"tool_name"`.

## Open Questions

1. **Resource granularity**: Resolved. The default resource is `Tool::"tool_name"`. For domain-specific resources, the `resource_resolver` parameter accepts a declarative dict, a JSON/TOML config file, or a callable. See "How the Authorization Request Is Built" → "Resource" for details.

2. **Entity provider pattern**: For dynamic entity stores (e.g., user roles from a database), the plugin needs an async-capable entity provider interface. What should the contract look like?

3. **Multi-agent**: In swarm/graph multi-agent setups, should each sub-agent carry its own policy set, or should policies be evaluated at the orchestrator level? Cedar's action hierarchy (`Action::"use_tool"` as parent of all tool actions) makes top-level policies composable with agent-specific ones.

4. **Verified Permissions integration**: Deferred. AVP adoption for agent authorization is early-stage. The plugin architecture (single evaluation point in `before_tool_call`) makes it straightforward to introduce a pluggable `Authorizer` backend later. In the meantime, the config file and Full Cedar approaches already support cloud-hosted permissions by storing `.toml`, `.json`, or `.cedar` files in S3 or a config service — a practical stepping stone to centralized management without AVP's full infrastructure. See "How to choose" for details.

5. **Schema auto-generation**: Should the plugin auto-generate a Cedar schema from `agent.tools` at startup? This would enable Cedar's validator to catch policy errors at deploy time (e.g., referencing a tool that doesn't exist). The `cedar-policy-mcp-schema-generator` does this for MCP; the same approach applies to Strands tool type hints.
