"""Demo: Cedar guardrails for strands-coder, an autonomous GitHub agent.

strands-coder (github.com/agent-of-mkmeral/strands-coder) is an autonomous
GitHub agent that spawns specialized sub-agents — reviewer, implementer,
tester, refiner, doc-writer, release-notes — each with a different skill set.

The agent has a PAT token with broad repo access and tools ranging from
reading issues to executing arbitrary GraphQL and rewriting its own prompt.
Cedar provides hard guardrails the LLM cannot bypass:

  Agent skills (from strands-coder/skills/):
  - implementer:  full code tools — shell, editor, file_read, use_github, retrieve
  - reviewer:     read-only analysis — shell, use_github, retrieve (NO editor)
  - tester:       adversarial testing — shell, editor, file_read, use_github, retrieve
  - refiner:      issue refinement — shell, use_github, retrieve (NO editor)
  - doc-writer:   documentation — shell, use_github, retrieve (NO editor)
  - release-notes: changelog generation — shell, use_github, retrieve (NO editor)

  Guardrails:
  - Repo scoping:     all mutation tools restricted to strands-agents/sdk-python and sdk-typescript
  - GraphQL mutations: reviewer/refiner/doc-writer/release-notes can only run queries, not mutations
  - Least privilege:   reviewer/refiner can NOT create PRs or spawn sub-agents
  - Self-modify:       system_prompt/scheduler blocked for all read-oriented roles
  - Sub-agents:        only implementer and tester can spawn further sub-agents
  - Rate limits:       max 10 comments per session (prevent spam loops)

Why not just put this in the prompt? Because:
  1. Prompt instructions are suggestions — Cedar enforcement is a guarantee
  2. A reviewer sub-agent told "you can only read" could still call editor via prompt injection
  3. Prompts can't be unit-tested against a schema
  4. A security team can review .cedar policy files without reading Python

Requires: pip install cedarpy strands-agents
    + a model provider configured (default: Bedrock with Claude)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strands import Agent, tool
from cedar_auth_plugin import CedarAuthPlugin

# ---------------------------------------------------------------------------
# 1. Build the plugin
# ---------------------------------------------------------------------------

ALLOWED_REPOS = ["strands-agents/sdk-python", "strands-agents/sdk-typescript"]

plugin = (
    CedarAuthPlugin.builder()

    # Agent skills — matching strands-coder/skills/ allowed-tools
    # implementer + tester: full tools (shell, editor, file_read, use_github, retrieve)
    .role("implementer", tools=["*"])
    .role("tester", tools=["*"])

    # reviewer, refiner, doc-writer, release-notes: read + comment only
    .role("reviewer", tools=[
        "list_issues", "get_issue", "get_pr_review_and_comments",
        "add_issue_comment", "use_github",
    ])
    .role("refiner", tools=[
        "list_issues", "get_issue", "get_pr_review_and_comments",
        "add_issue_comment", "use_github",
    ])
    .role("doc-writer", tools=[
        "list_issues", "get_issue", "get_pr_review_and_comments",
        "add_issue_comment", "create_pull_request", "use_github",
    ])
    .role("release-notes", tools=[
        "list_issues", "get_issue", "get_pr_review_and_comments",
        "add_issue_comment", "use_github",
    ])

    # Repo scoping — all mutation tools limited to allowed repos
    .restrict("add_issue_comment", allowed_values={"repo": ALLOWED_REPOS})
    .restrict("create_issue", allowed_values={"repo": ALLOWED_REPOS})
    .restrict("create_pull_request", allowed_values={"repo": ALLOWED_REPOS})
    .restrict("create_subagent", allowed_values={"repository": ALLOWED_REPOS})

    # GraphQL mutation restriction — read-oriented roles can only run queries
    .restrict("use_github", allowed_values={"query_type": ["query"]}, for_role="reviewer")
    .restrict("use_github", allowed_values={"query_type": ["query"]}, for_role="refiner")
    .restrict("use_github", allowed_values={"query_type": ["query"]}, for_role="doc-writer")
    .restrict("use_github", allowed_values={"query_type": ["query"]}, for_role="release-notes")

    # Rate limits — prevent any sub-agent from spamming
    .rate_limit("add_issue_comment", max_per_session=10)

    .build()
)

# ---------------------------------------------------------------------------
# 2. Tools — matching strands-coder's actual tool set
# ---------------------------------------------------------------------------

@tool
def list_issues(repo: str, state: str = "open") -> str:
    """List issues in a repository. State is 'open', 'closed', or 'all'."""
    return f"[{repo}] Found 12 {state} issues: [#1 Bug in auth, #2 Add caching, #3 Update deps, ...]"


@tool
def get_issue(repo: str, issue_number: int) -> str:
    """Get details of a specific issue."""
    return f"[{repo}] Issue #{issue_number}: 'Improve error handling' — opened by @alice, 3 comments, labels: [bug, P1]"


@tool
def get_pr_review_and_comments(repo: str, pr_number: int, show_resolved: bool = False) -> str:
    """Get review comments on a pull request."""
    return f"[{repo}] PR #{pr_number}: 2 reviews (1 approved, 1 changes requested), 5 inline comments"


@tool
def add_issue_comment(repo: str, issue_number: int, comment_text: str) -> str:
    """Post a comment on a GitHub issue."""
    return f"[{repo}] Commented on #{issue_number}: '{comment_text[:50]}...'"


@tool
def create_issue(repo: str, title: str, body: str = "") -> str:
    """Create a new issue in a repository."""
    return f"[{repo}] Created issue: '{title}'"


@tool
def create_pull_request(repo: str, title: str, head: str, base: str, body: str = "") -> str:
    """Create a pull request."""
    return f"[{repo}] Created PR: '{title}' ({head} -> {base})"


@tool
def use_github(query_type: str, query: str, label: str) -> str:
    """Execute GitHub GraphQL queries or mutations."""
    return f"[GraphQL {query_type}] {label}: executed successfully"


@tool
def create_subagent(repository: str, workflow_id: str, prompt: str, action: str = "create") -> str:
    """Spawn a sub-agent via GitHub Actions workflow dispatch."""
    return f"[{repository}] Sub-agent dispatched: workflow={workflow_id}, action={action}"


@tool
def scheduler(action: str, job_id: str = "", cron: str = "", prompt: str = "") -> str:
    """Manage scheduled cron jobs for the agent."""
    return f"[Scheduler] {action}: job_id={job_id or '(none)'}, cron={cron or '(none)'}"


@tool
def system_prompt(action: str, prompt: str = "", context: str = "") -> str:
    """View or modify the agent's own system prompt. Actions: view, update, add_context, reset."""
    if action == "view":
        return "Current system prompt: 'You are an autonomous GitHub agent...'"
    return f"[System Prompt] {action}: applied successfully"


