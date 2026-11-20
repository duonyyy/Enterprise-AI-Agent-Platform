/** Generate a cryptographically secure UUID or key without Math.random() */
export function generateSecureUuid(prefix?: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    const uuid = crypto.randomUUID();
    return prefix ? `${prefix}-${uuid}` : uuid;
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // RFC 4122 v4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    const uuid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    return prefix ? `${prefix}-${uuid}` : uuid;
  }
  throw new Error("Secure random generation is unavailable in this environment");
}

export function generateSecureIdempotencyKey(prefix = "pub"): string {
  return generateSecureUuid(prefix);
}

/** Slug generator with proper Vietnamese diacritics and 'đ' / 'Đ' stroke handling */
export function slugifyAgentName(name: string): string {
  return name
    .toLowerCase()
    .replace(/đ/g, "d")
    .replace(/Đ/g, "d")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
}
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

    const operationId = generateSecureUuid("pub-op");

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
 * Tracks an active promotion/rollback dialog/operation lifetime (Step 04-D).
 * Guarantees that late promotion responses do not affect other operations or workspaces.
 */
export interface ActivePromotionOperation {
  operationId: string;
  workspaceId: string;
  agentId: string;
  environment: string;
  expectedRevision: number;
  signal: AbortSignal;
  isCurrent: () => boolean;
  matchesResponse: (result: { agent_id: string; environment: string }) => boolean;
}

export class AgentPromotionScope {
  private controller = new AbortController();
  private currentOperationId: string | null = null;

  begin(
    workspaceId: string,
    agentId: string,
    environment: string,
    expectedRevision: number
  ): ActivePromotionOperation {
    this.controller.abort();
    this.controller = new AbortController();

    const operationId = generateSecureUuid("promo-op");

    this.currentOperationId = operationId;
    const signal = this.controller.signal;

    return {
      operationId,
      workspaceId,
      agentId,
      environment,
      expectedRevision,
      signal,
      isCurrent: (): boolean =>
        !signal.aborted && this.currentOperationId === operationId,
      matchesResponse: (result: { agent_id: string; environment: string }): boolean =>
        !signal.aborted &&
        this.currentOperationId === operationId &&
        result.agent_id === agentId &&
        result.environment === environment,
    };
  }

  invalidate(): void {
    this.controller.abort();
    this.currentOperationId = null;
  }
}

/**
 * Checks if a promotion is a rollback (target version number < current active version number).
 */
export function isRollbackPromotion(
  targetVersion: number,
  currentVersion: number | null | undefined
): boolean {
  if (currentVersion === null || currentVersion === undefined) return false;
  return targetVersion < currentVersion;
}

/**
 * Pending publish intent for recovery across dialog close/reopen or reload (R04C-04).
 * Stores only non-sensitive recovery metadata (exact operation key and expected revision).
 * Session tokens, credentials, or raw spec are strictly excluded.
 */
export interface PendingPublishIntent {
  actorId: string;
  workspaceId: string;
  agentId: string;
  operationKey: string;
  expectedRevision: number;
  status: "pending" | "succeeded" | "failed" | "unknown";
  createdAt: number;
}

// Legacy entries cannot be safely attributed to an actor. Never adopt them for
// the next account signing into the same tab.
const PENDING_PUBLISH_STORAGE_KEY = "enterprise_pending_publish_intents_v2";
let memoryIntentStore: PendingPublishIntent[] = [];

