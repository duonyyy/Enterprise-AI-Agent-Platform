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

export interface AgentDraftEdits {
  mode: string;
  modelRef: string;
  instructions: string;
  maxSteps: number;
  maxWallTimeSeconds: number;
  maxTotalTokens: number;
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
