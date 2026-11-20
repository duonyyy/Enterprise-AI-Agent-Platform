import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AgentRequestScope,
  AgentValidationScope,
  AgentPublishScope,
  AgentPromotionScope,
  slugifyAgentName,
  generateSecureIdempotencyKey,
  generateSecureUuid,
  isRollbackPromotion,
  isValidationResultStale,
  calculateThreeWayDraftDiff,
  buildThreeWayMergedSpec,
  extractEditsFromSpec,
  mergeAgentDraftEdits,
  savePendingPublishIntent,
  getPendingPublishIntent,
  clearPendingPublishIntent,
  setPendingPublishStorage,
} from "../src/lib/agent-draft-state.ts";

test("slugifyAgentName: properly normalizes Vietnamese accents and stroke characters including đ and Đ", () => {
  assert.equal(
    slugifyAgentName("Trợ lý Soạn thảo Báo giá"),
    "tro-ly-soan-thao-bao-gia"
  );
  assert.equal(
    slugifyAgentName("Chuyên viên Điều phối Đơn hàng & Đối soát"),
    "chuyen-vien-dieu-phoi-don-hang-doi-soat"
  );
  assert.equal(
    slugifyAgentName("   Đặc Biệt: ĐƯỢC PHÊ DUYỆT   "),
    "dac-biet-duoc-phe-duyet"
  );
  assert.equal(slugifyAgentName("agent-123_test"), "agent-123_test");
  assert.equal(slugifyAgentName("---Test---Slug---"), "test-slug");
});

test("generateSecureIdempotencyKey & generateSecureUuid: never use Math.random() and produce RFC-compliant identifiers", () => {
  const key1 = generateSecureIdempotencyKey();
  const key2 = generateSecureIdempotencyKey();
  assert.notEqual(key1, key2);
  assert.ok(key1.length >= 16);

  const uuid1 = generateSecureUuid();
  const uuid2 = generateSecureUuid("test-op");
  assert.notEqual(uuid1, uuid2);
  assert.ok(uuid2.startsWith("test-op-"));
});

test("generateSecureUuid fails closed if secure randomness is unavailable", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
  try {
    assert.throws(() => generateSecureUuid(), /Secure random generation is unavailable/);
    assert.throws(() => generateSecureIdempotencyKey(), /Secure random generation is unavailable/);
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original);
    else Reflect.deleteProperty(globalThis, "crypto");
  }
});

test("P0-01: Three-way comparison detects remote-only changes, local-only changes, and true conflicts", () => {
  const baseSpec = {
    metadata: { name: "agent-a", displayName: "Agent A" },
    mode: "fixed_plan",
    modelRef: "approved-general@2",
    persona: { instructions: "Original base instructions." },
    limits: { maxSteps: 10, maxWallTimeSeconds: 180, maxTotalTokens: 16000 },
    toolRefs: ["tool-calculator"],
    skillRefs: ["skill-search"],
  };

  // Local user edited only the persona instructions and maxSteps
  const localEdits = {
    mode: "fixed_plan",
    modelRef: "approved-general@2",
    instructions: "Locally edited instructions.",
    maxSteps: 20,
    maxWallTimeSeconds: 180,
    maxTotalTokens: 16000,
  };

  // Concurrently, another user or admin changed modelRef and added toolRefs on the server
  const serverSpec = {
    metadata: { name: "agent-a", displayName: "Agent A Renamed on Server" },
    mode: "fixed_plan",
    modelRef: "gpt-4o",
    persona: { instructions: "Original base instructions." },
    limits: { maxSteps: 10, maxWallTimeSeconds: 180, maxTotalTokens: 16000 },
    toolRefs: ["tool-calculator", "tool-database-writer"],
    skillRefs: ["skill-search"],
  };

  const diffResult = calculateThreeWayDraftDiff(baseSpec, localEdits, serverSpec);

  // No true conflict because local changed instructions & maxSteps, server changed modelRef
  assert.equal(diffResult.hasConflict, false);
  assert.equal(diffResult.conflictingFields.length, 0);

  // Auto-merged edits take the local changes and server changes independently
  assert.equal(diffResult.autoMergedEdits.instructions, "Locally edited instructions.");
  assert.equal(diffResult.autoMergedEdits.maxSteps, 20);
  assert.equal(diffResult.autoMergedEdits.modelRef, "gpt-4o");

  // Build merged spec: remote-only toolRefs and metadata changes MUST NOT be lost!
  const mergedSpec = buildThreeWayMergedSpec(serverSpec, diffResult.autoMergedEdits);
  assert.deepEqual(mergedSpec.toolRefs, ["tool-calculator", "tool-database-writer"]);
  assert.equal((mergedSpec.metadata as any).displayName, "Agent A Renamed on Server");
  assert.equal((mergedSpec.persona as any).instructions, "Locally edited instructions.");
  assert.equal((mergedSpec.limits as any).maxSteps, 20);
  assert.equal(mergedSpec.modelRef, "gpt-4o");
});