export function getPendingPublishStorage(): PendingPublishIntent[] {
  if (typeof sessionStorage !== "undefined") {
    try {
      const raw = sessionStorage.getItem(PENDING_PUBLISH_STORAGE_KEY);
      if (!raw) return [];
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((entry): entry is PendingPublishIntent =>
        entry !== null && typeof entry === "object" &&
        typeof entry.actorId === "string" && entry.actorId.length > 0 &&
        typeof entry.workspaceId === "string" &&
        typeof entry.agentId === "string" &&
        typeof entry.operationKey === "string" &&
        Number.isInteger(entry.expectedRevision) && entry.expectedRevision > 0 &&
        ["pending", "succeeded", "failed", "unknown"].includes(entry.status) &&
        typeof entry.createdAt === "number" && Number.isFinite(entry.createdAt)
      );
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
    (i) => !(i.actorId === intent.actorId && i.workspaceId === intent.workspaceId && i.agentId === intent.agentId)
  );
  filtered.push(intent);
  setPendingPublishStorage(filtered);
}

export function getPendingPublishIntent(
  actorId: string,
  workspaceId: string,
  agentId: string
): PendingPublishIntent | null {
  const current = getPendingPublishStorage();
  return current.find((i) => i.actorId === actorId && i.workspaceId === workspaceId && i.agentId === agentId) ?? null;
}

export function clearPendingPublishIntent(
  actorId: string,
  workspaceId: string,
  agentId: string
): void {
  const current = getPendingPublishStorage();
  const filtered = current.filter(
    (i) => !(i.actorId === actorId && i.workspaceId === workspaceId && i.agentId === agentId)
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

/** Estimate approximate LLM token count from text length. */
export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 3.8);
}

/** Lightweight heuristic check for potential prompt injection or jailbreak patterns. */
export function detectPromptSafetyRisk(instructions: string): {
  hasRisk: boolean;
  reason?: string;
} {
  const lower = instructions.toLowerCase();
  const patterns = [
    {
      regex: /ignore (all )?(previous|above) instructions/i,
      reason: "Phát hiện chỉ lệnh bỏ qua tiền đề (Instruction Override)",
    },
    {
      regex: /disregard.*guidelines/i,
      reason: "Phát hiện cụm từ vô hiệu hóa ranh giới an toàn",
    },
    {
      regex: /you are now in developer mode/i,
      reason: "Phát hiện kịch bản Developer Mode Jailbreak",
    },
    {
      regex: /system prompt leak|repeat your system prompt/i,
      reason: "Phát hiện yêu cầu trích xuất System Prompt",
    },
  ];
  for (const p of patterns) {
    if (p.regex.test(lower)) {
      return { hasRisk: true, reason: p.reason };
    }
  }
  return { hasRisk: false };
}

export interface CategorizedDiagnostic {
  field: string;
  message: string;
  code: string;
  severity: "critical" | "warning";
  tabTarget: "persona" | "model" | "dependencies";
}

/** Categorize diagnostics into Critical Blockers vs Warnings, mapping to their UI tab. */
export function categorizeDiagnostics(
  diagnostics: Array<{ field: string; message: string; code: string }>
): CategorizedDiagnostic[] {
  return diagnostics.map((d) => {
    const fieldLower = (d.field || "").toLowerCase();
    const codeLower = (d.code || "").toLowerCase();
    const isCritical =
      codeLower.includes("missing") ||
      codeLower.includes("forbidden") ||
      codeLower.includes("invalid") ||
      codeLower.includes("required") ||
      codeLower.includes("conflict") ||
      codeLower.includes("error");

    let tabTarget: "persona" | "model" | "dependencies" = "persona";
    if (
      fieldLower.includes("limit") ||
      fieldLower.includes("step") ||
      fieldLower.includes("time") ||
      fieldLower.includes("token") ||
      fieldLower.includes("model") ||
      fieldLower.includes("mode")
    ) {
      tabTarget = "model";
    } else if (
      fieldLower.includes("tool") ||
      fieldLower.includes("skill") ||
      fieldLower.includes("knowledge")
    ) {
      tabTarget = "dependencies";
    }

    return {
      ...d,
      severity: isCritical ? "critical" : "warning",
      tabTarget,
    };
  });
}

export interface DraftComparisonDiff {
  field: string;
  label: string;
  serverValue: string;
  localValue: string;
  isDifferent: boolean;
}

/** Compare server spec with uncommitted local edits for side-by-side conflict resolution. */
export function compareDraftStates(
  serverSpec: Record<string, unknown> | null,
  localEdits: AgentDraftEdits
): DraftComparisonDiff[] {
  if (!serverSpec) return [];
  const serverPersona =
    (serverSpec.persona as { instructions?: string }) || {};
  const serverLimits =
    (serverSpec.limits as {
      maxSteps?: number;
      maxWallTimeSeconds?: number;
      maxTotalTokens?: number;
    }) || {};

  return [
    {
      field: "modelRef",
      label: "Model Reference",
      serverValue: String(serverSpec.modelRef || ""),
      localValue: localEdits.modelRef,
      isDifferent: String(serverSpec.modelRef || "") !== localEdits.modelRef,
    },
    {
      field: "mode",
      label: "Execution Mode",
      serverValue: String(serverSpec.mode || "fixed_plan"),
      localValue: localEdits.mode,
      isDifferent:
        String(serverSpec.mode || "fixed_plan") !== localEdits.mode,
    },
    {
      field: "maxSteps",
      label: "Max Steps",
      serverValue: String(serverLimits.maxSteps ?? 10),
      localValue: String(localEdits.maxSteps),
      isDifferent:
        Number(serverLimits.maxSteps ?? 10) !== localEdits.maxSteps,
    },
    {
      field: "maxWallTimeSeconds",
      label: "Max Wall Time (s)",
      serverValue: String(serverLimits.maxWallTimeSeconds ?? 180),
      localValue: String(localEdits.maxWallTimeSeconds),
      isDifferent:
        Number(serverLimits.maxWallTimeSeconds ?? 180) !==
        localEdits.maxWallTimeSeconds,
    },
    {
      field: "maxTotalTokens",
      label: "Max Tokens",
      serverValue: String(serverLimits.maxTotalTokens ?? 16000),
      localValue: String(localEdits.maxTotalTokens),
      isDifferent:
        Number(serverLimits.maxTotalTokens ?? 16000) !==
        localEdits.maxTotalTokens,
    },
    {
      field: "instructions",
      label: "Persona Instructions",
      serverValue: serverPersona.instructions || "",
      localValue: localEdits.instructions,
      isDifferent:
        (serverPersona.instructions || "").trim() !==
        localEdits.instructions.trim(),
    },
  ];
}

export interface ThreeWayFieldDiff {
  field: keyof AgentDraftEdits;
  label: string;
  baseValue: string;
  serverValue: string;
  localValue: string;
  hasLocalChange: boolean;
  hasServerChange: boolean;
  isConflict: boolean;
  autoMergeValue: string | number;
}

export interface ThreeWayDiffResult {
  hasConflict: boolean;
  conflictingFields: ThreeWayFieldDiff[];
  diffs: ThreeWayFieldDiff[];
  autoMergedEdits: AgentDraftEdits;
}

export function extractEditsFromSpec(
  spec: Record<string, unknown> | null | undefined
): AgentDraftEdits {
  if (!spec) {
    return {
      mode: "fixed_plan",
      modelRef: "",
      instructions: "",
      maxSteps: 10,
      maxWallTimeSeconds: 180,
      maxTotalTokens: 16000,
    };
  }
  const persona = (spec.persona as { instructions?: string }) || {};
  const limits = (spec.limits as {
    maxSteps?: number;
    maxWallTimeSeconds?: number;
    maxTotalTokens?: number;
  }) || {};

  return {
    mode: String(spec.mode || "fixed_plan"),
    modelRef: String(spec.modelRef || ""),
    instructions: persona.instructions || "",
    maxSteps: Number(limits.maxSteps ?? 10),
    maxWallTimeSeconds: Number(limits.maxWallTimeSeconds ?? 180),
    maxTotalTokens: Number(limits.maxTotalTokens ?? 16000),
  };
}

export function calculateThreeWayDraftDiff(
  baseSpec: Record<string, unknown> | null,
  localEdits: AgentDraftEdits,
  serverSpec: Record<string, unknown> | null
): ThreeWayDiffResult {
  const baseEdits = extractEditsFromSpec(baseSpec);
  const serverEdits = extractEditsFromSpec(serverSpec);

  const fieldDefs: Array<{ field: keyof AgentDraftEdits; label: string }> = [
    { field: "instructions", label: "Persona Instructions" },
    { field: "modelRef", label: "Model Reference" },
    { field: "mode", label: "Execution Mode" },
    { field: "maxSteps", label: "Max Steps" },
    { field: "maxWallTimeSeconds", label: "Max Wall Time (s)" },
    { field: "maxTotalTokens", label: "Max Tokens" },
  ];

  const diffs: ThreeWayFieldDiff[] = [];
  const conflictingFields: ThreeWayFieldDiff[] = [];
  const autoMergedEdits: AgentDraftEdits = { ...localEdits };

  for (const def of fieldDefs) {
    const field = def.field;
    const baseVal = baseEdits[field];
    const serverVal = serverEdits[field];
    const localVal = localEdits[field];

    const hasLocalChange = String(localVal).trim() !== String(baseVal).trim();
    const hasServerChange = String(serverVal).trim() !== String(baseVal).trim();
    const isConflict =
      hasLocalChange &&
      hasServerChange &&
      String(localVal).trim() !== String(serverVal).trim();

    let autoMergeValue: string | number;
    if (isConflict) {
      autoMergeValue = localVal;
    } else if (hasLocalChange) {
      autoMergeValue = localVal;
    } else if (hasServerChange) {
      autoMergeValue = serverVal;
    } else {
      autoMergeValue = baseVal;
    }

    if (
      field === "maxSteps" ||
      field === "maxWallTimeSeconds" ||
      field === "maxTotalTokens"
    ) {
      autoMergedEdits[field] = Number(autoMergeValue);
    } else {
      autoMergedEdits[field] = String(autoMergeValue);
    }

    const item: ThreeWayFieldDiff = {
      field,
      label: def.label,
      baseValue: String(baseVal),
      serverValue: String(serverVal),
      localValue: String(localVal),
      hasLocalChange,
      hasServerChange,
      isConflict,
      autoMergeValue,
    };

    diffs.push(item);
    if (isConflict) {
      conflictingFields.push(item);
    }
  }

  return {
    hasConflict: conflictingFields.length > 0,
    conflictingFields,
    diffs,
    autoMergedEdits,
  };
}

/**
 * Builds a merged AgentSpec retaining all server-side fields (toolRefs, skillRefs, knowledgeRefs, metadata, extensions)
 * while safely applying resolved form edits.
 */
export function buildThreeWayMergedSpec(
  serverSpec: Record<string, unknown>,
  resolvedEdits: AgentDraftEdits
): Record<string, unknown> {
  return mergeAgentDraftEdits(serverSpec, resolvedEdits);
}
