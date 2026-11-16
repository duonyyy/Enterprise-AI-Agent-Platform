import { test } from "node:test";
import assert from "node:assert/strict";
import { AgentRequestScope, mergeAgentDraftEdits } from "../src/lib/agent-draft-state.ts";

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
