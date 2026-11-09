/**
 * @enterprise/api-client
 * Production-grade, contract-compliant HTTP client for Control Plane API
 */

export interface AgentResponse {
  agent_id: string;
  slug: string;
  display_name: string;
  lifecycle: "PUBLISHED" | "DRAFT" | "ARCHIVED" | string;
  description?: string;
  updated_at?: string;
  version?: number;
}

export interface CreateAgentDraftDTO {
  slug: string;
  display_name: string;
  spec?: Record<string, unknown>;
  description?: string;
}

export interface CreateAgentDraftResponse {
  agent_id: string;
  status: string;
  slug: string;
}

export interface PublishResponse {
  agent_id: string;
  version: number;
  spec_hash: string;
  is_immutable: boolean;
}

export interface ApprovalRequest {
  id: string;
  run_id: string;
  step_key: string;
  action_hash: string;
  status: "pending" | "approved" | "rejected" | "expired" | string;
  approver_role: string;
  revision: number;
  expires_at: string;
  created_at: string;
}

export interface DecisionInputDTO {
  decision: "approved" | "rejected";
  expected_action_hash: string;
  decision_reason?: string;
}

export interface DecisionResponse {
  approval_id: string;
  status: string;
  decided_by: string;
  action_hash: string;
  revision: number;
}

export interface LoginInitiateResponse {
  authorization_url: string;
  state: string;
}

export interface WorkspaceMembershipInfo {
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  workspace_id: string;
  workspace_slug: string;
  workspace_name: string;
  role: string;
}

export interface CurrentUserResponse {
  user_id: string;
  issuer: string;
  subject: string;
  display_name?: string | null;
  email_normalized?: string | null;
  workspaces: WorkspaceMembershipInfo[];
}

export interface WorkspaceTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly statusText: string,
    public readonly body?: unknown,
    public readonly requestId?: string
  ) {
    super(`API Error ${status} (${statusText})`);
    this.name = "ApiClientError";
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

export interface ClientConfig {
  baseUrl?: string;
  getToken?: () => string | null | Promise<string | null>;
  timeoutMs?: number;
}

export class ControlApiClientV2 {
  private readonly baseUrl: string;
  private readonly getToken?: () => string | null | Promise<string | null>;
  private readonly timeoutMs: number;

  constructor(config: ClientConfig = {}) {
    this.baseUrl = config.baseUrl || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");
    this.getToken = config.getToken;
    this.timeoutMs = config.timeoutMs || 15000;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
    const headers = new Headers(options.headers);

    if (!headers.has("Accept")) {
      headers.set("Accept", "application/json");
    }

    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    if (this.getToken) {
      const token = await this.getToken();
      if (token && !headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
      }
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);

    const mergedSignal = options.signal
      ? anySignal([options.signal, controller.signal])
      : controller.signal;

    try {
      const response = await fetch(url, {
        ...options,
        headers,
        signal: mergedSignal,
      });

      const requestId = response.headers.get("x-request-id") || undefined;

      if (!response.ok) {
        let errorBody: unknown = undefined;
        try {
          errorBody = await response.json();
        } catch {
          errorBody = await response.text();
        }
        throw new ApiClientError(response.status, response.statusText, errorBody, requestId);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      return (await response.json()) as T;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // --- Agents API ---
  async listAgents(workspaceId: string, signal?: AbortSignal): Promise<AgentResponse[]> {
    return this.request<AgentResponse[]>(`/v1/workspaces/${encodeURIComponent(workspaceId)}/agents`, {
      method: "GET",
      signal,
    });
  }

  async createAgentDraft(
    workspaceId: string,
    dto: CreateAgentDraftDTO,
    signal?: AbortSignal
  ): Promise<CreateAgentDraftResponse> {
    return this.request<CreateAgentDraftResponse>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents`,
      {
        method: "POST",
        body: JSON.stringify(dto),
        signal,
      }
    );
  }

  async publishAgent(
    workspaceId: string,
    agentId: string,
    signal?: AbortSignal
  ): Promise<PublishResponse> {
    return this.request<PublishResponse>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentId)}/publish`,
      {
        method: "POST",
        signal,
      }
    );
  }

  // --- Approvals API ---
  async listApprovals(workspaceId: string, signal?: AbortSignal): Promise<ApprovalRequest[]> {
    return this.request<ApprovalRequest[]>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/approvals`,
      {
        method: "GET",
        signal,
      }
    );
  }

  async commitApprovalDecision(
    workspaceId: string,
    approvalId: string,
    dto: DecisionInputDTO,
    signal?: AbortSignal
  ): Promise<DecisionResponse> {
    return this.request<DecisionResponse>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/approvals/${encodeURIComponent(approvalId)}/decision`,
      {
        method: "POST",
        body: JSON.stringify(dto),
        signal,
      }
    );
  }

  // --- Auth & Session API (SPEC-AUTH-001) ---
  async getLoginUrl(returnTo?: string, signal?: AbortSignal): Promise<LoginInitiateResponse> {
    const query = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : "";
    return this.request<LoginInitiateResponse>(`/v1/auth/login${query}`, {
      method: "GET",
      signal,
    });
  }

  async getCurrentUser(signal?: AbortSignal): Promise<CurrentUserResponse> {
    return this.request<CurrentUserResponse>("/v1/auth/me", {
      method: "GET",
      signal,
    });
  }

  async refreshSession(signal?: AbortSignal): Promise<{ status: string }> {
    return this.request<{ status: string }>("/v1/auth/refresh", {
      method: "POST",
      signal,
    });
  }

  async logout(signal?: AbortSignal): Promise<{ status: string }> {
    return this.request<{ status: string }>("/v1/auth/logout", {
      method: "POST",
      signal,
    });
  }

  async exchangeWorkspaceToken(
    workspaceId: string,
    signal?: AbortSignal
  ): Promise<WorkspaceTokenResponse> {
    return this.request<WorkspaceTokenResponse>("/v1/auth/token", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
      signal,
    });
  }
}

function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort();
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  return controller.signal;
}

export const defaultApiClient = new ControlApiClientV2();
