/**
 * Intervention Primitive — a unified abstraction for agent control.
 *
 * "Intervention" is the primitive. Cedar auth, LLM steering, content guardrails,
 * and operational controls all implement InterventionHandler — the shared interface.
 * An InterventionPipeline composes them with ordering and conflict resolution.
 *
 * Architecture:
 *   InterventionHandler  (the shared interface)
 *     ├── CedarAuthHandler           (sub-ms, Cedar WASM policy evaluation)
 *     ├── OperationalControlHandler   (sub-ms, rate limits + env gating)
 *     ├── ContentGuardrailHandler     (sub-ms, regex pattern matching)
 *     └── MockLLMSteeringHandler      (simulates Strands LLMSteeringHandler)
 *
 *   InterventionPipeline  (composes handlers → evaluates in order)
 *
 *   This is an alternative approach we explored. Today, each concern is a
 *   separate plugin with no shared interface. If Strands made interventions
 *   first-class, this simplifies to:
 *     Agent({ tools: [...], interventions: [cedar, guardrails, steering] })
 *
 * The MockLLMSteeringHandler uses deterministic rules because the Strands TS SDK
 * doesn't have steering yet. The Python demo uses the real LLMSteeringHandler
 * making actual LLM calls — see python/strands-cedar-auth/demos/intervention_pipeline.py.
 *
 * See docs/INTERVENTION_EXPLORATION.md for the full design rationale.
 *
 * Demo usage:
 *   npx tsx intervention.ts
 */

import * as cedar from "@cedar-policy/cedar-wasm/nodejs";

// ============================================================================
// 1. Core Types
// ============================================================================

/** What the agent is about to do (or just did). */
export interface InterventionContext {
  // Identity
  principal?: { type: string; id: string; roles: string[] };

  // Current tool call
  tool?: { name: string; input: Record<string, unknown> };

  // Model output (for model-level intervention)
  modelResponse?: { content: string; stopReason: string };

  // History of previous tool calls this session
  toolHistory: Array<{
    name: string;
    input: unknown;
    output: unknown;
    durationMs: number;
  }>;

  // Environment
  environment?: string;
  timestamp: Date;

  // Session-scoped metadata (counters, flags, etc.)
  session: Record<string, unknown>;
}

/** The four possible outcomes of an intervention evaluation. */
export type InterventionAction =
  | { type: "proceed" }
  | { type: "guide"; feedback: string } // cancel + retry with guidance
  | { type: "deny"; reason: string } // hard block, no retry
  | { type: "interrupt"; prompt: string }; // pause for human input

/** Audit record emitted by every handler on every evaluation. */
export interface InterventionRecord {
  handler: string;
  timestamp: string;
  action: InterventionAction;
  toolName?: string;
  principal?: string;
  detail?: unknown; // handler-specific (policy IDs, LLM reasoning, rule matches)
}

// ============================================================================
// 2. Handler Interface — the primitive that all handlers implement
// ============================================================================

/**
 * An InterventionHandler evaluates context and returns an action.
 *
 * Handlers are engine-agnostic — Cedar policies, LLM judges, pattern matchers,
 * and simple rule checks all implement this same interface.
 *
 * In the Python demo, Strands' real LLMSteeringHandler is wrapped in an adapter
 * that implements this interface. If Strands made InterventionHandler native,
 * LLMSteeringHandler would implement it directly.
 */
export interface InterventionHandler {
  readonly name: string;

  /** Evaluate before a tool call. Return undefined to skip (equivalent to proceed). */
  evaluateToolCall?(ctx: InterventionContext): InterventionAction | undefined;

  /** Evaluate after a model response. Return undefined to skip. */
  evaluateModelResponse?(
    ctx: InterventionContext,
  ): InterventionAction | undefined;
}

// ============================================================================
// 3. Pipeline — composes handlers with ordering and conflict resolution
// ============================================================================

