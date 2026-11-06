<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/brand/svg/logo-horizontal-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/brand/svg/logo-horizontal-light.svg">
    <img alt="Enterprise AI Agent Platform" src="./assets/brand/svg/logo-horizontal-light.svg" width="460">
  </picture>
</p>

<p align="center">
  <strong>Build AI agents that bring knowledge, tools, and people together to move work forward.</strong>
</p>

<p align="center">
  <a href="#the-vision">Vision</a> ·
  <a href="#what-you-can-build">Use cases</a> ·
  <a href="#platform-capabilities">Capabilities</a> ·
  <a href="#how-it-fits-together">How it works</a> ·
  <a href="#technical-overview">Architecture</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="./assets/">Brand & Assets</a>
</p>

<p align="center">
  <img src="./assets/Sơ đồ Harness Agent Pastel Màu Nước.png" alt="Conceptual overview of an agent harness, including workflow, capabilities, context, governance, and observability." width="1000">
</p>

Enterprise AI Agent Platform is a workspace for teams building AI agents around real business needs. It brings together the building blocks for defining agents, connecting knowledge and tools, organizing work, and involving people when decisions need their attention.

**Project status:** The platform is under active development. The diagram, examples, and capabilities on this page describe the product direction; individual features are at different stages of implementation.

## The vision

An effective agent needs more than a model. It needs context about the task, access to useful knowledge, a way to interact with its environment, and clear boundaries for what it may do. Teams also need to understand its work and bring human judgment into the process.

The platform is being developed as a common foundation for those needs, so teams can focus on designing useful agent experiences for their own organization.

## What you can build

The following examples illustrate possible applications as the platform develops:

- **Knowledge assistant:** Help a team find and understand information from its approved documents and resources.
- **Workflow assistant:** Gather context, prepare a draft, and route work to the right person for review.
- **Operations assistant:** Bring information from connected tools together to help staff investigate a request or issue.
- **Specialist agent:** Give an agent a focused role and a set of skills suited to a particular business task.

These are examples of intended use, not claims that ready-made solutions are currently available.

## Platform capabilities

### Agents and skills

Define an agent around a purpose, instructions, and boundaries. Skills provide reusable guidance for specific tasks, helping teams organize expertise across different agents.

### Knowledge and context

Give agents access to relevant organizational information so they can respond with the context needed for a task. Knowledge should be accessible according to the permissions of the people and workspace involved.

### Tools and integrations

Connect agents to approved capabilities and external systems. The platform's design places tool use behind a common governance boundary, where requests can be checked before action.

### Workflows and human approval

Bring agent work and human decisions into a shared process. A workflow can describe how a task moves forward and where a person needs to review an important action.

### Visibility and improvement

Help teams follow agent activity, investigate issues, and evaluate how well agents support their intended tasks. Feedback can then inform improvements to instructions, knowledge, tools, and workflows.

## How it fits together

An agent experience can begin with a person asking for help. The agent receives the relevant context, uses the knowledge and tools available to it, and works through the task. When an action needs human judgment, the process can pause for review before continuing. Throughout the journey, the team can follow what happened and improve the experience over time.

The illustration above shows this broader **agent harness**: the surrounding capabilities, context, governance, and visibility that support an agent as it works.

## Technical overview

The repository is organized as a monorepo, with separate applications for administration, execution, background work, and the user interface. The following diagram shows the intended relationships; individual integrations are still under development.

```mermaid
flowchart LR
    User[User] --> Studio[Studio Web]
    Studio -.-> Control[Control API]
    Studio -.-> Runtime[Agent Runtime]
    Control --> DB[(PostgreSQL)]
    Runtime --> DB
    Worker[Jobs Worker] --> DB
    Runtime -.-> Gateway[Tool Gateway]
    Gateway -.-> External[External tools and services]
```

Solid lines represent the intended core service and data boundaries; dotted lines represent connections that require implementation or end-to-end verification. The diagram is an architectural view, not a guarantee that every user journey is operational.

| Component | Technology | Role |
| --- | --- | --- |
| [Studio Web](./apps/studio-web/) | Next.js, React, TypeScript | Interface foundation for managing agents and their work. |
| [Control API](./apps/control-api/) | Python, FastAPI, Pydantic | Administration and governance API foundation. |
| [Agent Runtime](./apps/agent-runtime/) | Python, FastAPI, LangGraph | Execution foundation for runs, state, tools, and events. |
| [Jobs Worker](./apps/jobs-worker/) | Python, SQLAlchemy | Background job processing backed by PostgreSQL. |
| Data and infrastructure | PostgreSQL, Redis, Qdrant, MinIO | Storage and supporting services configured for local development. |
| Contracts | JSON Schema, OpenAPI | Versioned definitions for shared payloads and APIs. |

The architecture separates the **control plane**, where configurations and policies are managed, from the **execution plane**, where agent work runs. PostgreSQL is used for durable application state; the development stack also includes supporting services for caching, retrieval, and object storage. Kafka is available as an optional development profile.

### Engineering principles

