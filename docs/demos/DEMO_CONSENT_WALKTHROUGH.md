# Cedar Tool Consent — Allow, Deny, or Ask the Human

- [The Problem](#the-problem-no-standard-way-to-gate-tools-on-consent)
- [How Consent Works Today](#how-consent-works-today)
- [How Cedar Resolves This](#how-cedar-resolves-this)
- [The Demo](#the-demo)
- Appendix
  - [Appendix A: Example Session](#appendix-a-example-session)
  - [Appendix B: Cedar Policies](#appendix-b-cedar-policies)
  - [Appendix C: Consent Flow Diagram](#appendix-c-consent-flow-diagram)

## The Problem: No Standard Way to Gate Tools on Consent

Some tools should never run without explicit human approval. Sending an email, deleting a file, executing a shell command — these are actions where the cost of a mistake is high and the model's confidence is not enough. The human needs to see what's about to happen and say "yes" or "no."

Today there's no standard way to do this in agent frameworks. Developers either build ad-hoc confirmation flows per tool, or skip consent entirely and hope the prompt instructions are enough.

## How Consent Works Today

Products like Kiro and Claude Code solve this by hardcoding tool permission categories in application code:

| Category | Examples | Behavior |
|---|---|---|
| Always allowed | Read file, search | Executes silently |
| Requires approval | Write file, run shell | Pauses, shows args, waits for y/n |
| Always denied | (not exposed) | Not available to the model |

This works for a single product, but the permission model is baked into the client. Deployers can't customize which tools need consent without changing application code, and there's no way to vary the consent model by user, role, or context.

## How Cedar Resolves This

Cedar models consent as a **residual policy** — a `permit` that would approve the request *if* `context.user_consent == true` were present. On the first evaluation, that context field is missing, so Cedar denies. But the handler knows a residual exists, so instead of hard-blocking, it fires a Strands **Interrupt** to pause execution and ask the human.

```cedar
// Always allowed — no consent needed
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::search", Action::"use_tool::read_file"],
  resource
);

// Requires consent — residual policy, gated on user_consent
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::send_email", Action::"use_tool::delete_file"],
  resource
) when {
  context.user_consent == true
};

// install_package has no permit — always denied by Cedar default-deny
```

When the model calls `send_email`:

1. Cedar evaluates the request — `user_consent` is not in context, so the consent policy doesn't match. Cedar denies.
2. The handler detects this is a consent-gated tool (a residual policy exists that *would* approve with consent).
3. Instead of returning `Deny`, the handler returns `Interrupt` — one of the four [Intervention actions](../../docs/INTERVENTION_EXPLORATION.md) (Proceed / Deny / Guide / Interrupt).
4. The `InterventionRegistry` maps `Interrupt` to `event.interrupt()` — the Strands SDK's built-in mechanism for pausing execution and requesting human input.
5. The agent pauses and returns to the caller with `stop_reason == "interrupt"`. The caller prompts the human and resumes with their response.
6. On resume, if the human approved, the tool executes. If denied, the tool call is cancelled.

This uses the existing `CedarAuthHandler` from the [native intervention demo](../../python/strands-cedar-auth/demos/intervention/native.py) with `.consent()` on its builder — no separate handler class. Consent is built into the Cedar intervention handler itself.

The key properties:

- **Configurable per tool, per role, per context.** An admin might not need consent for `delete_file`; an intern does. This is a policy change, not a code change.
- **Composable with identity.** Consent policies layer on top of RBAC — a tool can require consent *and* be restricted to certain roles.
- **Uses the SDK's interrupt system.** No raw `input()` calls — the handler returns `Interrupt`, the registry calls `event.interrupt()`, and the SDK's built-in interrupt/resume flow handles the rest.
- **Auditable.** Every consent decision (approved, rejected, auto-allowed, hard-denied) is logged.
- **Default-deny is the safety net.** A tool with no `permit` policy is always blocked — consent can't override a missing permission.

## The Demo

```bash
cd python/strands-cedar-auth
python demos/consent.py
```

The demo uses the existing `CedarAuthHandler` with `.consent()` on its builder, passed as an intervention:

```python
cedar = (
    CedarAuthHandler.builder()
    .role("developer", tools=["search", "read_file"])
    .consent(tools=["send_email", "delete_file"])
    .build()
)

agent = Agent(
    tools=[search, read_file, send_email, delete_file, install_package],
    interventions=[cedar],
)
```

You type natural language requests, and the agent calls tools. When a consent-gated tool fires, the Strands interrupt system pauses execution and asks you to approve or deny.

**Three permission levels:**

| Level | Tools | What happens |
|---|---|---|
| Always allowed | `search`, `read_file` | Executes silently |
| Requires consent | `send_email`, `delete_file` | Interrupt pauses for approval |
| Always denied | `install_package` | Hard block, no prompt |

Requires `pip install cedarpy strands-agents` (interventions branch) and a model provider configured (default: Bedrock with Claude). See [Appendix A](#appendix-a-example-session) for a full example session transcript.

---

<details>
<summary><strong>Appendix A: Example Session</strong></summary>

```
======================================================================
Cedar Auth — Consent via Strands Interrupt
======================================================================

Permission levels:
  ALLOW     — search, read_file (Proceed)
  CONSENT   — send_email, delete_file (Interrupt → y/n)
  DENY      — install_package (Deny)

You are: alice (role: developer)
Type your request, or 'quit' to exit.
======================================================================

You: Search for strands agents documentation

Agent: I found several results for "strands agents documentation"...

You: Send an email to bob@acme.com about the quarterly results

  ⚠ CONSENT REQUIRED
    Tool 'send_email' requires your approval. Args: {"to": "bob@acme.com", ...}
    Approve? [y/n]: y

Agent: I've sent the email to bob@acme.com with the quarterly results.

You: Delete the file /tmp/old_data.csv

  ⚠ CONSENT REQUIRED
    Tool 'delete_file' requires your approval. Args: {"path": "/tmp/old_data.csv"}
    Approve? [y/n]: n

Agent: I wasn't able to delete that file — you denied the request.

You: Install the requests package

Agent: I'm not authorized to install packages.

You: quit

======================================================================
Audit Log
======================================================================
  [  PROCEED] search
  [INTERRUPT] send_email
  [INTERRUPT] delete_file
  [     DENY] install_package
```

</details>

<details>
<summary><strong>Appendix B: Cedar Policies</strong></summary>

The full policy set used in the demo:

```cedar
// Always allowed — search and read_file need no approval
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::search", Action::"use_tool::read_file"],
  resource
);

// Requires consent — send_email and delete_file need human approval
// This is the "residual policy" — gated on context.user_consent
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::send_email", Action::"use_tool::delete_file"],
  resource
) when {
  context.user_consent == true
};

// install_package has no permit — always denied by Cedar default-deny
```

Entities:

```json
[
  {"uid": {"type": "Role", "id": "developer"}, "parents": [], "attrs": {}},
  {"uid": {"type": "User", "id": "alice"}, "parents": [{"type": "Role", "id": "developer"}], "attrs": {}}
]
```

</details>

<details>
<summary><strong>Appendix C: Consent Flow Diagram</strong></summary>

```
Model calls a tool
       │
       ▼
CedarAuthHandler.evaluate()
       │
       ▼
Cedar evaluates request
(user_consent not in context)
       │
       ├── ALLOW ────────► return Proceed ──► Tool executes
       │                   (search, read_file — unconditional permit)
       │
       └── DENY ─────────► Is this a consent-gated tool?
                           (residual policy would permit
                            if user_consent were true)
                                  │
                           ┌──────┴──────┐
                           │             │
                          No            Yes
                           │             │
                           ▼             ▼
                     return Deny    return Interrupt
                   (no permit at    (residual permit exists,
                    all — e.g.       but user_consent missing)
                    install_package)      │
                                         ▼
                              InterventionRegistry
                              calls event.interrupt()
                                         │
                                         ▼
                                  Agent pauses, returns
                                  to caller with interrupts
                                         │
                                         ▼
                                  Caller prompts human
                                         │
                                  ┌──────┴──────┐
                                  │             │
                                 No            Yes
                                  │             │
                                  ▼             ▼
                             cancel_tool   Tool executes
                            "User denied"
```

</details>
