# Data Dictionary v1.0 — Enterprise AI Agent Platform

**56 bảng / 7 schemas, PostgreSQL >=16.** Hợp nhất toàn diện Core (50) + CR-SKILL-001 (3) + CR-MCP-001 (3). Dựa trên `ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql`, trạng thái thiết kế `NOT_EXECUTED`.

Ý nghĩa: `tenant_id`/`workspace_id` thể hiện phạm vi tenant; `id` là UUID chính; các cột `*_ref` trỏ sang object storage hoặc secret manager tùy ngữ cảnh.

## iam — 5 bảng

### `iam.tenants`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,62}$') |
| `name` | `text` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closing')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `updated_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**


---

### `iam.users`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `issuer` | `text` | NOT NULL |
| `subject` | `text` | NOT NULL |
| `display_name` | `text` | nullable |
| `email_normalized` | `text` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (issuer, subject)`

---

### `iam.workspaces`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL REFERENCES iam.tenants(id) |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$') |
| `name` | `text` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, id)`
- `UNIQUE (tenant_id, slug)`

---

### `iam.memberships`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `user_id` | `uuid` | NOT NULL REFERENCES iam.users(id) |
| `role` | `text` | NOT NULL CHECK (role IN ('owner','admin','developer','operator','viewer')) |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','invited','revoked')) |
| `joined_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, user_id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `iam.service_accounts`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')) |
| `auth_subject` | `text` | NOT NULL |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, auth_subject), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

## catalog — 16 bảng

### `catalog.model_configs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `provider` | `text` | NOT NULL |
| `provider_model_id` | `text` | NOT NULL |
| `secret_ref` | `text` | nullable |
| `settings` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object') |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, name), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.business_processes`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `process_key` | `text` | NOT NULL |
| `name` | `text` | NOT NULL |
| `description` | `text` | NOT NULL DEFAULT '' |
| `goal` | `text` | NOT NULL DEFAULT '' |
| `guidance` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `constraints_json` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `examples` | `jsonb` | NOT NULL DEFAULT '[]'::jsonb |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, process_key), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.workflows`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL |
| `name` | `text` | NOT NULL |
| `draft_spec` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(draft_spec) = 'object') |
| `revision` | `integer` | NOT NULL DEFAULT 1 CHECK (revision > 0) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, slug), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.workflow_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `workflow_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `schema_version` | `text` | NOT NULL |
| `spec` | `jsonb` | NOT NULL CHECK (jsonb_typeof(spec) = 'object') |
| `sha256` | `char(64)` | NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$') |
| `published_by` | `uuid` | REFERENCES iam.users(id) |
| `published_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id, workspace_id, workflow_id, version)`
- `FOREIGN KEY (tenant_id,workspace_id,workflow_id) REFERENCES catalog.workflows(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.tools`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `tool_key` | `text` | NOT NULL |
| `display_name` | `text` | NOT NULL |
| `kind` | `text` | NOT NULL CHECK (kind IN ('http','mcp','builtin')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,tool_key), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.tool_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `tool_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `input_schema` | `jsonb` | NOT NULL |
| `output_schema` | `jsonb` | NOT NULL |
| `execution_config` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `risk` | `text` | NOT NULL CHECK (risk IN ('read','write','high_impact')) |
| `sha256` | `char(64)` | NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$') |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,tool_id,version)`
- `FOREIGN KEY (tenant_id,workspace_id,tool_id) REFERENCES catalog.tools(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.agents`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL |
| `display_name` | `text` | NOT NULL |
| `draft_spec` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `revision` | `integer` | NOT NULL DEFAULT 1 CHECK (revision > 0) |
| `archived_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,slug), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.agent_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `schema_version` | `text` | NOT NULL |
| `frozen_spec` | `jsonb` | NOT NULL CHECK (jsonb_typeof(frozen_spec) = 'object') |
| `bundle_sha256` | `char(64)` | NOT NULL CHECK (bundle_sha256 ~ '^[a-f0-9]{64}$') |
| `workflow_version_id` | `uuid` | nullable |
| `model_config_id` | `uuid` | nullable |
| `published_by` | `uuid` | REFERENCES iam.users(id) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,agent_id,version)`
- `UNIQUE (tenant_id,workspace_id,agent_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,workflow_version_id) REFERENCES catalog.workflow_versions(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,model_config_id) REFERENCES catalog.model_configs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.agent_publications`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_id` | `uuid` | NOT NULL |
| `agent_version_id` | `uuid` | NOT NULL |
| `environment` | `text` | NOT NULL CHECK (environment IN ('development','staging','production')) |
| `revision` | `integer` | NOT NULL DEFAULT 1 CHECK (revision > 0) |
| `published_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,agent_id,environment)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_id,agent_version_id)`
- `REFERENCES catalog.agent_versions(tenant_id,workspace_id,agent_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.tool_grants`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `tool_id` | `uuid` | NOT NULL |
| `principal_kind` | `text` | NOT NULL CHECK (principal_kind IN ('role','service_account')) |
| `principal_key` | `text` | NOT NULL |
| `allowed_operation` | `text` | NOT NULL CHECK (allowed_operation IN ('invoke','manage')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,tool_id,principal_kind,principal_key,allowed_operation)`
- `FOREIGN KEY (tenant_id,workspace_id,tool_id) REFERENCES catalog.tools(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.digital_workers`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `agent_id` | `uuid` | NOT NULL |
| `default_agent_version_id` | `uuid` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','disabled')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,name)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_id,default_agent_version_id)`
- `REFERENCES catalog.agent_versions(tenant_id,workspace_id,agent_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.agent_tool_bindings`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_version_id` | `uuid` | NOT NULL |
| `tool_version_id` | `uuid` | NOT NULL |
| `usage_mode` | `text` | NOT NULL DEFAULT 'allowed' CHECK (usage_mode IN ('allowed','requires_approval')) |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,agent_version_id,tool_version_id)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,tool_version_id) REFERENCES catalog.tool_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `catalog.agent_knowledge_bindings`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_version_id` | `uuid` | NOT NULL |
| `base_id` | `uuid` | NOT NULL |
| `retrieval_policy` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,agent_version_id,base_id)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---


