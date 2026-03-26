# Cedar Guardrails for Multi-User SaaS Agents

- [Cedar Guardrails for Multi-User SaaS Agents](#cedar-guardrails-for-multi-user-saas-agents)
  - [The Problem: Shared Agents, Different Users, Same Credentials](#the-problem-shared-agents-different-users-same-credentials)
  - [How Cedar Resolves This](#how-cedar-resolves-this)
  - [A Concrete Example](#a-concrete-example)
    - [What This Demonstrates](#what-this-demonstrates)
    - [Why Not Just Use Database Permissions?](#why-not-just-use-database-permissions)
  - [The Demo](#the-demo)

## The Problem: Shared Agents, Different Users, Same Credentials

Organizations are deploying AI agents as shared internal services — Slack bots, internal APIs, copilot features — where multiple users interact with the same agent. The agent runs with a single set of credentials (database connections, API keys, cloud access), but users have different roles and should see different things.

Today, there's no standard way to enforce this. The agent has one database connection string. When Alice the analyst asks "show me last month's revenue," the agent runs a SQL query with the same credentials it would use if an admin asked "delete all inactive users." The LLM decides what to do based on prompt instructions — but prompt instructions can be overridden, ignored, or circumvented.

This creates three problems:

1. **No least-privilege.** Every user gets the full power of the agent's credentials. A viewer who should only see dashboards can ask the agent to run raw SQL or export data.
2. **No audit trail.** When something goes wrong ("who deleted those records?"), there's no log of which user asked for what and whether it was authorized.
3. **Prompt injection across privilege levels.** A low-privilege user can craft prompts that trick the agent into performing actions their role shouldn't allow. Prompt-level restrictions ("you are a read-only assistant") compete with the user's input — and the LLM decides which to follow.

## How Cedar Resolves This

Cedar adds an authorization layer between the user's request and the tool's execution. Every tool call is evaluated against a policy before it runs. The policy checks *who* is asking (principal), *what* they're trying to do (action), and *on what* (resource) — with additional context like which database, what environment, and how many times they've already called this tool.

The key properties:

- **Deny-by-default.** If there's no `permit` policy for a user/tool combination, the call is blocked. A new user with no role gets zero access.
- **`forbid` overrides `permit`.** Even if an admin has wildcard access, a `forbid` policy can block specific actions (e.g., querying the secrets database, or destructive ops in production).
- **Outside the LLM.** Cedar enforcement happens in a plugin layer. The LLM never sees a tool result for a denied call — it gets "access denied." Prompt injection can't bypass this.
- **Auditable.** Every decision (allow or deny) is logged with the principal, tool, arguments, and timestamp. (See [Appendix D](#appendix-d-audit-log).)

## A Concrete Example

The demo (`demo_saas.py`) models a shared internal agent with three roles:

| Role | Can do | Can't do |
|---|---|---|
| **admin** | Query any database (except secrets), manage users, generate reports, export data | Query secrets DB (global forbid), destructive ops in production env |
| **analyst** | Query analytics/reporting DBs, generate reports, export data (rate-limited) | Query production DB, manage users, delete records |
| **viewer** | Search dashboards, list tables | Query databases, export data, generate reports, manage users |

### What This Demonstrates

**Database scoping by role.** The analyst can query `analytics` and `reporting` but not `production`. This isn't a prompt instruction — it's a Cedar `forbid` policy that checks the `database` argument before the query executes.

**Global forbid policies.** Nobody — not even admin — can query the `secrets` database. The global `.restrict("query_database", allowed_values={"database": [...]})` creates a `forbid` that overrides every `permit`. This is useful for compliance: certain data sources are off-limits regardless of role.

**Environment-based restrictions.** Destructive tools (`delete_records`, `drop_table`) are blocked in the `production` environment even for admins. The same admin can use these tools in `staging`. This prevents accidental (or malicious) data loss in production.

**Rate limiting.** `export_csv` is limited to 3 calls per session. This prevents bulk data exfiltration — even an authorized analyst can't export the entire database in one session. The 4th export call is blocked.

**Deny-by-default.** A user with no roles (`stranger@external.com`) gets zero access. They can't even search dashboards. This is the safe default — you have to explicitly grant permissions.

### Why Not Just Use Database Permissions?

Database-level permissions (Postgres roles, IAM policies) control what the *connection* can do — but the agent uses a single connection for all users. You'd need to maintain a separate database user per SaaS user, switch connections per request, and hope the agent framework supports this. And database permissions don't cover non-database tools like `generate_report`, `export_csv`, or `manage_users`.

Cedar operates at the *application layer* — one level above the database. It controls which tools the agent can call on behalf of which user, with what arguments. The agent keeps its single database connection, and Cedar gates access before the tool runs. (See [Appendix C](#appendix-c-database-permissions-vs-cedar) for a detailed comparison.)

## The Demo

```bash
cd python/cedar-strands-agent-plugin
pip install cedarpy strands-agents
python demo_saas.py
```

The demo uses dummy tool implementations (they return strings instead of hitting a real database) and requires a model provider configured (default: Amazon Bedrock with Claude). The Cedar authorization is real — the same plugin and policies would work with actual database connections in production. (See [Appendix A](#appendix-a-demo-scenarios) for the full scenario list and [Appendix B](#appendix-b-generated-cedar-policies) for the generated policies.)

---

<details>
<summary><h2>Appendix A: Demo Scenarios</h2></summary>

**Admin**

| Scenario | Expected | Why |
|---|---|---|
| Query production database | ALLOWED | Admin has wildcard access, production is in the allowed list |
| Manage a user account | ALLOWED | Admin can manage users |
| Query secrets database | DENIED | Global `forbid` — secrets DB is not in the allowed list for anyone |
| Delete records (production env) | DENIED | `deny_tools_in_env("production", ...)` blocks destructive ops |
| Delete records (staging env) | ALLOWED | Staging is not restricted |

The "admin queries secrets" scenario is the most important. The admin has `tools=["*"]` — wildcard access. But the global `.restrict("query_database", allowed_values={"database": ["analytics", "reporting", "production", "staging"]})` creates a `forbid` policy that applies to all principals. In Cedar, `forbid` always overrides `permit`. This is how you express "no one should ever do X" in a system where some users have wildcard access.

**Analyst**

| Scenario | Expected | Why |
|---|---|---|
| Query analytics database | ALLOWED | Analyst + analytics is in their allowed databases |
| Query production database | DENIED | Analyst restricted to analytics/reporting only |
| Generate a report | ALLOWED | Analyst has `generate_report` in their tool list |
| Manage users | DENIED | `manage_users` not in analyst's tool list |
| Delete records | DENIED | `delete_records` not in analyst's tool list |

The "analyst queries production" scenario shows argument-level restrictions. The analyst has `query_database` in their tool list — but the `.restrict("query_database", allowed_values={"database": ["analytics", "reporting"]}, for_role="analyst")` policy checks the `database` argument. Passing `database="production"` triggers the `forbid`. The tool call is blocked before the SQL executes.

**Viewer**

| Scenario | Expected | Why |
|---|---|---|
| Search dashboards | ALLOWED | Viewer can search |
| List tables | ALLOWED | Viewer can list tables |
| Query a database | DENIED | `query_database` not in viewer's tool list |
| Export data | DENIED | `export_csv` not in viewer's tool list |

**Unknown User (No Roles)**

| Scenario | Expected | Why |
|---|---|---|
| Search dashboards | DENIED | No roles = no permits = no access |

**Rate Limits** (export_csv, max 3/session)

| Call | Expected | Why |
|---|---|---|
| 1st export | ALLOWED | Under limit |
| 2nd export | ALLOWED | Under limit |
| 3rd export | ALLOWED | At limit |
| 4th export | DENIED | Exceeds max_per_session=3 |
| 5th export | DENIED | Still over limit |

</details>

<details>
<summary><h2>Appendix B: Generated Cedar Policies</h2></summary>

```cedar
// Admin: wildcard access
permit (
  principal in Role::"admin",
  action,
  resource
);

// Analyst: specific tools
permit (
  principal in Role::"analyst",
  action in [Action::"use_tool::query_database", Action::"use_tool::list_tables",
             Action::"use_tool::generate_report", Action::"use_tool::export_csv"],
  resource
);

// Viewer: read-only tools
permit (
  principal in Role::"viewer",
  action in [Action::"use_tool::search_dashboards", Action::"use_tool::list_tables"],
  resource
);

// Analyst: restrict query_database to analytics/reporting
forbid (
  principal in Role::"analyst",
  action == Action::"use_tool::query_database",
  resource
) when {
  !(context.database == "analytics" || context.database == "reporting")
};

// Global: nobody can query secrets (applies to all principals, including admin)
forbid (
  principal,
  action == Action::"use_tool::query_database",
  resource
) when {
  !(context.database == "analytics" || context.database == "reporting"
    || context.database == "production" || context.database == "staging")
};

// Rate limit: max 3 CSV exports per session
forbid (
  principal,
  action == Action::"use_tool::export_csv",
  resource
) when {
  context.session_call_count >= 3
};

// Environment: no destructive ops in production
forbid (
  principal,
  action in [Action::"use_tool::delete_records", Action::"use_tool::drop_table"],
  resource
) when {
  context.environment == "production"
};
```

</details>

<details>
<summary><h2>Appendix C: Database Permissions vs. Cedar</h2></summary>

| | Database permissions (Postgres roles, IAM) | Cedar policies |
|---|---|---|
| **Scope** | Controls what the DB connection can do | Controls what the agent can do on behalf of a user |
| **Granularity** | Per-connection or per-role | Per-user, per-tool, per-argument, per-session |
| **Connection model** | Requires separate DB user/connection per SaaS user | Agent uses one connection; Cedar gates access before the query runs |
| **Non-DB tools** | Doesn't cover `generate_report`, `export_csv`, `manage_users` | Covers all tools uniformly |
| **Rate limiting** | Not supported at the auth layer | Built-in: `max_per_session` on any tool |
| **Environment scoping** | Separate infrastructure per environment | Same agent, same tools — Cedar blocks by `context.environment` |
| **Audit** | Database query logs (no user attribution for shared connections) | Per-user, per-tool decision log with allow/deny |

The practical problem: if your agent uses a single Postgres connection (as most do), database roles don't help. Every query runs as the same DB user. Cedar solves this by checking *who asked* before the query reaches the database.

</details>

<details>
<summary><h2>Appendix D: Audit Log</h2></summary>

Every authorization decision is logged:

```
[ALLOW] User::"admin@acme.com"              query_database
[ALLOW] User::"admin@acme.com"              manage_users
[DENY ] User::"admin@acme.com"              query_database        (secrets DB)
[DENY ] User::"admin@acme.com"              delete_records        (production env)
[ALLOW] User::"alice@acme.com"              query_database        (analytics)
[DENY ] User::"alice@acme.com"              query_database        (production)
[DENY ] User::"alice@acme.com"              manage_users
[ALLOW] User::"bob@acme.com"                search_dashboards
[DENY ] User::"bob@acme.com"                query_database
[DENY ] User::"stranger@external.com"       search_dashboards
```

This log answers questions like:
- "Did any non-admin try to query the production database?"
- "How many exports did alice run this session?"
- "Who tried to access tools they don't have permissions for?"

</details>
