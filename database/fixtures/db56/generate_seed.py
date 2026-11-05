#!/usr/bin/env python3
"""Generate deterministic, dev-only SQL sample records matching the user's DB-56 DDL.

Stdlib only. No network, no database writes; writes SQL and metadata to --output-dir.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import uuid

NS = uuid.UUID('d443eb3b-6d70-4f02-bb26-8a780fc1a85f')
STAMP = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
HEX_ZERO = '0' * 64
PROFILE = {'small': (2, 2, 4, 2), 'demo': (2, 3, 8, 4), 'load': (3, 6, 36, 8)}
SCENARIOS = [
    ('knowledge-assistant', 'Trợ lý Tri thức Nội bộ', 'Tìm kiếm chính sách, quy trình và cung cấp trích dẫn', 'knowledge'),
    ('quotation-worker', 'Nhân sự số Xử lý Báo giá', 'Soạn báo giá từ dữ liệu thử nghiệm, chờ duyệt trước khi gửi', 'quotation'),
    ('analytics-agent', 'Agent Phân tích Dữ liệu', 'Trả lời câu hỏi KPI và báo cáo trên dữ liệu demo', 'analytics'),
    ('customer-support', 'Agent Chăm sóc Khách hàng', 'Phân loại yêu cầu, tìm câu trả lời và chuẩn bị cập nhật CRM', 'support'),
    ('hr-onboarding', 'Nhân sự số Hội nhập Nhân viên', 'Tạo checklist hội nhập và đề xuất nhiệm vụ HR/IT', 'hr'),
    ('invoice-review', 'Nhân sự số Kiểm tra Hóa đơn', 'Đối chiếu hóa đơn giả lập với đơn mua hàng', 'invoice'),
    ('contract-review', 'Agent Rà soát Hợp đồng', 'Trích xuất điều khoản, đối chiếu checklist và nêu rủi ro', 'legal'),
    ('it-service-desk', 'Agent Hỗ trợ IT Nội bộ', 'Phân loại ticket, tra cứu runbook và đề xuất cách xử lý', 'it'),
]
TENANTS = [
    ('saoviet-demo', 'Sao Việt Digital — DEMO', ['Vận hành', 'Kinh doanh']),
    ('minhhai-demo', 'Minh Hải Logistics — DEMO', ['Điều hành', 'Chăm sóc khách hàng']),
    ('bachdang-demo', 'Bạch Đằng Services — DEMO', ['Dịch vụ', 'Nhân sự', 'Công nghệ']),
]


def uid(*parts: object) -> str:
    return str(uuid.uuid5(NS, '|'.join(str(x) for x in parts)))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sql(value: object) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).replace("'", "''") + "'::jsonb"
    if isinstance(value, datetime):
        value = value.isoformat()
    return "'" + str(value).replace("'", "''") + "'"


class Seed:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.counts: Counter[str] = Counter()
        self.rows: dict[str, list[dict]] = {}
        self.present: dict[str, set[str]] = {}

    def row(self, table: str, **data: object) -> None:
        if 'id' not in data:
            raise ValueError(f'Missing deterministic id: {table}')
        key = str(data['id'])
        if key in self.present.setdefault(table, set()):
            raise ValueError(f'Duplicate generated id {table} {key}')
        self.present[table].add(key)
        self.rows.setdefault(table, []).append(data)
        self.counts[table] += 1
        cols = ', '.join(data)
        vals = ', '.join(sql(v) for v in data.values())
        self.lines.append(f'INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT (id) DO NOTHING;')

    def add(self, statement: str) -> None:
        self.lines.append(statement)


def make(profile: str, schema_sql: Path) -> tuple[str, dict]:
    cfg_tenants, cfg_workspaces, agents_per_ws, runs_per_agent = PROFILE[profile]
    ddl = schema_sql.read_text(encoding='utf-8-sig')
    all_tables = re.findall(r'CREATE\s+TABLE\s+([a-z_]+\.[a-z_]+)\s*\(', ddl, flags=re.I)
    if len(all_tables) != 56:
        raise ValueError(f'Expected 56 CREATE TABLEs in source SQL, got {len(all_tables)}')
    s = Seed()
    s.add('-- GENERATED TEST FIXTURES. NEVER RUN ON PRODUCTION. No authentic user/LLM data.')
    s.add('-- Reference DDL: ' + schema_sql.name)
    s.add('-- Non-destructive INSERTs; deterministic UUIDs and PK idempotency. Other existing unique conflicts stop the transaction.')
    s.add('BEGIN;')
    s.add('SET LOCAL statement_timeout = \'120s\';')
    s.add("DO $guard$ BEGIN IF current_database() !~* '(dev|test|demo|local|staging)' THEN RAISE EXCEPTION 'Refusing demo seed: database name must include dev/test/demo/local/staging'; END IF; IF current_setting('app.allow_demo_seed', true) IS DISTINCT FROM 'yes' THEN RAISE EXCEPTION 'Refusing demo seed: set app.allow_demo_seed=yes for this psql session'; END IF; END $guard$;")

    # Global identities have no tenant RLS in the supplied DB-56 baseline.
    users: dict[tuple[int, int, str], str] = {}
    layouts = []
    for ti, (slug, name, wnames) in enumerate(TENANTS[:cfg_tenants]):
        wlimit = (2 if ti == 0 else 1) if profile == 'demo' else (1 if profile == 'small' else 2)
        for wi, wn in enumerate(wnames[:wlimit]):
            layouts.append((ti, wi, slug, name, wn))
    if len(layouts) != cfg_workspaces:
        raise AssertionError((profile, len(layouts), cfg_workspaces))
    for ti, wi, tslug, _, _ in layouts:
        for role in ('owner', 'developer', 'viewer'):
            sub = f'{tslug}-w{wi+1}-{role}'
            user_id = uid('user', sub)
            users[(ti, wi, role)] = user_id
            s.row('iam.users', id=user_id, issuer='https://identity.example.test/demo',
                  subject=sub, display_name=f'Người dùng {role.title()} (DEMO {ti+1}.{wi+1})',
                  email_normalized=f'{sub}@example.test')

    fixture = {'profile': profile, 'tenants': [], 'schema_tables': all_tables, 'source': schema_sql.name,
               'warnings': ['Synthetic only; do not infer operational readiness',
                            'seed:// artifacts, checkpoints and sample ciphertext are not real external resources',
                            'MCP connections and tools are disabled by default; no external calls']}

    for ti, wi, tslug, tname, wname in layouts:
        tid = uid('tenant', tslug)
        wslug = ('operations' if wi == 0 else 'business') if ti == 0 else ('workplace' if wi == 0 else 'office')
        wid = uid('workspace', tslug, wslug)
        wkey = f'{tslug}/{wslug}'
        s.add(f'\n-- ===== Workspace {wkey} =====')
        s.add(f"SET LOCAL app.tenant_id = '{tid}';")
        s.add(f"SET LOCAL app.workspace_id = '{wid}';")
        s.row('iam.tenants', id=tid, slug=tslug, name=tname, status='active') if wi == 0 else None
        s.row('iam.workspaces', id=wid, tenant_id=tid, slug=wslug, name=f'{wname} (DEMO)', status='active')
        wr = {'tenant_slug': tslug, 'tenant_id': tid, 'workspace_slug': wslug, 'workspace_id': wid, 'agents': []}
        fixture['tenants'].append(wr)

        def put(table: str, key: str, **values: object) -> str:
            row_id = uid(table, wkey, key)
            s.row(table, id=row_id, tenant_id=tid, workspace_id=wid, **values)
            return row_id

        for role in ('owner', 'developer', 'viewer'):
            put('iam.memberships', f'member-{role}', user_id=users[(ti, wi, role)], role=role, status='active')
        put('iam.service_accounts', 'worker', name='Worker thử nghiệm (không có credentials)',
            auth_subject=f'fixture:{wkey}:worker', status='disabled')
        model = put('catalog.model_configs', 'model', name='Demo Model (không cấu hình API key)',
                    provider='fixture', provider_model_id='fixture-not-callable', settings={'mock_only': True}, status='disabled')
        bp = put('catalog.business_processes', 'process', process_key='sample-operation',
                 name='Quy trình demo liên phòng ban', description='Dữ liệu hoàn toàn giả lập',
                 goal='Kiểm chứng UI và FK', guidance={'fixture': True},
                 constraints_json={'external_write': False}, examples=[{'input': 'Yêu cầu thử nghiệm'}])
        workflow = put('catalog.workflows', 'wf', slug='demo-human-approval',
                       name='Workflow Phê duyệt Demo',
                       draft_spec={'schema_version': 'demo-only', 'nodes': ['start', 'verify', 'human_approval', 'end'], 'demo_only': True})
        wf_version = put('catalog.workflow_versions', 'wf-v1', workflow_id=workflow, version=1,
                         schema_version='demo-only', spec={'schema_version': 'demo-only', 'demo_only': True},
                         sha256=sha('wf:' + wkey), published_by=users[(ti, wi, 'developer')])

        t_read = put('catalog.tools', 'tool-read', tool_key='demo-read-only',
                     display_name='Đọc dữ liệu demo (không có executor)', kind='builtin')
        tv_read = put('catalog.tool_versions', 'tool-read-v1', tool_id=t_read, version=1,
                      input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'additionalProperties': False},
                      output_schema={'type': 'object'}, execution_config={'demo_only': True, 'executable': False},
                      risk='read', sha256=sha('tool-read:' + wkey))
        t_write = put('catalog.tools', 'tool-write', tool_key='demo-external-write',
                      display_name='Ghi hệ thống ngoài (CHƯA KÍCH HOẠT)', kind='mcp')
        tv_write = put('catalog.tool_versions', 'tool-write-v1', tool_id=t_write, version=1,
                       input_schema={'type': 'object', 'properties': {'target': {'type': 'string'}}, 'required': ['target']},
                       output_schema={'type': 'object'}, execution_config={'demo_only': True, 'executable': False},
                       risk='write', sha256=sha('tool-write:' + wkey))
        put('catalog.tool_grants', 'grant-read', tool_id=t_read, principal_kind='role',
            principal_key='developer', allowed_operation='invoke')

        kb = put('knowledge.bases', 'kb', slug='internal-knowledge-demo',
                 name='Kho tri thức thử nghiệm (chưa ingest)', visibility='restricted')
        put('knowledge.base_grants', 'kb-grant-dev', base_id=kb, principal_kind='role',
            principal_key='developer', permission='read')
        put('knowledge.base_grants', 'kb-grant-viewer', base_id=kb, principal_kind='role',
            principal_key='viewer', permission='read')
        docs = []
        for j, (title, content) in enumerate([
            ('Quy trình tiếp nhận nhân viên', 'Bước 1: Xác minh hồ sơ. Bước 2: Cấp thiết bị sau phê duyệt.'),
            ('Chính sách xử lý báo giá', 'Báo giá phải được xác nhận bởi người có thẩm quyền trước khi gửi.'),
            ('Runbook hỗ trợ IT', 'Ưu tiên phân loại sự cố và không tự thay đổi môi trường production.'),
        ]):
            did = put('knowledge.documents', f'doc-{j}', base_id=kb,
                      title=f'{title} (DEMO)', source_uri=f'seed://documents/{tslug}/{wslug}/{j}')
            dvid = put('knowledge.document_versions', f'docv-{j}', document_id=did, version=1,
                       object_key=f'seed://objects/{tslug}/{wslug}/document-{j}',
                       checksum_sha256=sha(content), status='draft')
            chunkid = put('knowledge.chunks', f'chunk-{j}', document_version_id=dvid,
                          ordinal=0, content=content + ' [DỮ LIỆU GIẢ LẬP]', metadata={'demo': True, 'language': 'vi'},
                          token_count=len(content.split()))
            put('knowledge.vector_refs', f'vector-{j}', chunk_id=chunkid,
                collection_name='seed_pending_not_created', point_id=uid('point', wkey, j),
                embedding_model='not-generated', state='pending')
            put('knowledge.ingestion_jobs', f'ingest-{j}', document_version_id=dvid, status='failed',
                attempt=1, error_code='FIXTURE_NOT_UPLOADED')
            docs.append(did)

        skill = put('catalog.skills', 'skill', slug='company-procedures',
                    display_name='Hướng dẫn quy trình doanh nghiệp (DEMO)',
                    draft_skill_md='---\nname: company-procedures\ndescription: Demo skill, not executable\n---\n# Quy trình demo\n',
                    draft_manifest={'name': 'company-procedures', 'description': 'Demo skill', 'demo_only': True},
                    revision=1, status='disabled', created_by=users[(ti, wi, 'developer')])
        skill_md = '---\nname: company-procedures\ndescription: Demo instructions, no tools\n---\n# Demo Skill\nDo not perform external writes.\n'
        skv = put('catalog.skill_versions', 'skill-v1', skill_id=skill, version=1,
                  schema_version='agentskills/v1', manifest={'name': 'company-procedures', 'demo_only': True},
                  skill_md=skill_md, bundle_ref=f'seed://skills/{wkey}/v1',
                  bundle_sha256=sha(skill_md), published_by=users[(ti, wi, 'developer')])

        conn = put('integration.connections', 'conn', name='Mock MCP — không kết nối mạng', kind='mcp',
                   config={'demo_only': True, 'transport': 'stub', 'server': 'not-configured'}, status='disabled')
        snap = put('integration.mcp_discovery_snapshots', 'snapshot', connection_id=conn,
                   protocol_version='fixture', protocol_era='unknown',
                   server_capabilities={'tools': False, 'fixture': True}, status='partial', error_code='NO_REMOTE_SERVER')
        item = put('integration.mcp_discovered_items', 'item', connection_id=conn, snapshot_id=snap,
                   item_kind='tool', remote_key='demo_write',
                   descriptor={'name': 'demo_write', 'description': 'Fixture only', 'inputSchema': {'type': 'object'}},
                   descriptor_sha256=sha('demo-item:' + wkey), schema_sha256=sha('{}'))
        put('integration.mcp_tool_mappings', 'mapping', connection_id=conn, remote_tool_name='demo_write',
            discovery_item_id=item, tool_version_id=tv_write,
            approved_schema_sha256=sha('{}'), status='pending')

        pset = put('governance.policy_sets', 'policy', name='Demo Default Deny', resource_type='tool')
        put('governance.policy_versions', 'policy-v1', policy_set_id=pset, version=1,
            policy_body={'fixture': True, 'default': 'deny', 'external_writes': 'deny'},
            sha256=sha('policy:' + wkey))
        put('governance.retention_policies', 'retention', data_class='demo_events', retention_days=7,
            legal_hold=False)

        scenarios = [SCENARIOS[(i % len(SCENARIOS))] for i in range(agents_per_ws)]
        created = []
        for ai, (slug, label, desc, domain) in enumerate(scenarios):
            akey = slug if ai < len(SCENARIOS) else f'{slug}-{ai+1:02d}'
            agent = put('catalog.agents', f'agent-{ai}', slug=akey,
                        display_name=f'{label} (DEMO)' if ai < 8 else f'{label} — Kịch bản {ai+1} (DEMO)',
                        draft_spec={'schema_version': 'fixture/v1', 'name': label,
                                    'description': desc, 'demo_only': True}, revision=1)
            a_version = put('catalog.agent_versions', f'agentv-{ai}', agent_id=agent, version=1,
                            schema_version='fixture/v1',
                            frozen_spec={'schema_version': 'fixture/v1', 'name': label, 'description': desc,
                                         'domain': domain, 'demo_only': True},
                            bundle_sha256=sha(f'agent:{wkey}:{ai}'), workflow_version_id=wf_version,
                            model_config_id=model, published_by=users[(ti, wi, 'developer')])
            # Publish some, leave others draft for UI states.
            if ai % 4 != 1:
                put('catalog.agent_publications', f'publication-{ai}', agent_id=agent,
                    agent_version_id=a_version, environment='development', revision=1)
            put('catalog.agent_tool_bindings', f'agent-tool-{ai}', agent_version_id=a_version,
                tool_version_id=tv_read, usage_mode='allowed')
            put('catalog.agent_skill_bindings', f'agent-skill-{ai}', agent_version_id=a_version,
                skill_id=skill, skill_version_id=skv)
            put('catalog.agent_knowledge_bindings', f'agent-kb-{ai}', agent_version_id=a_version,
                base_id=kb, retrieval_policy={'demo_only': True, 'citations_required': True})
            if ai < 3:
                put('catalog.digital_workers', f'dw-{ai}', name=f'Digital Worker {domain.upper()} (DEMO)',
                    agent_id=agent, default_agent_version_id=a_version, status='paused')
            created.append((ai, agent, a_version, label, domain))
            wr['agents'].append({'slug': akey, 'id': agent, 'agent_version_id': a_version})

        # Runs are strictly historical UI fixtures. No queued jobs or runnable checkpoints.
        for ai, aid, av, label, domain in created:
            conv = put('runtime.conversations', f'conv-{ai}', external_subject=f'demo-user-{ai}',
                       status='closed')
            for ri in range(runs_per_agent):
                msg_key = f'msg-{ai}-{ri}'
                put('runtime.messages', msg_key, conversation_id=conv, role='user',
                    body_ref=f'seed://messages/{wkey}/{ai}/{ri}', token_count=12 + ri)
                state = ('succeeded', 'failed', 'cancelled', 'waiting_approval')[ri % 4]
                runid = put('runtime.runs', f'run-{ai}-{ri}', agent_version_id=av,
                            conversation_id=conv, workflow_version_id=wf_version,
                            initiated_by=users[(ti, wi, 'developer')],
                            external_request_key=f'fixture-{wkey.replace("/", "-")}-{ai}-{ri}',
                            status=state, trace_id=f'fixture-{uid("trace", wkey, ai, ri)}',
                            next_event_seq=3,
                            started_at=STAMP - timedelta(hours=ai + ri + 1),
                            finished_at=(STAMP - timedelta(hours=ai + ri)) if state != 'waiting_approval' else None,
                            created_at=STAMP - timedelta(hours=ai + ri + 2))
                put('runtime.run_steps', f'step-{ai}-{ri}', run_id=runid,
                    step_key='demo-step', attempt=1,
                    status=('waiting' if state == 'waiting_approval' else 'succeeded' if state == 'succeeded' else 'failed'),
                    started_at=STAMP - timedelta(hours=ai + ri + 1),
                    finished_at=None if state == 'waiting_approval' else STAMP - timedelta(hours=ai + ri))
                put('runtime.run_events', f'event-{ai}-{ri}-1', run_id=runid, sequence=1,
                    event_type='fixture.run.started', redacted_payload={'demo': True, 'agent': label})
                put('runtime.run_events', f'event-{ai}-{ri}-2', run_id=runid, sequence=2,
                    event_type=f'fixture.run.{state}', redacted_payload={'demo': True, 'state': state})
                put('runtime.checkpoint_refs', f'cp-{ai}-{ri}', run_id=runid,
                    thread_id=f'fixture-{uid("thread", wkey, ai, ri)}',
                    checkpoint_id=f'fixture-{uid("checkpoint", wkey, ai, ri)}',
                    backend='fixture_not_resumable')
                put('runtime.jobs', f'job-{ai}-{ri}', run_id=runid, job_type='run',
                    status='done' if state == 'succeeded' else 'dead',
                    attempt=1, max_attempts=1)
                if state == 'waiting_approval':
                    app_id = put('runtime.approval_requests', f'approval-{ai}-{ri}', run_id=runid,
                                 step_key='demo-step', action_hash=sha(f'action:{wkey}:{ai}:{ri}'),
                                 status='pending', approver_role='owner', revision=1,
                                 expires_at=STAMP + timedelta(days=7))
                    put('runtime.work_items', f'work-{ai}-{ri}', run_id=runid,
                        title=f'Demo: phê duyệt tác vụ {domain}', assigned_to='owner',
                        status='waiting_human', due_at=STAMP + timedelta(days=3))
                else:
                    # Historical approved/rejected decisions have corresponding approval requests and runs.
                    if ri % 4 == 0:
                        app_id = put('runtime.approval_requests', f'approval-{ai}-{ri}', run_id=runid,
                                     step_key='demo-step', action_hash=sha(f'action:{wkey}:{ai}:{ri}'),
                                     status='approved', approver_role='owner', revision=1,
                                     expires_at=STAMP + timedelta(days=7),
                                     decided_by=users[(ti, wi, 'owner')], decided_at=STAMP - timedelta(hours=ai + ri))
                        put('runtime.approval_decisions', f'decision-{ai}-{ri}', approval_request_id=app_id,
                            actor_id=users[(ti, wi, 'owner')], decision='approved',
                            decided_at=STAMP - timedelta(hours=ai + ri))
                    if state == 'failed':
                        put('runtime.work_items', f'work-{ai}-{ri}', run_id=runid,
                            title=f'Demo: cần kiểm tra lỗi {domain}', status='failed')
                put('runtime.tool_operations', f'operation-{ai}-{ri}', run_id=runid,
                    tool_version_id=tv_read,
                    operation_key=f'fixture-{wkey.replace("/", "-")}-{ai}-{ri}',
                    request_hash=sha(f'request:{wkey}:{ai}:{ri}'),
                    status=('succeeded' if state == 'succeeded' else 'reserved' if state == 'waiting_approval' else 'failed'),
                    result_ref=f'seed://tool-results/{wkey}/{ai}/{ri}' if state == 'succeeded' else None)
                put('ops.model_usage', f'usage-{ai}-{ri}', run_id=runid, provider='fixture',
                    model_id='offline-simulation', input_tokens=190 + ai*12 + ri,
                    output_tokens=80 + ri*7, usage_source='estimated',
                    cost_usd=Decimal('0.00000000'), pricing_version='not-billable')

        # A synthetic memory byte sequence is intentionally NOT a real encrypted payload.
        put('runtime.memory_records', 'memory', subject_key='demo:subject', memory_kind='profile',
            ciphertext='\\x736565642d6e6f742d656e63727970746564', valid_until=STAMP + timedelta(days=5))
        put('integration.outbox_events', 'outbox', aggregate_type='fixture', aggregate_id=uid('event-agg', wkey),
            event_type='fixture.seed.created', payload={'demo': True, 'not_deliverable': True}, state='dead')
        put('integration.inbox_receipts', 'receipt', consumer_key='demo-consumer',
            event_id=uid('event-id', wkey))
        put('governance.audit_events', 'audit', actor_subject='fixture/system', action='fixture.seed',
            resource_type='workspace', resource_id=wid, outcome='succeeded',
            details_redacted={'demo': True, 'external_executed': False})
        ds = put('ops.eval_datasets', 'eval-dataset', name='Bộ đánh giá demo (chưa chạy)',
                 description='Test data, not evaluation evidence')
        dsv = put('ops.eval_dataset_versions', 'eval-dataset-v1', dataset_id=ds,
                  version=1, checksum_sha256=sha('eval-dataset:' + wkey))
        case = put('ops.eval_cases', 'eval-case', dataset_version_id=dsv,
                   case_key='fixture-01', input_ref=f'seed://eval-inputs/{wkey}/case-01',
                   assertions={'demo_only': True, 'expect_no_external_write': True})
        evr = put('ops.eval_runs', 'eval-run', agent_version_id=created[0][2],
                  dataset_version_id=dsv, status='failed')
        put('ops.eval_results', 'eval-result', eval_run_id=evr, case_id=case,
            metric_name='fixture_only_not_validated', score=None, passed=None)

    # Validate INSERT column names against the source DDL's declared columns.
    for table, block in re.findall(r'CREATE TABLE\s+(\w+\.\w+)\s*\((.*?)\n\);', ddl, flags=re.S | re.I):
        columns = set(re.findall(r'^\s*([a-z_][a-z0-9_]*)\s+(?:uuid|text|integer|bigint|boolean|timestamptz|jsonb|bytea|numeric|char)\b', block, flags=re.M | re.I))
        for row in s.rows.get(table, []):
            unknown = set(row) - columns
            if unknown:
                raise AssertionError(f'Unknown columns in {table}: {sorted(unknown)}')
    missing = set(all_tables) - set(s.counts)
    extra = set(s.counts) - set(all_tables)
    if missing or extra:
        raise AssertionError(f'Missing: {sorted(missing)}, extra: {sorted(extra)}')
    # static FK validation from schema; all references should exist in generated rowset
    fk_re = re.compile(r'FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([\w.]+)\s*\(([^)]+)\)', re.I)
    create_re = re.compile(r'CREATE TABLE\s+(\w+\.\w+)\s*\((.*?)\n\);', re.S | re.I)
    for table, block in create_re.findall(ddl):
        for left, target, right in fk_re.findall(block):
            local_cols = [x.strip() for x in left.split(',')]
            foreign_cols = [x.strip() for x in right.split(',')]
            if not all(c in s.rows[table][0] for c in local_cols):
                continue
            foreign_keys = {(tuple(str(r[c]) if r[c] is not None else None for c in foreign_cols)) for r in s.rows[target]}
            for row in s.rows[table]:
                values = tuple(str(row.get(c)) if row.get(c) is not None else None for c in local_cols)
                if None not in values and values not in foreign_keys:
                    raise AssertionError(f'Generated FK failed {table} ({local_cols}) -> {target} ({foreign_cols}): {values}')
    # Validate table-level UNIQUE composite constraints (NULLs are distinct by default in PG).
    for table, block in create_re.findall(ddl):
        for unique in re.findall(r'\bUNIQUE\s*\(([^)]+)\)', block, flags=re.I):
            cols = [c.strip() for c in unique.split(',')]
            seen = set()
            for row in s.rows[table]:
                value = tuple(row.get(c) for c in cols)
                if any(x is None for x in value):
                    continue
                if value in seen:
                    raise AssertionError(f'Duplicate generated UNIQUE value in {table} on {cols}: {value}')
                seen.add(value)
    # PostgreSQL still remains the final authority for indexes, CHECK constraints and triggers.
    s.add('COMMIT;')
    manifest = {'profile': profile, 'generated_rows': sum(s.counts.values()),
                'counts_per_table': dict(sorted(s.counts.items())),
                'tenants': fixture['tenants'], 'ddl_tables': len(all_tables),
                'warnings': fixture['warnings']}
    return '\n'.join(s.lines) + '\n', manifest


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate DB56 demo-only PostgreSQL seed SQL')
    parser.add_argument('--profile', choices=PROFILE, default='demo')
    parser.add_argument('--schema-sql', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('.'))
    args = parser.parse_args()
    body, manifest = make(args.profile, args.schema_sql)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / f'seed_{args.profile}.sql').write_text(body, encoding='utf-8')
    (args.output_dir / f'manifest_{args.profile}.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"{args.profile}: {manifest['generated_rows']} rows, {len(manifest['counts_per_table'])} tables, "
          f"{len(manifest['tenants'])} workspaces, output={args.output_dir}")


if __name__ == '__main__':
    main()
