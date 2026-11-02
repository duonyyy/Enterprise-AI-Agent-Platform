-- Smoke test for freshly applied Unified v2.0 baseline (56 tables).
-- Must be run with a non-BYPASSRLS role able to insert test rows; all changes intentionally ROLLBACK.
BEGIN;

-- 1. Setup Tenant One & Workspace
SELECT set_config('app.tenant_id','11111111-1111-4111-8111-111111111111',true);
INSERT INTO iam.tenants (id,slug,name)
VALUES ('11111111-1111-4111-8111-111111111111','test-tenant-one','Test Tenant One');
INSERT INTO iam.workspaces (id,tenant_id,slug,name)
VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','11111111-1111-4111-8111-111111111111','primary','Primary');

-- 2. Insert Agent & Immutable Version
INSERT INTO catalog.agents (id,tenant_id,workspace_id,slug,display_name)
VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc','11111111-1111-4111-8111-111111111111',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','agent-sample','Sample Agent');
INSERT INTO catalog.agent_versions
 (id,tenant_id,workspace_id,agent_id,version,schema_version,frozen_spec,bundle_sha256)
VALUES
 ('dddddddd-dddd-4ddd-8ddd-dddddddddddd','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  1,'1.0','{"persona":"test"}'::jsonb,repeat('a',64));
INSERT INTO catalog.agent_publications
 (tenant_id,workspace_id,agent_id,agent_version_id,environment)
VALUES
 ('11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc','dddddddd-dddd-4ddd-8ddd-dddddddddddd','staging');

-- 3. Insert Skill, Immutable SkillVersion, and AgentSkillBinding
INSERT INTO catalog.skills (id,tenant_id,workspace_id,slug,display_name,draft_skill_md,status)
VALUES ('11111111-2222-4333-8444-555555555555','11111111-1111-4111-8111-111111111111',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','weekly-report','Weekly Report Skill','# Report','active');
INSERT INTO catalog.skill_versions
 (id,tenant_id,workspace_id,skill_id,version,schema_version,manifest,skill_md,bundle_ref,bundle_sha256)
VALUES
 ('22222222-3333-4444-8555-666666666666','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','11111111-2222-4333-8444-555555555555',
  1,'agentskills/v1','{"name":"weekly-report"}'::jsonb,'# Instructions','s3://bundle-1',repeat('b',64));
INSERT INTO catalog.agent_skill_bindings
 (id,tenant_id,workspace_id,agent_version_id,skill_id,skill_version_id)
VALUES
 ('33333333-4444-4555-8666-777777777777','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  '11111111-2222-4333-8444-555555555555','22222222-3333-4444-8555-666666666666');

-- 4. Insert Tool, ToolVersion, and MCP Connection, Snapshot, Discovered Item, Tool Mapping
INSERT INTO catalog.tools (id,tenant_id,workspace_id,name,kind)
VALUES ('44444444-5555-4666-8777-888888888888','11111111-1111-4111-8111-111111111111',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','crm.lookup','mcp');
INSERT INTO catalog.tool_versions
 (id,tenant_id,workspace_id,tool_id,version,input_schema,output_schema,risk,sha256)
VALUES
 ('55555555-6666-4777-8888-999999999999','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','44444444-5555-4666-8777-888888888888',
  1,'{"type":"object"}'::jsonb,'{"type":"object"}'::jsonb,'read',repeat('c',64));

INSERT INTO integration.connections (id,tenant_id,workspace_id,name,kind,config,status)
VALUES ('66666666-7777-4888-8999-aaaaaaaaaaaa','11111111-1111-4111-8111-111111111111',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','crm-mcp-sandbox','mcp','{"endpoint":"https://crm.internal/mcp"}'::jsonb,'active');
INSERT INTO integration.mcp_discovery_snapshots
 (id,tenant_id,workspace_id,connection_id,protocol_version,protocol_era,status,catalog_sha256)
VALUES
 ('77777777-8888-4999-8aaa-bbbbbbbbbbbb','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','66666666-7777-4888-8999-aaaaaaaaaaaa',
  '2026-07-28','modern','complete',repeat('d',64));
INSERT INTO integration.mcp_discovered_items
 (id,tenant_id,workspace_id,connection_id,snapshot_id,item_kind,remote_key,descriptor,descriptor_sha256,schema_sha256)
VALUES
 ('88888888-9999-4aaa-8bbb-cccccccccccc','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','66666666-7777-4888-8999-aaaaaaaaaaaa',
  '77777777-8888-4999-8aaa-bbbbbbbbbbbb','tool','crm.lookup',
  '{"name":"crm.lookup"}'::jsonb,repeat('e',64),repeat('f',64));
INSERT INTO integration.mcp_tool_mappings
 (id,tenant_id,workspace_id,connection_id,remote_tool_name,discovery_item_id,tool_version_id,approved_schema_sha256,status)
VALUES
 ('99999999-aaaa-4bbb-8ccc-dddddddddddd','11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','66666666-7777-4888-8999-aaaaaaaaaaaa',
  'crm.lookup','88888888-9999-4aaa-8bbb-cccccccccccc','55555555-6666-4777-8888-999999999999',
  repeat('f',64),'approved');

-- 5. Setup Tenant Two & Workspace
SELECT set_config('app.tenant_id','22222222-2222-4222-8222-222222222222',true);
INSERT INTO iam.tenants (id,slug,name)
VALUES ('22222222-2222-4222-8222-222222222222','test-tenant-two','Test Tenant Two');
INSERT INTO iam.workspaces (id,tenant_id,slug,name)
VALUES ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','22222222-2222-4222-8222-222222222222','primary','Primary');

-- 6. RLS fail-closed cross-tenant SELECT checks for Agents, Skills, and MCP Connections
DO $$ BEGIN
  IF (SELECT count(*) FROM catalog.agents) <> 0 THEN
      RAISE EXCEPTION 'FAIL: tenant TWO can see tenant ONE agent';
  END IF;
  IF (SELECT count(*) FROM catalog.skills) <> 0 THEN
      RAISE EXCEPTION 'FAIL: tenant TWO can see tenant ONE skill';
  END IF;
  IF (SELECT count(*) FROM catalog.skill_versions) <> 0 THEN
      RAISE EXCEPTION 'FAIL: tenant TWO can see tenant ONE skill_version';
  END IF;
  IF (SELECT count(*) FROM integration.connections) <> 0 THEN
      RAISE EXCEPTION 'FAIL: tenant TWO can see tenant ONE mcp connection';
  END IF;
  IF (SELECT count(*) FROM integration.mcp_tool_mappings) <> 0 THEN
      RAISE EXCEPTION 'FAIL: tenant TWO can see tenant ONE mcp tool mapping';
  END IF;
END $$;

-- 7. Switch back to Tenant One and verify visibility
SELECT set_config('app.tenant_id','11111111-1111-4111-8111-111111111111',true);
DO $$ BEGIN
  IF (SELECT count(*) FROM catalog.agents) <> 1 THEN
      RAISE EXCEPTION 'FAIL: tenant ONE cannot see its agent';
  END IF;
  IF (SELECT count(*) FROM catalog.skills) <> 1 THEN
      RAISE EXCEPTION 'FAIL: tenant ONE cannot see its skill';
  END IF;
  IF (SELECT count(*) FROM catalog.skill_versions) <> 1 THEN
      RAISE EXCEPTION 'FAIL: tenant ONE cannot see its skill_version';
  END IF;
  IF (SELECT count(*) FROM integration.connections) <> 1 THEN
      RAISE EXCEPTION 'FAIL: tenant ONE cannot see its mcp connection';
  END IF;
  IF (SELECT count(*) FROM integration.mcp_tool_mappings) <> 1 THEN
      RAISE EXCEPTION 'FAIL: tenant ONE cannot see its mcp tool mapping';
  END IF;
END $$;

-- 8. Verify Immutable table triggers (agent_versions, skill_versions, mcp_discovery_snapshots)
DO $$ DECLARE blocked boolean := false; BEGIN
  BEGIN
    UPDATE catalog.agent_versions SET frozen_spec='{"tampered":true}'::jsonb
     WHERE id='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
  EXCEPTION WHEN raise_exception THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'FAIL: immutable agent_versions was updated';
  END IF;

  blocked := false;
  BEGIN
    UPDATE catalog.skill_versions SET skill_md='# Tampered'
     WHERE id='22222222-3333-4444-8555-666666666666';
  EXCEPTION WHEN raise_exception THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'FAIL: immutable skill_versions was updated';
  END IF;

  blocked := false;
  BEGIN
    UPDATE integration.mcp_discovery_snapshots SET catalog_sha256=repeat('9',64)
     WHERE id='77777777-8888-4999-8aaa-bbbbbbbbbbbb';
  EXCEPTION WHEN raise_exception THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'FAIL: immutable mcp_discovery_snapshots was updated';
  END IF;
END $$;

ROLLBACK;

