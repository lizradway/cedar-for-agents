from __future__ import annotations

import json
import re

from .types import CedarAgentConfig, ValidationResult, ValidationError


def _build_schema_stub(config: CedarAgentConfig) -> str:
    ns = config.namespace or "Agent"
    principal_type = config.principal.type
    resource_type = config.resource["type"] if config.resource else "McpServer"

    return f"""namespace {ns} {{
  @mcp_principal("{principal_type}")
  entity {principal_type} = {{
    role: String,
  }};

  @mcp_resource("{resource_type}")
  entity {resource_type};
}}"""


def _build_tools_json(config: CedarAgentConfig) -> str:
    tools = []
    for t in config.tools or []:
        tool_entry: dict = {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        if t.output_schema:
            tool_entry["outputSchema"] = t.output_schema
        tools.append(tool_entry)
    return json.dumps({"result": {"tools": tools}})


def generate_schema(config: CedarAgentConfig) -> str | None:
    if not config.tools:
        return None

    try:
        from cedar_policy_mcp_schema_generator import generate_schema as generate_schema_wasm
    except ImportError:
        return None

    schema_stub = _build_schema_stub(config)
    tools_json = _build_tools_json(config)
    raw = generate_schema_wasm(schema_stub, tools_json, "{}")

    if isinstance(raw, str):
        result = json.loads(raw)
    else:
        result = raw

    if not result.get("isOk"):
        raise RuntimeError(f"Schema generation failed: {result.get('error')}")

    schema: str = result["schema"]
    # Strip namespace wrapper for validation compatibility
    schema = re.sub(r"^namespace\s+[\w:]+\s*\{", "", schema)
    schema = re.sub(r"\}\s*$", "", schema)
    return schema.strip()


def validate_policies(policies: str, schema: str) -> ValidationResult:
    try:
        from cedar_policy import validate as cedar_validate
    except ImportError:
        return ValidationResult(valid=True)

    result = cedar_validate(
        policies={"staticPolicies": policies, "templates": {}},
        schema=schema,
        validation_settings={"mode": "strict"},
    )

    if result.get("type") != "success":
        messages = "; ".join(e.get("message", "") for e in result.get("errors", []))
        return ValidationResult(
            valid=False,
            errors=[ValidationError(policy_id="", message=messages or "validation failed")],
        )

    errors = [
        ValidationError(
            policy_id=e.get("policyId", ""),
            message=e.get("error", {}).get("message", ""),
            help=e.get("error", {}).get("help"),
        )
        for e in result.get("validationErrors", [])
    ]
    warnings = [
        ValidationError(
            policy_id=e.get("policyId", ""),
            message=e.get("error", {}).get("message", ""),
            help=e.get("error", {}).get("help"),
        )
        for e in result.get("validationWarnings", [])
    ]
    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
