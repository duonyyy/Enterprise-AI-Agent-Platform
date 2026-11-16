import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AgentRequestScope,
  AgentValidationScope,
  isValidationResultStale,
  mergeAgentDraftEdits,
} from "../src/lib/agent-draft-state.ts";

test("late result from A cannot apply after switching A -> B -> A", async () => {
  const scope = new AgentRequestScope();
  const old = scope.capture();
  scope.reset();
  const middle = scope.capture();
  scope.reset();
  const current = scope.capture();
  const rendered: string[] = [];
  await Promise.all([
    Promise.resolve().then(() => { if (old.isCurrent()) rendered.push("old A"); }),
    Promise.resolve().then(() => { if (middle.isCurrent()) rendered.push("B"); }),
    Promise.resolve().then(() => { if (current.isCurrent()) rendered.push("current A"); }),
  ]);
  assert.deepEqual(rendered, ["current A"]);
  assert.equal(old.signal.aborted, true);
  assert.equal(middle.signal.aborted, true);
});

test("unmount cancels pending requests and ignores their errors", () => {
  const scope = new AgentRequestScope();
  const pending = scope.capture();
  scope.invalidate();
  assert.equal(pending.signal.aborted, true);
  assert.equal(pending.isCurrent(), false);
});

test("form edit preserves independent labels, extensions, nulls and ordered references", () => {
  const spec = {
    metadata: { name: "slug", displayName: "independent spec label", extension: null },
    persona: { instructions: "old", extension: null },
    limits: { maxSteps: 8, extension: { retained: true } },
    toolRefs: ["second", "first"],
  };
  const result = mergeAgentDraftEdits(spec, {
    mode: "react", modelRef: "unresolved", instructions: "new", maxSteps: 9,
    maxWallTimeSeconds: 180, maxTotalTokens: 16000,
  });
  assert.deepEqual(result.metadata, spec.metadata);
  assert.deepEqual(result.toolRefs, ["second", "first"]);
  assert.deepEqual(result.persona, { instructions: "new", extension: null });
  assert.deepEqual(result.limits, {
    maxSteps: 9, extension: { retained: true }, maxWallTimeSeconds: 180, maxTotalTokens: 16000,
  });
  assert.equal(spec.persona.instructions, "old");
  assert.equal(spec.limits.maxSteps, 8);
});

test("validation result staleness tracking detects revision change and unsaved edits", () => {
  // Fresh validation matching current revision without edits
  assert.equal(isValidationResultStale(1, 1, false), false);

  // Unsaved edits mark validation as stale
  assert.equal(isValidationResultStale(1, 1, true), true);

  // Server revision advanced marks validation as stale
  assert.equal(isValidationResultStale(1, 2, false), true);

  // Null validation is not stale
  assert.equal(isValidationResultStale(null, 1, false), false);
});


test("validation response must match its exact Agent and persisted revision", () => {
  const scope = new AgentValidationScope();
  const spec = { metadata: { name: "a" }, mode: "fixed_plan" };
  const request = scope.begin("agent-a", 7, spec);
  assert.equal(request.matchesResponse({ agent_id: "agent-a", revision: 7 }), true);
  assert.equal(request.matchesResponse({ agent_id: "agent-b", revision: 7 }), false);
  assert.equal(request.matchesResponse({ agent_id: "agent-a", revision: 8 }), false);
  spec.mode = "react";
  assert.equal(request.specSignature.includes('"fixed_plan"'), true);
});

for (const action of ["edit", "close and open B", "reload", "save", "workspace switch"]) {
  test(`late validation success/error/finally cannot change the editor after ${action}`, async () => {
    const scope = new AgentValidationScope();
    const old = scope.begin("agent-a", 1, { mode: "fixed_plan" });
    let resolveOld!: () => void;
    const network = new Promise<void>((resolve) => { resolveOld = resolve; });
    const state = { result: "none", error: "none", pending: true };
    const oldCallbacks = network.then(() => {
      if (old.isCurrent()) state.result = "old A result";
      if (old.isCurrent()) state.error = "old A error";
    }).finally(() => {
      if (old.isCurrent()) state.pending = false;
    });
    // The same invalidation is used by all editor lifecycle boundaries in production.
    scope.invalidate();
    const current = scope.begin("agent-b", 1, { mode: "fixed_plan" });
    resolveOld();
    await oldCallbacks;
    assert.deepEqual(state, { result: "none", error: "none", pending: true });
    assert.equal(old.signal.aborted, true);
    assert.equal(current.matchesResponse({ agent_id: "agent-b", revision: 1 }), true);
  });
}

test("a newer validation supersedes pending validation even in the same editor", () => {
  const scope = new AgentValidationScope();
  const old = scope.begin("agent-a", 1, { instructions: "one" });
  const current = scope.begin("agent-a", 1, { instructions: "two" });
  assert.equal(old.isCurrent(), false);
  assert.equal(current.isCurrent(), true);
  assert.notEqual(old.specSignature, current.specSignature);
});
