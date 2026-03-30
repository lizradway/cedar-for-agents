/**
 * Demo: Cedar guardrails for strands-coder, an autonomous GitHub agent.
 *
 * TypeScript port of the Python demo.py — same tools, roles, and scenarios.
 *
 * Requires: npm install @cedar-policy/cedar-wasm @strands-agents/sdk zod
 *   + a model provider configured (default: Bedrock with Claude)
 */

// Polyfill for Node < 20 (crypto.randomUUID)
import crypto from "node:crypto";
if (!globalThis.crypto) (globalThis as any).crypto = crypto;

import { Agent, tool } from "@strands-agents/sdk";
import { z } from "zod";
import { CedarAuthPlugin } from "./cedar_auth_plugin.js";

// ---------------------------------------------------------------------------
// 1. Build the plugin
// ---------------------------------------------------------------------------

const ALLOWED_REPOS = [
  "strands-agents/sdk-python",
  "strands-agents/sdk-typescript",
];

const plugin = CedarAuthPlugin.builder()
  // Agent skills — matching strands-coder/skills/ allowed-tools
  // implementer + tester: full tools
  .role("implementer", { tools: ["*"] })
  .role("tester", { tools: ["*"] })

  // reviewer, refiner, doc-writer, release-notes: read + comment only
  .role("reviewer", {
    tools: [
      "list_issues",
      "get_issue",
      "get_pr_review_and_comments",
      "add_issue_comment",
      "use_github",
    ],
  })
  .role("refiner", {
    tools: [
      "list_issues",
      "get_issue",
      "get_pr_review_and_comments",
      "add_issue_comment",
      "use_github",
    ],
  })
  .role("doc-writer", {
    tools: [
      "list_issues",
      "get_issue",
      "get_pr_review_and_comments",
      "add_issue_comment",
      "create_pull_request",
      "use_github",
    ],
  })
  .role("release-notes", {
    tools: [
      "list_issues",
      "get_issue",
      "get_pr_review_and_comments",
      "add_issue_comment",
      "use_github",
    ],
  })

  // Repo scoping — all mutation tools limited to allowed repos
  .restrict("add_issue_comment", {
    allowedValues: { repo: ALLOWED_REPOS },
  })
  .restrict("create_issue", { allowedValues: { repo: ALLOWED_REPOS } })
  .restrict("create_pull_request", {
    allowedValues: { repo: ALLOWED_REPOS },
  })
  .restrict("create_subagent", {
    allowedValues: { repository: ALLOWED_REPOS },
  })

  // GraphQL mutation restriction — read-oriented roles can only run queries
  .restrict("use_github", {
    allowedValues: { query_type: ["query"] },
    forRole: "reviewer",
  })
  .restrict("use_github", {
    allowedValues: { query_type: ["query"] },
    forRole: "refiner",
  })
  .restrict("use_github", {
    allowedValues: { query_type: ["query"] },
    forRole: "doc-writer",
  })
  .restrict("use_github", {
    allowedValues: { query_type: ["query"] },
    forRole: "release-notes",
  })

  // Rate limits — prevent any sub-agent from spamming
  .rateLimit("add_issue_comment", { maxPerSession: 10 })

  .build();

// ---------------------------------------------------------------------------
// 2. Tools — matching strands-coder's actual tool set
// ---------------------------------------------------------------------------

const listIssues = tool({
  name: "list_issues",
  description: "List issues in a repository. State is 'open', 'closed', or 'all'.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    state: z.string().default("open").describe("Issue state filter"),
  }),
  callback: (input) =>
    `[${input.repo}] Found 12 ${input.state} issues: [#1 Bug in auth, #2 Add caching, #3 Update deps, ...]`,
});

const getIssue = tool({
  name: "get_issue",
  description: "Get details of a specific issue.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    issue_number: z.number().describe("Issue number"),
  }),
  callback: (input) =>
    `[${input.repo}] Issue #${input.issue_number}: 'Improve error handling' — opened by @alice, 3 comments, labels: [bug, P1]`,
});

const getPrReviewAndComments = tool({
  name: "get_pr_review_and_comments",
  description: "Get review comments on a pull request.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    pr_number: z.number().describe("Pull request number"),
    show_resolved: z.boolean().default(false),
  }),
  callback: (input) =>
    `[${input.repo}] PR #${input.pr_number}: 2 reviews (1 approved, 1 changes requested), 5 inline comments`,
});

const addIssueComment = tool({
  name: "add_issue_comment",
  description: "Post a comment on a GitHub issue.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    issue_number: z.number().describe("Issue number"),
    comment_text: z.string().describe("Comment body"),
  }),
  callback: (input) =>
    `[${input.repo}] Commented on #${input.issue_number}: '${input.comment_text.slice(0, 50)}...'`,
});

