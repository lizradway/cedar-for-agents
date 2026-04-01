"""Intervention Pipeline — a real Strands agent with Cedar + Guardrails + real LLM Steering.

"Intervention" is the primitive. Cedar auth, content guardrails, and Strands' real
LLMSteeringHandler all implement InterventionHandler — the shared interface. An
InterventionPipeline composes them into a single Strands Plugin.

Architecture:
    InterventionHandler  (abstract — the shared primitive)
      ├── CedarAuthHandler           (sub-ms, Cedar policy evaluation)
      ├── ContentGuardrailHandler     (sub-ms, regex pattern matching)
      └── StrandsSteeringAdapter      (wraps real Strands LLMSteeringHandler)

    InterventionPipeline(Plugin)  (composes handlers → single Strands Plugin)
      └── Agent(plugins=[pipeline], tools=[...])

    This is an alternative approach we explored. Today, each concern is a
    separate plugin with no shared interface. If Strands made interventions
    first-class, this simplifies to:
      Agent(tools=[...], interventions=[cedar, guardrails, steering])

Run:
    pip install cedarpy strands-agents
    python demos/intervention_pipeline.py

Requires a model provider (default: Bedrock with Claude).
See docs/INTERVENTION_EXPLORATION.md for the full design rationale.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import cedarpy

from strands import Agent, tool
from strands.hooks.events import BeforeToolCallEvent
from strands.plugins.decorator import hook
from strands.plugins.plugin import Plugin
from strands.vended_plugins.steering import LLMSteeringHandler, Proceed as StrandsProceed, Guide as StrandsGuide, Interrupt as StrandsInterrupt

logger = logging.getLogger(__name__)


# ============================================================================
# 1. Intervention Actions — the shared vocabulary
# ============================================================================

@dataclass
class Proceed:
    """Allow the action."""
    reason: str = ""

@dataclass
class Deny:
    """Hard block. No retry — the agent should explain the denial, not work around it."""
    reason: str = ""

@dataclass
class Guide:
    """Soft redirect. Cancel and provide feedback so the agent retries differently."""
    feedback: str = ""

@dataclass
class Interrupt:
    """Pause for human input."""
    prompt: str = ""

InterventionAction = Proceed | Deny | Guide | Interrupt


# ============================================================================
# 2. Intervention Context — what every handler receives
# ============================================================================

@dataclass
class InterventionContext:
    """Shared context passed to every handler on every evaluation."""
    principal_id: str | None = None
    principal_type: str = "User"
    roles: list[str] = field(default_factory=list)
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    environment: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ============================================================================
# 3. Audit Record
# ============================================================================

@dataclass
class InterventionRecord:
    handler: str
    tool_name: str
    principal: str
    action_type: str
    detail: str
    timestamp: str


# ============================================================================
# 4. InterventionHandler — the primitive interface
# ============================================================================

class InterventionHandler(ABC):
    """The primitive that Cedar, guardrails, and steering all implement.

    Each handler evaluates the same InterventionContext and returns the same
    InterventionAction types. The evaluation engine is a black box — Cedar
    policies, regex patterns, LLM calls, or anything else.

    evaluate_tool_call is async to support handlers that need I/O (like LLM steering).
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def evaluate_tool_call(self, ctx: InterventionContext) -> InterventionAction:
        """Evaluate before a tool call. Must return an InterventionAction."""
        ...

    def init_pipeline(self, agent: Agent) -> None:
        """Optional: called when the pipeline is attached to an agent."""
        pass


# ============================================================================
# 5. Concrete Handlers — each implements InterventionHandler
# ============================================================================

# --- Cedar Auth ---

