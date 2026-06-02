"""Cedar authorization plugin for Strands Agents SDK.

Three ways to use:

1. Builder:
    plugin = (
        CedarAuthPlugin.builder()
        .role("admin", tools=["*"])
        .role("analyst", tools=["search", "query_database"])
        .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]})
        .rate_limit("send_email", max_per_session=10)
        .time_window(hour_start=9, hour_end=17)
        .deny_tools_in_env("production", ["delete_record", "drop_table"])
        .build()
    )

2. Config file (TOML/JSON):
    plugin = CedarAuthPlugin.from_config("./cedar_auth.toml")

3. Full Cedar (advanced):
    plugin = CedarAuthPlugin(policies="...", entities=[...])

Requires: pip install cedarpy strands-agents
"""

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cedarpy

from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
from strands.plugins.decorator import hook
from strands.plugins.plugin import Plugin

logger = logging.getLogger(__name__)


@dataclass
class AuthzDecision:
    """Record of a single authorization decision."""

    principal: str
    action: str
    resource: str
    allowed: bool
    tool_name: str
    timestamp: str


# ------------------------------------------------------------------
# Builder
# ------------------------------------------------------------------

class CedarAuthBuilder:
    """Fluent builder for common authorization patterns.

    Generates Cedar policies and manages stateful constraints (rate limits).
    """

    def __init__(self) -> None:
        self._roles: dict[str, list[str]] = {}
        self._restrictions: list[dict[str, Any]] = []
        self._rate_limits: dict[str, int] = {}
        self._time_window: tuple[int, int] | None = None
        self._env_denials: list[tuple[str, list[str]]] = []
        self._principal_key: str = "user_id"
        self._principal_type: str = "User"

    def principal(self, key: str, type: str = "User") -> "CedarAuthBuilder":
        """Set which invocation_state key holds the identity and what Cedar type to use.

        Defaults to key="user_id", type="User" — so the plugin reads
        invocation_state["user_id"] and produces User::"alice".

        Examples:
            .principal(key="email")                        # User::"alice@acme.com"
            .principal(key="iam_role", type="IamRole")     # IamRole::"arn:aws:iam::..."
            .principal(key="service_name", type="Service") # Service::"nightly-report"
        """
        self._principal_key = key
        self._principal_type = type
        return self

    def role(self, name: str, tools: list[str]) -> "CedarAuthBuilder":
        """Grant a role access to specific tools. Use ["*"] for all tools.

        Example:
            .role("admin", tools=["*"])
            .role("analyst", tools=["search", "query_database"])
        """
        self._roles[name] = tools
        return self

    def restrict(self, tool: str, allowed_values: dict[str, list[str]], for_role: str | None = None) -> "CedarAuthBuilder":
        """Restrict a tool's arguments to specific allowed values.

        Examples:
            # Global — applies to all roles:
            .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]})

            # Role-scoped — only analysts are restricted:
            .restrict("query_database", allowed_values={"database": ["analytics", "reporting"]}, for_role="analyst")

        Without for_role, the restriction applies to all principals (useful for
        universal constraints like "nobody can query the secrets database").
        With for_role, only that role is restricted — other roles with access
        to the tool are unaffected.
        """
        self._restrictions.append({"tool": tool, "allowed_values": allowed_values, "for_role": for_role})
        return self

    def rate_limit(self, tool: str, max_per_session: int) -> "CedarAuthBuilder":
        """Limit how many times a tool can be called per session.

        Example:
            .rate_limit("send_email", max_per_session=10)

        Note: The plugin tracks call counts in memory. Cedar evaluates the
        threshold; the plugin maintains the counter.
        """
        self._rate_limits[tool] = max_per_session
        return self

    def time_window(self, hour_start: int, hour_end: int) -> "CedarAuthBuilder":
        """Only allow tool calls during a time window (UTC hours).

        Example:
            .time_window(hour_start=9, hour_end=17)  # 9am-5pm UTC
        """
        self._time_window = (hour_start, hour_end)
        return self

    def deny_tools_in_env(self, environment: str, tools: list[str]) -> "CedarAuthBuilder":
        """Deny specific tools when running in a given environment.

        The environment is read from invocation_state["environment"].

        Example:
            .deny_tools_in_env("production", ["delete_record", "drop_table"])
        """
        self._env_denials.append((environment, tools))
        return self

    def build(self) -> "CedarAuthPlugin":
        """Build the plugin with generated policies and state tracking."""
        policies = self._generate_policies()
        base_entities = self._generate_entities()
        principal_key = self._principal_key
        principal_type = self._principal_type

        return CedarAuthPlugin(
            policies=policies,
            entities=CedarAuthPlugin._dynamic_entities(base_entities, principal_key, principal_type),
            principal_resolver=CedarAuthPlugin._make_principal_resolver(principal_key, principal_type),
            _rate_limits=dict(self._rate_limits),
            _time_window=self._time_window,
        )

    def _generate_policies(self) -> str:
        parts: list[str] = []

        # Role → tools permits
        for role, tools in self._roles.items():
            if tools == ["*"]:
                parts.append(
                    f'permit (\n'
                    f'  principal in Role::"{role}",\n'
                    f'  action,\n'
                    f'  resource\n'
                    f');'
                )
            else:
                actions = ", ".join(f'Action::"use_tool::{t}"' for t in tools)
                parts.append(
                    f'permit (\n'
                    f'  principal in Role::"{role}",\n'
                    f'  action in [{actions}],\n'
                    f'  resource\n'
                    f');'
                )

        # Argument restrictions (forbid policies)
        for r in self._restrictions:
            tool = r["tool"]
            for_role = r.get("for_role")
            principal_clause = f'principal in Role::"{for_role}"' if for_role else "principal"
            for param, allowed in r["allowed_values"].items():
                # Cedar doesn't have "in list" for strings in context directly,
                # so we generate an OR of allowed values
                conditions = " || ".join(f'context.{param} == "{v}"' for v in allowed)
                parts.append(
                    f'forbid (\n'
                    f'  {principal_clause},\n'
                    f'  action == Action::"use_tool::{tool}",\n'
                    f'  resource\n'
                    f') when {{\n'
                    f'  !({conditions})\n'
                    f'}};'
                )

        # Rate limits (forbid when count exceeded)
        for tool, max_calls in self._rate_limits.items():
            parts.append(
                f'forbid (\n'
                f'  principal,\n'
                f'  action == Action::"use_tool::{tool}",\n'
                f'  resource\n'
                f') when {{\n'
                f'  context.session_call_count >= {max_calls}\n'
                f'}};'
            )

        # Time window (forbid outside hours)
        if self._time_window:
            h_start, h_end = self._time_window
            parts.append(
                f'forbid (\n'
                f'  principal,\n'
                f'  action,\n'
                f'  resource\n'
                f') when {{\n'
                f'  context.hour_utc < {h_start} || context.hour_utc >= {h_end}\n'
                f'}};'
            )

        # Environment denials
        for env, tools in self._env_denials:
            actions = ", ".join(f'Action::"use_tool::{t}"' for t in tools)
            parts.append(
                f'forbid (\n'
                f'  principal,\n'
                f'  action in [{actions}],\n'
                f'  resource\n'
                f') when {{\n'
                f'  context.environment == "{env}"\n'
                f'}};'
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
        for tool in self._rate_limits:
            all_tools.add(tool)
        for _, tools in self._env_denials:
            all_tools.update(tools)
        for t in all_tools:
            entities.append({"uid": {"type": "Tool", "id": t}, "parents": [], "attrs": {}})
        return entities


# ------------------------------------------------------------------
# Plugin
# ------------------------------------------------------------------

class CedarAuthPlugin(Plugin):
    """Strands plugin that enforces Cedar policies on tool calls."""

    name = "cedar-auth"

    # Resolver types — both principal and resource accept multiple formats for parity:
    #   - None: use defaults
    #   - dict: declarative config ({"key": "...", "type": "..."} for principal,
    #           {"tool": {"key": "...", "type": "..."}} for resources)
    #   - callable: full control
    PrincipalResolver = dict[str, str] | Callable[[dict[str, Any]], str] | None
    ResourceResolver = dict[str, dict[str, str]] | str | Path | Callable[[str, dict[str, Any]], str] | None

    def __init__(
        self,
        policies: str | Path,
        entities: list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]] | str | Path,
        principal_resolver: "CedarAuthPlugin.PrincipalResolver" = None,
        resource_resolver: "CedarAuthPlugin.ResourceResolver" = None,
        _rate_limits: dict[str, int] | None = None,
        _time_window: tuple[int, int] | None = None,
    ) -> None:
        self._policies = self._load_policies(policies)
        self._entities = self._load_entities(entities)
        self._principal_resolver = self._load_principal_resolver(principal_resolver)
        self._resource_map = self._load_resource_resolver(resource_resolver)
        self._audit_log: list[AuthzDecision] = []
        # Stateful tracking for rate limits
        self._rate_limits = _rate_limits or {}
        self._call_counts: dict[str, defaultdict[str, int]] = {}  # session_id -> {tool -> count}
        self._time_window = _time_window
        super().__init__()

    @classmethod
    def builder(cls) -> CedarAuthBuilder:
        """Start building a plugin with common authorization patterns."""
        return CedarAuthBuilder()

    @classmethod
    def from_config(cls, config_path: str | Path) -> "CedarAuthPlugin":
        """Build a plugin entirely from a TOML or JSON config file.

        Example config (TOML):
            [principal]
            key = "email"
            type = "User"

            [roles]
            admin = ["*"]
            analyst = ["search", "query_database"]

            [resources.delete_record]
            key = "record_id"
            type = "Record"

            [restrictions.query_database]
            for_role = "analyst"
            allowed_values = {database = ["analytics", "reporting"]}

            [rate_limits]
            send_email = 3

            [time_window]
            start = 9
            end = 17

            [deny_in_env.production]
            tools = ["delete_record", "drop_table"]
        """
        path = Path(config_path)
        text = path.read_text()
        if path.suffix == ".toml":
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore[no-redef]
            config = tomllib.loads(text)
        else:
            config = json.loads(text)

        b = cls.builder()

        # Principal
        principal = config.get("principal", {})
        if principal:
            b.principal(key=principal.get("key", "user_id"), type=principal.get("type", "User"))

        # Roles
        for role, tools in config.get("roles", {}).items():
            b.role(role, tools)

        # Restrictions
        for tool, restriction in config.get("restrictions", {}).items():
            for_role = restriction.get("for_role")
            allowed_values = {k: v for k, v in restriction.items() if k not in ("for_role",)}
            if allowed_values:
                b.restrict(tool, allowed_values=allowed_values, for_role=for_role)

        # Rate limits
        for tool, limit in config.get("rate_limits", {}).items():
            b.rate_limit(tool, max_per_session=limit)

        # Time window
        tw = config.get("time_window")
        if tw:
            b.time_window(hour_start=tw["start"], hour_end=tw["end"])

        # Environment denials
        for env, denial in config.get("deny_in_env", {}).items():
            b.deny_tools_in_env(env, denial["tools"])

        plugin = b.build()

        # Resource resolver (a Full Cedar feature, applied after build)
        resources = config.get("resources")
        if resources:
            plugin._resource_map = resources

        return plugin

    # ------------------------------------------------------------------
    # Entity / principal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _dynamic_entities(
        base_entities: list[dict[str, Any]],
        principal_key: str = "user_id",
        principal_type: str = "User",
    ) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
        def resolve(invocation_state: dict[str, Any]) -> list[dict[str, Any]]:
            entities = list(base_entities)
            identity = invocation_state.get(principal_key)
            roles = invocation_state.get("roles", [])
            if identity:
                entities.append({
                    "uid": {"type": principal_type, "id": identity},
                    "parents": [{"type": "Role", "id": r} for r in roles],
                    "attrs": {},
                })
            return entities
        return resolve

    @staticmethod
    def _make_principal_resolver(
        principal_key: str, principal_type: str,
    ) -> Callable[[dict[str, Any]], str]:
        def resolve(invocation_state: dict[str, Any]) -> str:
            identity = invocation_state.get(principal_key)
            if not identity:
                raise KeyError(f"No '{principal_key}' in invocation_state.")
            return f'{principal_type}::"{identity}"'
        return resolve

    @staticmethod
    def _load_policies(policies: str | Path) -> str:
        if isinstance(policies, Path) or (isinstance(policies, str) and Path(policies).suffix == ".cedar"):
            return Path(policies).read_text()
        return policies

    @staticmethod
    def _load_entities(
        entities: list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]] | str | Path,
    ) -> list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]]:
        if isinstance(entities, (str, Path)):
            return json.loads(Path(entities).read_text())
        return entities

    @staticmethod
    def _load_principal_resolver(
        resolver: "CedarAuthPlugin.PrincipalResolver",
    ) -> Callable[[dict[str, Any]], str]:
        if resolver is None:
            return CedarAuthPlugin._default_principal_resolver
        if callable(resolver):
            return resolver
        # Dict: {"key": "iam_role", "type": "IamRole"}
        return CedarAuthPlugin._make_principal_resolver(
            resolver.get("key", "user_id"),
            resolver.get("type", "User"),
        )

    @staticmethod
    def _default_principal_resolver(invocation_state: dict[str, Any]) -> str:
        user_id = invocation_state.get("user_id")
        if not user_id:
            raise KeyError("No 'user_id' in invocation_state.")
        return f'User::"{user_id}"'

    def _resolve_entities(self, invocation_state: dict[str, Any]) -> list[dict[str, Any]]:
        if callable(self._entities):
            return self._entities(invocation_state)
        return self._entities

    @staticmethod
    def _load_resource_resolver(
        resolver: "CedarAuthPlugin.ResourceResolver",
    ) -> dict[str, dict[str, str]] | Callable[[str, dict[str, Any]], str] | None:
        if resolver is None or callable(resolver):
            return resolver
        if isinstance(resolver, dict):
            return resolver
        # File path — load JSON or TOML
        path = Path(resolver)
        text = path.read_text()
        if path.suffix == ".toml":
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(text)
            return data.get("resources", data)
        # Default to JSON
        return json.loads(text)

    def _resolve_resource(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if self._resource_map is None:
            return f'Tool::"{tool_name}"'
        if callable(self._resource_map):
            return self._resource_map(tool_name, tool_input)
        # Dict-based: {"delete_record": {"key": "record_id", "type": "Record"}}
        mapping = self._resource_map.get(tool_name)
        if mapping is None:
            return f'Tool::"{tool_name}"'
        key = mapping["key"]
        resource_type = mapping["type"]
        value = tool_input.get(key, tool_name)
        return f'{resource_type}::"{value}"'

    def _get_session_id(self, invocation_state: dict[str, Any]) -> str:
        return invocation_state.get("session_id", invocation_state.get("user_id", "_default"))

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------
    @hook
    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Evaluate Cedar policy before each tool call."""
        tool_name = event.tool_use.get("name", "unknown")
        tool_input = event.tool_use.get("input", {})

        try:
            principal = self._principal_resolver(event.invocation_state)
        except (KeyError, TypeError) as e:
            logger.error("cedar-auth: failed to resolve principal: %s", e)
            event.cancel_tool = "Authorization failed: no user identity provided."
            return

        action = f'Action::"use_tool::{tool_name}"'
        resource = self._resolve_resource(tool_name, tool_input)
        entities = self._resolve_entities(event.invocation_state)

        # Build context: tool inputs + enrichments for helpers
        now = datetime.now(timezone.utc)
        context: dict[str, Any] = {
            k: v for k, v in tool_input.items() if isinstance(v, (str, int, float, bool))
        }
        context["timestamp"] = now.isoformat()
        context["hour_utc"] = now.hour

        # Enrich with environment if present
        env = event.invocation_state.get("environment")
        if env:
            context["environment"] = env

        # Enrich with rate limit counter
        if tool_name in self._rate_limits:
            session_id = self._get_session_id(event.invocation_state)
            if session_id not in self._call_counts:
                self._call_counts[session_id] = defaultdict(int)
            context["session_call_count"] = self._call_counts[session_id][tool_name]

        # Evaluate
        result = cedarpy.is_authorized(
            request={"principal": principal, "action": action, "resource": resource, "context": context},
            policies=self._policies,
            entities=entities,
        )

        self._audit_log.append(AuthzDecision(
            principal=principal, action=action, resource=resource,
            allowed=result.allowed, tool_name=tool_name, timestamp=context["timestamp"],
        ))

        if not result.allowed:
            logger.warning("cedar-auth: DENIED %s -> %s", principal, tool_name)
            event.cancel_tool = (
                f"Access denied: {principal} is not authorized to use tool '{tool_name}'. "
                "Contact your administrator to request access."
            )
        else:
            logger.info("cedar-auth: ALLOWED %s -> %s", principal, tool_name)
            # Increment call count on success
            if tool_name in self._rate_limits:
                session_id = self._get_session_id(event.invocation_state)
                self._call_counts[session_id][tool_name] += 1

    @hook
    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Log tool call outcome."""
        tool_name = event.tool_use.get("name", "unknown")
        logger.info(
            "cedar-auth: tool=%s cancelled=%s error=%s",
            tool_name, event.cancel_message is not None, event.exception is not None,
        )

    @property
    def audit_log(self) -> list[AuthzDecision]:
        return list(self._audit_log)