- **Trusted identity and scope:** Authorization and workspace context must be established by the server.
- **Governed tool use:** External operations are designed to pass through a shared Tool Gateway with policy and approval checks.
- **Durable execution:** Runs and background jobs are designed around persistent state and recovery rather than a RAM-only queue.
- **Human oversight:** High-impact actions require an authorized approval tied to the proposed action.
- **Versioned contracts:** API and data definitions provide a shared boundary between services and teams.

These are engineering requirements and design goals. Consult the [architecture overview](./ARCHITECTURE.md), versioned [contracts](./contracts/), and current source before assuming a capability is complete. Detailed design specifications and implementation plans are maintained separately with restricted access.

### Repository layout

```text
apps/        Studio Web, Control API, Agent Runtime, and Jobs Worker
assets/      Design assets, brand system, and iconography
libs/        Shared Python libraries
packages/    Shared TypeScript packages
contracts/   JSON Schema and OpenAPI definitions
database/    Database migrations
infra/       Local development infrastructure
tests/       Contract, integration, security, and other test suites
scripts/     Repository checks and development utilities
```

## Who is it for?

- **Product and business teams** exploring where agents can assist people and improve existing processes.
- **Engineers and AI practitioners** building agents, integrations, and workflows on a shared foundation.
- **Operators and reviewers** who need to understand agent activity and participate in important decisions.

## Get started

The steps below start and verify the **local development environment** from the repository root using PowerShell on Windows. This setup is for contributors and local verification; it is not a production deployment.

### Prerequisites

- Git and Docker Desktop, with Docker running.
- Python 3.12 or later and `uv` 0.11.26.
- Node.js 22 and pnpm 10.18.3.

### 1. First-time local setup

Run the setup script from the repository root. If `.env` does not exist yet, it creates `.env` from `.env.example` with generated development secrets. If `.env` already exists, it preserves all existing credentials and appends only missing keys. It also syncs Python (`uv` including the `retrieval` extra) and Node (`pnpm`) workspaces, verifies the offline `intfloat/multilingual-e5-small` model snapshot (`614241f622f53c4eeff9890bdc4f31cfecc418b3`), and validates the Compose configuration:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-setup.ps1
```

### 2. Build and start the local stack

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start.ps1
```

This builds and starts the core infrastructure (`postgres`, `redis`, `qdrant`, `minio`, `minio-init`) and applications (`control-api`, `agent-runtime`, `studio-web`), binding host ports to `127.0.0.1`:

- **Studio Web:** `http://127.0.0.1:3000` (e.g. `http://127.0.0.1:3000/login`)
- **Control API:** `http://127.0.0.1:8000` (`/health/live`, `/health/ready`, `/docs`)
- **Agent Runtime:** `http://127.0.0.1:8001` (`/health/live`, `/health/ready`, `/health/deep`, `/docs`)
- **PostgreSQL 16:** `127.0.0.1:5432`
- **Redis 7:** `127.0.0.1:6379`
- **Qdrant:** `http://127.0.0.1:6333`
- **MinIO S3 API & Console:** `http://127.0.0.1:9000` / `http://127.0.0.1:9001`

### 3. Verify the running environment

Run the automated verification suite to check Compose configuration, container readiness, PostgreSQL `platform_app` permissions and fail-closed RLS, Redis ephemeral cache behavior, MinIO non-root object read/write, Qdrant + E5 384-d embeddings, HTTP auth enforcement, restart persistence, and dependency-loss readiness transitions:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-verify.ps1
```

Public-safe redacted evidence is written to `artifacts/evidence/infra-local/<timestamp>/`.

### 4. View logs and enable optional profiles

```powershell
# View service logs
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml logs --tail=100

# Optional profiles (Kafka event bus or background worker when scope/executor are configured)
powershell -ExecutionPolicy Bypass -File .\scripts\local-start.ps1 -Profiles kafka
powershell -ExecutionPolicy Bypass -File .\scripts\local-start.ps1 -Profiles worker
```

### 5. Stop the stack while preserving data

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-stop.ps1
```

This stops the containers without `-v`, preserving `postgres_data`, `qdrant_data`, and `minio_data` volumes.

Before changing code, read the [agent instructions](./AGENTS.md) and the [contribution and branching guide](./CONTRIBUTING.md). Implementation contributors also need the approved task specifications supplied by the maintainer.

## Explore the project

- [Architecture overview](./ARCHITECTURE.md) — service boundaries, persistence, and trust.
- [API and payload contracts](./contracts/) — versioned definitions used by the implementation.
- [Brand and design assets](./assets/) — official logos, icon system, and design tokens.
- [Agent coding rules](./AGENT_CODING_RULES.md) — the public engineering baseline.
- [Contributing](./CONTRIBUTING.md) — local setup and ways to contribute.

Internal use cases, design decisions, audits, evidence, and detailed runbooks are not distributed in this code repository. Contributors receive the relevant approved material through a private task packet; contact the maintainer for access before implementing features that depend on it.

## Security

For security concerns, follow the private reporting guidance in [SECURITY.md](./SECURITY.md).
