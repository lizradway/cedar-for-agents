"""Native Intervention Integration — real Agent(interventions=[...]) API.

This demo uses the REAL native interventions parameter added to the Strands SDK.
No wrappers, no bridges — handlers are passed directly to Agent and dispatched
through InterventionRegistry at every lifecycle point.

What this proves:
  - Agent(interventions=[...]) works as a first-class SDK parameter
  - InterventionHandler (event-driven: handles() + evaluate()) receives real
    Strands events (BeforeToolCallEvent, AfterToolCallEvent, etc.)
  - Short-circuiting, Guide accumulation, and audit logging work end-to-end
  - Multiple handlers compose naturally with ordering and conflict resolution

Architecture:
    from strands import Agent, InterventionHandler, Proceed, Deny

    agent = Agent(
        tools=[query_database, send_email, search],
        interventions=[cedar, guardrails, steering],
    )
    result = agent("Do something", invocation_state={"user_id": "alice"})

Run:
    cd strands-agents-sdk && pip install -e .
    pip install cedarpy
    python demos/intervention_native.py

Requires a model provider (default: Bedrock with Claude).
"""

from __future__ import annotations

import re
from typing import Any

import cedarpy

from strands import Agent, InterventionHandler, Proceed, Deny, Guide, tool
from strands.hooks.events import BeforeToolCallEvent


# ============================================================================
# 1. Concrete Handlers — implement InterventionHandler directly
# ============================================================================


class CedarAuthHandler(InterventionHandler):
    """Identity-aware authorization via Cedar policies."""

    @property
    def name(self) -> str:
        return "cedar-auth"

    def handles(self) -> set[type]:
        return {BeforeToolCallEvent}

    def __init__(self, policies: str, base_entities: list[dict[str, Any]],
                 principal_type: str = "User") -> None:
        self.policies = policies
        self.base_entities = base_entities
        self.principal_type = principal_type

    @classmethod
    def builder(cls) -> CedarAuthBuilder:
        return CedarAuthBuilder()

    async def evaluate(self, event: BeforeToolCallEvent) -> Proceed | Deny:
        user_id = event.invocation_state.get("user_id")
        roles = event.invocation_state.get("roles", [])
        environment = event.invocation_state.get("environment")
        tool_name = event.tool_use.get("name", "unknown")
        tool_input = event.tool_use.get("input", {})

        if not user_id:
            return Deny(reason="No principal identity provided")

        entities = list(self.base_entities)
        entities.append({
            "uid": {"type": self.principal_type, "id": user_id},
            "parents": [{"type": "Role", "id": r} for r in roles],
            "attrs": {},
        })
        if not any(e["uid"]["type"] == "Tool" and e["uid"]["id"] == tool_name for e in entities):
            entities.append({"uid": {"type": "Tool", "id": tool_name}, "parents": [], "attrs": {}})

        context: dict[str, Any] = {
            k: v for k, v in tool_input.items()
            if isinstance(v, (str, int, float, bool))
        }
        if environment:
            context["environment"] = environment

        principal = f'{self.principal_type}::"{user_id}"'
        result = cedarpy.is_authorized(
            request={
                "principal": principal,
                "action": f'Action::"use_tool::{tool_name}"',
                "resource": f'Tool::"{tool_name}"',
                "context": context,
            },
            policies=self.policies,
            entities=entities,
        )

        if result.allowed:
            return Proceed(reason=f"{principal} authorized")
        return Deny(reason=f"{principal} not authorized for '{tool_name}'")


class ContentGuardrailHandler(InterventionHandler):
    """Pattern-based content guardrails — blocks PII, injection, etc."""

    @property
    def name(self) -> str:
        return "content-guardrail"

    def handles(self) -> set[type]:
        return {BeforeToolCallEvent}

    def __init__(self, rules: list[dict[str, str]]) -> None:
        self.rules = rules

    async def evaluate(self, event: BeforeToolCallEvent) -> Proceed | Deny:
        input_str = str(event.tool_use.get("input", {}))
        for rule in self.rules:
            if re.search(rule["pattern"], input_str):
                return Deny(reason=rule.get("reason", "content policy violation"))
        return Proceed()


