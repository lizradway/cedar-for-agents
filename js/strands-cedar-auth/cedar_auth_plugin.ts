/**
 * Cedar authorization plugin for Strands Agents SDK (TypeScript).
 *
 * Three ways to use:
 *
 * 1. Builder:
 *   const plugin = CedarAuthPlugin.builder()
 *     .role("admin", { tools: ["*"] })
 *     .role("analyst", { tools: ["search", "query_database"] })
 *     .restrict("query_database", { allowedValues: { database: ["analytics", "reporting"] } })
 *     .rateLimit("send_email", { maxPerSession: 10 })
 *     .timeWindow(9, 17)
 *     .denyToolsInEnv("production", ["delete_record", "drop_table"])
 *     .build();
 *
 * 2. Config file (JSON):
 *   const plugin = CedarAuthPlugin.fromConfig("./cedar_auth.json");
 *
 * 3. Full Cedar (advanced):
 *   const plugin = new CedarAuthPlugin({ policies: "...", entities: [...] });
 *
 * Requires: @cedar-policy/cedar-wasm @strands-agents/sdk
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as cedar from "@cedar-policy/cedar-wasm/nodejs";
import type {
  Plugin,
  LocalAgent,
  Tool,
} from "@strands-agents/sdk";
import { BeforeToolCallEvent, AfterToolCallEvent } from "@strands-agents/sdk";

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

/** Resolves a principal string from invocation state. */
export type PrincipalResolver =
  | Record<string, string>             // { key: "email", type: "User" }
  | ((state: Record<string, unknown>) => string)
  | undefined;

/** Resolves a Cedar resource string from tool name + input. */
export type ResourceResolver =
  | Record<string, { key: string; type: string }>  // { delete_record: { key: "record_id", type: "Record" } }
  | ((toolName: string, toolInput: Record<string, unknown>) => string)
  | undefined;

