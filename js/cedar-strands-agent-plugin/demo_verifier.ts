/**
 * Demo: CedarPolicyVerifier — catch policy bugs at build time, not runtime.
 *
 * The verifier generates a Cedar schema from tool definitions and validates
 * policies against it. No model or agent runtime needed.
 */

import { CedarPolicyVerifier, type VerificationResult } from "./cedar_policy_verifier.js";

// -- Tool definitions (same format as JSON Schema) ----------

const TOOLS = {
  search: {
    type: "object" as const,
    properties: {
      query: { type: "string" },
      max_results: { type: "integer" },
    },
    required: ["query"],
  },
  query_database: {
    type: "object" as const,
    properties: {
      database: { type: "string" },
      query: { type: "string" },
      limit: { type: "integer" },
    },
    required: ["database", "query"],
  },
  send_email: {
    type: "object" as const,
    properties: {
      to: { type: "string" },
      subject: { type: "string" },
      body: { type: "string" },
    },
    required: ["to", "subject", "body"],
  },
  delete_record: {
    type: "object" as const,
    properties: {
      record_id: { type: "integer" },
    },
    required: ["record_id"],
  },
};

let passed = 0;
let failed = 0;

function check(
  name: string,
  result: VerificationResult,
  expectPassed: boolean,
  opts?: { expectErrorSubstr?: string; expectWarningSubstr?: string },
) {
  let ok = true;

  if (result.passed !== expectPassed) {
    console.log(`  FAIL ${name}: expected passed=${expectPassed}, got ${result.passed}`);
    console.log(`        errors: ${JSON.stringify(result.errors)}`);
    ok = false;
  } else if (
    opts?.expectErrorSubstr &&
    !result.errors.some((e) => e.includes(opts.expectErrorSubstr!))
  ) {
    console.log(`  FAIL ${name}: expected error containing '${opts.expectErrorSubstr}'`);
    console.log(`        errors: ${JSON.stringify(result.errors)}`);
    ok = false;
  } else if (
    opts?.expectWarningSubstr &&
    !result.warnings.some((w) => w.includes(opts.expectWarningSubstr!))
  ) {
    console.log(`  FAIL ${name}: expected warning containing '${opts.expectWarningSubstr}'`);
    console.log(`        warnings: ${JSON.stringify(result.warnings)}`);
    ok = false;
  } else {
    console.log(`  PASS ${name}`);
  }

  if (ok) passed++;
  else failed++;
}

// -- Test 1: Valid policies pass validation --------------------------------

console.log("\n--- Valid policies ---");

const verifier = new CedarPolicyVerifier(TOOLS);

const validPolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);

permit (
    principal in Role::"analyst",
    action in [Action::"use_tool::search", Action::"use_tool::query_database"],
    resource
);

forbid (
    principal in Role::"analyst",
    action == Action::"use_tool::query_database",
    resource
) when {
    !(context.database == "analytics" || context.database == "reporting")
};

forbid (
    principal,
    action == Action::"use_tool::send_email",
    resource
) when {
    context.session_call_count >= 5
};

forbid (
    principal,
    action,
    resource
) when {
    context.hour_utc < 9 || context.hour_utc >= 17
};
`;

let result = verifier.verify(validPolicies);
check("valid policies pass", result, true);

// -- Test 2: Typo in context attribute -------------------------------------

console.log("\n--- Typo in context attribute ---");

const typoPolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);

forbid (
    principal,
    action == Action::"use_tool::query_database",
    resource
) when {
    context.databas == "secrets"
};
`;

result = verifier.verify(typoPolicies);
check("catches context attribute typo", result, false, {
  expectErrorSubstr: "databas",
});

// -- Test 3: Nonexistent tool reference ------------------------------------

console.log("\n--- Nonexistent tool reference ---");

const badToolPolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);

forbid (
    principal,
    action == Action::"use_tool::drop_table",
    resource
) when { true };
`;

result = verifier.verify(badToolPolicies);
check("catches nonexistent tool", result, false, {
  expectErrorSubstr: "drop_table",
});

// -- Test 4: Wrong type comparison -----------------------------------------

console.log("\n--- Wrong type comparison ---");

const wrongTypePolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);

forbid (
    principal,
    action == Action::"use_tool::query_database",
    resource
) when {
    context.limit == "not_a_number"
};
`;

result = verifier.verify(wrongTypePolicies);
check("catches type mismatch (Long vs String)", result, false, {
  expectErrorSubstr: "Long and String",
});

// -- Test 5: Completeness warning (tool without policy) --------------------

console.log("\n--- Completeness warning ---");

const incompletePolicies = `
permit (
    principal in Role::"analyst",
    action in [Action::"use_tool::search", Action::"use_tool::query_database"],
    resource
);
`;

result = verifier.verify(incompletePolicies);
check("warns about uncovered tools", result, true, {
  expectWarningSubstr: "send_email",
});
check("warns about delete_record too", result, true, {
  expectWarningSubstr: "delete_record",
});

// -- Test 6: Wildcard action suppresses completeness warning ---------------

console.log("\n--- Wildcard action suppresses warning ---");

const wildcardPolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);
`;

result = verifier.verify(wildcardPolicies);
check("wildcard action = no completeness warnings", result, true);
if (result.warnings.length > 0) {
  console.log(`  FAIL: unexpected warnings: ${JSON.stringify(result.warnings)}`);
  failed++;
} else {
  console.log(`  PASS: no warnings with wildcard`);
  passed++;
}

// -- Test 7: Schema is inspectable ----------------------------------------

console.log("\n--- Generated schema ---");

const schema = verifier.generateSchema();
const hasSearch = schema.includes('action "use_tool::search"');
const hasQueryDb = schema.includes('action "use_tool::query_database"');
const hasContext = schema.includes('"query": __cedar::String');
const hasEnrichment = schema.includes('"hour_utc": __cedar::Long');

if (hasSearch && hasQueryDb && hasContext && hasEnrichment) {
  console.log("  PASS schema contains expected actions and context");
  passed++;
} else {
  console.log("  FAIL schema missing expected content");
  console.log(schema);
  failed++;
}

// -- Test 8: Custom principal/resource types -------------------------------

console.log("\n--- Custom entity types ---");

const verifierCustom = new CedarPolicyVerifier(TOOLS, {
  principalTypes: ["User", "IamRole"],
  resourceTypes: ["Tool", "Record"],
});

const customPolicies = `
permit (
    principal in Role::"admin",
    action,
    resource
);
`;

result = verifierCustom.verify(customPolicies);
check("custom principal/resource types accepted", result, true);
const hasIam = result.schema.includes("entity IamRole");
const hasRecord = result.schema.includes("entity Record");
if (hasIam && hasRecord) {
  console.log("  PASS schema has custom entity types");
  passed++;
} else {
  console.log("  FAIL schema missing custom types");
  failed++;
}

// -- Summary ---------------------------------------------------------------

console.log(`\n${"=".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
