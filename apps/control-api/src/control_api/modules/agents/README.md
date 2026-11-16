# Agents module

Follows the Workspace layout: `api / dto / services / models / repositories`.

```text
api/             HTTP router, error translation and dependency wiring
dto/             requests.py, responses.py
services/        agent_service.py, publish_service.py
models/          records.py, errors.py, ports.py
repositories/    agent_repository.py
```

The API dependency provider injects the PostgreSQL adapter into `AgentDraftService`
through `AgentRepositoryPort`. The caller owns the database session and transaction.
File layout changes do not alter API contracts, authorization, RLS, CAS or audit semantics.
Publishing remains unavailable in Step 04-A and fails closed.

Tests with repository doubles verify service boundaries only. Database durability,
authorization and browser journeys require their separate integration evidence.