class MockLLMSteeringHandler(InterventionHandler):
    """Mock steering — deterministic rules standing in for LLM-based guidance."""

    @property
    def name(self) -> str:
        return "llm-steering"

    def handles(self) -> set[type]:
        return {BeforeToolCallEvent}

    def __init__(self, tool_rules: list[dict[str, Any]] | None = None) -> None:
        self.tool_rules = tool_rules or []

    async def evaluate(self, event: BeforeToolCallEvent) -> Proceed | Guide:
        tool_input = event.tool_use.get("input", {})
        tool_name = event.tool_use.get("name", "")
        for rule in self.tool_rules:
            if rule["match"](tool_name, tool_input):
                return Guide(feedback=rule["feedback"])
        return Proceed()


# ============================================================================
# 2. Builder for CedarAuthHandler
# ============================================================================


class CedarAuthBuilder:
    def __init__(self) -> None:
        self._roles: dict[str, list[str]] = {}
        self._restrictions: list[dict[str, Any]] = []
        self._principal_type: str = "User"

    def role(self, name: str, tools: list[str]) -> CedarAuthBuilder:
        self._roles[name] = tools
        return self

    def restrict(self, tool: str, allowed_values: dict[str, list[str]], for_role: str | None = None) -> CedarAuthBuilder:
        self._restrictions.append({"tool": tool, "allowed_values": allowed_values, "for_role": for_role})
        return self

    def build(self) -> CedarAuthHandler:
        return CedarAuthHandler(
            policies=self._generate_policies(),
            base_entities=self._generate_entities(),
            principal_type=self._principal_type,
        )

    def _generate_policies(self) -> str:
        parts: list[str] = []
        for role, tools in self._roles.items():
            if tools == ["*"]:
                parts.append(f'permit (\n  principal in Role::"{role}",\n  action,\n  resource\n);')
            else:
                actions = ", ".join(f'Action::"use_tool::{t}"' for t in tools)
                parts.append(f'permit (\n  principal in Role::"{role}",\n  action in [{actions}],\n  resource\n);')
        for r in self._restrictions:
            tool_name = r["tool"]
            for_role = r.get("for_role")
            principal_clause = f'principal in Role::"{for_role}"' if for_role else "principal"
            for param, allowed in r["allowed_values"].items():
                conditions = " || ".join(f'context.{param} == "{v}"' for v in allowed)
                parts.append(
                    f'forbid (\n  {principal_clause},\n  action == Action::"use_tool::{tool_name}",\n'
                    f'  resource\n) when {{\n  !({conditions})\n}};'
                )
        return "\n\n".join(parts)

    def _generate_entities(self) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        for role in self._roles:
            entities.append({"uid": {"type": "Role", "id": role}, "parents": [], "attrs": {}})
        all_tools: set[str] = set()
        for tools in self._roles.values():
            if tools != ["*"]:
                all_tools.update(tools)
        for r in self._restrictions:
            all_tools.add(r["tool"])
        for t in all_tools:
            entities.append({"uid": {"type": "Tool", "id": t}, "parents": [], "attrs": {}})
        return entities


# ============================================================================
# 3. Tools
# ============================================================================


@tool
def query_database(database: str, query: str) -> str:
    """Execute a SQL query against a database."""
    return f"[{database}] Results for: {query[:60]}... -> 42 rows returned"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    return f"Email sent to {to}: '{subject}'"


@tool
def search(query: str) -> str:
    """Search internal knowledge base."""
    return f"Found 5 results for '{query}': [Doc A, Doc B, Doc C, ...]"


@tool
def delete_record(table: str, record_id: str) -> str:
    """Delete a record from a database table. Destructive."""
    return f"Deleted record {record_id} from {table}"


# ============================================================================
# 4. Build and run
# ============================================================================


