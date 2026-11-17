import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AgentPublishScope,
  savePendingPublishIntent,
  getPendingPublishIntent,
  clearPendingPublishIntent,
  getPendingPublishStorage,
  setPendingPublishStorage,
  type PendingPublishIntent,
} from "../src/lib/agent-draft-state.ts";

test("R04C-05: AgentPublishScope binds operation lifetime to exact agent and operation", () => {
  const scope = new AgentPublishScope();
  const opA = scope.begin("ws-1", "agent-a", "key-a", 1);

  assert.equal(opA.isCurrent(), true);
  assert.equal(opA.signal.aborted, false);
  assert.equal(opA.matchesResponse({ agent_id: "agent-a" }), true);
  assert.equal(opA.matchesResponse({ agent_id: "agent-b" }), false);

  // Invalidate scope (e.g. dialog close, unmount)
  scope.invalidate();
  assert.equal(opA.isCurrent(), false);
  assert.equal(opA.signal.aborted, true);
  assert.equal(opA.matchesResponse({ agent_id: "agent-a" }), false);
});

test("R04C-05: Late publish callback from operation A cannot affect operation B", async () => {
  const scope = new AgentPublishScope();
  const opA = scope.begin("ws-1", "agent-a", "key-a", 1);

  let resolveOpA!: (val: { agent_id: string; version: number }) => void;
  const promiseA = new Promise<{ agent_id: string; version: number }>((resolve) => {
    resolveOpA = resolve;
  });

  const state = {
    closedModalFor: "" as string,
    errorMessage: "" as string,
    isPublishingB: true,
  };

  // Callback simulation for operation A
  const callbackA = promiseA
    .then((res) => {
      if (opA.isCurrent() && opA.matchesResponse(res)) {
        state.closedModalFor = "agent-a";
      }
    })
    .catch((err: Error) => {
      if (opA.isCurrent()) {
        state.errorMessage = err.message;
      }
    })
    .finally(() => {
      if (opA.isCurrent()) {
        state.isPublishingB = false;
      }
    });

  // User closes modal A and opens/starts operation B
  scope.invalidate();
  const opB = scope.begin("ws-1", "agent-b", "key-b", 1);

  // Now late response from A arrives
  resolveOpA({ agent_id: "agent-a", version: 2 });
  await callbackA;

  // Assert that operation A's late callback had NO effect
  assert.equal(state.closedModalFor, "");
  assert.equal(state.errorMessage, "");
  assert.equal(state.isPublishingB, true, "Operation B spinner was NOT cleared by late opA finally");
  assert.equal(opB.isCurrent(), true, "Operation B is still the active current operation");
  assert.equal(opA.isCurrent(), false);
});

test("R04C-04: Pending publish intent is preserved across close/reopen and recovers exact key/revision", () => {
  // Clear any existing store state
  setPendingPublishStorage([]);

  const ws = "ws-prod";
  const agentId = "agent-quote-assistant";
  const opKey = "pub-key-12345678-abcd";
  const expectedRevision = 3;

  // 1. Save pending intent when publish begins
  const intent: PendingPublishIntent = {
    workspaceId: ws,
    agentId,
    operationKey: opKey,
    expectedRevision,
    status: "pending",
    createdAt: Date.now(),
  };
  savePendingPublishIntent(intent);

  // 2. Simulate dialog close and reopen for the same agent
  const retrieved = getPendingPublishIntent(ws, agentId);
  assert.ok(retrieved !== null);
  assert.equal(retrieved.operationKey, opKey, "Exact operation key preserved");
  assert.equal(retrieved.expectedRevision, expectedRevision, "Exact expected revision preserved");
  assert.equal(retrieved.status, "pending");

  // 3. Opening dialog for a DIFFERENT agent returns null (generates fresh intent)
  const otherAgent = getPendingPublishIntent(ws, "agent-other-uuid");
  assert.equal(otherAgent, null);

  // 4. Opening in a DIFFERENT workspace returns null
  const otherWs = getPendingPublishIntent("ws-different", agentId);
  assert.equal(otherWs, null);

  // 5. Explicit clear (e.g. after successful commit or when user clicks 'New Intent')
  clearPendingPublishIntent(ws, agentId);
  assert.equal(getPendingPublishIntent(ws, agentId), null);
});

test("R04C-04: Retry of unresolved intent preserves old revision even if draft was edited", () => {
  setPendingPublishStorage([]);

  const ws = "ws-1";
  const agentId = "agent-1";
  const originalKey = "pub-idempotent-key-original";
  const originalRevision = 2;

  // Save pending intent for revision 2
  savePendingPublishIntent({
    workspaceId: ws,
    agentId,
    operationKey: originalKey,
    expectedRevision: originalRevision,
    status: "pending",
    createdAt: Date.now(),
  });

  // Suppose draft revision was incremented to 3 by another session or user
  const currentDraftRevision = 3;

  // When recovering pending intent, it must yield originalRevision (2), not currentDraftRevision (3)
  const pending = getPendingPublishIntent(ws, agentId);
  assert.ok(pending);
  assert.equal(pending.expectedRevision, originalRevision);
  assert.notEqual(pending.expectedRevision, currentDraftRevision);
  assert.equal(pending.operationKey, originalKey);
});