class CedarAuthHandler(InterventionHandler):
    """Identity-aware authorization via Cedar policy evaluation.

    Unique value: the identity x action x arguments matrix, plus formal verification.
    """

    @property
    def name(self) -> str:
        return "cedar-auth"

    def __init__(self, policies: str, base_entities: list[dict[str, Any]],
                 principal_type: str = "User") -> None:
        self.policies = policies
        self.base_entities = base_entities
        self.principal_type = principal_type

    @classmethod
    def builder(cls) -> CedarAuthBuilder:
        return CedarAuthBuilder()

    async def evaluate_tool_call(self, ctx: InterventionContext) -> InterventionAction:
        if not ctx.principal_id:
            return Deny(reason="No principal identity provided")

        entities = list(self.base_entities)
        entities.append({
            "uid": {"type": ctx.principal_type, "id": ctx.principal_id},
            "parents": [{"type": "Role", "id": r} for r in ctx.roles],
            "attrs": {},
        })
        if not any(e["uid"]["type"] == "Tool" and e["uid"]["id"] == ctx.tool_name for e in entities):
            entities.append({"uid": {"type": "Tool", "id": ctx.tool_name}, "parents": [], "attrs": {}})

        context: dict[str, Any] = {
            k: v for k, v in ctx.tool_input.items()
            if isinstance(v, (str, int, float, bool))
        }
        if ctx.environment:
            context["environment"] = ctx.environment

        principal = f'{ctx.principal_type}::"{ctx.principal_id}"'
        result = cedarpy.is_authorized(
            request={
                "principal": principal,
                "action": f'Action::"use_tool::{ctx.tool_name}"',
                "resource": f'Tool::"{ctx.tool_name}"',
                "context": context,
            },
            policies=self.policies,
            entities=entities,
        )

        if result.allowed:
            return Proceed(reason=f"{principal} authorized for {ctx.tool_name}")
        return Deny(reason=f"{principal} is not authorized to use '{ctx.tool_name}'")


# --- Content Guardrails ---

class ContentGuardrailHandler(InterventionHandler):
    """Fast pattern-matching guardrails (PII, injection, etc).

    Content-aware but identity-unaware — checks what's being said, not who's saying it.
    """

    @property
    def name(self) -> str:
        return "content-guardrail"

    def __init__(self, rules: list[dict[str, str]]) -> None:
        self.rules = rules

    async def evaluate_tool_call(self, ctx: InterventionContext) -> InterventionAction:
        input_str = str(ctx.tool_input)
        for rule in self.rules:
            if re.search(rule["pattern"], input_str):
                return Deny(reason=rule.get("reason", "content policy violation"))
        return Proceed()


# --- Strands LLM Steering Adapter ---

class StrandsSteeringAdapter(InterventionHandler):
    """Wraps the real Strands LLMSteeringHandler as an InterventionHandler.

    This adapter bridges two worlds:
      - Strands' SteeringHandler interface (async, takes Agent + ToolUse)
      - InterventionHandler interface (async, takes InterventionContext)

    The LLMSteeringHandler internally creates its own Agent to call the LLM,
    so it doesn't need the main agent reference for model access. We still
    pass it through for any context providers that might need it.
    """

    @property
    def name(self) -> str:
        return "llm-steering"

    def __init__(self, system_prompt: str, **kwargs: Any) -> None:
        self._handler = LLMSteeringHandler(system_prompt=system_prompt, **kwargs)
        self._agent: Agent | None = None

    def init_pipeline(self, agent: Agent) -> None:
        self._agent = agent

    async def evaluate_tool_call(self, ctx: InterventionContext) -> InterventionAction:
        tool_use = {"name": ctx.tool_name, "input": ctx.tool_input}

        action = await self._handler.steer_before_tool(
            agent=self._agent,
            tool_use=tool_use,
        )

        # Map Strands steering actions → intervention actions
        if isinstance(action, StrandsProceed):
            return Proceed(reason=action.reason)
        elif isinstance(action, StrandsGuide):
            return Guide(feedback=action.reason)
        elif isinstance(action, StrandsInterrupt):
            return Interrupt(prompt=action.reason)

        return Proceed()


# ============================================================================
# 6. InterventionPipeline — composes handlers into a Strands Plugin
# ============================================================================