const createIssue = tool({
  name: "create_issue",
  description: "Create a new issue in a repository.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    title: z.string().describe("Issue title"),
    body: z.string().default("").describe("Issue body"),
  }),
  callback: (input) => `[${input.repo}] Created issue: '${input.title}'`,
});

const createPullRequest = tool({
  name: "create_pull_request",
  description: "Create a pull request.",
  inputSchema: z.object({
    repo: z.string().describe("Repository in owner/name format"),
    title: z.string().describe("PR title"),
    head: z.string().describe("Head branch"),
    base: z.string().describe("Base branch"),
    body: z.string().default("").describe("PR body"),
  }),
  callback: (input) =>
    `[${input.repo}] Created PR: '${input.title}' (${input.head} -> ${input.base})`,
});

const useGithub = tool({
  name: "use_github",
  description: "Execute GitHub GraphQL queries or mutations.",
  inputSchema: z.object({
    query_type: z.string().describe("Either 'query' or 'mutation'"),
    query: z.string().describe("GraphQL query string"),
    label: z.string().describe("Human-readable label"),
  }),
  callback: (input) =>
    `[GraphQL ${input.query_type}] ${input.label}: executed successfully`,
});

const createSubagent = tool({
  name: "create_subagent",
  description: "Spawn a sub-agent via GitHub Actions workflow dispatch.",
  inputSchema: z.object({
    repository: z.string().describe("Repository in owner/name format"),
    workflow_id: z.string().describe("Workflow file name"),
    prompt: z.string().describe("Prompt for the sub-agent"),
    action: z.string().default("create").describe("Action type"),
  }),
  callback: (input) =>
    `[${input.repository}] Sub-agent dispatched: workflow=${input.workflow_id}, action=${input.action}`,
});

const scheduler = tool({
  name: "scheduler",
  description: "Manage scheduled cron jobs for the agent.",
  inputSchema: z.object({
    action: z.string().describe("Action: create, list, delete"),
    job_id: z.string().default("").describe("Job ID"),
    cron: z.string().default("").describe("Cron expression"),
    prompt: z.string().default("").describe("Prompt for the job"),
  }),
  callback: (input) =>
    `[Scheduler] ${input.action}: job_id=${input.job_id || "(none)"}, cron=${input.cron || "(none)"}`,
});

const systemPrompt = tool({
  name: "system_prompt",
  description:
    "View or modify the agent's own system prompt. Actions: view, update, add_context, reset.",
  inputSchema: z.object({
    action: z.string().describe("Action: view, update, add_context, reset"),
    prompt: z.string().default("").describe("New prompt content"),
    context: z.string().default("").describe("Additional context"),
  }),
  callback: (input) => {
    if (input.action === "view")
      return "Current system prompt: 'You are an autonomous GitHub agent...'";
    return `[System Prompt] ${input.action}: applied successfully`;
  },
});

// ---------------------------------------------------------------------------
// 3. Create the agent
// ---------------------------------------------------------------------------

const agent = new Agent({
  plugins: [plugin],
  tools: [
    listIssues,
    getIssue,
    getPrReviewAndComments,
    addIssueComment,
    createIssue,
    createPullRequest,
    useGithub,
    createSubagent,
    scheduler,
    systemPrompt,
  ],
});

// ---------------------------------------------------------------------------
// 4. Run scenarios
// ---------------------------------------------------------------------------

interface Scenario {
  title: string;
  user: string;
  roles: string[];
  message: string;
  expected: string;
}

