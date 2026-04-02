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

Cedar models consent as a policy condition. A tool that requires approval has a `permit` policy gated on `context.user_consent == true`:

```cedar
// Always allowed — no consent needed
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::search", Action::"use_tool::read_file"],
  resource
);

// Requires consent — won't execute until the human approves
permit(
  principal in Role::"developer",
  action in [Action::"use_tool::send_email", Action::"use_tool::delete_file"],
  resource
) when {
  context.user_consent == true
};

// install_package has no permit — always denied by Cedar default-deny
```

When the model calls `send_email`, Cedar evaluates the request without `user_consent` in context. The policy doesn't match, so Cedar denies. But the plugin recognizes this is a consent-gated tool (not a hard deny), so instead of blocking it surfaces the tool call to the human:

```
  ⚠ CONSENT REQUIRED  send_email
    Args: {"to": "client@acme.com", "subject": "Q1 Report"}
    Approve? [y/n]:
```

If the human approves, the plugin re-evaluates with `context.user_consent = true`. The policy matches, and the tool executes. If denied, the agent gets a rejection message and can adjust.

The key properties:

- **Configurable per tool, per role, per context.** An admin might not need consent for `delete_file`; an intern does. This is a policy change, not a code change.
- **Composable with identity.** Consent policies layer on top of RBAC — a tool can require consent *and* be restricted to certain roles.
- **Auditable.** Every consent decision (approved, rejected, auto-allowed, hard-denied) is logged.
- **Default-deny is the safety net.** A tool with no `permit` policy is always blocked — consent can't override a missing permission.

## The Demo

```bash
cd python/strands-cedar-auth
python demos/consent.py
```

The demo runs an interactive Strands agent with Cedar policies. You type natural language requests, and the agent calls tools. When a consent-gated tool fires, you're prompted to approve or deny in real time.

**Three permission levels:**

| Level | Tools | What happens |
|---|---|---|
| Always allowed | `search`, `read_file` | Executes silently |
| Requires consent | `send_email`, `delete_file` | Pauses, shows args, asks `[y/n]` |
| Always denied | `install_package` | Hard block, no prompt |

Requires `pip install cedarpy strands-agents` and a model provider configured (default: Bedrock with Claude). See [Appendix A](#appendix-a-example-session) for a full example session transcript.

---

<details>
<summary><strong>Appendix A: Example Session</strong></summary>

```
======================================================================
Cedar Auth — Interactive Consent Demo
======================================================================

Permission levels:
  ALLOW     — search, read_file (no approval needed)
  CONSENT   — send_email, delete_file (you'll be asked)
  DENY      — install_package (hard block)

You are: alice (role: developer)
Type your request, or 'quit' to exit.
======================================================================

You: Search for strands agents documentation

  ✓ ALLOWED  search({"query": "strands agents documentation"})

Agent: I found several results for "strands agents documentation"...

You: Send an email to bob@acme.com about the quarterly results

  ⚠ CONSENT REQUIRED  send_email
    Args: {"to": "bob@acme.com", "subject": "Quarterly Results", "body": "..."}
    Approve? [y/n]: y
  ✓ APPROVED  Proceeding with send_email

Agent: I've sent the email to bob@acme.com with the quarterly results.

You: Delete the file /tmp/old_data.csv

  ⚠ CONSENT REQUIRED  delete_file
    Args: {"path": "/tmp/old_data.csv"}
    Approve? [y/n]: n
  ✗ REJECTED  User denied consent for delete_file

Agent: I wasn't able to delete that file — you denied the request.

You: Install the requests package

  ✗ DENIED   install_package — not authorized for this role

Agent: I'm not authorized to install packages.

You: quit

======================================================================
Audit Log
======================================================================
  [   ALLOW] search
  [APPROVED] send_email
  [REJECTED] delete_file
  [    DENY] install_package
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
Model calls send_email(to="bob@acme.com", subject="Q1")
    │
    ▼
Plugin: BeforeToolCall hook fires
    │
    ▼
Cedar evaluates (no user_consent in context)
    │
    ├─ Has permit policy with consent condition? ──► Yes
    │                                                 │
    │                                                 ▼
    │                                          Prompt user: "Approve? [y/n]"
    │                                                 │
    │                                          ┌──────┴──────┐
    │                                          │              │
    │                                        y/yes          n/no
    │                                          │              │
    │                                          ▼              ▼
    │                                   Re-evaluate       cancel_tool =
    │                                   with consent      "User denied"
    │                                          │
    │                                          ▼
    │                                   Cedar: ALLOW
    │                                   Tool executes
    │
    ├─ No permit policy at all? ──────► Hard DENY
    │                                   (install_package)
    │
    └─ Permit matches without consent? ► ALLOW
                                         (search, read_file)
```

</details>
