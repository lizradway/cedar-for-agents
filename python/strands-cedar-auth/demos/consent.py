"""Demo: Interactive tool consent — allow, deny, or ask the human.

A real Strands agent with Cedar policies that implement the same permission
model as Kiro and Claude Code:

  - Always allowed:      search, read_file — executes silently
  - Requires consent:    send_email, delete_file — pauses and asks you [y/n]
  - Always denied:       install_package — hard block, no prompt

When the model tries to call a consent-gated tool, the Cedar plugin detects
the missing `user_consent` context, prompts you in the terminal, and either
proceeds or blocks based on your response.

Run:
    cd python/strands-cedar-auth
    PYTHONPATH=. python demos/consent.py

Requires: pip install cedarpy strands-agents
    + a model provider configured (default: Bedrock with Claude)
"""

import json
import logging
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cedarpy

from strands import Agent, tool
from strands.hooks.events import BeforeToolCallEvent
from strands.plugins.decorator import hook
from strands.plugins.plugin import Plugin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Cedar policies — three permission levels
# ---------------------------------------------------------------------------

POLICIES = """
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
"""

ENTITIES = [
    {"uid": {"type": "Role", "id": "developer"}, "parents": [], "attrs": {}},
    {"uid": {"type": "User", "id": "alice"}, "parents": [{"type": "Role", "id": "developer"}], "attrs": {}},
]

# Tools that have a consent-gated permit policy
CONSENT_TOOLS = {"send_email", "delete_file"}


# ---------------------------------------------------------------------------
# 2. Plugin with interactive consent
# ---------------------------------------------------------------------------

class ConsentCedarPlugin(Plugin):
    """Cedar auth plugin that prompts for human consent on gated tools."""

    name = "cedar-consent"

    def __init__(self) -> None:
        self._audit: list[dict[str, Any]] = []
        super().__init__()

    def _evaluate(self, principal: str, tool_name: str, tool_input: dict[str, Any], consent: bool = False) -> bool:
        context: dict[str, Any] = {
            k: v for k, v in tool_input.items() if isinstance(v, (str, int, float, bool))
        }
        context["timestamp"] = datetime.now(timezone.utc).isoformat()
        context["hour_utc"] = datetime.now(timezone.utc).hour
        if consent:
            context["user_consent"] = True

        result = cedarpy.is_authorized(
            request={
                "principal": principal,
                "action": f'Action::"use_tool::{tool_name}"',
                "resource": f'Tool::"{tool_name}"',
                "context": context,
            },
            policies=POLICIES,
            entities=ENTITIES,
        )
        return result.allowed

    @hook
    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        tool_name = event.tool_use.get("name", "unknown")
        tool_input = event.tool_use.get("input", {})
        principal = f'User::"{event.invocation_state.get("user_id", "unknown")}"'

        # First pass: evaluate without consent
        allowed = self._evaluate(principal, tool_name, tool_input, consent=False)

        if allowed:
            print(f"\n  \033[32m✓ ALLOWED\033[0m  {tool_name}({json.dumps(tool_input, indent=None)})")
            self._audit.append({"tool": tool_name, "decision": "ALLOW"})
            return

        # Check if this is a consent-gated tool
        if tool_name in CONSENT_TOOLS:
            print(f"\n  \033[33m⚠ CONSENT REQUIRED\033[0m  {tool_name}")
            print(f"    Args: {json.dumps(tool_input, indent=2)}")
            response = input("    Approve? [y/n]: ").strip().lower()

            if response == "y":
                # Re-evaluate with consent
                allowed = self._evaluate(principal, tool_name, tool_input, consent=True)
                if allowed:
                    print(f"  \033[32m✓ APPROVED\033[0m  Proceeding with {tool_name}")
                    self._audit.append({"tool": tool_name, "decision": "APPROVED"})
                    return

            print(f"  \033[31m✗ REJECTED\033[0m  User denied consent for {tool_name}")
            self._audit.append({"tool": tool_name, "decision": "REJECTED"})
            event.cancel_tool = f"User denied consent for tool '{tool_name}'."
            return

        # Hard deny — not a consent tool, just unauthorized
        print(f"\n  \033[31m✗ DENIED\033[0m   {tool_name} — not authorized for this role")
        self._audit.append({"tool": tool_name, "decision": "DENY"})
        event.cancel_tool = (
            f"Access denied: not authorized to use tool '{tool_name}'. "
            "No consent policy exists for this tool."
        )


# ---------------------------------------------------------------------------
# 3. Tools
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
# 4. Agent
# ---------------------------------------------------------------------------

plugin = ConsentCedarPlugin()

agent = Agent(
    plugins=[plugin],
    tools=[search, read_file, send_email, delete_file, install_package],
)


# ---------------------------------------------------------------------------
# 5. Interactive loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Cedar Auth — Interactive Consent Demo")
    print("=" * 70)
    print()
    print("Permission levels:")
    print("  \033[32mALLOW\033[0m     — search, read_file (no approval needed)")
    print("  \033[33mCONSENT\033[0m   — send_email, delete_file (you'll be asked)")
    print("  \033[31mDENY\033[0m      — install_package (hard block)")
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
            print(f"\n\033[1mAgent:\033[0m {result}")
        except Exception as e:
            print(f"\n\033[31mError:\033[0m {e}")

    # Print audit log
    if plugin._audit:
        print(f"\n{'=' * 70}")
        print("Audit Log")
        print(f"{'=' * 70}")
        for entry in plugin._audit:
            decision = entry["decision"]
            color = {"ALLOW": "32", "APPROVED": "32", "REJECTED": "31", "DENY": "31"}.get(decision, "0")
            print(f"  [\033[{color}m{decision:>8}\033[0m] {entry['tool']}")


if __name__ == "__main__":
    main()
