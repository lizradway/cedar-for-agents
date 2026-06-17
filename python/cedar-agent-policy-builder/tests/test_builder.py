from cedar_agent_policy_builder import CedarAgentPolicyBuilder
from cedar_agent_policy_builder.types import EntityJson, PrincipalConfig, SchemaConfig


class TestCedarAgentPolicyBuilder:
    def test_supports_fluent_chaining(self):
        result = (
            CedarAgentPolicyBuilder(SchemaConfig(principal=PrincipalConfig(key="user_id", type="User")))
            .role("admin", ["*"])
            .role("analyst", ["search", "query_database"])
            .restrict("query_database", {"allowedValues": {"database": ["analytics", "reporting"]}})
            .rate_limit("send_email", 3)
            .time_window(hour_start=9, hour_end=17)
            .deny_tools_in_env("production", ["delete_record"])
            .build()
        )

        assert 'principal.role == "admin"' in result.policies
        assert 'principal.role == "analyst"' in result.policies
        assert 'Action::"query_database"' in result.policies
        assert 'context.session has "call_count" && context.session.call_count >= 3' in result.policies
        assert 'context.session has "hour_utc"' in result.policies
        assert 'context.session has "environment" && context.session.environment == "production"' in result.policies
        assert len(result.entities) == 3  # 2 roles + McpServer

    def test_defaults_principal_type_to_user(self):
        result = CedarAgentPolicyBuilder().role("viewer", ["read"]).build()
        assert "principal is User" in result.policies

    def test_generates_mcp_server_entity_even_with_no_roles(self):
        result = CedarAgentPolicyBuilder().build()
        assert result.policies == ""
        assert result.entities == [
            EntityJson(uid={"type": "McpServer", "id": "default"}, attrs={}, parents=[]),
        ]

    def test_supports_consent_method(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("developer", ["search", "read_file"])
            .consent(["send_email", "delete_file"])
            .build()
        )

        assert 'context.session has "user_consent" && context.session.user_consent == true' in result.policies
        assert 'Action::"send_email"' in result.policies
        assert 'Action::"delete_file"' in result.policies
        assert 'principal.role == "developer"' in result.policies

    def test_supports_custom_resource_entity(self):
        result = CedarAgentPolicyBuilder(
            SchemaConfig(resource={"type": "AgentServer", "id": "my-agent"})
        ).build()
        assert EntityJson(uid={"type": "AgentServer", "id": "my-agent"}, attrs={}, parents=[]) in result.entities

    def test_no_schema_when_no_tools(self):
        result = CedarAgentPolicyBuilder().role("admin", ["*"]).build()
        assert result.schema is None


class TestConsentRoleInteraction:
    def test_auto_excludes_consent_tools_from_role_permit(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("analyst", ["search", "send_email"])
            .consent(["send_email"])
            .build()
        )

        assert 'Action::"search"' in result.policies
        assert 'context.session has "user_consent" && context.session.user_consent == true' in result.policies
        # send_email should NOT have an unconditional role permit
        lines = result.policies.split("\n\n")
        unconditional_send_email = [
            p for p in lines
            if 'Action::"send_email"' in p and "user_consent" not in p
        ]
        assert unconditional_send_email == []

    def test_wildcard_role_excludes_consent_tools(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("admin", ["*"])
            .consent(["send_email"])
            .build()
        )

        assert '!(action == Action::"send_email")' in result.policies
        assert 'context.session has "user_consent" && context.session.user_consent == true' in result.policies

    def test_wildcard_without_consent_has_no_exclusion(self):
        result = CedarAgentPolicyBuilder().role("admin", ["*"]).build()
        assert "!(" not in result.policies

    def test_role_scoped_consent_only_applies_to_that_role(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("admin", ["*"])
            .role("analyst", ["search", "send_email"])
            .consent(["send_email"], for_role="analyst")
            .build()
        )

        # Admin wildcard should NOT have send_email excluded
        assert '!(action == Action::"send_email")' not in result.policies
        # Analyst should not have unconditional send_email
        policies_list = result.policies.split("\n\n")
        analyst_unconditional = [
            p for p in policies_list
            if 'Action::"send_email"' in p
            and 'principal.role == "analyst"' in p
            and "user_consent" not in p
        ]
        assert analyst_unconditional == []