/**
 * Evaluates handlers in registration order (cheapest/most-deterministic first).
 *
 * Conflict resolution:
 *   - Any Deny    → final Deny (short-circuits, skips remaining handlers)
 *   - Any Interrupt → Interrupt (if no deny)
 *   - Any Guide   → Guide with accumulated feedback
 *   - Otherwise   → Proceed
 *
 * If Strands made interventions first-class, this logic would live in the
 * framework and users wouldn't need to build it themselves.
 */
export class InterventionPipeline {
  private handlers: InterventionHandler[] = [];
  private _auditLog: InterventionRecord[] = [];

  add(handler: InterventionHandler): this {
    this.handlers.push(handler);
    return this;
  }

  get auditLog(): InterventionRecord[] {
    return [...this._auditLog];
  }

  evaluateToolCall(ctx: InterventionContext): InterventionAction {
    const actions: Array<{ handler: string; action: InterventionAction }> = [];

    for (const h of this.handlers) {
      if (!h.evaluateToolCall) continue;
      const action = h.evaluateToolCall(ctx);
      if (!action || action.type === "proceed") {
        this.log(h.name, { type: "proceed" }, ctx);
        continue;
      }

      this.log(h.name, action, ctx);
      actions.push({ handler: h.name, action });

      // Short-circuit on deny — no point evaluating further
      if (action.type === "deny") {
        return action;
      }
    }

    return this.resolve(actions);
  }

  evaluateModelResponse(ctx: InterventionContext): InterventionAction {
    const actions: Array<{ handler: string; action: InterventionAction }> = [];

    for (const h of this.handlers) {
      if (!h.evaluateModelResponse) continue;
      const action = h.evaluateModelResponse(ctx);
      if (!action || action.type === "proceed") {
        this.log(h.name, { type: "proceed" }, ctx);
        continue;
      }

      this.log(h.name, action, ctx);
      actions.push({ handler: h.name, action });

      if (action.type === "deny") return action;
    }

    return this.resolve(actions);
  }

  private resolve(
    actions: Array<{ handler: string; action: InterventionAction }>,
  ): InterventionAction {
    if (actions.length === 0) return { type: "proceed" };

    const deny = actions.find((a) => a.action.type === "deny");
    if (deny) return deny.action;

    const interrupt = actions.find((a) => a.action.type === "interrupt");
    if (interrupt) return interrupt.action;

    const guides = actions.filter((a) => a.action.type === "guide");
    if (guides.length > 0) {
      const feedback = guides
        .map(
          (g) =>
            `[${g.handler}] ${(g.action as { type: "guide"; feedback: string }).feedback}`,
        )
        .join("\n");
      return { type: "guide", feedback };
    }

    return { type: "proceed" };
  }

  private log(
    handler: string,
    action: InterventionAction,
    ctx: InterventionContext,
  ): void {
    this._auditLog.push({
      handler,
      timestamp: ctx.timestamp.toISOString(),
      action,
      toolName: ctx.tool?.name,
      principal: ctx.principal
        ? `${ctx.principal.type}::${ctx.principal.id}`
        : undefined,
    });
  }
}

// ============================================================================
// 4. Handler Implementations — each implements InterventionHandler
// ============================================================================

// --- Cedar Authorization Handler ---

interface CedarHandlerConfig {
  roles: Record<string, string[]>;
  restrictions: Array<{
    tool: string;
    allowedValues: Record<string, string[]>;
    forRole?: string;
  }>;
}

export class CedarAuthHandler implements InterventionHandler {
  readonly name = "cedar-auth";
  private policies: string;
  private baseEntities: cedar.EntityJson[];

  constructor(private config: CedarHandlerConfig) {
    this.policies = this.generatePolicies();
    this.baseEntities = this.generateEntities();
  }

  static builder(): CedarAuthHandlerBuilder {
    return new CedarAuthHandlerBuilder();
  }

