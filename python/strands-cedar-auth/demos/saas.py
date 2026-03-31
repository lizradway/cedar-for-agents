"""Demo: Cedar guardrails for a multi-user SaaS AI agent.

Scenario: A company deploys an AI agent as a shared internal service (Slack bot,
internal API, etc). Multiple users interact with the same agent, but each has
different permissions based on their role:

  - admin: Full access — can query any database, manage users, delete records
  - analyst: Can query analytics/reporting databases, generate reports, export data
  - viewer: Can only search and view dashboards — no writes, no raw SQL, no exports

Cedar enforces these boundaries so the agent can't be tricked (via prompt injection
or hallucination) into running queries or actions beyond the user's role.

Requires: pip install cedarpy strands-agents
    + a model provider configured (default: Bedrock with Claude)
"""

from strands import Agent, tool

from cedar_auth_plugin import CedarAuthPlugin

# ---------------------------------------------------------------------------
# 1. Build the plugin
# ---------------------------------------------------------------------------
plugin = (
    CedarAuthPlugin.builder()
    # Roles
    .role("admin", tools=["*"])
    .role("analyst", tools=[
        "query_database", "list_tables", "generate_report", "export_csv",
    ])
    .role("viewer", tools=[
        "search_dashboards", "list_tables",
    ])
    # Analysts can only query analytics and reporting databases
    .restrict("query_database", allowed_values={
        "database": ["analytics", "reporting"],
    }, for_role="analyst")
    # Nobody can query the secrets database — not even admins
    .restrict("query_database", allowed_values={
        "database": ["analytics", "reporting", "production", "staging"],
    })
    # Rate limit exports to prevent bulk data exfiltration
    .rate_limit("export_csv", max_per_session=3)
    # No destructive operations in production
    .deny_tools_in_env("production", ["delete_records", "drop_table"])
    .build()
)

# ---------------------------------------------------------------------------
# 2. Tools
# ---------------------------------------------------------------------------

@tool
def query_database(database: str, query: str) -> str:
    """Execute a SQL query against a database."""
    return f"[{database}] Results for: {query[:60]}... → 42 rows returned"


@tool
def list_tables(database: str) -> str:
    """List all tables in a database."""
    tables = {
        "analytics": "users, events, sessions, page_views, conversions",
        "reporting": "monthly_revenue, churn_rate, active_users, cohorts",
        "production": "users, orders, payments, subscriptions, invoices",
        "secrets": "api_keys, tokens, credentials, ssh_keys",
    }
    return f"[{database}] Tables: {tables.get(database, 'unknown database')}"


@tool
def generate_report(report_type: str, date_range: str) -> str:
    """Generate a business report."""
    return f"Generated {report_type} report for {date_range}: revenue $1.2M, churn 3.2%, MRR growth 8%"


@tool
def export_csv(table: str, row_limit: int) -> str:
    """Export a table to CSV. Limited to prevent bulk exfiltration."""
    return f"Exported {row_limit} rows from {table} to /tmp/export_{table}.csv"


@tool
def search_dashboards(query: str) -> str:
    """Search available dashboards by keyword."""
    return f"Found 3 dashboards matching '{query}': Revenue Overview, User Growth, Churn Analysis"


@tool
def delete_records(table: str, condition: str) -> str:
    """Delete records from a table matching a condition. Destructive."""
    return f"Deleted records from {table} where {condition}"


@tool
def drop_table(table: str) -> str:
    """Drop an entire table. Irreversible."""
    return f"Dropped table {table}"


@tool
def manage_users(action: str, user_email: str, role: str) -> str:
    """Manage user accounts — create, update roles, deactivate."""
    return f"User {user_email}: {action} with role={role}"


# ---------------------------------------------------------------------------
# 3. Agent
# ---------------------------------------------------------------------------
agent = Agent(
    plugins=[plugin],
    tools=[query_database, list_tables, generate_report, export_csv,
           search_dashboards, delete_records, drop_table, manage_users],
)