### `catalog.skills`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$') |
| `display_name` | `text` | NOT NULL |
| `draft_skill_md` | `text` | NOT NULL DEFAULT '' |
| `draft_manifest` | `jsonb` | NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(draft_manifest) = 'object') |
| `draft_bundle_ref` | `text` | nullable |
| `revision` | `integer` | NOT NULL DEFAULT 1 CHECK (revision >= 1) |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','archived')) |
| `created_by` | `uuid` | nullable REFERENCES iam.users(id) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `updated_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, slug)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

### `catalog.skill_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `skill_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version >= 1) |
| `schema_version` | `text` | NOT NULL DEFAULT 'agentskills/v1' |
| `manifest` | `jsonb` | NOT NULL CHECK (jsonb_typeof(manifest) = 'object') |
| `skill_md` | `text` | NOT NULL |
| `bundle_ref` | `text` | NOT NULL |
| `bundle_sha256` | `char(64)` | NOT NULL CHECK (bundle_sha256 ~ '^[a-f0-9]{64}$') |
| `published_by` | `uuid` | nullable REFERENCES iam.users(id) |
| `published_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, skill_id, version)`
- `FOREIGN KEY (tenant_id, workspace_id, skill_id) REFERENCES catalog.skills(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `Trigger: reject_immutable_mutation()`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

### `catalog.agent_skill_bindings`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_version_id` | `uuid` | NOT NULL |
| `skill_id` | `uuid` | NOT NULL |
| `skill_version_id` | `uuid` | NOT NULL |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, agent_version_id, skill_id)`
- `FOREIGN KEY (tenant_id, workspace_id, agent_version_id) REFERENCES catalog.agent_versions(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id, skill_id) REFERENCES catalog.skills(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id, skill_version_id) REFERENCES catalog.skill_versions(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `Trigger: reject_immutable_mutation()`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

## knowledge — 7 bảng

### `knowledge.bases`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `slug` | `text` | NOT NULL |
| `name` | `text` | NOT NULL |
| `visibility` | `text` | NOT NULL DEFAULT 'restricted' CHECK (visibility IN ('restricted','workspace')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,slug), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.base_grants`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `base_id` | `uuid` | NOT NULL |
| `principal_kind` | `text` | NOT NULL CHECK (principal_kind IN ('role','user','service_account')) |
| `principal_key` | `text` | NOT NULL |
| `permission` | `text` | NOT NULL CHECK (permission IN ('read','write','manage')) |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,base_id,principal_kind,principal_key,permission)`
- `FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.documents`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `base_id` | `uuid` | NOT NULL |
| `title` | `text` | NOT NULL |
| `source_uri` | `text` | nullable |
| `deleted_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.document_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `document_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `object_key` | `text` | NOT NULL |
| `checksum_sha256` | `char(64)` | NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$') |
| `status` | `text` | NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ingesting','ready','published','superseded','failed')) |
| `published_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,document_id,version)`
- `CHECK ((status = 'published' AND published_at IS NOT NULL) OR status <> 'published')`
- `FOREIGN KEY (tenant_id,workspace_id,document_id) REFERENCES knowledge.documents(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.chunks`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `document_version_id` | `uuid` | NOT NULL |
| `ordinal` | `integer` | NOT NULL CHECK (ordinal >= 0) |
| `content` | `text` | NOT NULL |
| `metadata` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `token_count` | `integer` | CHECK (token_count >= 0) |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,document_version_id,ordinal)`
- `FOREIGN KEY (tenant_id,workspace_id,document_version_id) REFERENCES knowledge.document_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.vector_refs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `chunk_id` | `uuid` | NOT NULL |
| `collection_name` | `text` | NOT NULL |
| `point_id` | `uuid` | NOT NULL |
| `embedding_model` | `text` | NOT NULL |
| `state` | `text` | NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','indexed','delete_pending','deleted','failed')) |
| `indexed_at` | `timestamptz` | nullable |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,chunk_id,embedding_model)`
- `UNIQUE (collection_name,point_id)`
- `FOREIGN KEY (tenant_id,workspace_id,chunk_id) REFERENCES knowledge.chunks(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `knowledge.ingestion_jobs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `document_version_id` | `uuid` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')) |
| `attempt` | `integer` | NOT NULL DEFAULT 0 CHECK (attempt >= 0) |
| `error_code` | `text` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `finished_at` | `timestamptz` | nullable |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,document_version_id) REFERENCES knowledge.document_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

## runtime — 12 bảng

### `runtime.conversations`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `external_subject` | `text` | nullable |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `last_activity_at` | `timestamptz` | NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id) |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.messages`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `conversation_id` | `uuid` | NOT NULL |
| `role` | `text` | NOT NULL CHECK (role IN ('user','assistant','tool','system')) |
| `body_ref` | `text` | nullable |
| `body_ciphertext` | `bytea` | nullable |
| `token_count` | `integer` | CHECK (token_count >= 0) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `CHECK ((body_ref IS NOT NULL) <> (body_ciphertext IS NOT NULL))`
- `FOREIGN KEY (tenant_id,workspace_id,conversation_id) REFERENCES runtime.conversations(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.runs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_version_id` | `uuid` | NOT NULL |
| `conversation_id` | `uuid` | nullable |
| `workflow_version_id` | `uuid` | nullable |
| `initiated_by` | `uuid` | REFERENCES iam.users(id) |
| `external_request_key` | `text` | nullable |
| `status` | `text` | NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','waiting_approval','succeeded','failed','cancelled','timed_out')) |
| `trace_id` | `text` | nullable |
| `next_event_seq` | `bigint` | NOT NULL DEFAULT 1 CHECK (next_event_seq > 0) |
| `started_at` | `timestamptz` | nullable |
| `finished_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,external_request_key)`
- `FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,conversation_id) REFERENCES runtime.conversations(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,workflow_version_id) REFERENCES catalog.workflow_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.work_items`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `digital_worker_id` | `uuid` | nullable |
| `run_id` | `uuid` | nullable |
| `title` | `text` | NOT NULL |
| `assigned_to` | `text` | nullable |
| `status` | `text` | NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','waiting_human','completed','failed','cancelled')) |
| `due_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `completed_at` | `timestamptz` | nullable |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,digital_worker_id) REFERENCES catalog.digital_workers(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.run_steps`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `step_key` | `text` | NOT NULL |
| `attempt` | `integer` | NOT NULL DEFAULT 1 CHECK (attempt > 0) |
| `status` | `text` | NOT NULL CHECK (status IN ('pending','running','waiting','succeeded','failed','skipped')) |
| `started_at` | `timestamptz` | nullable |
| `finished_at` | `timestamptz` | nullable |
| `error_code` | `text` | nullable |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,run_id,step_key,attempt)`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.run_events`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `sequence` | `bigint` | NOT NULL CHECK (sequence > 0) |
| `event_type` | `text` | NOT NULL |
| `redacted_payload` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,run_id,sequence)`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.checkpoint_refs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `thread_id` | `text` | NOT NULL |
| `checkpoint_namespace` | `text` | NOT NULL DEFAULT '' |
| `checkpoint_id` | `text` | NOT NULL |
| `backend` | `text` | NOT NULL DEFAULT 'langgraph_postgres' |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,run_id,checkpoint_namespace,checkpoint_id)`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.memory_records`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `subject_key` | `text` | NOT NULL |
| `memory_kind` | `text` | NOT NULL CHECK (memory_kind IN ('semantic','episodic','profile')) |
| `ciphertext` | `bytea` | NOT NULL |
| `consent_ref` | `text` | nullable |
| `valid_until` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id) |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.approval_requests`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `step_key` | `text` | NOT NULL |
| `action_hash` | `char(64)` | NOT NULL CHECK (action_hash ~ '^[a-f0-9]{64}$') |
| `status` | `text` | NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired','cancelled')) |
| `approver_role` | `text` | NOT NULL |
| `revision` | `integer` | NOT NULL DEFAULT 1 CHECK (revision > 0) |
| `expires_at` | `timestamptz` | NOT NULL |
| `decided_by` | `uuid` | REFERENCES iam.users(id) |
| `decided_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,run_id,step_key,action_hash)`
- `CHECK ((status IN ('approved','rejected') AND decided_at IS NOT NULL AND decided_by IS NOT NULL) OR status NOT IN ('approved','rejected'))`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.approval_decisions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `approval_request_id` | `uuid` | NOT NULL |
| `actor_id` | `uuid` | NOT NULL REFERENCES iam.users(id) |
| `decision` | `text` | NOT NULL CHECK (decision IN ('approved','rejected')) |
| `decision_note_ref` | `text` | nullable |
| `decided_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,approval_request_id)`
- `FOREIGN KEY (tenant_id,workspace_id,approval_request_id) REFERENCES runtime.approval_requests(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.tool_operations`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `tool_version_id` | `uuid` | NOT NULL |
| `operation_key` | `text` | NOT NULL |
| `request_hash` | `char(64)` | NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$') |
| `status` | `text` | NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','executing','succeeded','failed','unknown')) |
| `result_ref` | `text` | nullable |
| `external_reference` | `text` | nullable |
| `last_attempt_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,operation_key)`
- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,tool_version_id) REFERENCES catalog.tool_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `runtime.jobs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | nullable |
| `job_type` | `text` | NOT NULL CHECK (job_type IN ('run','resume','ingest','reconcile')) |
| `payload_ref` | `text` | nullable |
| `status` | `text` | NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','done','dead')) |
| `available_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `leased_until` | `timestamptz` | nullable |
| `leased_by` | `text` | nullable |
| `attempt` | `integer` | NOT NULL DEFAULT 0 CHECK (attempt >= 0) |
| `max_attempts` | `integer` | NOT NULL DEFAULT 5 CHECK (max_attempts > 0) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