  evaluateToolCall(ctx: InterventionContext): InterventionAction {
    if (!ctx.principal || !ctx.tool) {
      return { type: "deny", reason: "No principal or tool in context" };
    }

    const entities: cedar.EntityJson[] = [
      ...this.baseEntities,
      {
        uid: { type: ctx.principal.type, id: ctx.principal.id },
        parents: ctx.principal.roles.map((r) => ({ type: "Role", id: r })),
        attrs: {},
      },
    ];

    // Ensure the Tool entity exists (wildcard roles may reference tools not in base set)
    const hasToolEntity = entities.some(
      (e) => e.uid.type === "Tool" && e.uid.id === ctx.tool!.name,
    );
    if (!hasToolEntity) {
      entities.push({
        uid: { type: "Tool", id: ctx.tool.name },
        parents: [],
        attrs: {},
      });
    }

    const cedarCtx: Record<string, cedar.CedarValueJson> = {};
    for (const [k, v] of Object.entries(ctx.tool.input)) {
      if (
        typeof v === "string" ||
        typeof v === "number" ||
        typeof v === "boolean"
      ) {
        cedarCtx[k] = v;
      }
    }
    if (ctx.environment) cedarCtx.environment = ctx.environment;

    const result = cedar.isAuthorized({
      principal: { type: ctx.principal.type, id: ctx.principal.id },
      action: { type: "Action", id: `use_tool::${ctx.tool.name}` },
      resource: { type: "Tool", id: ctx.tool.name },
      context: cedarCtx,
      policies: { staticPolicies: this.policies },
      entities,
    });

    const allowed =
      result.type === "success" && result.response.decision === "allow";

    if (allowed) return { type: "proceed" };
    return {
      type: "deny",
      reason: `${ctx.principal.type}::"${ctx.principal.id}" is not authorized to use '${ctx.tool.name}'`,
    };
  }

  private generatePolicies(): string {
    const parts: string[] = [];
    for (const [role, tools] of Object.entries(this.config.roles)) {
      if (tools.length === 1 && tools[0] === "*") {
        parts.push(
          `permit (\n  principal in Role::"${role}",\n  action,\n  resource\n);`,
        );
      } else {
        const actions = tools
          .map((t) => `Action::"use_tool::${t}"`)
          .join(", ");
        parts.push(
          `permit (\n  principal in Role::"${role}",\n  action in [${actions}],\n  resource\n);`,
        );
      }
    }
    for (const r of this.config.restrictions) {
      const principalClause = r.forRole
        ? `principal in Role::"${r.forRole}"`
        : "principal";
      for (const [param, allowed] of Object.entries(r.allowedValues)) {
        const conditions = allowed
          .map((v) => `context.${param} == "${v}"`)
          .join(" || ");
        parts.push(
          `forbid (\n  ${principalClause},\n  action == Action::"use_tool::${r.tool}",\n  resource\n) when {\n  !(${conditions})\n};`,
        );
      }
    }
    return parts.join("\n\n");
  }

  private generateEntities(): cedar.EntityJson[] {
    const entities: cedar.EntityJson[] = [];
    for (const role of Object.keys(this.config.roles)) {
      entities.push({
        uid: { type: "Role", id: role },
        parents: [],
        attrs: {},
      });
    }
    const allTools = new Set<string>();
    for (const tools of Object.values(this.config.roles)) {
      if (!(tools.length === 1 && tools[0] === "*")) {
        tools.forEach((t) => allTools.add(t));
      }
    }
    for (const r of this.config.restrictions) allTools.add(r.tool);
    for (const t of allTools) {
      entities.push({
        uid: { type: "Tool", id: t },
        parents: [],
        attrs: {},
      });
    }
    return entities;
  }
}

class CedarAuthHandlerBuilder {
  private _roles: Record<string, string[]> = {};
  private _restrictions: CedarHandlerConfig["restrictions"] = [];

  role(name: string, opts: { tools: string[] }): this {
    this._roles[name] = opts.tools;
    return this;
  }

  restrict(
    tool: string,
    opts: { allowedValues: Record<string, string[]>; forRole?: string },
  ): this {
    this._restrictions.push({
      tool,
      allowedValues: opts.allowedValues,
      forRole: opts.forRole,
    });
    return this;
  }

  build(): CedarAuthHandler {
    return new CedarAuthHandler({
      roles: { ...this._roles },
      restrictions: [...this._restrictions],
    });
  }
}

