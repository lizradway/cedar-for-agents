/**
 * Cedar authorization plugin for Strands Agents SDK (TypeScript).
 *
 * Usage:
 *   const plugin = CedarAuthPlugin.builder()
 *     .role("admin", { tools: ["*"] })
 *     .role("analyst", { tools: ["search", "query_database"] })
 *     .restrict("query_database", { allowedValues: { database: ["analytics", "reporting"] } })
 *     .rateLimit("send_email", { maxPerSession: 10 })
 *     .denyToolsInEnv("production", ["delete_record", "drop_table"])
 *     .build();
 *
 * Requires: @cedar-policy/cedar-wasm @strands-agents/sdk
 */

// @cedar-policy/cedar-wasm exports differently for Node.js vs ESM
// eslint-disable-next-line @typescript-eslint/no-require-imports
import * as cedar from "@cedar-policy/cedar-wasm/nodejs";
import type {
  Plugin,
  LocalAgent,
  Tool,
} from "@strands-agents/sdk";
import { BeforeToolCallEvent } from "@strands-agents/sdk";

// ----------------------------------------------------------------
// Types
// ----------------------------------------------------------------

export interface AuthzDecision {
  principal: string;
  action: string;
  resource: string;
  allowed: boolean;
  toolName: string;
  timestamp: string;
}

interface Restriction {
  tool: string;
  allowedValues: Record<string, string[]>;
  forRole?: string;
}

// ----------------------------------------------------------------
// Builder
// ----------------------------------------------------------------

export class CedarAuthBuilder {
  private _roles: Record<string, string[]> = {};
  private _restrictions: Restriction[] = [];
  private _rateLimits: Record<string, number> = {};
  private _timeWindow: [number, number] | null = null;
  private _envDenials: Array<[string, string[]]> = [];
  private _principalKey = "user_id";
  private _principalType = "User";

  principal(key: string, type = "User"): this {
    this._principalKey = key;
    this._principalType = type;
    return this;
  }

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

  rateLimit(tool: string, opts: { maxPerSession: number }): this {
    this._rateLimits[tool] = opts.maxPerSession;
    return this;
  }

  timeWindow(hourStart: number, hourEnd: number): this {
    this._timeWindow = [hourStart, hourEnd];
    return this;
  }

  denyToolsInEnv(environment: string, tools: string[]): this {
    this._envDenials.push([environment, tools]);
    return this;
  }

  build(): CedarAuthPlugin {
    const policies = this._generatePolicies();
    const baseEntities = this._generateEntities();
    return new CedarAuthPlugin({
      policies,
      baseEntities,
      principalKey: this._principalKey,
      principalType: this._principalType,
      rateLimits: { ...this._rateLimits },
      timeWindow: this._timeWindow,
    });
  }

  // -- Policy generation --

