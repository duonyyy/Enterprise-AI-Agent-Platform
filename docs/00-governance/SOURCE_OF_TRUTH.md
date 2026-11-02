# Source of Truth & Conflict Register — DB 56

**Status:** Document governance / pending local repository verification.

| Concern | Canonical file | If conflict |
|---|---|---|
| DB 56 target inventory | `CANONICAL_DATABASE_56.md` | Baseline 50 is historical; Skill + MCP are not yet applied |
| 50-table SQL exact DDL | `references/ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql` | Do not infer columns/status from a module spec |
| Skill 3-table delta | `references/CR-SKILL-001.md` | New SPEC-SKILL links CR details; preserve 3 new tables |
| MCP 3-table delta | `references/CR-MCP-001.md` | New SPEC-MCP links CR; preserve 3 tables and existing Tool Registry |
| Agent versioning | `SPEC-AGENT-001.md` | Runtime not authorized to mutate published versions |
| Runtime & checkpoint | `SPEC-RUNTIME-001.md` | Checkpointer not memory/DB checkpoint_refs pointer |
| Tool execution | `SPEC-TOOL-001.md` | One shared Gateway; MCP can't bypass |
| Security/authorization | `SPEC-SECURITY-001.md` | Server-side enforcement + RLS, never just UI |
| Workflow/HITL | `SPEC-WORKFLOW-001.md` | Approval via human authorized decision |
| Other module behavior | Matching `SPEC-*.md` | API/path examples proposals; OpenAPI after review |
| Actual code/production | git commit, migrations, CI logs, runtime evidence | Documents are never proof of implementation |

## Known unresolved implementation decisions

1. Which concrete model provider SDK/versions are approved? `SPEC-LLM-001` requires ADR.
2. Checkpoint saver backend and framework tables/namespace inventory are not fixed; `SPEC-RUNTIME-001` identifies contract.
3. Qdrant collection/shard/embedding dimensions and per-tenant indexing strategy require benchmark (do not invent).
4. Auth provider, JWT issuer, KMS, secrets manager, release platform (Kubernetes vs simpler) need deployment ADR.
5. Production SLO thresholds, eval gates and tenant quotas must be derived from workload evidence.
6. When a new feature truly needs a 57th domain table, stop and raise ADR + migration review instead of silently changing baseline.
7. Current source local implementation remains unverified; inspect before opening first implementation PR.

## Required ADR format

`ADR-ID`, context, proposed choices, decision, domain/schema impact, security/tenant impact, rollout, downgrade/recovery, test proof and owner approval. Record accepted ADR in `docs/02-architecture/adr/` only if repository actually supports that location, else map to existing ADR structure.