class InterventionPipeline(Plugin):
    """Composes multiple InterventionHandlers into a single Strands Plugin.

    This is the adapter between the framework-agnostic InterventionHandler
    interface and the Strands-specific Plugin/hook system.

    Conflict resolution:
      - Any Deny     → final Deny (short-circuits, skips expensive handlers)
      - Any Interrupt → Interrupt (if no deny)
      - Any Guide    → Guide with accumulated feedback
      - Otherwise    → Proceed
    """

    name = "intervention-pipeline"

    def __init__(
        self,
        handlers: list[InterventionHandler],
        principal_key: str = "user_id",
        principal_type: str = "User",
    ) -> None:
        super().__init__()
        self.handlers = handlers
        self.principal_key = principal_key
        self.principal_type = principal_type
        self.audit_log: list[InterventionRecord] = []

    def init_agent(self, agent: Agent) -> None:
        """Wire up handlers that need an agent reference (e.g., LLM steering)."""
        for handler in self.handlers:
            handler.init_pipeline(agent)

    def _build_context(self, event: BeforeToolCallEvent) -> InterventionContext:
        """Extract InterventionContext from Strands event."""
        return InterventionContext(
            principal_id=event.invocation_state.get(self.principal_key),
            principal_type=self.principal_type,
            roles=event.invocation_state.get("roles", []),
            tool_name=event.tool_use.get("name", "unknown"),
            tool_input=event.tool_use.get("input", {}),
            environment=event.invocation_state.get("environment"),
        )

    async def evaluate(self, ctx: InterventionContext) -> InterventionAction:
        """Run all handlers in order with conflict resolution."""
        guides: list[str] = []

        for handler in self.handlers:
            action = await handler.evaluate_tool_call(ctx)
            principal_str = f'{ctx.principal_type}::"{ctx.principal_id}"' if ctx.principal_id else "*"

            if isinstance(action, Proceed):
                self.audit_log.append(InterventionRecord(
                    handler=handler.name, tool_name=ctx.tool_name, principal=principal_str,
                    action_type="proceed", detail=action.reason, timestamp=ctx.timestamp,
                ))
            elif isinstance(action, Deny):
                self.audit_log.append(InterventionRecord(
                    handler=handler.name, tool_name=ctx.tool_name, principal=principal_str,
                    action_type="deny", detail=action.reason, timestamp=ctx.timestamp,
                ))
                return action  # Short-circuit: skip remaining handlers
            elif isinstance(action, Guide):
                self.audit_log.append(InterventionRecord(
                    handler=handler.name, tool_name=ctx.tool_name, principal=principal_str,
                    action_type="guide", detail=action.feedback, timestamp=ctx.timestamp,
                ))
                guides.append(f"[{handler.name}] {action.feedback}")
            elif isinstance(action, Interrupt):
                self.audit_log.append(InterventionRecord(
                    handler=handler.name, tool_name=ctx.tool_name, principal=principal_str,
                    action_type="interrupt", detail=action.prompt, timestamp=ctx.timestamp,
                ))
                return action

        if guides:
            return Guide(feedback="\n".join(guides))
        return Proceed()

    @hook
    async def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Strands hook — adapts the async pipeline to the Plugin interface."""
        ctx = self._build_context(event)
        action = await self.evaluate(ctx)

        if isinstance(action, Deny):
            event.cancel_tool = f"Access denied: {action.reason}"
        elif isinstance(action, Guide):
            event.cancel_tool = f"Tool call cancelled. {action.feedback} You MUST follow this guidance immediately."
        elif isinstance(action, Interrupt):
            event.cancel_tool = f"Requires approval: {action.prompt}"
        # Proceed: do nothing, tool executes


# ============================================================================
# 7. Builder for CedarAuthHandler
# ============================================================================

class CedarAuthBuilder:
    def __init__(self) -> None:
        self._roles: dict[str, list[str]] = {}
        self._restrictions: list[dict[str, Any]] = []
        self._principal_type: str = "User"

    def principal_type(self, type: str) -> CedarAuthBuilder:
        self._principal_type = type
        return self

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
            tool = r["tool"]
            for_role = r.get("for_role")
            principal_clause = f'principal in Role::"{for_role}"' if for_role else "principal"
            for param, allowed in r["allowed_values"].items():
                conditions = " || ".join(f'context.{param} == "{v}"' for v in allowed)
                parts.append(
                    f'forbid (\n  {principal_clause},\n  action == Action::"use_tool::{tool}",\n'
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
# 8. Tools
# ============================================================================

@tool
def query_database(database: str, query: str) -> str:
    """Execute a SQL query against a database."""
    return f"[{database}] Results for: {query[:60]}... → 42 rows returned"


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
# 9. Build the pipeline and agent
# ============================================================================

# --- Handlers (each implements InterventionHandler — the shared primitive) ---

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

# Real Strands LLMSteeringHandler, wrapped in the InterventionHandler interface
steering = StrandsSteeringAdapter(
    system_prompt="""You are evaluating whether agent tool calls are appropriate.

    Rules:
    - Emails should have substantive bodies (at least a greeting and clear purpose)
    - Database queries should have a clear business justification
    - Search queries should be specific, not overly broad

    If a tool call seems low-quality or off-task, return "guide" with feedback.
    Otherwise return "proceed".
    """,
)

# --- Pipeline (composes handlers → single Plugin) ---
# Cheapest/most-deterministic first. LLM steering runs last — and only
# if Cedar and guardrails both said Proceed.

pipeline = InterventionPipeline(
    handlers=[cedar, guardrails, steering],
)

# --- Real Strands Agent ---

agent = Agent(
    plugins=[pipeline],
    tools=[query_database, send_email, search, delete_record],
)


# ============================================================================
# 10. Run scenarios
# ============================================================================

def demo():
    print("=" * 70)
    print("  Intervention Pipeline — Real Strands Agent")
    print()
    print("  InterventionHandler implementations:")
    print("    1. CedarAuthHandler            (sub-ms, Cedar policies)")
    print("    2. ContentGuardrailHandler      (sub-ms, regex patterns)")
    print("    3. StrandsSteeringAdapter       (real LLMSteeringHandler)")
    print()
    print("  All three implement InterventionHandler.")
    print("  InterventionPipeline composes them into one Strands Plugin.")
    print("=" * 70)

    scenarios = [
        # Cedar: identity x action x arguments
        {
            "title": "Admin queries secrets DB",
            "user": "alice", "roles": ["admin"],
            "message": "Query the secrets database with: SELECT * FROM api_keys",
            "expected": "ALLOWED — admin can query secrets",
        },
        {
            "title": "Analyst queries secrets DB",
            "user": "bob", "roles": ["analyst"],
            "message": "Query the secrets database with: SELECT * FROM api_keys",
            "expected": "DENIED (Cedar) — analyst restricted to analytics/reporting",
        },
        {
            "title": "Analyst queries analytics",
            "user": "bob", "roles": ["analyst"],
            "message": "Query the analytics database with: SELECT count(*) FROM events",
            "expected": "ALLOWED — analyst can query analytics",
        },
        {
            "title": "Unknown user tries search",
            "user": "rogue", "roles": [],
            "message": "Search for passwords",
            "expected": "DENIED (Cedar) — no roles = default deny",
        },

        # Guardrails: content-aware
        {
            "title": "Analyst sends email with PII",
            "user": "bob", "roles": ["analyst"],
            "message": "Send an email to client@acme.com with subject 'Account Info' and body 'Your SSN is 123-45-6789'",
            "expected": "DENIED (Guardrail) — PII detected, Cedar allowed it",
        },
        {
            "title": "Analyst query with SQL injection",
            "user": "bob", "roles": ["analyst"],
            "message": "Query the analytics database with: SELECT * FROM events'; -- DROP TABLE events",
            "expected": "DENIED (Guardrail) — injection detected",
        },

        # Steering: LLM-based quality guidance (real LLM call!)
        {
            "title": "Analyst sends a proper email",
            "user": "bob", "roles": ["analyst"],
            "message": "Send an email to team@acme.com with subject 'Q3 Report' and body 'Hi team, please find the Q3 analytics report attached. Key highlights: revenue up 15%, churn down 2%. Let me know if you have questions.'",
            "expected": "ALLOWED — all handlers happy (Cedar + guardrail + LLM steering)",
        },
    ]

    for s in scenarios:
        state = {"user_id": s["user"], "roles": s["roles"]}

        print(f"\n{'─' * 70}")
        print(f"  {s['title']}")
        print(f"  User: {s['user']} | Roles: {s['roles']}")
        print(f"  Prompt: \"{s['message']}\"")
        print(f"  Expected: {s['expected']}")
        print(f"{'─' * 70}")

        try:
            result = agent(s["message"], invocation_state=state)
            print(f"  Agent: {result}")
        except Exception as e:
            print(f"  Error: {e}")

    # Unified audit log
    print(f"\n{'=' * 70}")
    print("  Unified Audit Log (Cedar + Guardrails + LLM Steering)")
    print("=" * 70)
    for record in pipeline.audit_log:
        print(f"  [{record.handler:22s}] {record.tool_name:18s} {record.principal:20s} {record.action_type.upper()}: {record.detail}")

    # Note about proposed first-class API
    print(f"\n{'=' * 70}")
    print("  This demo uses InterventionPipeline as a Plugin — an alternative")
    print("  approach we explored. If Strands made interventions first-class:")
    print()
    print("    agent = Agent(")
    print("        tools=[query_database, send_email],")
    print("        interventions=[cedar, guardrails, steering],")
    print("    )")
    print()
    print("  See docs/INTERVENTION_EXPLORATION.md for the full proposal.")
    print("=" * 70)


if __name__ == "__main__":
    demo()