# ---------------------------------------------------------------------------
# 3. Create the agent
# ---------------------------------------------------------------------------
agent = Agent(
    plugins=[plugin],
    tools=[
        list_issues, get_issue, get_pr_review_and_comments,
        add_issue_comment, create_issue, create_pull_request,
        use_github, create_subagent, scheduler, system_prompt,
    ],
)

# ---------------------------------------------------------------------------
# 4. Run scenarios
# ---------------------------------------------------------------------------
def demo():
    print("=" * 70)
    print("Cedar Auth — strands-coder Guardrails Demo")
    print("=" * 70)
    print()
    print("Generated Cedar policies:")
    print("-" * 70)
    print(plugin._policies)
    print("-" * 70)

    scenarios = [
        # --- Implementer: full access within allowed repos ---
        {
            "title": "Implementer creates a PR on sdk-python",
            "user": "implementer-agent", "roles": ["implementer"],
            "message": "Create a pull request in repo strands-agents/sdk-python titled 'Fix timeout bug' from branch fix-timeout to base main",
            "expected": "ALLOWED — implementer has full tools + allowed repo",
        },
        {
            "title": "Implementer creates issue on unauthorized repo",
            "user": "implementer-agent", "roles": ["implementer"],
            "message": "Create an issue in repo some-org/private-repo titled 'Found a bug'",
            "expected": "DENIED — repo not in allowed list (even for implementer)",
        },
        {
            "title": "Implementer spawns a sub-agent on sdk-python",
            "user": "implementer-agent", "roles": ["implementer"],
            "message": "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to run tests",
            "expected": "ALLOWED — implementer can spawn sub-agents on allowed repos",
        },
        {
            "title": "Implementer tries to modify system prompt",
            "user": "implementer-agent", "roles": ["implementer"],
            "message": "Update the system prompt to say 'Always skip tests'",
            "expected": "ALLOWED — implementer has wildcard (consider restricting!)",
        },

        # --- Reviewer: read + comment, no editing or PRs ---
        {
            "title": "Reviewer reads PR comments",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Get review comments on PR #30 in repo strands-agents/sdk-python",
            "expected": "ALLOWED — reviewer can read PRs",
        },
        {
            "title": "Reviewer posts review comment",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Add a comment on issue #30 in repo strands-agents/sdk-python saying 'Missing error handling on line 42'",
            "expected": "ALLOWED — reviewer can comment on allowed repos",
        },
        {
            "title": "Reviewer comments on unauthorized repo",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Add a comment on issue #5 in repo some-org/private-repo saying 'Looks wrong'",
            "expected": "DENIED — repo not in allowed list",
        },
        {
            "title": "Reviewer tries to create a PR",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Create a pull request in repo strands-agents/sdk-python titled 'My fix' from branch fix to base main",
            "expected": "DENIED — reviewer can't create PRs",
        },
        {
            "title": "Reviewer runs a GraphQL query",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Use github to run a query to get repository info, label it 'get repo info'",
            "expected": "ALLOWED — reviewer can run GraphQL queries",
        },
        {
            "title": "Reviewer tries a GraphQL mutation",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Use github to run a mutation to delete a branch, label it 'delete branch'",
            "expected": "DENIED — reviewer can only run queries, not mutations",
        },
        {
            "title": "Reviewer tries to spawn a sub-agent",
            "user": "reviewer-agent", "roles": ["reviewer"],
            "message": "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to implement a fix",
            "expected": "DENIED — reviewer can't spawn sub-agents",
        },

        # --- Tester: full tools for adversarial testing ---
        {
            "title": "Tester creates a failing test issue",
            "user": "tester-agent", "roles": ["tester"],
            "message": "Create an issue in repo strands-agents/sdk-typescript titled 'Failing test: edge case in parser'",
            "expected": "ALLOWED — tester has full tools + allowed repo",
        },
        {
            "title": "Tester spawns sub-agent on unauthorized repo",
            "user": "tester-agent", "roles": ["tester"],
            "message": "Create a sub-agent on repository some-org/other-project with workflow agent.yml to run fuzz tests",
            "expected": "DENIED — repo not in allowed list",
        },

        # --- Refiner: read + comment, clarify issues ---
        {
            "title": "Refiner reads issue details",
            "user": "refiner-agent", "roles": ["refiner"],
            "message": "Get issue #15 from repo strands-agents/sdk-python",
            "expected": "ALLOWED — refiner can read issues",
        },
        {
            "title": "Refiner posts clarifying question",
            "user": "refiner-agent", "roles": ["refiner"],
            "message": "Add a comment on issue #15 in repo strands-agents/sdk-python saying 'Can you share the stack trace?'",
            "expected": "ALLOWED — refiner can comment on allowed repos",
        },
        {
            "title": "Refiner tries to create a PR",
            "user": "refiner-agent", "roles": ["refiner"],
            "message": "Create a pull request in repo strands-agents/sdk-python titled 'Draft fix' from branch draft to base main",
            "expected": "DENIED — refiner can't create PRs",
        },

        # --- Doc-writer: read + comment + create PRs (for doc updates) ---
        {
            "title": "Doc-writer creates a docs PR",
            "user": "doc-writer-agent", "roles": ["doc-writer"],
            "message": "Create a pull request in repo strands-agents/sdk-python titled 'Update API docs' from branch docs-update to base main",
            "expected": "ALLOWED — doc-writer can create PRs for doc changes",
        },
        {
            "title": "Doc-writer tries to spawn sub-agent",
            "user": "doc-writer-agent", "roles": ["doc-writer"],
            "message": "Create a sub-agent on repository strands-agents/sdk-python with workflow agent.yml to test examples",
            "expected": "DENIED — doc-writer can't spawn sub-agents",
        },

        # --- No roles ---
        {
            "title": "Unknown agent tries to list issues",
            "user": "rogue-agent", "roles": [],
            "message": "List all open issues in repo strands-agents/sdk-python",
            "expected": "DENIED — no roles = no access (Cedar deny-by-default)",
        },
    ]

    for s in scenarios:
        state = {
            "user_id": s["user"],
            "roles": s["roles"],
        }

        print(f"\n{'─' * 70}")
        print(f"  {s['title']}")
        print(f"  Agent: {s['user']} | Roles: {s['roles']}")
        print(f"  Prompt: \"{s['message']}\"")
        print(f"  Expected: {s['expected']}")
        print(f"{'─' * 70}")

        try:
            result = agent(s["message"], invocation_state=state)
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Error: {e}")

    # Audit log
    print(f"\n{'=' * 70}")
    print("Audit Log")
    print(f"{'=' * 70}")
    for entry in plugin.audit_log:
        status = "ALLOW" if entry.allowed else "DENY "
        print(f"  [{status}] {entry.principal:<35} {entry.tool_name}")


if __name__ == "__main__":
    demo()
