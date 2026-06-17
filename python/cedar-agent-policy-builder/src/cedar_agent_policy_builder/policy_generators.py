from __future__ import annotations

import json
from typing import Any

from .types import CedarAgentConfig


def escape_cedar_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_consent_roles(value: bool | list[str]) -> list[str]:
    if value is True:
        return ["*"]
    return value


def _get_consent_tools_for_role(config: CedarAgentConfig, role_name: str) -> set[str]:
    if not config.consent:
        return set()
    tools: set[str] = set()
    for tool, value in config.consent.items():
        roles = _normalize_consent_roles(value)
        if "*" in roles or role_name in roles:
            tools.add(tool)
    return tools


def _generate_role_policies(config: CedarAgentConfig) -> list[str]:
    if not config.roles:
        return []
    principal_type = config.principal.type
    policies: list[str] = []

    for role_name, tools in config.roles.items():
        consent_tools = _get_consent_tools_for_role(config, role_name)

        if "*" in tools:
            if consent_tools:
                exclusions = " || ".join(
                    f'action == Action::"{escape_cedar_string(t)}"'
                    for t in sorted(consent_tools)
                )
                policies.append(
                    f'permit(\n  principal is {principal_type},\n  action,\n  resource\n) when {{ principal.role == "{escape_cedar_string(role_name)}" && !({exclusions}) }};'
                )
            else:
                policies.append(
                    f'permit(\n  principal is {principal_type},\n  action,\n  resource\n) when {{ principal.role == "{escape_cedar_string(role_name)}" }};'
                )
        else:
            filtered_tools = [t for t in tools if t not in consent_tools]
            for tool in filtered_tools:
                policies.append(
                    f'permit(\n  principal is {principal_type},\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{ principal.role == "{escape_cedar_string(role_name)}" }};'
                )

    return policies


def _generate_restriction_policies(config: CedarAgentConfig) -> list[str]:
    if not config.restrictions:
        return []
    policies: list[str] = []

    for tool, restriction in config.restrictions.items():
        allowed_values = restriction
        if not allowed_values:
            policies.append(
                f'forbid(\n  principal,\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n);'
            )
            continue
        for field_name, values in allowed_values.items():
            value_checks = " || ".join(
                f"context.input.{field_name} == {json.dumps(v)}"
                for v in values
            )
            policies.append(
                f'forbid(\n  principal,\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{\n  !(context.input has "{escape_cedar_string(field_name)}" && ({value_checks}))\n}};'
            )

    return policies


def _generate_rate_limit_policies(config: CedarAgentConfig) -> list[str]:
    if not config.rate_limits:
        return []
    policies: list[str] = []

    for tool, max_count in config.rate_limits.items():
        policies.append(
            f'forbid(\n  principal,\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{ context.session has "call_count" && context.session.call_count >= {max_count} }};'
        )

    return policies


def _generate_time_window_policy(config: CedarAgentConfig) -> list[str]:
    if not config.time_window:
        return []
    hour_start = config.time_window["hourStart"]
    hour_end = config.time_window["hourEnd"]
    return [
        f'forbid(\n  principal,\n  action,\n  resource\n) when {{ context.session has "hour_utc" && (context.session.hour_utc < {hour_start} || context.session.hour_utc >= {hour_end}) }};'
    ]


def _generate_env_denial_policies(config: CedarAgentConfig) -> list[str]:
    if not config.deny_in_env:
        return []
    policies: list[str] = []

    for env, tools in config.deny_in_env.items():
        if "*" in tools:
            policies.append(
                f'forbid(\n  principal,\n  action,\n  resource\n) when {{ context.session has "environment" && context.session.environment == "{escape_cedar_string(env)}" }};'
            )
        else:
            for tool in tools:
                policies.append(
                    f'forbid(\n  principal,\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{ context.session has "environment" && context.session.environment == "{escape_cedar_string(env)}" }};'
                )

    return policies


def _generate_consent_policies(config: CedarAgentConfig) -> list[str]:
    if not config.consent:
        return []
    principal_type = config.principal.type
    policies: list[str] = []

    for tool, value in config.consent.items():
        roles = _normalize_consent_roles(value)
        if "*" in roles:
            policies.append(
                f'permit(\n  principal is {principal_type},\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{ context.session has "user_consent" && context.session.user_consent == true }};'
            )
        else:
            for role in roles:
                policies.append(
                    f'permit(\n  principal is {principal_type},\n  action == Action::"{escape_cedar_string(tool)}",\n  resource\n) when {{ principal.role == "{escape_cedar_string(role)}" && context.session has "user_consent" && context.session.user_consent == true }};'
                )

    return policies


def generate_policies(config: CedarAgentConfig) -> str:
    all_policies = [
        *_generate_role_policies(config),
        *_generate_restriction_policies(config),
        *_generate_rate_limit_policies(config),
        *_generate_time_window_policy(config),
        *_generate_env_denial_policies(config),
        *_generate_consent_policies(config),
    ]
    return "\n\n".join(all_policies)
