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

export interface CreateWorkspaceRequestDTO {
  name: string;
  slug: string;
  tenant_id?: string | null;
  tenant_slug?: string | null;
  tenant_name?: string | null;
  initial_owner_user_id?: string | null;
}

export interface WorkspaceResponseDTO {
  workspace_id: string;
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  workspace_slug: string;
  workspace_name: string;
  status: string;
  role: string;
  initial_owner_user_id?: string | null;
  audit_event_id?: string | null;
  created_at: string;
}

export interface WorkspaceSummaryDTO {
  workspace_id: string;
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  workspace_slug: string;
  workspace_name: string;
  status: string;
  role: string;
}

export interface WorkspaceListResponseDTO {
  items: WorkspaceSummaryDTO[];
}

export type InvitableWorkspaceRole = "admin" | "developer" | "operator" | "viewer";
export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";
export type InvitationDeliveryState =
  | "queued"
  | "sending"
  | "provider_accepted"
  | "failed"
  | "unknown"
  | "cancelled";

export interface CreateWorkspaceInvitationRequestDTO {
  email: string;
  role: InvitableWorkspaceRole;
}

export interface WorkspaceInvitationResponseDTO {
  invitation_id: string;
  tenant_id: string;
  workspace_id: string;
  recipient_email_normalized: string;
  recipient_issuer: string;
  role: InvitableWorkspaceRole;
  status: InvitationStatus;
  generation: number;
  delivery_state: InvitationDeliveryState;
  delivery_error_code?: string | null;
  created_by_user_id: string;
  delivery_requested_by_user_id: string;
  revoked_by_user_id?: string | null;
  accepted_by_user_id?: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
  provider_accepted_at?: string | null;
  revoked_at?: string | null;
  accepted_at?: string | null;
  audit_event_id?: string | null;
  outbox_event_id?: string | null;
}

export interface WorkspaceInvitationListResponseDTO {
  items: WorkspaceInvitationResponseDTO[];
  next_cursor?: string | null;
}