// --- Content Guardrail Handler (simulates Datadog-style rules) ---

interface GuardrailRule {
  name: string;
  check: (ctx: InterventionContext) => InterventionAction | undefined;
}

export class ContentGuardrailHandler implements InterventionHandler {
  readonly name = "content-guardrail";
  private rules: GuardrailRule[];

  constructor(rules: GuardrailRule[]) {
    this.rules = rules;
  }

  evaluateToolCall(ctx: InterventionContext): InterventionAction | undefined {
    for (const rule of this.rules) {
      const result = rule.check(ctx);
      if (result && result.type !== "proceed") return result;
    }
    return undefined;
  }

  evaluateModelResponse(
    ctx: InterventionContext,
  ): InterventionAction | undefined {
    for (const rule of this.rules) {
      const result = rule.check(ctx);
      if (result && result.type !== "proceed") return result;
    }
    return undefined;
  }
}

// --- Operational Control Handler (rate limits, env gating) ---

export class OperationalControlHandler implements InterventionHandler {
  readonly name = "operational-control";
  private callCounts: Record<string, number> = {};
  private rateLimits: Record<string, number>;
  private envDenials: Array<[string, string[]]>;

  constructor(opts: {
    rateLimits?: Record<string, number>;
    envDenials?: Array<[string, string[]]>;
  }) {
    this.rateLimits = opts.rateLimits ?? {};
    this.envDenials = opts.envDenials ?? [];
  }

  evaluateToolCall(ctx: InterventionContext): InterventionAction | undefined {
    const toolName = ctx.tool?.name;
    if (!toolName) return undefined;

    if (ctx.environment) {
      for (const [env, tools] of this.envDenials) {
        if (ctx.environment === env && tools.includes(toolName)) {
          return {
            type: "deny",
            reason: `'${toolName}' is blocked in ${env} environment`,
          };
        }
      }
    }

    if (toolName in this.rateLimits) {
      const count = this.callCounts[toolName] ?? 0;
      if (count >= this.rateLimits[toolName]) {
        return {
          type: "deny",
          reason: `Rate limit exceeded for '${toolName}' (${count}/${this.rateLimits[toolName]})`,
        };
      }
      this.callCounts[toolName] = count + 1;
    }

    return undefined;
  }
}

// --- Mock LLM Steering Handler ---
// Simulates what Strands' real LLMSteeringHandler does: evaluate tool calls
// against natural-language rules and return Guide/Proceed. Uses deterministic
// rules because the Strands TS SDK doesn't have steering yet.
// See the Python demo for real LLM steering via StrandsSteeringAdapter.

export class MockLLMSteeringHandler implements InterventionHandler {
  readonly name = "llm-steering";
  private systemPrompt: string;
  private rules: Array<{
    match: (ctx: InterventionContext) => boolean;
    feedback: string;
  }>;

  constructor(opts: {
    systemPrompt: string;
    rules: Array<{
      match: (ctx: InterventionContext) => boolean;
      feedback: string;
    }>;
  }) {
    this.systemPrompt = opts.systemPrompt;
    this.rules = opts.rules;
  }

  evaluateToolCall(ctx: InterventionContext): InterventionAction | undefined {
    for (const rule of this.rules) {
      if (rule.match(ctx)) {
        return { type: "guide", feedback: rule.feedback };
      }
    }
    return undefined;
  }
}

// ============================================================================
// 5. Demo
// ============================================================================

function makeContext(
  overrides: Partial<InterventionContext>,
): InterventionContext {
  return {
    toolHistory: [],
    timestamp: new Date(),
    session: {},
    ...overrides,
  };
}

interface Scenario {
  title: string;
  ctx: InterventionContext;
  expected: string;
}