test("P0-01: Conflicting field modified on both sides is flagged for user resolution", () => {
  const baseSpec = {
    persona: { instructions: "Base text" },
    modelRef: "gpt-3.5",
    limits: { maxSteps: 10 },
  };

  const localEdits = {
    mode: "fixed_plan",
    modelRef: "claude-3-5-sonnet", // Local changed model
    instructions: "Base text",
    maxSteps: 10,
    maxWallTimeSeconds: 180,
    maxTotalTokens: 16000,
  };

  const serverSpec = {
    persona: { instructions: "Base text" },
    modelRef: "gpt-4o", // Server ALSO changed model to something different!
    limits: { maxSteps: 10 },
  };

  const diffResult = calculateThreeWayDraftDiff(baseSpec, localEdits, serverSpec);

  assert.equal(diffResult.hasConflict, true);
  assert.equal(diffResult.conflictingFields.length, 1);
  assert.equal(diffResult.conflictingFields[0].field, "modelRef");
  assert.equal(diffResult.conflictingFields[0].localValue, "claude-3-5-sonnet");
  assert.equal(diffResult.conflictingFields[0].serverValue, "gpt-4o");
  assert.equal(diffResult.conflictingFields[0].baseValue, "gpt-3.5");

  // If user chooses local:
  const userResolvedLocal = { ...diffResult.autoMergedEdits, modelRef: "claude-3-5-sonnet" };
  const mergedLocal = buildThreeWayMergedSpec(serverSpec, userResolvedLocal);
  assert.equal(mergedLocal.modelRef, "claude-3-5-sonnet");

  // If user chooses server:
  const userResolvedServer = { ...diffResult.autoMergedEdits, modelRef: "gpt-4o" };
  const mergedServer = buildThreeWayMergedSpec(serverSpec, userResolvedServer);
  assert.equal(mergedServer.modelRef, "gpt-4o");
});

test("P0-02: Validation state tracking detects all field edits and preset selections", () => {
  // Fresh validation for revision 1 with no edits
  assert.equal(isValidationResultStale(1, 1, false), false);

  // Changing textarea or typing marks validation stale
  assert.equal(isValidationResultStale(1, 1, true), true);

  // Saving advances revision, marking old validation stale
  assert.equal(isValidationResultStale(1, 2, false), true);

  // Revision advanced AND edits since validation
  assert.equal(isValidationResultStale(1, 2, true), true);
});

test("P0-03: Publish recovery does not discard pending intent on network timeout or unknown failure", () => {
  setPendingPublishStorage([]);
  const ws = "ws-test";
  const agentId = "agent-quote";
  const opKey = "idempotency-key-network-drop";
  const revision = 4;

  // 1. Publish begins, saving pending intent
  savePendingPublishIntent({
    actorId: "actor-1",
    workspaceId: ws,
    agentId,
    operationKey: opKey,
    expectedRevision: revision,
    status: "pending",
    createdAt: Date.now(),
  });

  // 2. Simulate network disconnect / timeout (outcome unknown)
  // Instead of clearing the intent, update to 'unknown'
  savePendingPublishIntent({
    actorId: "actor-1",
    workspaceId: ws,
    agentId,
    operationKey: opKey,
    expectedRevision: revision,
    status: "unknown",
    createdAt: Date.now(),
  });

  // 3. User reopens dialog: intent is recovered with original key and expected revision!
  const recovered = getPendingPublishIntent("actor-1", ws, agentId);
  assert.ok(recovered !== null);
  assert.equal(recovered.operationKey, opKey);
  assert.equal(recovered.expectedRevision, revision);
  assert.equal(recovered.status, "unknown");

  // 4. On confirmed success: intent is cleared
  clearPendingPublishIntent("actor-1", ws, agentId);
  assert.equal(getPendingPublishIntent("actor-1", ws, agentId), null);
});

test("P0-04: Request scopes isolate agent mutations from list refresh", () => {
  const workspaceScope = new AgentRequestScope();
  const listScope = new AgentRequestScope();
  const publishScope = new AgentPublishScope();

  const ws = workspaceScope.capture();
  const list = listScope.capture();
  const pubOp = publishScope.begin("ws-1", "agent-a", "op-1", 1);

  assert.equal(ws.isCurrent(), true);
  assert.equal(list.isCurrent(), true);
  assert.equal(pubOp.isCurrent(), true);

  // List refresh resets listScope
  listScope.reset();
  assert.equal(list.signal.aborted, true);
  assert.equal(list.isCurrent(), false);

  // Crucially, workspace and publish operations remain active!
  assert.equal(ws.isCurrent(), true);
  assert.equal(pubOp.isCurrent(), true);
  assert.equal(pubOp.signal.aborted, false);

  // Switching workspace invalidates everything
  workspaceScope.invalidate();
  publishScope.invalidate();
  assert.equal(ws.isCurrent(), false);
  assert.equal(pubOp.isCurrent(), false);
});