# ---------------------------------------------------------------------------
# 4. Scenarios
# ---------------------------------------------------------------------------
def demo():
    print("=" * 70)
    print("Cedar Auth — Multi-User SaaS Agent Guardrails")
    print("=" * 70)
    print()
    print("Generated Cedar policies:")
    print("-" * 70)
    print(plugin._policies)
    print("-" * 70)

    scenarios = [
        # --- Admin: full access ---
        {
            "title": "Admin queries production database",
            "user": "admin@acme.com", "roles": ["admin"],
            "message": "Query the production database: SELECT count(*) FROM orders WHERE status='pending'",
            "expected": "ALLOWED — admin has wildcard access",
        },
        {
            "title": "Admin manages a user account",
            "user": "admin@acme.com", "roles": ["admin"],
            "message": "Deactivate user bob@acme.com and set their role to 'none'",
            "expected": "ALLOWED — admin can manage users",
        },
        {
            "title": "Admin queries secrets database",
            "user": "admin@acme.com", "roles": ["admin"],
            "message": "Query the secrets database: SELECT * FROM api_keys",
            "expected": "DENIED — nobody can query secrets, even admin (global restrict)",
        },
        {
            "title": "Admin deletes records (production env)",
            "user": "admin@acme.com", "roles": ["admin"],
            "message": "Delete records from users table where status='inactive'",
            "expected": "DENIED — destructive ops blocked in production env",
            "extra_state": {"environment": "production"},
        },
        {
            "title": "Admin deletes records (staging env)",
            "user": "admin@acme.com", "roles": ["admin"],
            "message": "Delete records from users table where status='inactive'",
            "expected": "ALLOWED — staging is not restricted",
            "extra_state": {"environment": "staging"},
        },

        # --- Analyst: scoped access ---
        {
            "title": "Analyst queries analytics database",
            "user": "alice@acme.com", "roles": ["analyst"],
            "message": "Query the analytics database: SELECT count(*) FROM events WHERE date > '2026-01-01'",
            "expected": "ALLOWED — analyst can query analytics",
        },
        {
            "title": "Analyst queries production database",
            "user": "alice@acme.com", "roles": ["analyst"],
            "message": "Query the production database: SELECT * FROM payments LIMIT 100",
            "expected": "DENIED — analyst restricted to analytics/reporting only",
        },
        {
            "title": "Analyst generates a report",
            "user": "alice@acme.com", "roles": ["analyst"],
            "message": "Generate a monthly revenue report for Q1 2026",
            "expected": "ALLOWED — analyst can generate reports",
        },
        {
            "title": "Analyst tries to manage users",
            "user": "alice@acme.com", "roles": ["analyst"],
            "message": "Create a new user charlie@acme.com with role 'analyst'",
            "expected": "DENIED — analyst doesn't have manage_users",
        },
        {
            "title": "Analyst tries to delete records",
            "user": "alice@acme.com", "roles": ["analyst"],
            "message": "Delete old records from the analytics events table",
            "expected": "DENIED — analyst doesn't have delete_records",
        },

        # --- Viewer: read-only ---
        {
            "title": "Viewer searches dashboards",
            "user": "bob@acme.com", "roles": ["viewer"],
            "message": "Search for dashboards about revenue",
            "expected": "ALLOWED — viewer can search dashboards",
        },
        {
            "title": "Viewer lists tables",
            "user": "bob@acme.com", "roles": ["viewer"],
            "message": "List all tables in the analytics database",
            "expected": "ALLOWED — viewer can list tables",
        },
        {
            "title": "Viewer tries to query a database",
            "user": "bob@acme.com", "roles": ["viewer"],
            "message": "Query the analytics database: SELECT * FROM users LIMIT 10",
            "expected": "DENIED — viewer can't run queries",
        },
        {
            "title": "Viewer tries to export data",
            "user": "bob@acme.com", "roles": ["viewer"],
            "message": "Export the users table to CSV",
            "expected": "DENIED — viewer can't export",
        },

        # --- Unknown user: no access ---
        {
            "title": "Unknown user tries to search dashboards",
            "user": "stranger@external.com", "roles": [],
            "message": "Search for dashboards about revenue",
            "expected": "DENIED — no roles = no access (deny-by-default)",
        },
    ]

    for s in scenarios:
        state = {
            "user_id": s["user"],
            "roles": s["roles"],
            **s.get("extra_state", {}),
        }
        env = s.get("extra_state", {}).get("environment", "")
        env_label = f" [env={env}]" if env else ""

        print(f"\n{'─' * 70}")
        print(f"  {s['title']}")
        print(f"  User: {s['user']} | Roles: {s['roles']}{env_label}")
        print(f"  Prompt: \"{s['message']}\"")
        print(f"  Expected: {s['expected']}")
        print(f"{'─' * 70}")

        try:
            result = agent(s["message"], invocation_state=state)
            print(f"  Agent: {result}")
        except Exception as e:
            print(f"  Error: {e}")

    # --- Rate limits ---
    print(f"\n{'=' * 70}")
    print("Rate Limit Tests (export_csv, max 3 per session)")
    print("Prevents bulk data exfiltration even for authorized users")
    print(f"{'=' * 70}")

    plugin._call_counts.clear()
    for i in range(1, 6):
        expected = "ALLOWED" if i <= 3 else "DENIED — rate limit exceeded"
        print(f"\n{'─' * 70}")
        print(f"  export_csv call {i}/3")
        print(f"  User: alice@acme.com | Role: analyst")
        print(f"  Expected: {expected}")
        print(f"{'─' * 70}")
        try:
            result = agent(
                f"Export the events table, limit {i * 1000} rows",
                invocation_state={
                    "user_id": "alice@acme.com",
                    "roles": ["analyst"],
                    "session_id": "analysis-run-1",
                },
            )
            print(f"  Agent: {result}")
        except Exception as e:
            print(f"  Error: {e}")

    # Audit log
    print(f"\n{'=' * 70}")
    print("Audit Log")
    print(f"{'=' * 70}")
    for entry in plugin.audit_log:
        status = "ALLOW" if entry.allowed else "DENY "
        print(f"  [{status}] {entry.principal:<40} {entry.tool_name}")


if __name__ == "__main__":
    demo()
