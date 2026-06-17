from cedar_agent_policy_builder.entities import generate_entities
from cedar_agent_policy_builder.types import CedarAgentConfig, EntityJson, PrincipalConfig


class TestGenerateEntities:
    def test_generates_mcp_server_resource_even_with_no_roles(self):
        config = CedarAgentConfig(principal=PrincipalConfig(key="user_id"))
        assert generate_entities(config) == [
            EntityJson(uid={"type": "McpServer", "id": "default"}, attrs={}, parents=[]),
        ]

    def test_generates_role_entities_and_mcp_server(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            roles={"admin": ["*"], "analyst": ["search"]},
        )
        entities = generate_entities(config)
        assert entities == [
            EntityJson(uid={"type": "Role", "id": "admin"}, attrs={}, parents=[]),
            EntityJson(uid={"type": "Role", "id": "analyst"}, attrs={}, parents=[]),
            EntityJson(uid={"type": "McpServer", "id": "default"}, attrs={}, parents=[]),
        ]

    def test_uses_custom_resource_type_and_id(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            roles={"viewer": ["read"]},
            resource={"type": "AgentServer", "id": "my-agent"},
        )
        entities = generate_entities(config)
        assert EntityJson(uid={"type": "AgentServer", "id": "my-agent"}, attrs={}, parents=[]) in entities
