/**
 * Cedar policy verifier — catch policy bugs at build time, not runtime.
 *
 * Generates a Cedar schema from tool definitions and validates policies
 * against it. Catches errors like referencing nonexistent tools, typos
 * in context attributes, and type mismatches.
 *
 * Usage:
 *   const verifier = new CedarPolicyVerifier({
 *     search: { type: "object", properties: { query: { type: "string" } } },
 *     delete: { type: "object", properties: { id: { type: "integer" } } },
 *   });
 *   const result = verifier.verify(policiesString);
 *
 * Requires: @cedar-policy/cedar-wasm
 */

import * as cedar from "@cedar-policy/cedar-wasm/nodejs";

// JSON Schema type -> Cedar type
const JSON_TO_CEDAR: Record<string, string> = {
  string: "__cedar::String",
  integer: "__cedar::Long",
  number: "__cedar::Long",
  boolean: "__cedar::Bool",
};

export interface VerificationResult {
  passed: boolean;
  errors: string[];
  warnings: string[];
  schema: string;
}

interface JsonSchemaProperty {
  type?: string;
  [key: string]: unknown;
}

interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  [key: string]: unknown;
}

export class CedarPolicyVerifier {
  private _tools: Record<string, JsonSchema>;
  private _principalTypes: string[];
  private _resourceTypes: string[];

  constructor(
    tools: Record<string, JsonSchema>,
    opts?: {
      principalTypes?: string[];
      resourceTypes?: string[];
    },
  ) {
    this._tools = tools;
    this._principalTypes = opts?.principalTypes ?? ["User"];
    this._resourceTypes = opts?.resourceTypes ?? ["Tool"];
  }

  generateSchema(): string {
    const lines: string[] = [];

    // Entity types
    lines.push("entity Role;");
    for (const pt of this._principalTypes) {
      if (pt !== "Role") lines.push(`entity ${pt} in [Role];`);
    }
    for (const rt of this._resourceTypes) {
      if (!this._principalTypes.includes(rt) && rt !== "Role") {
        lines.push(`entity ${rt};`);
      }
    }
    lines.push("");

    // One action per tool
    for (const [toolName, schema] of Object.entries(this._tools)) {
      const contextAttrs = this._schemaToContext(schema);
      const principals = [...this._principalTypes, "Role"].join(", ");
      const resources = this._resourceTypes.join(", ");
      lines.push(`action "use_tool::${toolName}" appliesTo {`);
      lines.push(`    principal: [${principals}],`);
      lines.push(`    resource: [${resources}],`);
      lines.push(`    context: {`);
      for (const [attrName, cedarType] of Object.entries(contextAttrs)) {
        lines.push(`        "${attrName}": ${cedarType},`);
      }
      lines.push(`    }`);
      lines.push(`};`);
      lines.push("");
    }

    return lines.join("\n");
  }

  verify(policies: string): VerificationResult {
    const schema = this.generateSchema();
    const errors: string[] = [];
    const warnings: string[] = [];

    // 1. Cedar schema validation
    const result = cedar.validate({
      schema,
      policies: { staticPolicies: policies },
    });

    if (result.type === "failure") {
      for (const err of result.errors) {
        errors.push(err.message);
      }
    } else {
      for (const err of result.validationErrors) {
        errors.push(err.error.message);
      }
    }

    // 2. Completeness check — tools without any policy referencing them
    for (const toolName of Object.keys(this._tools)) {
      const actionRef = `use_tool::${toolName}`;
      if (!policies.includes(actionRef)) {
        if (!this._hasWildcardAction(policies)) {
          warnings.push(
            `Tool "${toolName}" has no policy covering it. ` +
              `It will be denied by default (Cedar is deny-by-default).`,
          );
        }
      }
    }

    return {
      passed: errors.length === 0,
      errors,
      warnings,
      schema,
    };
  }

  private _schemaToContext(schema: JsonSchema): Record<string, string> {
    const attrs: Record<string, string> = {};
    const properties = schema.properties ?? {};

    for (const [name, prop] of Object.entries(properties)) {
      const jsonType = prop.type ?? "string";
      attrs[name] = JSON_TO_CEDAR[jsonType] ?? "__cedar::String";
    }

    // Enrichment fields the plugin always adds
    attrs["timestamp"] = "__cedar::String";
    attrs["hour_utc"] = "__cedar::Long";
    attrs["environment"] = "__cedar::String";
    attrs["session_call_count"] = "__cedar::Long";

    return attrs;
  }

  private _hasWildcardAction(policies: string): boolean {
    return /permit\s*\([^)]*\baction\s*[,)]/s.test(policies);
  }
}