def demo():
    # --- Handlers ---

    cedar = (
        CedarAuthHandler.builder()
        .role("admin", tools=["*"])
        .role("analyst", tools=["search", "query_database", "send_email"])
        .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]}, for_role="analyst")
        .restrict("query_database", allowed_values={"database": ["analytics", "reporting", "secrets"]}, for_role="admin")
        .build()
    )

    guardrails = ContentGuardrailHandler(rules=[
        {"pattern": r"\d{3}-\d{2}-\d{4}", "reason": "PII detected (SSN pattern)"},
        {"pattern": r"(?i)(drop\s+table|';\s*--)", "reason": "Potential SQL injection"},
    ])

    steering = MockLLMSteeringHandler(tool_rules=[
        {
            "match": lambda name, inp: (
                name == "send_email"
                and len(inp.get("body", "")) < 10
            ),
            "feedback": "Email body is very short. Add more context before sending.",
        },
    ])

    # --- The REAL native API — no wrappers ---

    agent = Agent(
        tools=[query_database, send_email, search, delete_record],
        interventions=[cedar, guardrails, steering],
    )

    print("=" * 72)
    print("  Native Intervention Integration — Real Agent(interventions=[...])")
    print()
    print("  API (what users write):")
    print("    agent = Agent(")
    print("        tools=[query_database, send_email, search],")
    print("        interventions=[cedar, guardrails, steering],")
    print("    )")
    print()
    print("  Handlers and their event subscriptions:")
    for h in [cedar, guardrails, steering]:
        events = ", ".join(t.__name__ for t in h.handles())
        print(f"    {h.name:25s} -> {events}")
    print()
    print("  No wrapper. No bridge plugin. Handlers pass directly to Agent.")
    print("  InterventionRegistry wires them into the hook system automatically.")
    print("=" * 72)

    scenarios = [
        {
            "title": "Admin queries secrets DB",
            "user": "alice", "roles": ["admin"],
            "message": "Query the secrets database with: SELECT * FROM api_keys",
            "expected": "ALLOWED - admin authorized by Cedar",
        },
        {
            "title": "Analyst queries secrets DB",
            "user": "bob", "roles": ["analyst"],
            "message": "Query the secrets database with: SELECT * FROM api_keys",
            "expected": "DENIED (Cedar) - analyst restricted to analytics/reporting",
        },
        {
            "title": "Analyst queries analytics",
            "user": "bob", "roles": ["analyst"],
            "message": "Query the analytics database with: SELECT count(*) FROM events",
            "expected": "ALLOWED - analyst can query analytics",
        },
        {
            "title": "Unknown user tries search",
            "user": "rogue", "roles": [],
            "message": "Search for passwords",
            "expected": "DENIED (Cedar) - no roles = default deny",
        },
        {
            "title": "Analyst sends email with PII",
            "user": "bob", "roles": ["analyst"],
            "message": "Send an email to client@acme.com with subject 'Account Info' and body 'Your SSN is 123-45-6789'",
            "expected": "DENIED (Guardrail) - PII detected",
        },
        {
            "title": "Analyst sends proper email",
            "user": "bob", "roles": ["analyst"],
            "message": "Send an email to team@acme.com with subject 'Q3 Report' and body 'Hi team, please find the Q3 analytics report attached. Key highlights: revenue up 15%, churn down 2%. Let me know if you have questions.'",
            "expected": "ALLOWED - all handlers pass",
        },
    ]

    for s in scenarios:
        state = {"user_id": s["user"], "roles": s["roles"]}

        print(f"\n{'~' * 72}")
        print(f"  {s['title']}")
        print(f"  User: {s['user']} | Roles: {s['roles']}")
        print(f"  Prompt: \"{s['message'][:80]}{'...' if len(s['message']) > 80 else ''}\"")
        print(f"  Expected: {s['expected']}")
        print(f"{'~' * 72}")

        try:
            result = agent(s["message"], invocation_state=state)
            print(f"  Agent: {result}")
        except Exception as e:
            print(f"  Error: {e}")

    # --- Unified Audit Log ---
    if agent._intervention_registry:
        print(f"\n{'=' * 72}")
        print("  Unified Audit Log (all event types, all handlers)")
        print("=" * 72)
        for r in agent._intervention_registry.audit_log:
            tool_str = f"  {r.tool_name:18s}" if r.tool_name else f"  {'':18s}"
            print(f"  [{r.handler:25s}] {r.event_type:20s}{tool_str} {r.principal:20s} {r.action_type}: {r.detail}")

    print(f"\n{'=' * 72}")
    print("  This demo uses the REAL Agent(interventions=[...]) parameter.")
    print("  No wrapper classes. No bridge plugins. Native SDK support.")
    print("=" * 72)


if __name__ == "__main__":
    demo()
