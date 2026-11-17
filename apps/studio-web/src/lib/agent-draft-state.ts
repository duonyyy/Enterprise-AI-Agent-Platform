/** Ignore both successes and errors belonging to a previous workspace lifetime. */
export class AgentRequestScope {
  private controller = new AbortController();

  reset(): void {
    this.controller.abort();
    this.controller = new AbortController();
  }

  invalidate(): void { this.controller.abort(); }

  capture(): { signal: AbortSignal; isCurrent: () => boolean } {
    const signal = this.controller.signal;
    return { signal, isCurrent: () => !signal.aborted };
  }
}

/** Local request ownership, not a canonical digest or a published content hash. */
export class AgentValidationScope extends AgentRequestScope {
  begin(agentId: string, revision: number, spec: Record<string, unknown>) {
    this.reset();
    const request = this.capture();
    const specSignature = JSON.stringify(spec);
    return {
      ...request,
      agentId,
      revision,
      specSignature,
      matchesResponse: (result: { agent_id: string; revision: number }): boolean =>
        request.isCurrent() && result.agent_id === agentId && result.revision === revision,
    };
  }
}

/**
 * Tracks an active publish dialog/operation lifetime and exact Agent/key/payload binding.
 * Guarantees that late publish responses from operation A do not affect operation B (R04C-05).
 */
export interface ActivePublishOperation {
  operationId: string;
  workspaceId: string;
  agentId: string;
  operationKey: string;
  expectedRevision: number;
  signal: AbortSignal;
  isCurrent: () => boolean;
  matchesResponse: (result: { agent_id: string }) => boolean;
}

export class AgentPublishScope {
  private controller = new AbortController();
  private currentOperationId: string | null = null;

  begin(
    workspaceId: string,
    agentId: string,
    operationKey: string,
    expectedRevision: number
  ): ActivePublishOperation {
    this.controller.abort();
    this.controller = new AbortController();

    const operationId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `pub-op-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;

    this.currentOperationId = operationId;
    const signal = this.controller.signal;

    return {
      operationId,
      workspaceId,
      agentId,
      operationKey,
      expectedRevision,
      signal,
      isCurrent: (): boolean =>
        !signal.aborted && this.currentOperationId === operationId,
      matchesResponse: (result: { agent_id: string }): boolean =>
        !signal.aborted &&
        this.currentOperationId === operationId &&
        result.agent_id === agentId,
    };
  }

  invalidate(): void {
    this.controller.abort();
    this.currentOperationId = null;
  }
}

/**
 * Pending publish intent for recovery across dialog close/reopen or reload (R04C-04).
 * Stores only non-sensitive recovery metadata (exact operation key and expected revision).
 * Session tokens, credentials, or raw spec are strictly excluded.
 */
export interface PendingPublishIntent {
  workspaceId: string;
  agentId: string;
  operationKey: string;
  expectedRevision: number;
  status: "pending" | "succeeded" | "failed" | "unknown";
  createdAt: number;
}

const PENDING_PUBLISH_STORAGE_KEY = "enterprise_pending_publish_intents";
let memoryIntentStore: PendingPublishIntent[] = [];

export function getPendingPublishStorage(): PendingPublishIntent[] {
  if (typeof sessionStorage !== "undefined") {
    try {
      const raw = sessionStorage.getItem(PENDING_PUBLISH_STORAGE_KEY);
      if (!raw) return [];
      return JSON.parse(raw) as PendingPublishIntent[];
    } catch {
      return memoryIntentStore;
    }
  }
  return memoryIntentStore;
}

export function setPendingPublishStorage(intents: PendingPublishIntent[]): void {
  memoryIntentStore = intents;
  if (typeof sessionStorage !== "undefined") {
    try {
      sessionStorage.setItem(PENDING_PUBLISH_STORAGE_KEY, JSON.stringify(intents));
    } catch {
      // Ignore quota or private-mode errors
    }
  }
}

export function savePendingPublishIntent(intent: PendingPublishIntent): void {
  const current = getPendingPublishStorage();
  const filtered = current.filter(
    (i) => !(i.workspaceId === intent.workspaceId && i.agentId === intent.agentId)
  );
  filtered.push(intent);
  setPendingPublishStorage(filtered);
}

export function getPendingPublishIntent(
  workspaceId: string,
  agentId: string
): PendingPublishIntent | null {
  const current = getPendingPublishStorage();
  return current.find((i) => i.workspaceId === workspaceId && i.agentId === agentId) ?? null;
}

export function clearPendingPublishIntent(
  workspaceId: string,
  agentId: string
): void {
  const current = getPendingPublishStorage();
  const filtered = current.filter(
    (i) => !(i.workspaceId === workspaceId && i.agentId === agentId)
  );
  setPendingPublishStorage(filtered);
}

export interface AgentDraftEdits {
  mode: string;
  modelRef: string;
  instructions: string;
  maxSteps: number;
  maxWallTimeSeconds: number;
  maxTotalTokens: number;
}

/** Check whether a validation result has become stale due to revision changes or form edits. */
export function isValidationResultStale(
  validationRevision: number | null,
  currentRevision: number,
  hasEditsSinceValidation: boolean
): boolean {
  if (validationRevision === null) return false;
  return hasEditsSinceValidation || validationRevision !== currentRevision;
}

/** A full-spec replacement retains fields the small form does not own. */
export function mergeAgentDraftEdits(
  spec: Record<string, unknown>, edits: AgentDraftEdits,
): Record<string, unknown> {
  const record = (value: unknown): Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
  return {
    ...spec,
    mode: edits.mode,
    modelRef: edits.modelRef,
    persona: { ...record(spec.persona), instructions: edits.instructions },
    limits: {
      ...record(spec.limits), maxSteps: edits.maxSteps,
      maxWallTimeSeconds: edits.maxWallTimeSeconds, maxTotalTokens: edits.maxTotalTokens,
    },
  };
}
