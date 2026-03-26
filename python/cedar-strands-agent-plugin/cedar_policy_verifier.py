"""Cedar policy verifier — catch policy bugs at build time, not runtime.

Generates a Cedar schema from tool definitions and validates policies
against it. Catches errors like referencing nonexistent tools, typos
in context attributes, and type mismatches.

Usage:
    # From an agent
    verifier = CedarPolicyVerifier.from_agent(agent)
    result = verifier.verify(plugin._policies)

    # From tool specs directly
    verifier = CedarPolicyVerifier(tools={
        "search": {"type": "object", "properties": {"query": {"type": "string"}}},
        "delete": {"type": "object", "properties": {"id": {"type": "integer"}}},
    })
    result = verifier.verify(policies_string)

Requires: pip install cedarpy
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cedarpy

# JSON Schema type → Cedar type
_JSON_TO_CEDAR = {
    "string": "__cedar::String",
    "integer": "__cedar::Long",
    "number": "__cedar::Long",
    "boolean": "__cedar::Bool",
}


@dataclass
class VerificationResult:
    """Result of policy verification."""

    passed: bool
    errors: list[str]
    warnings: list[str]
    schema: str  # the generated Cedar schema (for inspection)


class CedarPolicyVerifier:
    """Verifies Cedar policies against an agent's tool definitions.

    Generates a Cedar schema from tool definitions and validates policies
    against it. Catches errors like referencing nonexistent tools, typos
    in context attributes, and type mismatches — at build time, not runtime.
    """

    def __init__(
        self,
        tools: dict[str, dict[str, Any]],
        principal_types: list[str] | None = None,
        resource_types: list[str] | None = None,
    ) -> None:
        """Create a verifier from tool input schemas.

        Args:
            tools: Mapping of tool_name → JSON Schema for that tool's input.
                   e.g. {"search": {"type": "object", "properties": {"query": {"type": "string"}}}}
            principal_types: Cedar entity types that can be principals.
                             Defaults to ["User"].
            resource_types: Cedar entity types that can be resources.
                            Defaults to ["Tool"].
        """
        self._tools = tools
        self._principal_types = principal_types or ["User"]
        self._resource_types = resource_types or ["Tool"]

    @classmethod
    def from_agent(
        cls,
        agent: Any,
        principal_types: list[str] | None = None,
        resource_types: list[str] | None = None,
    ) -> "CedarPolicyVerifier":
        """Create a verifier from a Strands Agent instance.

        Extracts tool names and input schemas from agent.tool_registry.
        """
        all_config = agent.tool_registry.get_all_tools_config()
        tools = {}
        for name, spec in all_config.items():
            input_schema = spec.get("inputSchema", {})
            # Strands wraps it in {"json": {...}}
            if "json" in input_schema:
                input_schema = input_schema["json"]
            tools[name] = input_schema
        return cls(tools, principal_types=principal_types, resource_types=resource_types)

    def generate_schema(self) -> str:
        """Generate a Cedar schema from tool definitions.

        For each tool, creates a Cedar action with a context record that includes:
        - The tool's input parameters (mapped from JSON Schema types)
        - Enrichment fields the plugin always adds: timestamp, hour_utc
        - Optional enrichment fields: environment, session_call_count
        """
        lines: list[str] = []

        # Entity types
        lines.append("entity Role;")
        for pt in self._principal_types:
            if pt != "Role":
                lines.append(f"entity {pt} in [Role];")
        for rt in self._resource_types:
            if rt not in self._principal_types and rt != "Role":
                lines.append(f"entity {rt};")
        lines.append("")

        # One action per tool
        for tool_name, schema in self._tools.items():
            context_attrs = self._schema_to_context(schema)
            principals = ", ".join(self._principal_types + ["Role"])
            resources = ", ".join(self._resource_types)
            lines.append(f'action "use_tool::{tool_name}" appliesTo {{')
            lines.append(f"    principal: [{principals}],")
            lines.append(f"    resource: [{resources}],")
            lines.append(f"    context: {{")
            for attr_name, cedar_type in context_attrs.items():
                lines.append(f'        "{attr_name}": {cedar_type},')
            lines.append(f"    }}")
            lines.append(f"}};")
            lines.append("")

        return "\n".join(lines)

    def _schema_to_context(self, schema: dict[str, Any]) -> dict[str, str]:
        """Convert a JSON Schema's properties to Cedar context attributes."""
        attrs: dict[str, str] = {}

        properties = schema.get("properties", {})

        for name, prop in properties.items():
            json_type = prop.get("type", "string")
            cedar_type = _JSON_TO_CEDAR.get(json_type, "__cedar::String")
            attrs[name] = cedar_type

        # Enrichment fields the plugin always adds
        attrs["timestamp"] = "__cedar::String"
        attrs["hour_utc"] = "__cedar::Long"
        # Optional enrichments — include them so policies can reference them
        attrs["environment"] = "__cedar::String"
        attrs["session_call_count"] = "__cedar::Long"

        return attrs

    def verify(self, policies: str | Path) -> VerificationResult:
        """Verify policies against the generated schema.

        Returns a VerificationResult with:
        - passed: True if no errors
        - errors: Schema validation errors (typos, wrong types, unknown tools)
        - warnings: Completeness issues (tools without policies)
        - schema: The generated Cedar schema (for inspection/debugging)
        """
        if isinstance(policies, Path) or (isinstance(policies, str) and Path(policies).suffix == ".cedar"):
            policies = Path(policies).read_text()

        schema = self.generate_schema()
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Cedar schema validation — catches typos, type errors, unknown actions
        result = cedarpy.validate_policies(policies, schema)
        if not result.validation_passed:
            for err in result.errors:
                errors.append(str(err))

        # 2. Completeness check — tools without any policy referencing them
        for tool_name in self._tools:
            action_ref = f"use_tool::{tool_name}"
            if action_ref not in policies:
                has_wildcard = self._has_wildcard_action(policies)
                if not has_wildcard:
                    warnings.append(
                        f'Tool "{tool_name}" has no policy covering it. '
                        f"It will be denied by default (Cedar is deny-by-default)."
                    )

        return VerificationResult(
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            schema=schema,
        )

    @staticmethod
    def _has_wildcard_action(policies: str) -> bool:
        """Heuristic: check if any permit has an unscoped 'action' (covers all tools)."""
        return bool(re.search(r"permit\s*\([^)]*\baction\s*[,\)]", policies))
