from __future__ import annotations

import warnings

from .types import (
    BuildResult,
    CedarAgentConfig,
    McpToolDefinition,
    PrincipalConfig,
    SchemaConfig,
)
from .policy_generators import generate_policies
from .entities import generate_entities
from .schema import generate_schema


def from_config(config: CedarAgentConfig) -> BuildResult:
    policies = generate_policies(config)
    entities = generate_entities(config)
    schema = generate_schema(config) if config.tools else None

    return BuildResult(
        policies=policies,
        entities=entities,
        schema=schema,
        _config=config,
    )


class CedarAgentPolicyBuilder:
    def __init__(self, schema_config: SchemaConfig | None = None):
        self._config = CedarAgentConfig(
            principal=schema_config.principal or PrincipalConfig(key="user_id") if schema_config else PrincipalConfig(key="user_id"),
            resource=schema_config.resource if schema_config else None,
            tools=schema_config.tools if schema_config else None,
            namespace=schema_config.namespace if schema_config else None,
        )

    def role(self, name: str, tools: list[str]) -> CedarAgentPolicyBuilder:
        if self._config.roles is None:
            self._config.roles = {}
        self._config.roles[name] = tools
        return self

    def restrict(self, tool: str, config: dict) -> CedarAgentPolicyBuilder:
        if self._config.restrictions is None:
            self._config.restrictions = {}
        self._config.restrictions[tool] = config.get("allowedValues", config)
        return self

    def rate_limit(self, tool: str, max_per_session: int) -> CedarAgentPolicyBuilder:
        if self._config.rate_limits is None:
            self._config.rate_limits = {}
        self._config.rate_limits[tool] = max_per_session
        return self

    def time_window(self, *, hour_start: int, hour_end: int) -> CedarAgentPolicyBuilder:
        self._config.time_window = {"hourStart": hour_start, "hourEnd": hour_end}
        return self

    def deny_tools_in_env(self, env: str, tools: list[str] | None = None) -> CedarAgentPolicyBuilder:
        if self._config.deny_in_env is None:
            self._config.deny_in_env = {}
        self._config.deny_in_env[env] = tools if tools is not None else ["*"]
        return self

    def consent(self, tools: list[str], for_role: str | None = None) -> CedarAgentPolicyBuilder:
        if self._config.consent is None:
            self._config.consent = {}
        for tool in tools:
            if for_role:
                current = self._config.consent.get(tool)
                if not current or current is True:
                    self._config.consent[tool] = [for_role]
                else:
                    current.append(for_role)
            else:
                self._config.consent[tool] = True
        return self

    def resource(self, config: dict[str, str]) -> CedarAgentPolicyBuilder:
        self._config.resource = config
        return self

    def tools(self, definitions: list[McpToolDefinition]) -> CedarAgentPolicyBuilder:
        self._config.tools = definitions
        return self

    def namespace(self, ns: str) -> CedarAgentPolicyBuilder:
        self._config.namespace = ns
        return self

    def build(self) -> BuildResult:
        self._warn_unknown_tools()
        return from_config(self._config)

    def build_and_validate(self) -> BuildResult:
        result = self.build()
        validation = result.validate()
        if not validation.valid:
            messages = "; ".join(e.message for e in validation.errors)
            raise RuntimeError(f"Cedar policy validation failed: {messages}")
        return result

    def _warn_unknown_tools(self) -> None:
        if not self._config.roles:
            return
        declared_tools = set()
        for tools in self._config.roles.values():
            for t in tools:
                if t != "*":
                    declared_tools.add(t)
        if not declared_tools:
            return

        referenced: set[str] = set()
        if self._config.restrictions:
            referenced.update(self._config.restrictions.keys())
        if self._config.rate_limits:
            referenced.update(self._config.rate_limits.keys())
        if self._config.deny_in_env:
            for env_tools in self._config.deny_in_env.values():
                for tool in env_tools:
                    if tool != "*":
                        referenced.add(tool)
        if self._config.consent:
            referenced.update(self._config.consent.keys())

        for tool in referenced:
            if tool not in declared_tools:
                warnings.warn(
                    f'[cedar-agent-policy-builder] Warning: "{tool}" is referenced in restrict/deny_tools_in_env/consent but not declared in any role',
                    stacklevel=2,
                )
