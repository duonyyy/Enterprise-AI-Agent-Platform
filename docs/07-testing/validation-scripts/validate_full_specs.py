#!/usr/bin/env python3
"""Static QA: validates module specs inventory and 56-table canonical database design."""
from pathlib import Path
import re, hashlib, json

ROOT = Path(__file__).resolve().parents[3]
specs = list((ROOT / 'docs' / '04-module-specs').rglob('SPEC-*-001.md'))
expected = {'AGENT', 'RUNTIME', 'TOOL', 'SECURITY', 'WORKFLOW', 'KNOWLEDGE', 'MEMORY', 'LLM', 'EVAL', 'STUDIO', 'DEPLOY', 'SKILL', 'MCP'}
found = {p.stem.split('-')[1] for p in specs}
assert found == expected, (found, expected)

for f in specs:
    s = f.read_text(encoding='utf-8')
    assert '`DB-BASELINE-56`' in s, f
    assert 'Test' in s or 'test' in s, f
    assert len(s) > 3500, (f, len(s))
    assert s.count('```') % 2 == 0, f

# Verify V1 56-table baseline
sql_v1 = (ROOT / 'database' / 'migrations' / '0001_baseline.sql').read_text(encoding='utf-8')
tables_v1 = re.findall(r'(?m)^CREATE TABLE (\w+)\.(\w+) \(', sql_v1)
assert len(tables_v1) == 56, f"Expected 56 tables in V1 baseline, got {len(tables_v1)}"

counts = {s: sum(1 for t in tables_v1 if t[0] == s) for s in ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')}
expected_counts = {'iam': 5, 'catalog': 16, 'knowledge': 7, 'runtime': 12, 'integration': 6, 'governance': 4, 'ops': 6}
assert counts == expected_counts, (counts, expected_counts)

print(json.dumps({
    'STATIC_QA': 'PASS',
    'spec_count': len(specs),
    'v1_baseline_tables': len(tables_v1),
    'schemas': counts,
    'status': 'ALL 13 MODULE SPECS & 56 TABLES VERIFIED'
}, indent=2, ensure_ascii=False))