  private _generatePolicies(): string {
    const parts: string[] = [];

    // Role → tools permits
    for (const [role, tools] of Object.entries(this._roles)) {
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

    // Argument restrictions (forbid policies)
    for (const r of this._restrictions) {
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

    // Rate limits
    for (const [tool, maxCalls] of Object.entries(this._rateLimits)) {
      parts.push(
        `forbid (\n  principal,\n  action == Action::"use_tool::${tool}",\n  resource\n) when {\n  context.session_call_count >= ${maxCalls}\n};`,
      );
    }

    // Time window
    if (this._timeWindow) {
      const [hStart, hEnd] = this._timeWindow;
      parts.push(
        `forbid (\n  principal,\n  action,\n  resource\n) when {\n  context.hour_utc < ${hStart} || context.hour_utc >= ${hEnd}\n};`,
      );
    }

    // Environment denials
    for (const [env, tools] of this._envDenials) {
      const actions = tools
        .map((t) => `Action::"use_tool::${t}"`)
        .join(", ");
      parts.push(
        `forbid (\n  principal,\n  action in [${actions}],\n  resource\n) when {\n  context.environment == "${env}"\n};`,
      );
    }

    return parts.join("\n\n");
  }

  private _generateEntities(): cedar.EntityJson[] {
    const entities: cedar.EntityJson[] = [];

    for (const role of Object.keys(this._roles)) {
      entities.push({ uid: { type: "Role", id: role }, parents: [], attrs: {} });
    }

    const allTools = new Set<string>();
    for (const tools of Object.values(this._roles)) {
      if (!(tools.length === 1 && tools[0] === "*")) {
        tools.forEach((t) => allTools.add(t));
      }
    }
    for (const r of this._restrictions) allTools.add(r.tool);
    for (const tool of Object.keys(this._rateLimits)) allTools.add(tool);
    for (const [, tools] of this._envDenials) tools.forEach((t) => allTools.add(t));

    for (const t of allTools) {
      entities.push({ uid: { type: "Tool", id: t }, parents: [], attrs: {} });
    }

    return entities;
  }
}

// ----------------------------------------------------------------
// Plugin
// ----------------------------------------------------------------

interface CedarAuthPluginConfig {
  policies: string;
  baseEntities: cedar.EntityJson[];
  principalKey: string;
  principalType: string;
  rateLimits: Record<string, number>;
  timeWindow: [number, number] | null;
}

export class CedarAuthPlugin implements Plugin {
  readonly name = "cedar-auth";

  readonly _policies: string;
  private _baseEntities: cedar.EntityJson[];
  private _principalKey: string;
  private _principalType: string;
  private _rateLimits: Record<string, number>;
  private _timeWindow: [number, number] | null;
  private _auditLog: AuthzDecision[] = [];
  _callCounts: Record<string, Record<string, number>> = {};

  // Allow overriding Date.now for testing time windows
  _nowFn: () => Date = () => new Date();

  constructor(config: CedarAuthPluginConfig) {
    this._policies = config.policies;
    this._baseEntities = config.baseEntities;
    this._principalKey = config.principalKey;
    this._principalType = config.principalType;
    this._rateLimits = config.rateLimits;
    this._timeWindow = config.timeWindow;
  }

  static builder(): CedarAuthBuilder {
    return new CedarAuthBuilder();
  }

  get auditLog(): AuthzDecision[] {
    return [...this._auditLog];
  }

  // -- Plugin interface --

  initAgent(agent: LocalAgent): void {
    agent.addHook(BeforeToolCallEvent, (event) => {
      this._beforeToolCall(event);
    });
  }

  getTools(): Tool[] {
    return [];
  }

  // -- Authorization --

  private _beforeToolCall(event: BeforeToolCallEvent): void {
    const toolName = event.toolUse.name;
    const toolInput = (event.toolUse.input ?? {}) as Record<string, unknown>;
    const state = event.agent.appState.getAll();

    // Resolve principal
    const identity = state[this._principalKey] as string | undefined;
    if (!identity) {
      event.cancel = "Authorization failed: no user identity provided.";
      return;
    }
    const principal = `${this._principalType}::"${identity}"`;

    const action = `Action::"use_tool::${toolName}"`;
    const resource = `Tool::"${toolName}"`;

    // Build entities: base + user entity with role parents
    const roles = (state.roles ?? []) as string[];
    const entities: cedar.EntityJson[] = [
      ...this._baseEntities,
      {
        uid: { type: this._principalType, id: identity },
        parents: roles.map((r) => ({ type: "Role", id: r })),
        attrs: {},
      },
    ];

    // Build context
    const now = this._nowFn();
    const context: Record<string, cedar.CedarValueJson> = {};
    for (const [k, v] of Object.entries(toolInput)) {
      if (
        typeof v === "string" ||
        typeof v === "number" ||
        typeof v === "boolean"
      ) {
        context[k] = v;
      }
    }
    context.timestamp = now.toISOString();
    context.hour_utc = now.getUTCHours();

    // Environment
    const env = state.environment as string | undefined;
    if (env) context.environment = env;

    // Rate limit counter
    if (toolName in this._rateLimits) {
      const sessionId =
        (state.session_id as string) ?? (identity as string) ?? "_default";
      if (!this._callCounts[sessionId]) this._callCounts[sessionId] = {};
      context.session_call_count =
        this._callCounts[sessionId][toolName] ?? 0;
    }

    // Evaluate
    const result = cedar.isAuthorized({
      principal: { type: this._principalType, id: identity },
      action: { type: "Action", id: `use_tool::${toolName}` },
      resource: { type: "Tool", id: toolName },
      context,
      policies: { staticPolicies: this._policies },
      entities,
    });

    const allowed =
      result.type === "success" && result.response.decision === "allow";

    this._auditLog.push({
      principal,
      action,
      resource,
      allowed,
      toolName,
      timestamp: now.toISOString(),
    });

    if (!allowed) {
      event.cancel = `Access denied: ${principal} is not authorized to use tool '${toolName}'. Contact your administrator to request access.`;
    } else {
      // Increment rate limit counter on success
      if (toolName in this._rateLimits) {
        const sessionId =
          (state.session_id as string) ?? (identity as string) ?? "_default";
        if (!this._callCounts[sessionId]) this._callCounts[sessionId] = {};
        this._callCounts[sessionId][toolName] =
          (this._callCounts[sessionId][toolName] ?? 0) + 1;
      }
    }
  }
}