## integration — 6 bảng

### `integration.connections`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `kind` | `text` | NOT NULL CHECK (kind IN ('http','mcp','webhook')) |
| `config` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `secret_ref` | `text` | nullable |
| `status` | `text` | NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')) |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `integration.outbox_events`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `aggregate_type` | `text` | NOT NULL |
| `aggregate_id` | `uuid` | NOT NULL |
| `event_type` | `text` | NOT NULL |
| `payload` | `jsonb` | NOT NULL |
| `state` | `text` | NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','delivering','delivered','dead')) |
| `available_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `attempt` | `integer` | NOT NULL DEFAULT 0 CHECK (attempt >= 0) |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |
| `delivered_at` | `timestamptz` | , UNIQUE (tenant_id, workspace_id, id) |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `integration.inbox_receipts`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `consumer_key` | `text` | NOT NULL |
| `event_id` | `uuid` | NOT NULL |
| `processed_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,consumer_key,event_id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---


### `integration.mcp_discovery_snapshots`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `connection_id` | `uuid` | NOT NULL |
| `protocol_version` | `text` | nullable |
| `protocol_era` | `text` | NOT NULL CHECK (protocol_era IN ('modern','legacy','unknown')) |
| `server_capabilities` | `jsonb` | NOT NULL DEFAULT '{}' |
| `catalog_sha256` | `char(64)` | nullable CHECK (catalog_sha256 ~ '^[a-f0-9]{64}$') |
| `status` | `text` | NOT NULL CHECK (status IN ('complete','partial','failed')) |
| `error_code` | `text` | nullable |
| `captured_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, id, connection_id)`
- `FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `Trigger: reject_immutable_mutation()`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

### `integration.mcp_discovered_items`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `connection_id` | `uuid` | NOT NULL |
| `snapshot_id` | `uuid` | NOT NULL |
| `item_kind` | `text` | NOT NULL CHECK (item_kind IN ('tool','resource','resource_template','prompt')) |
| `remote_key` | `text` | NOT NULL |
| `descriptor` | `jsonb` | NOT NULL CHECK (jsonb_typeof(descriptor) = 'object') |
| `descriptor_sha256` | `char(64)` | NOT NULL CHECK (descriptor_sha256 ~ '^[a-f0-9]{64}$') |
| `schema_sha256` | `char(64)` | nullable CHECK (schema_sha256 ~ '^[a-f0-9]{64}$') |
| `captured_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, snapshot_id, item_kind, remote_key)`
- `UNIQUE (tenant_id, workspace_id, connection_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id, snapshot_id, connection_id) REFERENCES integration.mcp_discovery_snapshots(tenant_id, workspace_id, id, connection_id)`
- `FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `Trigger: reject_immutable_mutation()`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

