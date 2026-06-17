from cedar_agent_policy_builder.policy_generators import generate_policies, escape_cedar_string
from cedar_agent_policy_builder.types import CedarAgentConfig, PrincipalConfig


class TestEscapeCedarString:
    def test_escapes_backslashes_and_quotes(self):
        assert escape_cedar_string('hello "world"') == 'hello \\"world\\"'
        assert escape_cedar_string("back\\slash") == "back\\\\slash"

    def test_leaves_plain_strings_unchanged(self):
        assert escape_cedar_string("admin") == "admin"


class TestGenerateRolePolicies:
    def test_generates_permit_for_specific_tools(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"analyst": ["search", "query_database"]},
        )
        policies = generate_policies(config)
        assert 'permit(\n  principal is User,\n  action == Action::"search",\n  resource\n) when { principal.role == "analyst" };' in policies
        assert 'permit(\n  principal is User,\n  action == Action::"query_database",\n  resource\n) when { principal.role == "analyst" };' in policies

    def test_generates_wildcard_permit_for_admin_role(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
        )
        policies = generate_policies(config)
        assert 'permit(\n  principal is User,\n  action,\n  resource\n) when { principal.role == "admin" };' in policies

    def test_defaults_principal_type_to_user(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            roles={"viewer": ["read"]},
        )
        policies = generate_policies(config)
        assert "principal is User" in policies

    def test_uses_custom_principal_type(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="agent_id", type="Agent"),
            roles={"viewer": ["read"]},
        )
        policies = generate_policies(config)
        assert "principal is Agent" in policies

    def test_generates_nothing_for_empty_tools_array(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            roles={"empty": []},
        )
        policies = generate_policies(config)
        assert policies == ""

    def test_escapes_special_characters(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={'role"evil': ['tool"inject']},
        )
        policies = generate_policies(config)
        assert 'Action::"tool\\"inject"' in policies
        assert 'principal.role == "role\\"evil"' in policies


class TestGenerateRestrictionPolicies:
    def test_generates_forbid_with_allowed_values_check(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            restrictions={"query_database": {"database": ["analytics", "reporting"]}},
        )
        policies = generate_policies(config)
        assert 'forbid(\n  principal,\n  action == Action::"query_database",\n  resource\n) when {\n  !(context.input has "database" && (context.input.database == "analytics" || context.input.database == "reporting"))\n};' in policies

    def test_handles_single_allowed_value(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            restrictions={"delete_file": {"path": ["/tmp"]}},
        )
        policies = generate_policies(config)
        assert 'forbid(\n  principal,\n  action == Action::"delete_file",\n  resource\n) when {\n  !(context.input has "path" && (context.input.path == "/tmp"))\n};' in policies

    def test_multiple_fields(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            restrictions={"query_database": {"database": ["analytics", "reporting"], "schema": ["public"]}},
        )
        policies = generate_policies(config)
        assert 'context.input.database == "analytics" || context.input.database == "reporting"' in policies
        assert 'context.input.schema == "public"' in policies

    def test_numeric_allowed_values(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            restrictions={"set_limit": {"limit": [10, 50, 100]}},
        )
        policies = generate_policies(config)
        assert "context.input.limit == 10 || context.input.limit == 50 || context.input.limit == 100" in policies


class TestGenerateRateLimitPolicies:
    def test_generates_forbid_with_call_count_check(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            rate_limits={"send_email": 3},
        )
        policies = generate_policies(config)
        assert 'forbid(\n  principal,\n  action == Action::"send_email",\n  resource\n) when { context.session has "call_count" && context.session.call_count >= 3 };' in policies


class TestGenerateTimeWindowPolicies:
    def test_generates_forbid_with_hour_checks(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            time_window={"hourStart": 9, "hourEnd": 17},
        )
        policies = generate_policies(config)
        assert 'forbid(\n  principal,\n  action,\n  resource\n) when { context.session has "hour_utc" && (context.session.hour_utc < 9 || context.session.hour_utc >= 17) };' in policies


class TestGenerateEnvDenialPolicies:
    def test_generates_forbid_with_environment_check(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            deny_in_env={"production": ["delete_record"]},
        )
        policies = generate_policies(config)
        assert 'forbid(\n  principal,\n  action == Action::"delete_record",\n  resource\n) when { context.session has "environment" && context.session.environment == "production" };' in policies

    def test_generates_one_policy_per_tool(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id"),
            deny_in_env={"production": ["delete_record", "drop_table"]},
        )
        policies = generate_policies(config)
        assert 'Action::"delete_record"' in policies
        assert 'Action::"drop_table"' in policies


class TestCombinedPolicies:
    def test_joins_with_double_newline(self):
        config = CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
            rate_limits={"send_email": 3},
        )
        policies = generate_policies(config)
        parts = policies.split("\n\n")
        assert len(parts) == 2

    def test_returns_empty_string_when_no_policies(self):
        config = CedarAgentConfig(principal=PrincipalConfig(key="user_id"))
        assert generate_policies(config) == ""
