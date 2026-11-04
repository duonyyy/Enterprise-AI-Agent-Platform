# Architecture overview

This document is the public orientation for the code repository. It describes intended boundaries; it does not certify that every adapter, user journey or recovery path is implemented. Read the actual entrypoints, versioned contracts and tests for the behavior under review.

## Applications and shared code

| Area | Responsibility |
| --- | --- |
| [Control API](./apps/control-api/) | Administration of identities, workspaces, agent definitions, publication and governance operations. |
| [Agent Runtime](./apps/agent-runtime/) | Run admission, execution, events and the shared Tool Gateway. |
| [Jobs Worker](./apps/jobs-worker/) | Durable background work, leasing, retry and execution handoff. |
| [Studio Web](./apps/studio-web/) | User interface for the approved capabilities. |
| [Python libraries](./libs/python/) | Stable shared contracts and platform abstractions. |
| [TypeScript packages](./packages/) | Shared UI/configuration and API client. |
| [Contracts](./contracts/) | Versioned OpenAPI and JSON Schema definitions. |

Keep domain logic independent of framework adapters. Do not import another application's internal modules or ORM models across service boundaries. Cross-process calls use reviewed versioned contracts and authenticated transport. Dependency composition belongs at application/worker startup.

## Storage and recovery

PostgreSQL is the system of record for application definitions and operational state defined in the [migrations](./database/migrations/). MinIO/S3 holds objects and artifacts; Qdrant is a retrieval index. Redis may support caching or coordination under an approved design. A service in Compose is not proof of a working integration or durable storage configuration.

Keep definitions, conversation messages, graph checkpoints, long-term memory and enterprise knowledge distinct. A checkpoint reference is a pointer, not a checkpoint payload store. Framework-owned storage needs reviewed namespace, isolation, upgrade and backup rules.

Persist accepted work, authoritative inputs, outcomes and operation receipts so another process can recover them. Leases require stale-worker protection. Ambiguous external writes need reconciliation before retry; no exactly-once promise without cooperation from the external system. Test fixtures and RAM caches do not replace durable production state.

## Trust and execution

- Server-authenticated identity establishes tenant/workspace authority. Resource authorization and current grants are separate from token validity and database RLS.
- Model output, Skill content, MCP metadata, retrieved documents and browser input are untrusted.
- All Agent Tool calls, including HTTP, MCP and builtin tools, use the shared Dispatcher with input/schema validation, authorization, policy/risk/approval checks, durable operation reservation, controlled execution, output validation, audit and reconciliation.
- High-impact actions require a trusted, unexpired approval bound to the exact canonical action. Published versions are immutable and runs pin the versions they execute.
- Credentials use scoped secret references. Outbound tool/MCP connections require reviewed egress, connect-time SSRF defenses and resource limits. Skill scripts/local MCP subprocess remain disabled until an approved sandbox is implemented.

The exact ordering and semantics of the 12-Step ToolDispatcher are specified in the privately supplied approved task packet. The summary above is not a substitute for those requirements.

## Design access and evidence

Detailed use cases, specifications, ADRs, audits and implementation runbooks are maintained privately. Before a feature PR, obtain the applicable approved document IDs/revisions and code baseline from the maintainer. A public clone is sufficient for orientation and checks, but not permission to invent missing feature or security contracts.

Use [AGENTS.md](./AGENTS.md), [coding rules](./AGENT_CODING_RULES.md) and [CONTRIBUTING.md](./CONTRIBUTING.md) for workflow. Report source review, unit/mock tests and live integration evidence separately. Never claim production readiness from directory presence, static SQL checks or a green health response alone.
