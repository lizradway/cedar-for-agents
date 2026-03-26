"""Demo: CedarPolicyVerifier — catch policy bugs at build time, not runtime.

The verifier generates a Cedar schema from tool definitions and validates
policies against it. No model or agent runtime needed.
"""

from cedar_auth_plugin import CedarPolicyVerifier

# -- Tool definitions (same format as Strands inputSchema["json"]) ----------

TOOLS = {
    "search": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    },
    "query_database": {
        "type": "object",
        "properties": {
            "database": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["database", "query"],
    },
    "send_email": {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
    "delete_record": {
        "type": "object",
        "properties": {
            "record_id": {"type": "integer"},
        },
        "required": ["record_id"],
    },
}

passed = 0
failed = 0


def check(name: str, result, expect_passed: bool, expect_error_substr: str | None = None, expect_warning_substr: str | None = None):
    global passed, failed
    ok = True

    if result.passed != expect_passed:
        print(f"  FAIL {name}: expected passed={expect_passed}, got {result.passed}")
        print(f"        errors: {result.errors}")
        ok = False
    elif expect_error_substr and not any(expect_error_substr in e for e in result.errors):
        print(f"  FAIL {name}: expected error containing '{expect_error_substr}'")
        print(f"        errors: {result.errors}")
        ok = False
    elif expect_warning_substr and not any(expect_warning_substr in w for w in result.warnings):
        print(f"  FAIL {name}: expected warning containing '{expect_warning_substr}'")
        print(f"        warnings: {result.warnings}")
        ok = False
    else:
        print(f"  PASS {name}")

    if ok:
        passed += 1
    else:
        failed += 1


# -- Test 1: Valid policies pass validation --------------------------------

print("\n--- Valid policies ---")

verifier = CedarPolicyVerifier(tools=TOOLS)

valid_policies = """
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
"""

result = verifier.verify(valid_policies)
check("valid policies pass", result, expect_passed=True)


# -- Test 2: Typo in context attribute -------------------------------------

print("\n--- Typo in context attribute ---")

typo_policies = """
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
"""

result = verifier.verify(typo_policies)
check("catches context attribute typo", result, expect_passed=False, expect_error_substr="databas")


# -- Test 3: Nonexistent tool reference ------------------------------------

print("\n--- Nonexistent tool reference ---")

bad_tool_policies = """
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
"""

result = verifier.verify(bad_tool_policies)
check("catches nonexistent tool", result, expect_passed=False, expect_error_substr="drop_table")


# -- Test 4: Wrong type comparison -----------------------------------------

print("\n--- Wrong type comparison ---")

wrong_type_policies = """
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
"""

result = verifier.verify(wrong_type_policies)
check("catches type mismatch (Long vs String)", result, expect_passed=False, expect_error_substr="Long and String")


# -- Test 5: Completeness warning (tool without policy) --------------------

print("\n--- Completeness warning ---")

incomplete_policies = """
permit (
    principal in Role::"analyst",
    action in [Action::"use_tool::search", Action::"use_tool::query_database"],
    resource
);
"""

result = verifier.verify(incomplete_policies)
check("warns about uncovered tools", result, expect_passed=True, expect_warning_substr="send_email")
check("warns about delete_record too", result, expect_passed=True, expect_warning_substr="delete_record")


# -- Test 6: Wildcard action suppresses completeness warning ---------------

print("\n--- Wildcard action suppresses warning ---")

wildcard_policies = """
permit (
    principal in Role::"admin",
    action,
    resource
);
"""

result = verifier.verify(wildcard_policies)
check("wildcard action = no completeness warnings", result, expect_passed=True)
if result.warnings:
    print(f"  FAIL: unexpected warnings: {result.warnings}")
    failed += 1
else:
    print(f"  PASS: no warnings with wildcard")
    passed += 1


# -- Test 7: Schema is inspectable ----------------------------------------

print("\n--- Generated schema ---")

schema = verifier.generate_schema()
has_search = 'action "use_tool::search"' in schema
has_query_db = 'action "use_tool::query_database"' in schema
has_context = '"query": __cedar::String' in schema
has_enrichment = '"hour_utc": __cedar::Long' in schema

if has_search and has_query_db and has_context and has_enrichment:
    print("  PASS schema contains expected actions and context")
    passed += 1
else:
    print(f"  FAIL schema missing expected content")
    print(schema)
    failed += 1


# -- Test 8: Custom principal/resource types -------------------------------

print("\n--- Custom entity types ---")

verifier_custom = CedarPolicyVerifier(
    tools=TOOLS,
    principal_types=["User", "IamRole"],
    resource_types=["Tool", "Record"],
)

custom_policies = """
permit (
    principal in Role::"admin",
    action,
    resource
);
"""

result = verifier_custom.verify(custom_policies)
check("custom principal/resource types accepted", result, expect_passed=True)
has_iam = "entity IamRole" in result.schema
has_record = "entity Record" in result.schema
if has_iam and has_record:
    print("  PASS schema has custom entity types")
    passed += 1
else:
    print(f"  FAIL schema missing custom types")
    failed += 1


# -- Summary ---------------------------------------------------------------

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed:
    import sys
    sys.exit(1)
