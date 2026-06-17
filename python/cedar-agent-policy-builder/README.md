# cedar-agent-policy-builder (Python)

Generate [Cedar](https://github.com/cedar-policy/cedar) policies, entities, and schemas for agent authorization from declarative configuration.

Python port of the [TypeScript `cedar-agent-policy-builder`](../js/cedar-agent-policy-builder/).

## Installation

```bash
pip install cedar-agent-policy-builder
```

## Usage

### Builder API

```python
from cedar_agent_policy_builder import CedarAgentPolicyBuilder, SchemaConfig, PrincipalConfig

result = (
    CedarAgentPolicyBuilder(SchemaConfig(
        principal=PrincipalConfig(key="user_id", type="User"),
    ))
    .role("admin", ["*"])
    .role("analyst", ["search", "query_database"])
    .restrict("query_database", {"allowedValues": {"database": ["analytics", "reporting"]}})
    .rate_limit("send_email", 3)
    .time_window(hour_start=9, hour_end=17)
    .deny_tools_in_env("production", ["delete_record"])
    .consent(["send_email", "delete_file"])
    .build()
)

# result.policies: Generated Cedar permit/forbid rules as a string
# result.entities: Cedar entities list (Role entities + McpServer resource entity)
# result.schema: Cedar schema (if tools were provided)
```

### From config (dict/object)

```python
from cedar_agent_policy_builder import from_config
from cedar_agent_policy_builder.types import CedarAgentConfig, PrincipalConfig

result = from_config(CedarAgentConfig(
    principal=PrincipalConfig(key="user_id", type="User"),
    roles={"admin": ["*"], "analyst": ["search", "query_database"]},
    restrictions={"query_database": {"database": ["analytics", "reporting"]}},
    rate_limits={"send_email": 3},
    time_window={"hourStart": 9, "hourEnd": 17},
    deny_in_env={"production": ["delete_record"]},
    consent={"send_email": True, "delete_file": True},
))
```

## API Reference

### Constructor (schema configuration)

| Option | Description |
|--------|-------------|
| `principal` | Identity resolution. Default: `PrincipalConfig(key='user_id', type='User')` |
| `resource` | Custom resource entity dict `{"type": ..., "id": ...}`. Default: `McpServer::"default"`. |
| `tools` | MCP tool definitions for schema generation. |
| `namespace` | Cedar namespace. Default: `"Agent"`. |

### Policy methods

| Method | Description |
|--------|-------------|
| `.role(name, tools)` | Grant a role access to tools. `['*']` = all tools. |
| `.restrict(tool, config)` | Restrict tool arguments to specific values. Empty `{}` = deny tool entirely. |
| `.rate_limit(tool, max)` | Max calls per session. `0` = always denied. |
| `.time_window(hour_start, hour_end)` | Allow tools only during UTC hours. `start == end` = deny all. |
| `.deny_tools_in_env(env, tools?)` | Deny tools in an environment. No `tools` arg = deny all tools. |
| `.consent(tools, for_role?)` | Require human consent. No `for_role` = all roles need consent. |
| `.build()` | Generate `BuildResult` with policies, entities, and optional schema. |
| `.build_and_validate()` | Build and validate; raises on validation failure. |

## How it works

The builder generates Cedar policies following the [cedar-for-agents](https://github.com/cedar-policy/cedar-for-agents) MCP schema generator conventions:

- **Actions** are named directly after tools (e.g. `Action::"search"`)
- **Context** is nested: `context.input.*` for tool arguments, `context.session.*` for runtime state
- **Principals** are entity types with a `role` attribute
- **Resource** defaults to `McpServer::"default"`
- **Default-deny** — no permit = denied

All `context.session.*` field accesses include `has` guards for safety (Cedar errors on missing fields rather than returning false).

## License

Apache-2.0