### `integration.mcp_tool_mappings`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `connection_id` | `uuid` | NOT NULL |
| `remote_tool_name` | `text` | NOT NULL |
| `discovery_item_id` | `uuid` | NOT NULL |
| `tool_version_id` | `uuid` | NOT NULL |
| `approved_schema_sha256` | `char(64)` | NOT NULL CHECK (approved_schema_sha256 ~ '^[a-f0-9]{64}$') |
| `status` | `text` | NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','stale','revoked')) |
| `reviewed_by` | `uuid` | nullable REFERENCES iam.users(id) |
| `reviewed_at` | `timestamptz` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**
- `UNIQUE (tenant_id, workspace_id, id)`
- `UNIQUE (tenant_id, workspace_id, connection_id, remote_tool_name, tool_version_id)`
- `UNIQUE INDEX: uq_mcp_approved_active_tool WHERE status = 'approved'`
- `FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id, discovery_item_id) REFERENCES integration.mcp_discovered_items(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id, tool_version_id) REFERENCES catalog.tool_versions(tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`
- `RLS: ENABLE & FORCE ROW LEVEL SECURITY`

---

## governance — 4 bảng

### `governance.policy_sets`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `resource_type` | `text` | NOT NULL |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `governance.policy_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `policy_set_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `policy_body` | `jsonb` | NOT NULL |
| `sha256` | `char(64)` | NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$') |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,policy_set_id,version)`
- `FOREIGN KEY (tenant_id,workspace_id,policy_set_id) REFERENCES governance.policy_sets(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `governance.audit_events`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `actor_subject` | `text` | NOT NULL |
| `action` | `text` | NOT NULL |
| `resource_type` | `text` | NOT NULL |
| `resource_id` | `text` | NOT NULL |
| `outcome` | `text` | NOT NULL CHECK (outcome IN ('allowed','denied','succeeded','failed')) |
| `request_id` | `text` | nullable |
| `trace_id` | `text` | nullable |
| `reason_code` | `text` | nullable |
| `details_redacted` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |
| `occurred_at` | `timestamptz` | NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id) |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `governance.retention_policies`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `data_class` | `text` | NOT NULL |
| `retention_days` | `integer` | NOT NULL CHECK (retention_days BETWEEN 1 AND 36500) |
| `legal_hold` | `boolean` | NOT NULL DEFAULT false |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,data_class), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

## ops — 6 bảng

### `ops.model_usage`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `run_id` | `uuid` | NOT NULL |
| `step_id` | `uuid` | nullable |
| `provider` | `text` | NOT NULL |
| `model_id` | `text` | NOT NULL |
| `input_tokens` | `bigint` | CHECK (input_tokens >= 0) |
| `output_tokens` | `bigint` | CHECK (output_tokens >= 0) |
| `usage_source` | `text` | NOT NULL CHECK (usage_source IN ('provider','estimated','unknown')) |
| `cost_usd` | `numeric(18,8)` | CHECK (cost_usd >= 0) |
| `pricing_version` | `text` | nullable |
| `observed_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,step_id) REFERENCES runtime.run_steps(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `ops.eval_datasets`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `name` | `text` | NOT NULL |
| `description` | `text` | NOT NULL DEFAULT '' |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `ops.eval_dataset_versions`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `dataset_id` | `uuid` | NOT NULL |
| `version` | `integer` | NOT NULL CHECK (version > 0) |
| `checksum_sha256` | `char(64)` | NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$') |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,dataset_id,version)`
- `FOREIGN KEY (tenant_id,workspace_id,dataset_id) REFERENCES ops.eval_datasets(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `ops.eval_cases`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `dataset_version_id` | `uuid` | NOT NULL |
| `case_key` | `text` | NOT NULL |
| `input_ref` | `text` | NOT NULL |
| `assertions` | `jsonb` | NOT NULL DEFAULT '{}'::jsonb |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,dataset_version_id,case_key)`
- `FOREIGN KEY (tenant_id,workspace_id,dataset_version_id) REFERENCES ops.eval_dataset_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `ops.eval_runs`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `agent_version_id` | `uuid` | NOT NULL |
| `dataset_version_id` | `uuid` | NOT NULL |
| `status` | `text` | NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')) |
| `started_at` | `timestamptz` | nullable |
| `finished_at` | `timestamptz` | nullable |
| `model_settings_hash` | `text` | nullable |
| `created_at` | `timestamptz` | NOT NULL DEFAULT now() |

**Ràng buộc bổ sung:**

- `FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,dataset_version_id) REFERENCES ops.eval_dataset_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

### `ops.eval_results`

| Cột | Kiểu | Null/Default/Constraint |
|---|---|---|
| `tenant_id` | `uuid` | NOT NULL |
| `workspace_id` | `uuid` | NOT NULL |
| `id` | `uuid` | PRIMARY KEY DEFAULT gen_random_uuid() |
| `eval_run_id` | `uuid` | NOT NULL |
| `case_id` | `uuid` | NOT NULL |
| `metric_name` | `text` | NOT NULL |
| `score` | `numeric(12,6)` | nullable |
| `passed` | `boolean` | nullable |
| `trace_id` | `text` | nullable |
| `explanation_ref` | `text` | nullable |

**Ràng buộc bổ sung:**

- `UNIQUE (tenant_id,workspace_id,eval_run_id,case_id,metric_name)`
- `FOREIGN KEY (tenant_id,workspace_id,eval_run_id) REFERENCES ops.eval_runs(tenant_id,workspace_id,id)`
- `FOREIGN KEY (tenant_id,workspace_id,case_id) REFERENCES ops.eval_cases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id)`
- `FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)`

---

