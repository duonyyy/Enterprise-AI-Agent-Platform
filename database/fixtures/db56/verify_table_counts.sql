-- Đọc bằng role có quyền thống kê 56 bảng; với RLS, kết quả phụ thuộc transaction context.
SELECT schema_name, table_name, row_count FROM (
SELECT 'iam' AS schema_name, 'tenants' AS table_name, count(*)::bigint AS row_count FROM iam.tenants
UNION ALL
SELECT 'iam' AS schema_name, 'users' AS table_name, count(*)::bigint AS row_count FROM iam.users
UNION ALL
SELECT 'iam' AS schema_name, 'workspaces' AS table_name, count(*)::bigint AS row_count FROM iam.workspaces
UNION ALL
SELECT 'iam' AS schema_name, 'memberships' AS table_name, count(*)::bigint AS row_count FROM iam.memberships
UNION ALL
SELECT 'iam' AS schema_name, 'service_accounts' AS table_name, count(*)::bigint AS row_count FROM iam.service_accounts
UNION ALL
SELECT 'catalog' AS schema_name, 'model_configs' AS table_name, count(*)::bigint AS row_count FROM catalog.model_configs
UNION ALL
SELECT 'catalog' AS schema_name, 'business_processes' AS table_name, count(*)::bigint AS row_count FROM catalog.business_processes
UNION ALL
SELECT 'catalog' AS schema_name, 'workflows' AS table_name, count(*)::bigint AS row_count FROM catalog.workflows
UNION ALL
SELECT 'catalog' AS schema_name, 'workflow_versions' AS table_name, count(*)::bigint AS row_count FROM catalog.workflow_versions
UNION ALL
SELECT 'catalog' AS schema_name, 'tools' AS table_name, count(*)::bigint AS row_count FROM catalog.tools
UNION ALL
SELECT 'catalog' AS schema_name, 'tool_versions' AS table_name, count(*)::bigint AS row_count FROM catalog.tool_versions
UNION ALL
SELECT 'catalog' AS schema_name, 'agents' AS table_name, count(*)::bigint AS row_count FROM catalog.agents
UNION ALL
SELECT 'catalog' AS schema_name, 'agent_versions' AS table_name, count(*)::bigint AS row_count FROM catalog.agent_versions
UNION ALL
SELECT 'catalog' AS schema_name, 'agent_publications' AS table_name, count(*)::bigint AS row_count FROM catalog.agent_publications
UNION ALL
SELECT 'catalog' AS schema_name, 'tool_grants' AS table_name, count(*)::bigint AS row_count FROM catalog.tool_grants
UNION ALL
SELECT 'catalog' AS schema_name, 'digital_workers' AS table_name, count(*)::bigint AS row_count FROM catalog.digital_workers
UNION ALL
SELECT 'catalog' AS schema_name, 'agent_tool_bindings' AS table_name, count(*)::bigint AS row_count FROM catalog.agent_tool_bindings
UNION ALL
SELECT 'knowledge' AS schema_name, 'bases' AS table_name, count(*)::bigint AS row_count FROM knowledge.bases
UNION ALL
SELECT 'knowledge' AS schema_name, 'base_grants' AS table_name, count(*)::bigint AS row_count FROM knowledge.base_grants
UNION ALL
SELECT 'knowledge' AS schema_name, 'documents' AS table_name, count(*)::bigint AS row_count FROM knowledge.documents
UNION ALL
SELECT 'knowledge' AS schema_name, 'document_versions' AS table_name, count(*)::bigint AS row_count FROM knowledge.document_versions
UNION ALL
SELECT 'knowledge' AS schema_name, 'chunks' AS table_name, count(*)::bigint AS row_count FROM knowledge.chunks
UNION ALL
SELECT 'knowledge' AS schema_name, 'vector_refs' AS table_name, count(*)::bigint AS row_count FROM knowledge.vector_refs
UNION ALL
SELECT 'knowledge' AS schema_name, 'ingestion_jobs' AS table_name, count(*)::bigint AS row_count FROM knowledge.ingestion_jobs
UNION ALL
SELECT 'catalog' AS schema_name, 'agent_knowledge_bindings' AS table_name, count(*)::bigint AS row_count FROM catalog.agent_knowledge_bindings
UNION ALL
SELECT 'catalog' AS schema_name, 'skills' AS table_name, count(*)::bigint AS row_count FROM catalog.skills
UNION ALL
SELECT 'catalog' AS schema_name, 'skill_versions' AS table_name, count(*)::bigint AS row_count FROM catalog.skill_versions
UNION ALL
SELECT 'catalog' AS schema_name, 'agent_skill_bindings' AS table_name, count(*)::bigint AS row_count FROM catalog.agent_skill_bindings
UNION ALL
SELECT 'runtime' AS schema_name, 'conversations' AS table_name, count(*)::bigint AS row_count FROM runtime.conversations
UNION ALL
SELECT 'runtime' AS schema_name, 'messages' AS table_name, count(*)::bigint AS row_count FROM runtime.messages
UNION ALL
SELECT 'runtime' AS schema_name, 'runs' AS table_name, count(*)::bigint AS row_count FROM runtime.runs
UNION ALL
SELECT 'runtime' AS schema_name, 'work_items' AS table_name, count(*)::bigint AS row_count FROM runtime.work_items
UNION ALL
SELECT 'runtime' AS schema_name, 'run_steps' AS table_name, count(*)::bigint AS row_count FROM runtime.run_steps
UNION ALL
SELECT 'runtime' AS schema_name, 'run_events' AS table_name, count(*)::bigint AS row_count FROM runtime.run_events
UNION ALL
SELECT 'runtime' AS schema_name, 'checkpoint_refs' AS table_name, count(*)::bigint AS row_count FROM runtime.checkpoint_refs
UNION ALL
SELECT 'runtime' AS schema_name, 'memory_records' AS table_name, count(*)::bigint AS row_count FROM runtime.memory_records
UNION ALL
SELECT 'runtime' AS schema_name, 'approval_requests' AS table_name, count(*)::bigint AS row_count FROM runtime.approval_requests
UNION ALL
SELECT 'runtime' AS schema_name, 'approval_decisions' AS table_name, count(*)::bigint AS row_count FROM runtime.approval_decisions
UNION ALL
SELECT 'runtime' AS schema_name, 'tool_operations' AS table_name, count(*)::bigint AS row_count FROM runtime.tool_operations
UNION ALL
SELECT 'runtime' AS schema_name, 'jobs' AS table_name, count(*)::bigint AS row_count FROM runtime.jobs
UNION ALL
SELECT 'integration' AS schema_name, 'connections' AS table_name, count(*)::bigint AS row_count FROM integration.connections
UNION ALL
SELECT 'integration' AS schema_name, 'outbox_events' AS table_name, count(*)::bigint AS row_count FROM integration.outbox_events
UNION ALL
SELECT 'integration' AS schema_name, 'inbox_receipts' AS table_name, count(*)::bigint AS row_count FROM integration.inbox_receipts
UNION ALL
SELECT 'integration' AS schema_name, 'mcp_discovery_snapshots' AS table_name, count(*)::bigint AS row_count FROM integration.mcp_discovery_snapshots
UNION ALL
SELECT 'integration' AS schema_name, 'mcp_discovered_items' AS table_name, count(*)::bigint AS row_count FROM integration.mcp_discovered_items
UNION ALL
SELECT 'integration' AS schema_name, 'mcp_tool_mappings' AS table_name, count(*)::bigint AS row_count FROM integration.mcp_tool_mappings
UNION ALL
SELECT 'governance' AS schema_name, 'policy_sets' AS table_name, count(*)::bigint AS row_count FROM governance.policy_sets
UNION ALL
SELECT 'governance' AS schema_name, 'policy_versions' AS table_name, count(*)::bigint AS row_count FROM governance.policy_versions
UNION ALL
SELECT 'governance' AS schema_name, 'audit_events' AS table_name, count(*)::bigint AS row_count FROM governance.audit_events
UNION ALL
SELECT 'governance' AS schema_name, 'retention_policies' AS table_name, count(*)::bigint AS row_count FROM governance.retention_policies
UNION ALL
SELECT 'ops' AS schema_name, 'model_usage' AS table_name, count(*)::bigint AS row_count FROM ops.model_usage
UNION ALL
SELECT 'ops' AS schema_name, 'eval_datasets' AS table_name, count(*)::bigint AS row_count FROM ops.eval_datasets
UNION ALL
SELECT 'ops' AS schema_name, 'eval_dataset_versions' AS table_name, count(*)::bigint AS row_count FROM ops.eval_dataset_versions
UNION ALL
SELECT 'ops' AS schema_name, 'eval_cases' AS table_name, count(*)::bigint AS row_count FROM ops.eval_cases
UNION ALL
SELECT 'ops' AS schema_name, 'eval_runs' AS table_name, count(*)::bigint AS row_count FROM ops.eval_runs
UNION ALL
SELECT 'ops' AS schema_name, 'eval_results' AS table_name, count(*)::bigint AS row_count FROM ops.eval_results
) AS rows_by_table ORDER BY schema_name, table_name;
-- Chỉ kiểm tra số lượng: PK/FK/CHECK được PostgreSQL kiểm tra ngay khi nạp.