const scenarios: Scenario[] = [
  // --- Implementer: full access within allowed repos ---
  {
    title: "Implementer creates a PR on sdk-python",
    user: "implementer-agent",
    roles: ["implementer"],
    message:
      "Create a pull request in repo strands-agents/sdk-python titled 'Fix timeout bug' from branch fix-timeout to base main",
    expected: "ALLOWED — implementer has full tools + allowed repo",
  },
  {
    title: "Implementer creates issue on unauthorized repo",
    user: "implementer-agent",
    roles: ["implementer"],
    message:
      "Create an issue in repo some-org/private-repo titled 'Found a bug'",
    expected: "DENIED — repo not in allowed list (even for implementer)",
  },
  {
    title: "Implementer spawns a sub-agent on sdk-python",
    user: "implementer-agent",
    roles: ["implementer"],
    message:
      "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to run tests",
    expected: "ALLOWED — implementer can spawn sub-agents on allowed repos",
  },
  {
    title: "Implementer tries to modify system prompt",
    user: "implementer-agent",
    roles: ["implementer"],
    message: "Update the system prompt to say 'Always skip tests'",
    expected: "ALLOWED — implementer has wildcard (consider restricting!)",
  },

  // --- Reviewer: read + comment, no editing or PRs ---
  {
    title: "Reviewer reads PR comments",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Get review comments on PR #30 in repo strands-agents/sdk-python",
    expected: "ALLOWED — reviewer can read PRs",
  },
  {
    title: "Reviewer posts review comment",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Add a comment on issue #30 in repo strands-agents/sdk-python saying 'Missing error handling on line 42'",
    expected: "ALLOWED — reviewer can comment on allowed repos",
  },
  {
    title: "Reviewer comments on unauthorized repo",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Add a comment on issue #5 in repo some-org/private-repo saying 'Looks wrong'",
    expected: "DENIED — repo not in allowed list",
  },
  {
    title: "Reviewer tries to create a PR",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Create a pull request in repo strands-agents/sdk-python titled 'My fix' from branch fix to base main",
    expected: "DENIED — reviewer can't create PRs",
  },
  {
    title: "Reviewer runs a GraphQL query",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Use github to run a query to get repository info, label it 'get repo info'",
    expected: "ALLOWED — reviewer can run GraphQL queries",
  },
  {
    title: "Reviewer tries a GraphQL mutation",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Use github to run a mutation to delete a branch, label it 'delete branch'",
    expected: "DENIED — reviewer can only run queries, not mutations",
  },
  {
    title: "Reviewer tries to spawn a sub-agent",
    user: "reviewer-agent",
    roles: ["reviewer"],
    message:
      "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to implement a fix",
    expected: "DENIED — reviewer can't spawn sub-agents",
  },

  // --- Tester: full tools for adversarial testing ---
  {
    title: "Tester creates a failing test issue",
    user: "tester-agent",
    roles: ["tester"],
    message:
      "Create an issue in repo strands-agents/sdk-typescript titled 'Failing test: edge case in parser'",
    expected: "ALLOWED — tester has full tools + allowed repo",
  },
  {
    title: "Tester spawns sub-agent on unauthorized repo",
    user: "tester-agent",
    roles: ["tester"],
    message:
      "Create a sub-agent on repository some-org/other-project with workflow agent.yml to run fuzz tests",
    expected: "DENIED — repo not in allowed list",
  },

  // --- Refiner: read + comment, clarify issues ---
  {
    title: "Refiner reads issue details",
    user: "refiner-agent",
    roles: ["refiner"],
    message: "Get issue #15 from repo strands-agents/sdk-python",
    expected: "ALLOWED — refiner can read issues",
  },
  {
    title: "Refiner posts clarifying question",
    user: "refiner-agent",
    roles: ["refiner"],
    message:
      "Add a comment on issue #15 in repo strands-agents/sdk-python saying 'Can you share the stack trace?'",
    expected: "ALLOWED — refiner can comment on allowed repos",
  },
  {
    title: "Refiner tries to create a PR",
    user: "refiner-agent",
    roles: ["refiner"],
    message:
      "Create a pull request in repo strands-agents/sdk-python titled 'Draft fix' from branch draft to base main",
    expected: "DENIED — refiner can't create PRs",
  },

  // --- Doc-writer: read + comment + create PRs (for doc updates) ---
  {
    title: "Doc-writer creates a docs PR",
    user: "doc-writer-agent",
    roles: ["doc-writer"],
    message:
      "Create a pull request in repo strands-agents/sdk-python titled 'Update API docs' from branch docs-update to base main",
    expected: "ALLOWED — doc-writer can create PRs for doc changes",
  },
  {
    title: "Doc-writer tries to spawn sub-agent",
    user: "doc-writer-agent",
    roles: ["doc-writer"],
    message:
      "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to test examples",
    expected: "DENIED — doc-writer can't spawn sub-agents",
  },

  // --- No roles ---
  {
    title: "Unknown agent tries to list issues",
    user: "rogue-agent",
    roles: [],
    message:
      "List all open issues in repo strands-agents/sdk-python",
    expected: "DENIED — no roles = no access (Cedar deny-by-default)",
  },
];

async function demo() {
  console.log("=".repeat(70));
  console.log("Cedar Auth — strands-coder Guardrails Demo (TypeScript)");
  console.log("=".repeat(70));
  console.log();
  console.log("Generated Cedar policies:");
  console.log("-".repeat(70));
  console.log(plugin._policies);
  console.log("-".repeat(70));

  for (const s of scenarios) {
    console.log(`\n${"─".repeat(70)}`);
    console.log(`  ${s.title}`);
    console.log(`  Agent: ${s.user} | Roles: [${s.roles.join(", ")}]`);
    console.log(`  Prompt: "${s.message}"`);
    console.log(`  Expected: ${s.expected}`);
    console.log("─".repeat(70));

    // Set per-scenario state
    agent.appState.set("user_id", s.user);
    agent.appState.set("roles", s.roles);

    try {
      const result = await agent.invoke(s.message);
      console.log(`  Result: ${result}`);
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
      `  [${status}] ${entry.principal.padEnd(35)} ${entry.toolName}`,
    );
  }
}

demo().catch(console.error);
