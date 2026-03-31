/**
 * Demo: Cedar guardrails for a multi-user SaaS AI agent.
 *
 * TypeScript port of the Python demo_saas.py — same tools, roles, and scenarios.
 *
 * Requires: npm install @cedar-policy/cedar-wasm @strands-agents/sdk zod
 *   + a model provider configured (default: Bedrock with Claude)
 */

// Polyfill for Node < 20 (crypto.randomUUID)
import crypto from "node:crypto";
if (!globalThis.crypto) (globalThis as any).crypto = crypto;

import { Agent, tool } from "@strands-agents/sdk";
import { z } from "zod";
import { CedarAuthPlugin } from "../cedar_auth_plugin.js";

// ---------------------------------------------------------------------------
// 1. Build the plugin
// ---------------------------------------------------------------------------

const plugin = CedarAuthPlugin.builder()
  // Roles
  .role("admin", { tools: ["*"] })
  .role("analyst", {
    tools: ["query_database", "list_tables", "generate_report", "export_csv"],
  })
  .role("viewer", { tools: ["search_dashboards", "list_tables"] })

  // Analysts can only query analytics and reporting databases
  .restrict("query_database", {
    allowedValues: { database: ["analytics", "reporting"] },
    forRole: "analyst",
  })

  // Nobody can query the secrets database — not even admins
  .restrict("query_database", {
    allowedValues: {
      database: ["analytics", "reporting", "production", "staging"],
    },
  })

  // Rate limit exports to prevent bulk data exfiltration
  .rateLimit("export_csv", { maxPerSession: 3 })

  // No destructive operations in production
  .denyToolsInEnv("production", ["delete_records", "drop_table"])

  .build();

// ---------------------------------------------------------------------------
// 2. Tools
// ---------------------------------------------------------------------------

const queryDatabase = tool({
  name: "query_database",
  description: "Execute a SQL query against a database.",
  inputSchema: z.object({
    database: z.string().describe("Database name"),
    query: z.string().describe("SQL query"),
  }),
  callback: (input) =>
    `[${input.database}] Results for: ${input.query.slice(0, 60)}... → 42 rows returned`,
});

const listTables = tool({
  name: "list_tables",
  description: "List all tables in a database.",
  inputSchema: z.object({
    database: z.string().describe("Database name"),
  }),
  callback: (input) => {
    const tables: Record<string, string> = {
      analytics: "users, events, sessions, page_views, conversions",
      reporting: "monthly_revenue, churn_rate, active_users, cohorts",
      production: "users, orders, payments, subscriptions, invoices",
      secrets: "api_keys, tokens, credentials, ssh_keys",
    };
    return `[${input.database}] Tables: ${tables[input.database] ?? "unknown database"}`;
  },
});

const generateReport = tool({
  name: "generate_report",
  description: "Generate a business report.",
  inputSchema: z.object({
    report_type: z.string().describe("Type of report"),
    date_range: z.string().describe("Date range for the report"),
  }),
  callback: (input) =>
    `Generated ${input.report_type} report for ${input.date_range}: revenue $1.2M, churn 3.2%, MRR growth 8%`,
});

const exportCsv = tool({
  name: "export_csv",
  description: "Export a table to CSV. Limited to prevent bulk exfiltration.",
  inputSchema: z.object({
    table: z.string().describe("Table name"),
    row_limit: z.number().describe("Maximum rows to export"),
  }),
  callback: (input) =>
    `Exported ${input.row_limit} rows from ${input.table} to /tmp/export_${input.table}.csv`,
});

const searchDashboards = tool({
  name: "search_dashboards",
  description: "Search available dashboards by keyword.",
  inputSchema: z.object({
    query: z.string().describe("Search query"),
  }),
  callback: (input) =>
    `Found 3 dashboards matching '${input.query}': Revenue Overview, User Growth, Churn Analysis`,
});

const deleteRecords = tool({
  name: "delete_records",
  description: "Delete records from a table matching a condition. Destructive.",
  inputSchema: z.object({
    table: z.string().describe("Table name"),
    condition: z.string().describe("WHERE condition"),
  }),
  callback: (input) =>
    `Deleted records from ${input.table} where ${input.condition}`,
});