function demo() {
  // --- Build the pipeline: cheapest handlers first ---

  const pipeline = new InterventionPipeline();

  // 1. Cedar Auth — sub-ms, deterministic, identity-aware
  pipeline.add(
    CedarAuthHandler.builder()
      .role("admin", { tools: ["*"] })
      .role("analyst", {
        tools: ["search", "query_database", "send_email"],
      })
      .restrict("query_database", {
        allowedValues: { database: ["analytics", "reporting"] },
        forRole: "analyst",
      })
      .restrict("query_database", {
        allowedValues: { database: ["analytics", "reporting", "secrets"] },
        forRole: "admin",
      })
      .build(),
  );

  // 2. Operational controls — sub-ms, deterministic, identity-free
  pipeline.add(
    new OperationalControlHandler({
      rateLimits: { send_email: 3 },
      envDenials: [["production", ["delete_record", "drop_table"]]],
    }),
  );

  // 3. Content guardrails — fast pattern matching (simulates Datadog)
  pipeline.add(
    new ContentGuardrailHandler([
      {
        name: "pii-detection",
        check: (ctx) => {
          const input = JSON.stringify(ctx.tool?.input ?? {});
          if (/\d{3}-\d{2}-\d{4}/.test(input)) {
            return {
              type: "deny",
              reason: "PII detected in tool input (SSN pattern)",
            };
          }
          return undefined;
        },
      },
      {
        name: "sql-injection",
        check: (ctx) => {
          const input = JSON.stringify(ctx.tool?.input ?? {}).toLowerCase();
          if (input.includes("drop table") || input.includes("'; --")) {
            return {
              type: "deny",
              reason: "Potential SQL injection detected in tool input",
            };
          }
          return undefined;
        },
      },
    ]),
  );

  // 4. LLM Steering — mocked here; see Python demo for real LLMSteeringHandler
  pipeline.add(
    new MockLLMSteeringHandler({
      systemPrompt:
        "Ensure the agent stays focused on the user's task and uses appropriate tools.",
      rules: [
        {
          match: (ctx) =>
            ctx.tool?.name === "send_email" &&
            typeof ctx.tool.input.body === "string" &&
            ctx.tool.input.body.length < 10,
          feedback:
            "The email body is very short. Consider adding more context before sending.",
        },
      ],
    }),
  );

  // --- Scenarios ---

  const scenarios: Scenario[] = [
    {
      title: "Admin queries secrets database",
      ctx: makeContext({
        principal: { type: "User", id: "alice", roles: ["admin"] },
        tool: {
          name: "query_database",
          input: { database: "secrets", query: "SELECT * FROM keys" },
        },
      }),
      expected: "PROCEED — admin can query secrets",
    },
    {
      title: "Analyst queries secrets database",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "query_database",
          input: { database: "secrets", query: "SELECT * FROM keys" },
        },
      }),
      expected: "DENY (Cedar) — analyst restricted to analytics/reporting",
    },
    {
      title: "Analyst queries analytics — all clear",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "query_database",
          input: {
            database: "analytics",
            query: "SELECT count(*) FROM events",
          },
        },
      }),
      expected: "PROCEED — analyst can query analytics",
    },
    {
      title: "Admin deletes record in production",
      ctx: makeContext({
        principal: { type: "User", id: "alice", roles: ["admin"] },
        tool: { name: "delete_record", input: { id: "42" } },
        environment: "production",
      }),
      expected:
        "DENY (Operational) — delete_record blocked in production (Cedar allows admin, but ops blocks it)",
    },
    {
      title: "Admin deletes record in staging",
      ctx: makeContext({
        principal: { type: "User", id: "alice", roles: ["admin"] },
        tool: { name: "delete_record", input: { id: "42" } },
        environment: "staging",
      }),
      expected: "PROCEED — admin allowed and staging not blocked",
    },
    {
      title: "Analyst sends email with PII",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "send_email",
          input: {
            to: "client@example.com",
            body: "Your SSN is 123-45-6789",
          },
        },
      }),
      expected: "DENY (Guardrail) — PII detected in email body",
    },
    {
      title: "Analyst sends short email (steering guides)",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "send_email",
          input: { to: "client@example.com", body: "Hi" },
        },
      }),
      expected:
        "GUIDE (Steering) — email body too short, Cedar+ops allow it but steering nudges",
    },
    {
      title: "Analyst sends proper email",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "send_email",
          input: {
            to: "client@example.com",
            body: "Hi, please find the analytics report attached. Let me know if you have questions.",
          },
        },
      }),
      expected: "PROCEED — all handlers happy",
    },
    {
      title: "Unknown user (no roles) tries search",
      ctx: makeContext({
        principal: { type: "User", id: "rogue", roles: [] },
        tool: { name: "search", input: { query: "passwords" } },
      }),
      expected: "DENY (Cedar) — no roles = no access (default-deny)",
    },
    {
      title: "Analyst query with SQL injection attempt",
      ctx: makeContext({
        principal: { type: "User", id: "bob", roles: ["analyst"] },
        tool: {
          name: "query_database",
          input: {
            database: "analytics",
            query: "SELECT * FROM events'; -- DROP TABLE events",
          },
        },
      }),
      expected:
        "DENY (Guardrail) — SQL injection detected (Cedar would allow the tool, guardrail catches content)",
    },
  ];

  // --- Run ---

  console.log("=".repeat(72));
  console.log("  Intervention Primitive Demo (TypeScript)");
  console.log(
    "  Pipeline: Cedar Auth -> Operational Control -> Content Guardrail -> LLM Steering",
  );
  console.log();
  console.log("  All handlers implement InterventionHandler.");
  console.log(
    "  LLM steering is mocked; see Python demo for real LLMSteeringHandler.",
  );
  console.log("=".repeat(72));

  for (const s of scenarios) {
    console.log(`\n${"─".repeat(72)}`);
    console.log(`  ${s.title}`);
    if (s.ctx.principal) {
      console.log(
        `  Principal: ${s.ctx.principal.type}::${s.ctx.principal.id} [${s.ctx.principal.roles.join(", ")}]`,
      );
    }
    if (s.ctx.tool) {
      console.log(
        `  Tool: ${s.ctx.tool.name}(${JSON.stringify(s.ctx.tool.input)})`,
      );
    }
    if (s.ctx.environment) {
      console.log(`  Environment: ${s.ctx.environment}`);
    }
    console.log(`  Expected: ${s.expected}`);
    console.log(`${"─".repeat(72)}`);

    const result = pipeline.evaluateToolCall(s.ctx);

    const icon =
      result.type === "proceed"
        ? "PROCEED"
        : result.type === "deny"
          ? "DENY   "
          : result.type === "guide"
            ? "GUIDE  "
            : "INTERRUPT";

    let detail = "";
    if (result.type === "deny") detail = result.reason;
    if (result.type === "guide") detail = result.feedback;
    if (result.type === "interrupt") detail = result.prompt;

    console.log(`  Result: [${icon}] ${detail}`);
  }

  // --- Audit Log ---

  console.log(`\n${"=".repeat(72)}`);
  console.log("  Unified Audit Log");
  console.log("=".repeat(72));

  for (const entry of pipeline.auditLog) {
    const action =
      entry.action.type === "proceed"
        ? "PROCEED"
        : entry.action.type === "deny"
          ? `DENY: ${(entry.action as { type: "deny"; reason: string }).reason}`
          : entry.action.type === "guide"
            ? `GUIDE: ${(entry.action as { type: "guide"; feedback: string }).feedback}`
            : `INTERRUPT`;

    console.log(
      `  [${entry.handler.padEnd(20)}] ${(entry.toolName ?? "").padEnd(18)} ${(entry.principal ?? "").padEnd(20)} ${action}`,
    );
  }

  // --- Proposed first-class API ---

  console.log(`\n${"=".repeat(72)}`);
  console.log(
    "  This demo uses InterventionPipeline — an alternative approach we explored.",
  );
  console.log(
    "  If Strands made interventions first-class, this simplifies to:",
  );
  console.log();
  console.log("    const agent = new Agent({");
  console.log("      tools: [query_database, send_email],");
  console.log("      interventions: [cedar, guardrails, steering],");
  console.log("    });");
  console.log();
  console.log(
    "  See docs/INTERVENTION_EXPLORATION.md for the full proposal.",
  );
  console.log("=".repeat(72));
}

demo();
