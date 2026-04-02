"""Demo: Interactive tool consent via Cedar + Strands Interrupt.

Uses the existing CedarAuthHandler (the same intervention handler from the
native intervention demo) with `.consent()` on its builder. No new handler
class — consent is built into the Cedar handler itself.

When Cedar denies a consent-gated tool (because `user_consent` is missing
from context), the handler returns `Interrupt`. The InterventionRegistry
maps this to `event.interrupt()` which pauses the agent and returns
control to the caller. The caller provides a y/n response, and the agent
resumes — the handler runs again, sees the response, and re-evaluates
Cedar with `user_consent = true`.

  - Always allowed:      search, read_file — Proceed
  - Requires consent:    send_email, delete_file — Interrupt → y/n → Proceed/Deny
  - Always denied:       install_package — Deny

Run:
    cd python/strands-cedar-auth
    python demos/consent.py

Requires: pip install cedarpy strands-agents (interventions branch)
    + a model provider configured (default: Bedrock with Claude)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strands import Agent, tool

# Import the existing CedarAuthHandler — same one used in intervention demos
from demos.intervention.native import CedarAuthHandler

# ---------------------------------------------------------------------------
# 1. Build handler with consent support via the existing builder
# ---------------------------------------------------------------------------

cedar = (
    CedarAuthHandler.builder()
    .role("developer", tools=["search", "read_file"])
    .consent(tools=["send_email", "delete_file"])
    # install_package has no permit and no consent — always Deny
    .build()
)

# ---------------------------------------------------------------------------
# 2. Tools
# ---------------------------------------------------------------------------

@tool
def search(query: str) -> str:
    """Search for information on a topic."""
    return f"Found 5 results for '{query}': [Strands docs, Cedar guide, Plugin API, ...]"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file."""
    return f"Contents of {path}:\n# Main module\ndef main():\n    print('hello')"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to someone."""
    return f"Email sent to {to} with subject '{subject}'"


@tool
def delete_file(path: str) -> str:
    """Delete a file from the filesystem."""
    return f"Deleted {path}"


@tool
def install_package(package: str) -> str:
    """Install a Python package via pip."""
    return f"Installed {package}"


# ---------------------------------------------------------------------------
# 3. Agent — Cedar handler passed as an intervention
# ---------------------------------------------------------------------------

agent = Agent(
    tools=[search, read_file, send_email, delete_file, install_package],
    interventions=[cedar],
)


# ---------------------------------------------------------------------------
# 4. Interactive loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Cedar Auth — Consent via Strands Interrupt")
    print("=" * 70)
    print()
    print("Permission levels:")
    print("  \033[32mALLOW\033[0m     — search, read_file (Proceed)")
    print("  \033[33mCONSENT\033[0m   — send_email, delete_file (Interrupt → y/n)")
    print("  \033[31mDENY\033[0m      — install_package (Deny)")
    print()
    print("You are: alice (role: developer)")
    print("Type your request, or 'quit' to exit.")
    print("=" * 70)

    state = {"user_id": "alice", "roles": ["developer"]}

    while True:
        print()
        try:
            user_input = input("\033[1mYou:\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break

        try:
            result = agent(user_input, invocation_state=state)

            # Handle interrupts — the handler paused for consent
            while result.stop_reason == "interrupt":
                responses = []
                for interrupt in result.interrupts:
                    print(f"\n  \033[33m⚠ CONSENT REQUIRED\033[0m")
                    print(f"    {interrupt.reason}")
                    try:
                        answer = input("    Approve? [y/n]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        answer = "n"
                    responses.append({
                        "interruptResponse": {
                            "interruptId": interrupt.id,
                            "response": answer,
                        }
                    })
                result = agent(responses, invocation_state=state)

            print(f"\n\033[1mAgent:\033[0m {result}")
        except Exception as e:
            print(f"\n\033[31mError:\033[0m {e}")

    # Print audit log
    if agent._intervention_registry:
        print(f"\n{'=' * 70}")
        print("Audit Log")
        print("=" * 70)
        for r in agent._intervention_registry.audit_log:
            color = {"PROCEED": "32", "INTERRUPT": "33", "DENY": "31"}.get(r.action_type, "0")
            print(f"  [\033[{color}m{r.action_type:>9}\033[0m] {r.tool_name}")


if __name__ == "__main__":
    main()