export interface ListWorkspaceInvitationsOptions {
  limit?: number;
  cursor?: string | null;
  signal?: AbortSignal;
}

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly statusText: string;
  public readonly body?: unknown;
  public readonly requestId?: string;

  constructor(
    status: number,
    statusText: string,
    body?: unknown,
    requestId?: string
  ) {
    super(`API Error ${status} (${statusText})`);
    this.name = "ApiClientError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
    this.requestId = requestId;
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

  get isUnprocessable(): boolean {
    return this.status === 422;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  get errorCode(): string | undefined {
    if (typeof this.body === "object" && this.body !== null) {
      const record = this.body as Record<string, unknown>;
      if (typeof record.detail === "object" && record.detail !== null) {
        const detail = record.detail as Record<string, unknown>;
        if (typeof detail.code === "string") {
          return detail.code;
        }
      }
      if (typeof record.code === "string") {
        return record.code;
      }
    }
    return undefined;
  }

  get errorMessage(): string | undefined {
    if (typeof this.body === "object" && this.body !== null) {
      const record = this.body as Record<string, unknown>;
      if (typeof record.detail === "object" && record.detail !== null) {
        const detail = record.detail as Record<string, unknown>;
        if (typeof detail.message === "string") {
          return detail.message;
        }
      }
      if (typeof record.detail === "string") {
        return record.detail;
      }
      if (typeof record.message === "string") {
        return record.message;
      }
    }
    return undefined;
  }
}

export interface ClientConfig {
  baseUrl?: string;
  getToken?: () => string | null | Promise<string | null>;
  timeoutMs?: number;
  onUnauthorized?: () => void;
  autoRefreshOn401?: boolean;
  credentials?: RequestCredentials;
}

export class ControlApiClientV2 {
  private readonly baseUrl: string;
  private readonly getToken?: () => string | null | Promise<string | null>;
  private readonly timeoutMs: number;
  private onUnauthorizedCallback?: () => void;
  private readonly autoRefreshOn401: boolean;
  private readonly defaultCredentials?: RequestCredentials;
  private refreshPromise: Promise<boolean> | null = null;

  constructor(config: ClientConfig = {}) {
    this.baseUrl = config.baseUrl || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");
    this.getToken = config.getToken;
    this.timeoutMs = config.timeoutMs || 15000;
    this.onUnauthorizedCallback = config.onUnauthorized;
    this.autoRefreshOn401 = config.autoRefreshOn401 ?? true;
    this.defaultCredentials = config.credentials;
  }

  public setOnUnauthorized(callback: (() => void) | undefined): void {
    this.onUnauthorizedCallback = callback;
  }

  private async request<T>(path: string, options: RequestInit = {}, isRetry: boolean = false): Promise<T> {
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
      const fetchInit: RequestInit = {
        ...options,
        credentials: options.credentials ?? this.defaultCredentials ?? "same-origin",
        cache: options.cache ?? "no-store",
        headers,
        signal: mergedSignal,
      };

      const response = await fetch(url, fetchInit);

      const requestId = response.headers.get("x-request-id") || undefined;

      if (!response.ok) {
        let errorBody: unknown = undefined;
        try {
          errorBody = await response.json();
        } catch {
          errorBody = await response.text();
        }
        const error = new ApiClientError(response.status, response.statusText, errorBody, requestId);

        if (response.status === 401) {
          const normalizedPath = path.startsWith("/") ? path : `/${path}`;
          const isAuthEndpoint =
            normalizedPath.startsWith("/v1/auth/login") ||
            normalizedPath.startsWith("/v1/auth/callback") ||
            normalizedPath.startsWith("/v1/auth/refresh") ||
            normalizedPath.startsWith("/v1/auth/logout");

          if (this.autoRefreshOn401 && !isRetry && !isAuthEndpoint) {
            if (!this.refreshPromise) {
              this.refreshPromise = this.refreshSession()
                .then(() => true)
                .catch(() => false)
                .finally(() => {
                  this.refreshPromise = null;
                });
            }
            const refreshed = await this.refreshPromise;
            if (refreshed) {
              return this.request<T>(path, options, true);
            }
          }

          if (this.onUnauthorizedCallback && normalizedPath !== "/v1/auth/refresh") {
            this.onUnauthorizedCallback();
          }
        }

        throw error;
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

  // --- Workspaces API (SPEC-WORKSPACE-001 / Step 03-A) ---
  async createWorkspace(
    dto: CreateWorkspaceRequestDTO,
    signal?: AbortSignal
  ): Promise<WorkspaceResponseDTO> {
    return this.request<WorkspaceResponseDTO>("/v1/workspaces", {
      method: "POST",
      body: JSON.stringify(dto),
      signal,
    });
  }

  async listWorkspaces(signal?: AbortSignal): Promise<WorkspaceListResponseDTO> {
    return this.request<WorkspaceListResponseDTO>("/v1/workspaces", {
      method: "GET",
      signal,
    });
  }

  async getWorkspace(
    workspaceId: string,
    signal?: AbortSignal
  ): Promise<WorkspaceResponseDTO> {
    return this.request<WorkspaceResponseDTO>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}`,
      {
        method: "GET",
        signal,
      }
    );
  }

  // --- Workspace Invitations API (ADR-019 / Step 03-B) ---
  async createWorkspaceInvitation(
    workspaceId: string,
    dto: CreateWorkspaceInvitationRequestDTO,
    signal?: AbortSignal
  ): Promise<WorkspaceInvitationResponseDTO> {
    return this.request<WorkspaceInvitationResponseDTO>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/invitations`,
      {
        method: "POST",
        body: JSON.stringify(dto),
        signal,
      }
    );
  }

  async listWorkspaceInvitations(
    workspaceId: string,
    options: ListWorkspaceInvitationsOptions = {}
  ): Promise<WorkspaceInvitationListResponseDTO> {
    const params = new URLSearchParams();
    if (options.limit !== undefined) {
      params.set("limit", String(options.limit));
    }
    if (options.cursor) {
      params.set("cursor", options.cursor);
    }
    const query = params.toString();
    const suffix = query ? `?${query}` : "";
    return this.request<WorkspaceInvitationListResponseDTO>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/invitations${suffix}`,
      {
        method: "GET",
        signal: options.signal,
      }
    );
  }

  async resendWorkspaceInvitation(
    workspaceId: string,
    invitationId: string,
    signal?: AbortSignal
  ): Promise<WorkspaceInvitationResponseDTO> {
    return this.request<WorkspaceInvitationResponseDTO>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/invitations/${encodeURIComponent(invitationId)}/resend`,
      {
        method: "POST",
        signal,
      }
    );
  }

  async revokeWorkspaceInvitation(
    workspaceId: string,
    invitationId: string,
    signal?: AbortSignal
  ): Promise<WorkspaceInvitationResponseDTO> {
    return this.request<WorkspaceInvitationResponseDTO>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/invitations/${encodeURIComponent(invitationId)}/revoke`,
      {
        method: "POST",
        signal,
      }
    );
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
