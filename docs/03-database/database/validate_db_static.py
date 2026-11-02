from pathlib import Path
import re

sql = (Path(__file__).parent / "ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql").read_text(encoding="utf-8")
mt = list(re.finditer(r"CREATE TABLE (\w+\.\w+) \((.*?)\n\);", sql, re.S))
tables = {m.group(1): (m.group(2), m.start()) for m in mt}
errors = []

for name, (body, offset) in tables.items():
    cols = {
        m.group(1)
        for m in re.finditer(
            r"^\s+([a-z][a-z_0-9]*)\s+(?:uuid|text|bytea|integer|bigint|timestamptz|numeric\(|jsonb|char\(|boolean)",
            body,
            re.M,
        )
    }
    for target, fields in re.findall(r"REFERENCES (\w+\.\w+)\(([^)]*)\)", body):
        if target not in tables:
            errors.append(f"{name}: missing reference table {target}")
            continue
        if tables[target][1] > offset:
            errors.append(f"{name}: forward ref to {target}")
        targetcols = [x.strip() for x in fields.split(",")]
        targetbody = tables[target][0]
        if len(targetcols) > 1:
            candidates = []
            for entry in re.findall(r"UNIQUE\s*\(([^)]*)\)", targetbody):
                candidates.append([x.strip() for x in entry.split(",")])
            if targetcols not in candidates:
                errors.append(f"{name}: target {target} missing UNIQUE {targetcols}")
    if name not in ("iam.users", "iam.tenants", "iam.workspaces"):
        for key in ["tenant_id", "workspace_id"]:
            if key not in cols:
                errors.append(f"{name}: missing {key}")

assert len(tables) == 56, f"Expected 56 tables, got {len(tables)}"
assert sql.count("ENABLE ROW LEVEL SECURITY") == 55, f"Expected 55 ENABLE RLS, got {sql.count('ENABLE ROW LEVEL SECURITY')}"
assert sql.count("FORCE ROW LEVEL SECURITY") == 55, f"Expected 55 FORCE RLS, got {sql.count('FORCE ROW LEVEL SECURITY')}"
assert sql.count("CREATE POLICY tenant_isolation") == 55, f"Expected 55 policies, got {sql.count('CREATE POLICY tenant_isolation')}"

if errors:
    print("\n".join(errors))
    raise SystemExit(1)

print("PASS static checks: 56 tables, referenced parents declared earlier, composite FK targets unique, 55 RLS policies.")
print("NOT_RUN: actual PostgreSQL DDL execution, RLS behavior, migrations, chaos/recovery/load testing.")

