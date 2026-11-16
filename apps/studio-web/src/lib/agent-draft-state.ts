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
