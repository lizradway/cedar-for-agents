# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of `cedar-agent-policy-builder` (Python)
- Fluent builder API: `.role()`, `.restrict()`, `.rate_limit()`, `.time_window()`, `.deny_tools_in_env()`, `.consent()`, `.resource()`, `.namespace()`, `.tools()`
- `from_config()` function for dict/object-based configuration
- Cedar schema generation support (requires optional `cedar-policy-mcp-schema-generator` package)
- McpServer resource entity generation (aligns with MCP schema generator conventions)
- Consent-gated policies (permit when `context.session.user_consent == true`)
- Build-time warnings for undeclared tool name references
- Edge case handling: empty `allowedValues` = deny tool, `deny_tools_in_env()` without tools = deny all, `rate_limit(0)` = always deny
