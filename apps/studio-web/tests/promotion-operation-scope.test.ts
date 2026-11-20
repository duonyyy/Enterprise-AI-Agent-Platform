import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AgentPromotionScope,
  isRollbackPromotion,
} from "../src/lib/agent-draft-state.ts";

test("R04D-01: AgentPromotionScope binds operation lifetime to exact agent, environment, and revision", () => {
  const scope = new AgentPromotionScope();
  const opA = scope.begin("ws-1", "agent-a", "production", 2);

  assert.equal(opA.workspaceId, "ws-1");
  assert.equal(opA.agentId, "agent-a");
  assert.equal(opA.environment, "production");
  assert.equal(opA.expectedRevision, 2);
  assert.equal(opA.isCurrent(), true);
  assert.equal(opA.signal.aborted, false);

  assert.equal(opA.matchesResponse({ agent_id: "agent-a", environment: "production" }), true);
  assert.equal(opA.matchesResponse({ agent_id: "agent-a", environment: "staging" }), false);
  assert.equal(opA.matchesResponse({ agent_id: "agent-b", environment: "production" }), false);

  // Invalidate scope (e.g. dialog close, tab switch, unmount)
  scope.invalidate();
  assert.equal(opA.isCurrent(), false);
  assert.equal(opA.signal.aborted, true);
  assert.equal(opA.matchesResponse({ agent_id: "agent-a", environment: "production" }), false);
});

test("R04D-02: Late promotion callback from operation A cannot affect operation B", async () => {
  const scope = new AgentPromotionScope();
  const opA = scope.begin("ws-1", "agent-a", "staging", 1);

  let resolveOpA!: (val: { agent_id: string; environment: string; revision: number }) => void;
  const promiseA = new Promise<{ agent_id: string; environment: string; revision: number }>((resolve) => {
    resolveOpA = resolve;
  });

  const state = {
    closedModalFor: "" as string,
    errorMessage: "" as string,
    isPromotingB: true,
  };

  // Simulating async dispatch for operation A
  const callbackA = promiseA
    .then((res) => {
      if (opA.isCurrent() && opA.matchesResponse(res)) {
        state.closedModalFor = `${res.agent_id}:${res.environment}`;
      }
    })
    .catch((err: Error) => {
      if (opA.isCurrent()) {
        state.errorMessage = err.message;
      }
    })
    .finally(() => {
      if (opA.isCurrent()) {
        state.isPromotingB = false;
      }
    });

  // User closes modal A or switches to environment 'production' (starts operation B)
  const opB = scope.begin("ws-1", "agent-a", "production", 0);

  // Late response from A arrives
  resolveOpA({ agent_id: "agent-a", environment: "staging", revision: 2 });
  await callbackA;

  // Assert that operation A's late callback had NO effect on state
  assert.equal(state.closedModalFor, "", "Op A did not close modal for Op B");
  assert.equal(state.errorMessage, "");
  assert.equal(state.isPromotingB, true, "Operation B is still actively running");
  assert.equal(opB.isCurrent(), true, "Operation B is the current operation");
  assert.equal(opA.isCurrent(), false, "Operation A is superseded");
});

test("R04D-03: isRollbackPromotion correctly identifies rollbacks vs forward promotions", () => {
  // Target < Current is a rollback
  assert.equal(isRollbackPromotion(1, 3), true, "v1 < v3 is a rollback");
  assert.equal(isRollbackPromotion(2, 5), true, "v2 < v5 is a rollback");

  // Target > Current is forward promotion
  assert.equal(isRollbackPromotion(4, 2), false, "v4 > v2 is forward promotion");

  // Target == Current is a no-op/re-pointing, not a rollback
  assert.equal(isRollbackPromotion(3, 3), false, "v3 == v3 is not a rollback");

  // No active publication (current is null or undefined) is an initial promotion
  assert.equal(isRollbackPromotion(1, null), false, "null current is not a rollback");
  assert.equal(isRollbackPromotion(1, undefined), false, "undefined current is not a rollback");
});

test("R04D-04: CAS conflict simulation triggers conflict state without corrupting scope", () => {
  const scope = new AgentPromotionScope();
  const op = scope.begin("ws-test", "agent-uuid-1", "production", 1);

  // Simulate 412 Precondition Failed
  const error412 = {
    status: 412,
    code: "AGENT_STALE_REVISION",
    message: "Pointer revision mismatch. Expected 1, but found 2.",
  };

  let conflictDetected = false;
  if (op.isCurrent()) {
    if (error412.status === 412) {
      conflictDetected = true;
    }
  }

  assert.equal(conflictDetected, true);
  assert.equal(op.isCurrent(), true);

  // Refetching history invalidates stale op and begins new op with revised revision
  scope.invalidate();
  assert.equal(op.isCurrent(), false);

  const opRetry = scope.begin("ws-test", "agent-uuid-1", "production", 2);
  assert.equal(opRetry.expectedRevision, 2);
  assert.equal(opRetry.isCurrent(), true);
});
