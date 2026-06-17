"""Adversarial tests for policy generation.

These tests verify that the generated Cedar policies are syntactically sound
and handle edge cases like injection attempts, unicode, and degenerate configs.
Without the cedar-wasm runtime, we verify structural correctness of the output.
"""

from cedar_agent_policy_builder import CedarAgentPolicyBuilder, from_config
from cedar_agent_policy_builder.types import CedarAgentConfig, PrincipalConfig


class TestCedarSyntaxInjectionViaToolNames:
    def test_tool_name_with_quotes_does_not_break_policy_syntax(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ['tool"; forbid(principal, action, resource);// ']},
        ))
        # The quotes should be escaped in the output
        assert '\\"' in result.policies
        # Should produce exactly one policy (a permit), not a separate forbid statement
        policies_list = [p.strip() for p in result.policies.split("\n\n") if p.strip()]
        assert len(policies_list) == 1
        assert policies_list[0].startswith("permit(")

    def test_role_name_with_quotes_does_not_inject_policies(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={'admin"; permit(principal, action, resource);//': ["search"]},
        ))
        # Should produce exactly one policy statement
        policies_list = [p.strip() for p in result.policies.split("\n\n") if p.strip()]
        assert len(policies_list) == 1
        # Quotes should be escaped
        assert 'admin\\"' in result.policies

    def test_backslash_sequences_in_tool_names(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"user": ['tool\\', '"evil']},
        ))
        # Should produce valid policies with escaped backslashes
        assert "tool\\\\\\" in result.policies or "tool\\\\" in result.policies


class TestDenyByDefaultBehavior:
    def test_no_roles_produces_empty_policies(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
        ))
        assert result.policies == ""
        # No permit = deny by default in Cedar

    def test_unknown_role_gets_no_permit(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
        ))
        # Only admin role gets a permit
        assert 'principal.role == "admin"' in result.policies
        # No permit for unknown roles → Cedar defaults to deny

    def test_principal_type_mismatch_gets_no_permit(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["search"]},
        ))
        # Policies use "principal is User" — other entity types won't match
        assert "principal is User" in result.policies


class TestRestrictionBypassAttempts:
    def test_empty_allowed_values_denies_tool(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"analyst": ["query_database"]},
            restrictions={"query_database": {}},
        ))
        # Empty allowedValues should produce a blanket forbid
        assert 'forbid(\n  principal,\n  action == Action::"query_database",\n  resource\n);' in result.policies


class TestRateLimitBoundaryConditions:
    def test_rate_limit_zero_always_denies(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"user": ["search"]},
            rate_limits={"search": 0},
        ))
        # call_count >= 0 is always true
        assert "context.session.call_count >= 0" in result.policies


class TestTimeWindowEdgeCases:
    def test_same_start_and_end_denies_all_hours(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"user": ["search"]},
            time_window={"hourStart": 12, "hourEnd": 12},
        ))
        # hour < 12 || hour >= 12 is always true → always denied
        assert "context.session.hour_utc < 12 || context.session.hour_utc >= 12" in result.policies


class TestEnvironmentDenialBypassAttempts:
    def test_deny_all_tools_in_env_with_wildcard(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
            deny_in_env={"production": ["*"]},
        ))
        # Wildcard deny in production means forbid all actions
        assert 'forbid(\n  principal,\n  action,\n  resource\n) when { context.session has "environment" && context.session.environment == "production" };' in result.policies

    def test_deny_tools_in_env_without_tools_arg(self):
        result = CedarAgentPolicyBuilder().role("admin", ["*"]).deny_tools_in_env("production").build()
        # No tools arg means deny all (*)
        assert 'forbid(\n  principal,\n  action,\n  resource\n) when { context.session has "environment" && context.session.environment == "production" };' in result.policies


class TestPolicyInteractionPrecedence:
    def test_forbid_and_permit_both_present(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
            rate_limits={"search": 1},
        ))
        # Both permit (wildcard) and forbid (rate limit) should be present
        assert "permit(" in result.policies
        assert "forbid(" in result.policies

    def test_multiple_forbid_conditions_all_present(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"admin": ["*"]},
            time_window={"hourStart": 9, "hourEnd": 17},
            deny_in_env={"production": ["delete_record"]},
        ))
        assert 'context.session has "hour_utc"' in result.policies
        assert 'context.session has "environment"' in result.policies


class TestUnicodeAndSpecialCharacters:
    def test_unicode_role_names(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"管理者": ["search"]},
        ))
        assert 'principal.role == "管理者"' in result.policies

    def test_emoji_in_tool_names(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"user": ["🔍search"]},
        ))
        assert 'Action::"🔍search"' in result.policies

    def test_empty_string_tool_name(self):
        result = from_config(CedarAgentConfig(
            principal=PrincipalConfig(key="user_id", type="User"),
            roles={"user": [""]},
        ))
        assert 'Action::""' in result.policies


class TestConsentEnforcement:
    def test_role_permit_does_not_bypass_consent(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("analyst", ["search", "send_email"])
            .consent(["send_email"])
            .build()
        )

        # search should have unconditional permit
        policies_list = result.policies.split("\n\n")
        search_permits = [p for p in policies_list if 'Action::"search"' in p and "permit(" in p]
        assert len(search_permits) == 1
        assert "user_consent" not in search_permits[0]

        # send_email should NOT have unconditional permit
        send_unconditional = [
            p for p in policies_list
            if 'Action::"send_email"' in p and "permit(" in p and "user_consent" not in p
        ]
        assert send_unconditional == []

        # send_email should have consent-gated permit
        send_consent = [
            p for p in policies_list
            if 'Action::"send_email"' in p and "user_consent" in p
        ]
        assert len(send_consent) == 1

    def test_wildcard_role_does_not_bypass_consent(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("admin", ["*"])
            .consent(["send_email"])
            .build()
        )

        # Wildcard should exclude send_email
        assert '!(action == Action::"send_email")' in result.policies
        # Consent permit should exist
        assert 'context.session has "user_consent" && context.session.user_consent == true' in result.policies

    def test_role_scoped_consent_only_applies_to_that_role(self):
        result = (
            CedarAgentPolicyBuilder()
            .role("admin", ["*"])
            .role("analyst", ["search", "send_email"])
            .consent(["send_email"], for_role="analyst")
            .build()
        )

        # Admin wildcard should NOT exclude send_email
        assert '!(action == Action::"send_email")' not in result.policies
        # Analyst's send_email should be consent-gated
        policies_list = result.policies.split("\n\n")
        analyst_consent = [
            p for p in policies_list
            if 'Action::"send_email"' in p and "analyst" in p and "user_consent" in p
        ]
        assert len(analyst_consent) == 1