const dropTable = tool({
  name: "drop_table",
  description: "Drop an entire table. Irreversible.",
  inputSchema: z.object({
    table: z.string().describe("Table name"),
  }),
  callback: (input) => `Dropped table ${input.table}`,
});

const manageUsers = tool({
  name: "manage_users",
  description: "Manage user accounts — create, update roles, deactivate.",
  inputSchema: z.object({
    action: z.string().describe("Action: create, update, deactivate"),
    user_email: z.string().describe("User email"),
    role: z.string().describe("Role to assign"),
  }),
  callback: (input) =>
    `User ${input.user_email}: ${input.action} with role=${input.role}`,
});

// ---------------------------------------------------------------------------
// 3. Agent
// ---------------------------------------------------------------------------

const agent = new Agent({
  plugins: [plugin],
  tools: [
    queryDatabase,
    listTables,
    generateReport,
    exportCsv,
    searchDashboards,
    deleteRecords,
    dropTable,
    manageUsers,
  ],
});

// ---------------------------------------------------------------------------
// 4. Scenarios
// ---------------------------------------------------------------------------

interface Scenario {
  title: string;
  user: string;
  roles: string[];
  message: string;
  expected: string;
  extraState?: Record<string, string>;
}

const scenarios: Scenario[] = [
  // --- Admin: full access ---
  {
    title: "Admin queries production database",
    user: "admin@acme.com",
    roles: ["admin"],
    message:
      "Query the production database: SELECT count(*) FROM orders WHERE status='pending'",
    expected: "ALLOWED — admin has wildcard access",
  },
  {
    title: "Admin manages a user account",
    user: "admin@acme.com",
    roles: ["admin"],
    message: "Deactivate user bob@acme.com and set their role to 'none'",
    expected: "ALLOWED — admin can manage users",
  },
  {
    title: "Admin queries secrets database",
    user: "admin@acme.com",
    roles: ["admin"],
    message: "Query the secrets database: SELECT * FROM api_keys",
    expected: "DENIED — nobody can query secrets, even admin (global restrict)",
  },
  {
    title: "Admin deletes records (production env)",
    user: "admin@acme.com",
    roles: ["admin"],
    message: "Delete records from users table where status='inactive'",
    expected: "DENIED — destructive ops blocked in production env",
    extraState: { environment: "production" },
  },
  {
    title: "Admin deletes records (staging env)",
    user: "admin@acme.com",
    roles: ["admin"],
    message: "Delete records from users table where status='inactive'",
    expected: "ALLOWED — staging is not restricted",
    extraState: { environment: "staging" },
  },

  // --- Analyst: scoped access ---
  {
    title: "Analyst queries analytics database",
    user: "alice@acme.com",
    roles: ["analyst"],
    message:
      "Query the analytics database: SELECT count(*) FROM events WHERE date > '2026-01-01'",
    expected: "ALLOWED — analyst can query analytics",
  },
  {
    title: "Analyst queries production database",
    user: "alice@acme.com",
    roles: ["analyst"],
    message:
      "Query the production database: SELECT * FROM payments LIMIT 100",
    expected: "DENIED — analyst restricted to analytics/reporting only",
  },
  {
    title: "Analyst generates a report",
    user: "alice@acme.com",
    roles: ["analyst"],
    message: "Generate a monthly revenue report for Q1 2026",
    expected: "ALLOWED — analyst can generate reports",
  },
  {
    title: "Analyst tries to manage users",
    user: "alice@acme.com",
    roles: ["analyst"],
    message: "Create a new user charlie@acme.com with role 'analyst'",
    expected: "DENIED — analyst doesn't have manage_users",
  },
  {
    title: "Analyst tries to delete records",
    user: "alice@acme.com",
    roles: ["analyst"],
    message: "Delete old records from the analytics events table",
    expected: "DENIED — analyst doesn't have delete_records",
  },

  // --- Viewer: read-only ---
  {
    title: "Viewer searches dashboards",
    user: "bob@acme.com",
    roles: ["viewer"],
    message: "Search for dashboards about revenue",
    expected: "ALLOWED — viewer can search dashboards",
  },
  {
    title: "Viewer lists tables",
    user: "bob@acme.com",
    roles: ["viewer"],
    message: "List all tables in the analytics database",
    expected: "ALLOWED — viewer can list tables",
  },
  {
    title: "Viewer tries to query a database",
    user: "bob@acme.com",
    roles: ["viewer"],
    message:
      "Query the analytics database: SELECT * FROM users LIMIT 10",
    expected: "DENIED — viewer can't run queries",
  },
  {
    title: "Viewer tries to export data",
    user: "bob@acme.com",
    roles: ["viewer"],
    message: "Export the users table to CSV",
    expected: "DENIED — viewer can't export",
  },

  // --- Unknown user: no access ---
  {
    title: "Unknown user tries to search dashboards",
    user: "stranger@external.com",
    roles: [],
    message: "Search for dashboards about revenue",
    expected: "DENIED — no roles = no access (deny-by-default)",
  },
];

