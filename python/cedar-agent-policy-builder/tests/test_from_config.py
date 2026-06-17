from cedar_agent_policy_builder import from_config
from cedar_agent_policy_builder.types import CedarAgentConfig, EntityJson, PrincipalConfig


class TestFromConfig:
    def test_produces_expected_output_for_full_example(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"], "analyst": ["search", "query_database"]},
            restrictions={"query_database": {"database": ["analytics", "reporting"]}},
            rate_limits={"send_email": 3},
            time_window={"hourStart": 9, "hourEnd": 17},
            deny_in_env={"production": ["delete_record"]},
        )
        result = from_config(config)

        assert 'permit(\n  principal is User,\n  action,\n  resource\n) when { principal.role == "admin" };' in result.policies
        assert 'permit(\n  principal is User,\n  action == Action::"search",\n  resource\n) when { principal.role == "analyst" };' in result.policies
        assert 'permit(\n  principal is User,\n  action == Action::"query_database",\n  resource\n) when { principal.role == "analyst" };' in result.policies
        assert 'forbid(\n  principal,\n  action == Action::"query_database",\n  resource\n) when {\n  !(context.input has "database" && (context.input.database == "analytics" || context.input.database == "reporting"))\n};' in result.policies
        assert 'forbid(\n  principal,\n  action == Action::"send_email",\n  resource\n) when { context.session has "call_count" && context.session.call_count >= 3 };' in result.policies
        assert 'forbid(\n  principal,\n  action,\n  resource\n) when { context.session has "hour_utc" && (context.session.hour_utc < 9 || context.session.hour_utc >= 17) };' in result.policies
        assert 'forbid(\n  principal,\n  action == Action::"delete_record",\n  resource\n) when { context.session has "environment" && context.session.environment == "production" };' in result.policies

        assert result.entities == [
            EntityJson(uid={"type": "Role", "id": "admin"}, attrs={}, parents=[]),
            EntityJson(uid={"type": "Role", "id": "analyst"}, attrs={}, parents=[]),
            EntityJson(uid={"type": "McpServer", "id": "default"}, attrs={}, parents=[]),
        ]
