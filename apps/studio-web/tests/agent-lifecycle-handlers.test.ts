import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { AgentRequestScope, AgentPromotionScope, AgentPublishScope, AgentValidationScope, mergeAgentDraftEdits, extractEditsFromSpec, calculateThreeWayDraftDiff } from "../src/lib/agent-draft-state.ts";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const source = readFileSync(new URL("../src/app/(console)/w/[workspaceId]/agents/page.tsx", import.meta.url), "utf8");
const parsed = ts.createSourceFile("page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

// Execute the actual page handlers. Do not recreate their control flow in the test.
function loadHandlers(context: Record<string, unknown>, names: string[], fileName = "page.tsx") {
  const activeSource = fileName === "page.tsx" ? parsed : ts.createSourceFile(fileName,
    readFileSync(new URL(`../src/app/(console)/w/[workspaceId]/agents/${fileName}`, import.meta.url), "utf8"),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declarations = new Map<string, string>();
  function visit(node: any) {
    if (ts.isVariableStatement(node)) {
      for (const declaration of node.declarationList.declarations) {
        if (names.includes(declaration.name.getText(activeSource))) {
          declarations.set(declaration.name.getText(activeSource), node.getText(activeSource));
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(activeSource);
  assert.equal(declarations.size, names.length);
  const script = [...declarations.values()].join("\n") + `\nglobalThis.handlers = {${names.join(",")}};`;
  vm.createContext(context);
  vm.runInContext(ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return (context as any).handlers;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function historyContext(api: Record<string, unknown>) {
  const state: Record<string, any> = { versions: [], publications: [], loading: false, loadingMore: false, cursor: null };
  const context: Record<string, any> = {
    useCallback: (fn: unknown) => fn, workspaceId: "workspace",
    requestScope: { current: new AgentRequestScope() },
    editorScope: { current: new AgentRequestScope() },
    historyScope: { current: new AgentRequestScope() }, editorAgentId: { current: "A" },
    defaultApiClient: api, ApiClientError: class extends Error {},
    setIsLoadingHistory: (value: boolean) => state.loading = value,
    setIsLoadingHistoryMore: (value: boolean) => state.loadingMore = value,
    setHistoryError: (value: unknown) => state.error = value,
    setHistoryAgentId: (value: unknown) => state.agentId = value,
    setHistoryNextCursor: (value: unknown) => state.cursor = value,
    setHistoryVersions: (value: any) => state.versions = typeof value === "function" ? value(state.versions) : value,
    setHistoryPublications: (value: unknown) => state.publications = value,
  };
  return { state, context, handlers: loadHandlers(context, ["fetchAgentHistory"]) };
}

test("history: late Agent A success/error/finally cannot overwrite Agent B", async () => {
  const a = deferred<any>(); const b = deferred<any>();
  const { state, context, handlers } = historyContext({
    listAgentVersions: (_workspace: string, agent: string) => agent === "A" ? a.promise : b.promise,
    listAgentPublications: async (_workspace: string, agent: string) => ({ items: [{ agent_id: agent }] }),
  });
  const pendingA = handlers.fetchAgentHistory("A");
  context.editorScope.current.reset(); context.editorAgentId.current = "B";
  const pendingB = handlers.fetchAgentHistory("B");
  a.resolve({ items: [{ agent_id: "A", agent_version_id: "a1" }], next_cursor: null });
  await pendingA;
  assert.equal(state.loading, true, "A finally must not clear B loading");
  b.resolve({ items: [{ agent_id: "B", agent_version_id: "b1" }], next_cursor: null });
  await pendingB;
  assert.equal(state.agentId, "B");
  assert.equal(state.versions[0].agent_id, "B");
  assert.equal(state.publications[0].agent_id, "B");
  assert.equal(state.error, null);
});

test("history: newer refresh owns errors and a closed editor receives no update", async () => {
  const first = deferred<any>(); const second = deferred<any>(); let calls = 0;
  const { context, state, handlers } = historyContext({
    listAgentVersions: () => ++calls === 1 ? first.promise : second.promise,
    listAgentPublications: async () => ({ items: [] }),
  });
  const old = handlers.fetchAgentHistory("A"); const current = handlers.fetchAgentHistory("A");
  first.reject(new Error("old failure")); await old;
  assert.equal(state.error, null); assert.equal(state.loading, true);
  context.editorScope.current.invalidate(); context.editorAgentId.current = null;
  second.resolve({ items: [{ agent_id: "A", agent_version_id: "a1" }], next_cursor: null });
  await current;
  assert.equal(state.versions.length, 0);
});

test("history: cursor reaches older versions and overlapping pages are deduplicated", async () => {
  const requests: any[] = [];
  const { state, handlers } = historyContext({
    listAgentVersions: async (_workspace: string, _agent: string, options: any) => {
      requests.push(options);
      return options.cursor
        ? { items: [{ agent_id: "A", agent_version_id: "v6", version: 6 }, { agent_id: "A", agent_version_id: "v1", version: 1 }], next_cursor: null }
        : { items: Array.from({ length: 25 }, (_, i) => ({ agent_id: "A", agent_version_id: `v${30 - i}`, version: 30 - i })), next_cursor: "older" };
    },
    listAgentPublications: async () => ({ items: [] }),
  });
  await handlers.fetchAgentHistory("A"); assert.equal(state.cursor, "older");
  await handlers.fetchAgentHistory("A", state.cursor);
  assert.equal(requests[1].cursor, "older");
  assert.equal(state.versions.length, 26);
  assert.equal(state.versions.at(-1).version, 1);
  assert.equal(state.cursor, null);
});

test("promotion: close pending request then reopen does not leave loading or accept its late callback", async () => {
  const pending = deferred<any>(); let reconciliationReads = 0;
  const state: Record<string, any> = { loading: false, open: false, toasts: [] };
  const context: Record<string, any> = {
    workspaceId: "workspace", activeDraft: { agent_id: "A" }, historyAgentId: "A",
    promoteTargetVersion: null, promoteEnvironment: "development", historyPublications: [],
    isPromoting: false, promoteConflict: false, isLoadingHistory: false, isLoadingHistoryMore: false, historyError: null,
    requestScope: { current: new AgentRequestScope() }, editorScope: { current: new AgentRequestScope() },
    promoteScope: { current: new AgentPromotionScope() },
    promoteDialogScope: { current: new AgentRequestScope() },
    setPromoteTargetVersion: (v: unknown) => context.promoteTargetVersion = v,
    setPromoteEnvironment: (v: unknown) => context.promoteEnvironment = v,
    setPromoteError: (v: unknown) => state.error = v, setPromoteConflict: (v: boolean) => context.promoteConflict = v,
    setIsPromoteModalOpen: (v: boolean) => state.open = v,
    setIsPromoting: (v: boolean) => { state.loading = v; context.isPromoting = v; },
    defaultApiClient: { promoteAgentVersion: () => pending.promise }, ApiClientError: class extends Error {},
    fetchAgentHistory: async () => { reconciliationReads++; }, fetchAgents: async () => {},
    showToast: (...args: unknown[]) => state.toasts.push(args),
  };
  const handlers = loadHandlers(context, ["handleOpenPromoteModal", "handleClosePromoteModal", "handleConfirmPromote"]);
  handlers.handleOpenPromoteModal({ agent_id: "A", agent_version_id: "v1", version: 1 });
  const request = handlers.handleConfirmPromote(); assert.equal(state.loading, true);
  handlers.handleClosePromoteModal(); assert.equal(state.loading, false);
  handlers.handleOpenPromoteModal({ agent_id: "A", agent_version_id: "v2", version: 2 });
  pending.resolve({ agent_id: "A", environment: "development", version: 1 }); await request;
  assert.equal(state.loading, false); assert.equal(state.open, true);
  assert.equal(context.promoteTargetVersion.agent_version_id, "v2");
  assert.equal(reconciliationReads, 2, "Every reopen reads the authoritative pointer");
  assert.equal(state.toasts.some((args: unknown[]) => args[1] === "success"), false);
});

test("promotion: a late reconciliation cannot clear another dialog's error", async () => {
  const pending = deferred<boolean>(); const dialog = new AgentRequestScope();
  const state = { error: "new-dialog-error", conflict: true };
  const context: Record<string, any> = {
    activeDraft: { agent_id: "A" }, promoteDialogScope: { current: dialog },
    editorScope: { current: new AgentRequestScope() }, requestScope: { current: new AgentRequestScope() },
    fetchAgentHistory: () => pending.promise,
    setPromoteError: (value: string) => state.error = value,
    setPromoteConflict: (value: boolean) => state.conflict = value,
  };
  const handlers = loadHandlers(context, ["handleReloadPromotionState"]);
  const request = handlers.handleReloadPromotionState();
  dialog.reset(); // A closed and another dialog opened while GET was pending.
  pending.resolve(true); await request;
  assert.equal(state.error, "new-dialog-error"); assert.equal(state.conflict, true);
});

test("promotion: changing environment cannot clear an unresolved outcome", () => {
  const state = { environment: "development", error: "outcome unknown" };
  const context = {
    promoteConflict: true,
    setPromoteEnvironment: (value: string) => state.environment = value,
    setPromoteError: (value: string) => state.error = value,
  };
  const handlers = loadHandlers(context, ["handleSetPromoteEnvironment"]);
  handlers.handleSetPromoteEnvironment("staging");
  handlers.handleSetPromoteEnvironment("development");
  assert.equal(context.promoteConflict, true);
  assert.equal(state.error, "outcome unknown");
});

function workshopContext(api: Record<string, unknown>) {
  const updates: string[] = [];
  const context: Record<string, any> = {
    useCallback: (fn: unknown) => fn,
    workspaceId: "workspace", isOpen: true, activeDraft: { agent_id: "A", revision: 1, spec: {} },
    editorLifetime: { current: new AgentRequestScope() }, saveScope: { current: new AgentRequestScope() },
    validationScope: { current: new AgentValidationScope() },
    baseSpecRef: { current: {} },
    editGenerationRef: { current: 0 },
    conflictWorkbenchRef: { current: null },
    latestEditsRef: { current: { mode: "fixed_plan", modelRef: "model", instructions: "instructions", maxSteps: 10, maxWallTimeSeconds: 60, maxTotalTokens: 100 } },
    defaultApiClient: api,
    mode: "fixed_plan", modelRef: "model", instructions: "instructions", maxSteps: 10, maxWallTime: 60, maxTokens: 100,
    baseSpec: {}, extractEditsFromSpec, calculateThreeWayDraftDiff, mergeAgentDraftEdits,
    ApiClientError: class extends Error { status = 412; },
  };
  for (const name of ["setIsSaving", "setSaveError", "setBaseSpec", "setInstructions", "setModelRef", "setMode", "setMaxSteps", "setMaxWallTime", "setMaxTokens", "setConflictWorkbench", "invalidateValidation", "setValidationResult", "setHasEditsSinceValidation", "onDraftSaved", "onShowToast", "setIsValidating", "setValidationError"]) {
    context[name] = () => updates.push(name);
  }
  return { context, updates };
}

test("workshop: a late save for A cannot update the editor, callback, toast or loading after switching", async () => {
  const pending = deferred<any>();
  const { context, updates } = workshopContext({ replaceAgentDraftSpec: () => pending.promise });
  const handlers = loadHandlers(context, ["executeSave"], "agent-workshop.tsx");
  const request = handlers.executeSave({}, 1);
  const initialUpdates = updates.length;
  context.editorLifetime.current.reset(); context.saveScope.current.invalidate();
  pending.resolve({ agent_id: "A", revision: 2, spec: { persona: { instructions: "old" } } });
  await request;
  assert.equal(updates.length, initialUpdates, "All old save callbacks including finally must be detached");
});

test("workshop: a late 412 refetch cannot open a conflict workbench in another editor", async () => {
  const fetched = deferred<any>(); const entered = deferred<boolean>();
  const { context, updates } = workshopContext({
    getAgentDraft: () => { entered.resolve(true); return fetched.promise; },
  });
  context.defaultApiClient.replaceAgentDraftSpec = async () => { throw new context.ApiClientError(); };
  const handlers = loadHandlers(context, ["executeSave"], "agent-workshop.tsx");
  const request = handlers.executeSave({}, 1); await entered.promise;
  const initialUpdates = updates.length;
  context.editorLifetime.current.invalidate(); context.saveScope.current.invalidate();
  fetched.resolve({ agent_id: "A", revision: 2, spec: {} }); await request;
  assert.equal(updates.length, initialUpdates);
});

test("workshop: validation result and finally cannot update a departed workspace", async () => {
  const pending = deferred<any>();
  const { context, updates } = workshopContext({ validateAgentDraft: () => pending.promise });
  const handlers = loadHandlers(context, ["handleValidateDraft"], "agent-workshop.tsx");
  const request = handlers.handleValidateDraft();
  const initialUpdates = updates.length;
  context.editorLifetime.current.invalidate();
  pending.resolve({ agent_id: "A", revision: 1, ready: true, diagnostics: [] }); await request;
  assert.equal(updates.length, initialUpdates);
});

test("publish: no timer invents backend completion before server receipt", async () => {
  const pending = deferred<any>(); const state: Record<string, any> = { receipt: null, publishing: false };
  const saved: any[] = []; const cleared: any[] = [];
  const context: Record<string, any> = {
    actorId: "actor-a", workspaceId: "workspace", publishTargetAgent: { agent_id: "A", display_name: "Agent A" },
    publishRevision: 3, publishIdempotencyKey: "original-key", publishScope: { current: new AgentPublishScope() },
    requestScope: { current: new AgentRequestScope() },
    savePendingPublishIntent: (v: unknown) => saved.push(v), clearPendingPublishIntent: (...args: unknown[]) => cleared.push(args),
    setIsPublishing: (v: boolean) => state.publishing = v, setPublishMessage: () => {},
    setPublishReceipt: (v: unknown) => state.receipt = v,
    setTimeout: () => { throw new Error("Fake progress timer is forbidden"); },
    defaultApiClient: { publishAgent: () => pending.promise }, ApiClientError: class extends Error {},
    showToast: () => {}, fetchAgents: async () => {},
  };
  const handlers = loadHandlers(context, ["handleConfirmPublish"]);
  const request = handlers.handleConfirmPublish();
  assert.equal(state.receipt, null); assert.equal(state.publishing, true);
  assert.equal(saved[0].actorId, "actor-a"); assert.equal(saved[0].operationKey, "original-key");
  pending.resolve({ agent_id: "A", version: 1 }); await request;
  assert.equal(state.receipt.version, 1); assert.equal(state.publishing, false);
  assert.equal(cleared[0].join(":"), "actor-a:workspace:A");
});

test("publish: missing secure randomness neither reuses another dialog key nor erases recovery", () => {
  const changes: string[] = [];
  const context: Record<string, any> = {
    actorId: "actor-a", workspaceId: "workspace", publishTargetAgent: { agent_id: "A", revision: 1 },
    getPendingPublishIntent: () => null,
    generateSecureIdempotencyKey: () => { throw new Error("Secure random generation is unavailable"); },
    showToast: (_message: string, type: string) => assert.equal(type, "error"),
    clearPendingPublishIntent: () => changes.push("erased pending intent"),
  };
  for (const name of ["setPublishMessage", "setPublishReceipt", "setPublishTargetAgent", "setPublishIdempotencyKey", "setPublishRevision", "setPendingCreatedAt", "setIsRecoveredIntent"]) {
    context[name] = () => changes.push(name);
  }
  const handlers = loadHandlers(context, ["createPublishKey", "handleOpenPublishModal", "handleCreateFreshIntent"]);
  handlers.handleOpenPublishModal({ agent_id: "B", revision: 2 });
  handlers.handleCreateFreshIntent();
  assert.equal(changes.length, 0);
});