async function demo() {
  console.log("=".repeat(70));
  console.log("Cedar Auth — Multi-User SaaS Agent Guardrails (TypeScript)");
  console.log("=".repeat(70));
  console.log();
  console.log("Generated Cedar policies:");
  console.log("-".repeat(70));
  console.log(plugin._policies);
  console.log("-".repeat(70));

  for (const s of scenarios) {
    const env = s.extraState?.environment ?? "";
    const envLabel = env ? ` [env=${env}]` : "";

    console.log(`\n${"─".repeat(70)}`);
    console.log(`  ${s.title}`);
    console.log(
      `  User: ${s.user} | Roles: [${s.roles.join(", ")}]${envLabel}`,
    );
    console.log(`  Prompt: "${s.message}"`);
    console.log(`  Expected: ${s.expected}`);
    console.log("─".repeat(70));

    // Set per-scenario state
    agent.appState.set("user_id", s.user);
    agent.appState.set("roles", s.roles);
    if (s.extraState) {
      for (const [k, v] of Object.entries(s.extraState)) {
        agent.appState.set(k, v);
      }
    } else {
      agent.appState.delete("environment");
    }

    try {
      const result = await agent.invoke(s.message);
      console.log(`  Agent: ${result}`);
    } catch (e) {
      console.log(`  Error: ${e}`);
    }
  }

  // --- Rate limits ---
  console.log(`\n${"=".repeat(70)}`);
  console.log("Rate Limit Tests (export_csv, max 3 per session)");
  console.log("Prevents bulk data exfiltration even for authorized users");
  console.log("=".repeat(70));

  plugin._callCounts = {};
  for (let i = 1; i <= 5; i++) {
    const expected =
      i <= 3 ? "ALLOWED" : "DENIED — rate limit exceeded";

    console.log(`\n${"─".repeat(70)}`);
    console.log(`  export_csv call ${i}/3`);
    console.log(`  User: alice@acme.com | Role: analyst`);
    console.log(`  Expected: ${expected}`);
    console.log("─".repeat(70));

    agent.appState.set("user_id", "alice@acme.com");
    agent.appState.set("roles", ["analyst"]);
    agent.appState.set("session_id", "analysis-run-1");
    agent.appState.delete("environment");

    try {
      const result = await agent.invoke(
        `Export the events table, limit ${i * 1000} rows`,
      );
      console.log(`  Agent: ${result}`);
    } catch (e) {
      console.log(`  Error: ${e}`);
    }
  }

  // Audit log
  console.log(`\n${"=".repeat(70)}`);
  console.log("Audit Log");
  console.log("=".repeat(70));
  for (const entry of plugin.auditLog) {
    const status = entry.allowed ? "ALLOW" : "DENY ";
    console.log(
      `  [${status}] ${entry.principal.padEnd(40)} ${entry.toolName}`,
    );
  }
}

demo().catch(console.error);
