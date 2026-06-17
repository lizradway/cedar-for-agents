from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PrincipalConfig:
    key: str
    type: str = "User"


@dataclass
class McpToolDefinition:
    name: str
    input_schema: dict[str, Any]
    description: str = ""
    output_schema: dict[str, Any] | None = None


@dataclass
class SchemaConfig:
    principal: PrincipalConfig | None = None
    resource: dict[str, str] | None = None
    tools: list[McpToolDefinition] | None = None
    namespace: str | None = None


@dataclass
class CedarAgentConfig:
    principal: PrincipalConfig
    roles: dict[str, list[str]] | None = None
    restrictions: dict[str, dict[str, list[Any]]] | None = None
    rate_limits: dict[str, int] | None = None
    time_window: dict[str, int] | None = None
    deny_in_env: dict[str, list[str]] | None = None
    consent: dict[str, bool | list[str]] | None = None
    resource: dict[str, str] | None = None
    tools: list[McpToolDefinition] | None = None
    namespace: str | None = None


@dataclass
class ValidationError:
    policy_id: str
    message: str
    help: str | None = None


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)


@dataclass
class EntityJson:
    uid: dict[str, str]
    attrs: dict[str, Any]
    parents: list[dict[str, str]]


@dataclass
class BuildResult:
    policies: str
    entities: list[EntityJson]
    schema: str | None = None
    _config: CedarAgentConfig | None = field(default=None, repr=False)

    def validate(self) -> ValidationResult:
        if not self.schema:
            return ValidationResult(valid=True)

        try:
            from cedar_agent_policy_builder.schema import validate_policies
            return validate_policies(self.policies, self.schema)
        except ImportError:
            return ValidationResult(valid=True)