/** Entities can be static, dynamic (function), or a file path. */
export type EntitiesInput =
  | cedar.EntityJson[]
  | ((state: Record<string, unknown>) => cedar.EntityJson[])
  | string;  // file path

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

  /**
   * Set which appState key holds the identity and what Cedar type to use.
   *
   * Defaults to key="user_id", type="User" — so the plugin reads
   * appState.get("user_id") and produces User::"alice".
   */
  principal(key: string, type = "User"): this {
    this._principalKey = key;
    this._principalType = type;
    return this;
  }

  /** Grant a role access to specific tools. Use ["*"] for all tools. */
  role(name: string, opts: { tools: string[] }): this {
    this._roles[name] = opts.tools;
    return this;
  }

  /**
   * Restrict a tool's arguments to specific allowed values.
   *
   * Without forRole, the restriction applies to all principals (useful for
   * universal constraints like "nobody can query the secrets database").
   * With forRole, only that role is restricted.
   */
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

  /** Limit how many times a tool can be called per session. */
  rateLimit(tool: string, opts: { maxPerSession: number }): this {
    this._rateLimits[tool] = opts.maxPerSession;
    return this;
  }

  /** Only allow tool calls during a time window (UTC hours). */
  timeWindow(hourStart: number, hourEnd: number): this {
    this._timeWindow = [hourStart, hourEnd];
    return this;
  }

  /** Deny specific tools when running in a given environment. */
  denyToolsInEnv(environment: string, tools: string[]): this {
    this._envDenials.push([environment, tools]);
    return this;
  }

  /** Build the plugin with generated policies and state tracking. */
  build(): CedarAuthPlugin {
    const policies = this._generatePolicies();
    const baseEntities = this._generateEntities();
    const principalKey = this._principalKey;
    const principalType = this._principalType;

    return new CedarAuthPlugin({
      policies,
      entities: CedarAuthPlugin._dynamicEntities(baseEntities, principalKey, principalType),
      principalResolver: CedarAuthPlugin._makePrincipalResolver(principalKey, principalType),
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
  /** Cedar policy text, or a path to a .cedar file. */
  policies: string;
  /** Entity list, dynamic resolver function, or path to a JSON file. */
  entities?: EntitiesInput;
  /** How to resolve the principal from appState. */
  principalResolver?: PrincipalResolver;
  /** How to resolve the resource from tool name + input. */
  resourceResolver?: ResourceResolver;
  /** Rate limit thresholds (tool name → max calls per session). */
  rateLimits?: Record<string, number>;
  /** Time window (UTC hours) outside which all tools are denied. */
  timeWindow?: [number, number] | null;
}

export class CedarAuthPlugin implements Plugin {
  readonly name = "cedar-auth";

  readonly _policies: string;
  private _entities: cedar.EntityJson[] | ((state: Record<string, unknown>) => cedar.EntityJson[]);
  private _principalResolver: (state: Record<string, unknown>) => string;
  private _resourceMap: ResourceResolver;
  private _auditLog: AuthzDecision[] = [];
  private _rateLimits: Record<string, number>;
  _callCounts: Record<string, Record<string, number>> = {};
  private _timeWindow: [number, number] | null;

  // Allow overriding Date.now for testing time windows
  _nowFn: () => Date = () => new Date();

  constructor(config: CedarAuthPluginConfig) {
    this._policies = CedarAuthPlugin._loadPolicies(config.policies);
    this._entities = CedarAuthPlugin._loadEntities(config.entities);
    this._principalResolver = CedarAuthPlugin._loadPrincipalResolver(config.principalResolver);
    this._resourceMap = CedarAuthPlugin._loadResourceResolver(config.resourceResolver);
    this._rateLimits = config.rateLimits ?? {};
    this._timeWindow = config.timeWindow ?? null;
  }

  static builder(): CedarAuthBuilder {
    return new CedarAuthBuilder();
  }

  /**
   * Build a plugin entirely from a JSON config file.
   *
   * Example config:
   * {
   *   "principal": { "key": "email", "type": "User" },
   *   "roles": { "admin": ["*"], "analyst": ["search", "query_database"] },
   *   "restrictions": {
   *     "query_database": { "for_role": "analyst", "database": ["analytics", "reporting"] }
   *   },
   *   "rate_limits": { "send_email": 3 },
   *   "time_window": { "start": 9, "end": 17 },
   *   "deny_in_env": { "production": { "tools": ["delete_record", "drop_table"] } },
   *   "resources": { "delete_record": { "key": "record_id", "type": "Record" } }
   * }
   */
  static fromConfig(configPath: string): CedarAuthPlugin {
    const text = fs.readFileSync(configPath, "utf-8");
    const config = JSON.parse(text) as Record<string, unknown>;

    const b = CedarAuthPlugin.builder();

    // Principal
    const principal = config.principal as Record<string, string> | undefined;
    if (principal) {
      b.principal(principal.key ?? "user_id", principal.type ?? "User");
    }

    // Roles
    const roles = config.roles as Record<string, string[]> | undefined;
    if (roles) {
      for (const [role, tools] of Object.entries(roles)) {
        b.role(role, { tools });
      }
    }

    // Restrictions
    const restrictions = config.restrictions as Record<string, Record<string, unknown>> | undefined;
    if (restrictions) {
      for (const [tool, restriction] of Object.entries(restrictions)) {
        const forRole = restriction.for_role as string | undefined;
        const allowedValues: Record<string, string[]> = {};
        for (const [k, v] of Object.entries(restriction)) {
          if (k !== "for_role" && Array.isArray(v)) {
            allowedValues[k] = v as string[];
          }
        }
        if (Object.keys(allowedValues).length > 0) {
          b.restrict(tool, { allowedValues, forRole });
        }
      }
    }

    // Rate limits
    const rateLimits = config.rate_limits as Record<string, number> | undefined;
    if (rateLimits) {
      for (const [tool, limit] of Object.entries(rateLimits)) {
        b.rateLimit(tool, { maxPerSession: limit });
      }
    }

    // Time window
    const tw = config.time_window as { start: number; end: number } | undefined;
    if (tw) {
      b.timeWindow(tw.start, tw.end);
    }

    // Environment denials
    const denyInEnv = config.deny_in_env as Record<string, { tools: string[] }> | undefined;
    if (denyInEnv) {
      for (const [env, denial] of Object.entries(denyInEnv)) {
        b.denyToolsInEnv(env, denial.tools);
      }
    }

    const plugin = b.build();

    // Resource resolver (a Full Cedar feature, applied after build)
    const resources = config.resources as Record<string, { key: string; type: string }> | undefined;
    if (resources) {
      plugin._resourceMap = resources;
    }

    return plugin;
  }

  get auditLog(): AuthzDecision[] {
    return [...this._auditLog];
  }

  // -- Plugin interface --

  initAgent(agent: LocalAgent): void {
    agent.addHook(BeforeToolCallEvent, (event) => {
      this._beforeToolCall(event);
    });
    agent.addHook(AfterToolCallEvent, (event) => {
      this._afterToolCall(event);
    });
  }

  getTools(): Tool[] {
    return [];
  }

  // -- Entity / principal helpers --

  static _dynamicEntities(
    baseEntities: cedar.EntityJson[],
    principalKey = "user_id",
    principalType = "User",
  ): (state: Record<string, unknown>) => cedar.EntityJson[] {
    return (state: Record<string, unknown>): cedar.EntityJson[] => {
      const entities = [...baseEntities];
      const identity = state[principalKey] as string | undefined;
      const roles = (state.roles ?? []) as string[];
      if (identity) {
        entities.push({
          uid: { type: principalType, id: identity },
          parents: roles.map((r) => ({ type: "Role", id: r })),
          attrs: {},
        });
      }
      return entities;
    };
  }

  static _makePrincipalResolver(
    principalKey: string,
    principalType: string,
  ): (state: Record<string, unknown>) => string {
    return (state: Record<string, unknown>): string => {
      const identity = state[principalKey] as string | undefined;
      if (!identity) {
        throw new Error(`No '${principalKey}' in appState.`);
      }
      return `${principalType}::"${identity}"`;
    };
  }

  private static _defaultPrincipalResolver(state: Record<string, unknown>): string {
    const userId = state.user_id as string | undefined;
    if (!userId) {
      throw new Error("No 'user_id' in appState.");
    }
    return `User::"${userId}"`;
  }

  private static _loadPolicies(policies: string): string {
    if (policies.endsWith(".cedar") && fs.existsSync(policies)) {
      return fs.readFileSync(policies, "utf-8");
    }
    return policies;
  }

  private static _loadEntities(
    entities?: EntitiesInput,
  ): cedar.EntityJson[] | ((state: Record<string, unknown>) => cedar.EntityJson[]) {
    if (entities === undefined) return [];
    if (typeof entities === "string") {
      return JSON.parse(fs.readFileSync(entities, "utf-8")) as cedar.EntityJson[];
    }
    return entities;
  }

  private static _loadPrincipalResolver(
    resolver: PrincipalResolver,
  ): (state: Record<string, unknown>) => string {
    if (resolver === undefined) {
      return CedarAuthPlugin._defaultPrincipalResolver;
    }
    if (typeof resolver === "function") {
      return resolver;
    }
    // Dict: { key: "iam_role", type: "IamRole" }
    return CedarAuthPlugin._makePrincipalResolver(
      resolver.key ?? "user_id",
      resolver.type ?? "User",
    );
  }

  private static _loadResourceResolver(
    resolver: ResourceResolver,
  ): ResourceResolver {
    return resolver;
  }

  private _resolveEntities(state: Record<string, unknown>): cedar.EntityJson[] {
    if (typeof this._entities === "function") {
      return this._entities(state);
    }
    return this._entities;
  }

  private _resolveResource(toolName: string, toolInput: Record<string, unknown>): string {
    if (this._resourceMap === undefined) {
      return `Tool::"${toolName}"`;
    }
    if (typeof this._resourceMap === "function") {
      return this._resourceMap(toolName, toolInput);
    }
    // Dict-based: { delete_record: { key: "record_id", type: "Record" } }
    const mapping = this._resourceMap[toolName];
    if (!mapping) {
      return `Tool::"${toolName}"`;
    }
    const value = (toolInput[mapping.key] as string) ?? toolName;
    return `${mapping.type}::"${value}"`;
  }

  private _getSessionId(state: Record<string, unknown>): string {
    return (state.session_id as string) ?? (state.user_id as string) ?? "_default";
  }

  // -- Hooks --

  private _beforeToolCall(event: BeforeToolCallEvent): void {
    const toolName = event.toolUse.name;
    const toolInput = (event.toolUse.input ?? {}) as Record<string, unknown>;
    const state = event.agent.appState.getAll();

    // Resolve principal
    let principal: string;
    try {
      principal = this._principalResolver(state);
    } catch (e) {
      event.cancel = "Authorization failed: no user identity provided.";
      return;
    }

    const action = `Action::"use_tool::${toolName}"`;
    const resource = this._resolveResource(toolName, toolInput);
    const entities = this._resolveEntities(state);

    // Build context: tool inputs + enrichments
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
      const sessionId = this._getSessionId(state);
      if (!this._callCounts[sessionId]) this._callCounts[sessionId] = {};
      context.session_call_count =
        this._callCounts[sessionId][toolName] ?? 0;
    }

    // Parse principal for Cedar entity UID
    // Principal string is like User::"alice" — extract type and id
    const principalMatch = principal.match(/^(\w+)::"(.+)"$/);
    const principalType = principalMatch?.[1] ?? "User";
    const principalId = principalMatch?.[2] ?? principal;

    // Evaluate
    const result = cedar.isAuthorized({
      principal: { type: principalType, id: principalId },
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
        const sessionId = this._getSessionId(state);
        if (!this._callCounts[sessionId]) this._callCounts[sessionId] = {};
        this._callCounts[sessionId][toolName] =
          (this._callCounts[sessionId][toolName] ?? 0) + 1;
      }
    }
  }

  private _afterToolCall(event: AfterToolCallEvent): void {
    const toolName = event.toolUse.name;
    const hasError = event.error !== undefined;
    // Log tool call outcome (mirrors Python's after_tool_call)
    console.debug(
      `cedar-auth: tool=${toolName} error=${hasError}`,
    );
  }
}
